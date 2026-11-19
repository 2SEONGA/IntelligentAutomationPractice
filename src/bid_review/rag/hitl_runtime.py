"""로컬 단일 작업자용 SQLite 체크포인트 실행기. 프로세스 종료 후에도 재개 가능하다."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from ..settings import PROJECT_ROOT
from .hitl_review import validate_review_response

DEFAULT_DB = PROJECT_ROOT / "outputs/langgraph_checkpoints/reviews.sqlite3"


def _factory(checkpointer):
    from .workflow import create_bid_review_graph
    return create_bid_review_graph(checkpointer=checkpointer)


def _config(thread_id: str) -> dict[str, Any]:
    if not isinstance(thread_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", thread_id):
        raise ValueError("thread_id는 영문·숫자·밑줄·점·하이픈으로 된 1~101자 식별자여야 합니다.")
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 100}


def _fingerprint(path: str | Path) -> dict[str, str]:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(resolved), "sha256": digest.hexdigest()}


@contextmanager
def _open(db_path, graph_factory):
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # 이 실행기를 통한 동시 재개를 직렬화한다. 다른 thread의 오래된 응답도 별도로 검사한다.
    with path.with_suffix(path.suffix + ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with SqliteSaver.from_conn_string(str(path)) as saver:
                yield graph_factory(saver)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _snapshot(graph, thread_id: str) -> dict[str, Any]:
    snapshot = graph.get_state(_config(thread_id))
    if not snapshot.values:
        raise ValueError("해당 thread_id의 저장된 실행이 없습니다.")
    interruptions = [{"id": entry.id, "value": entry.value}
                     for task in snapshot.tasks for entry in task.interrupts]
    errors = [str(task.error) for task in snapshot.tasks if task.error]
    status = "paused" if interruptions else "failed" if errors else "incomplete" if snapshot.next else "completed"
    return {"thread_id": thread_id, "status": status, "next_nodes": list(snapshot.next),
            "interrupts": interruptions, "task_errors": errors, "state": dict(snapshot.values)}


def start_review(pdf_path, *, company_path=None, notice_pdf_path=None, notice_no=None, notice_order="000",
                 retrieval_limit=2, thread_id=None, db_path=DEFAULT_DB, graph_factory=_factory):
    thread_id = thread_id or uuid4().hex
    config = _config(thread_id)
    if isinstance(retrieval_limit, bool) or not isinstance(retrieval_limit, int) or not 0 <= retrieval_limit <= 3:
        raise ValueError("retrieval_limit은 0~3 정수여야 합니다.")
    inputs = {"pdf_path": pdf_path, "notice_pdf_path": notice_pdf_path, "company_path": company_path}
    fingerprints = {key: _fingerprint(value) for key, value in inputs.items() if value is not None}
    initial = {key: fingerprints[key]["path"] if key in fingerprints else None for key in inputs}
    initial.update(notice_no=notice_no, notice_order=notice_order, retrieval_limit=retrieval_limit,
                   run_id=thread_id, source_fingerprints=fingerprints, review_history=[])
    with _open(db_path, graph_factory) as graph:
        if graph.get_state(config).values:
            raise ValueError("이미 사용한 thread_id입니다. 기존 실행은 status/resume으로 확인하세요.")
        graph.invoke(initial, config)
        return _snapshot(graph, thread_id)


def review_status(thread_id, *, db_path=DEFAULT_DB, graph_factory=_factory):
    _config(thread_id)
    with _open(db_path, graph_factory) as graph:
        return _snapshot(graph, thread_id)


def resume_review(thread_id, interrupt_id, response, *, db_path=DEFAULT_DB, graph_factory=_factory):
    _config(thread_id)
    with _open(db_path, graph_factory) as graph:
        snapshot = _snapshot(graph, thread_id)
        pending = snapshot["interrupts"]
        if snapshot["status"] != "paused" or len(pending) != 1 or pending[0]["id"] != interrupt_id:
            raise ValueError("현재 중단 지점과 일치하지 않는 응답입니다. 최신 status를 확인하세요.")
        state = snapshot["state"]
        for key, stored in (state.get("source_fingerprints") or {}).items():
            if state.get(key) != stored["path"] or _fingerprint(stored["path"]) != stored:
                raise ValueError(f"입력 파일이 변경됐습니다: {key}. 새 실행으로 검토해야 합니다.")
        # 잘못된 응답은 체크포인트에 전달하기 전에 거부한다. 중단 상태를 그대로 유지한다.
        clean = validate_review_response(response, state)
        # 정규화된 후보의 내부 메타데이터 대신 원래 네 필드 응답을 전달한다.
        clean["corrections"] = response.get("corrections", {})
        graph.invoke(Command(resume=clean), _config(thread_id))
        return _snapshot(graph, thread_id)
