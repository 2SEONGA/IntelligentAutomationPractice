"""실행 작업 관리자 검사 (가짜 명령 사용, 네트워크·LLM 없음): `python tests/test_jobs.py`."""
import asyncio
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bid_review.web import jobs, runs  # noqa: E402
from bid_review.web.jobs import JobBusy, JobManager, mask, parse_line  # noqa: E402

REPORT = "입찰검토보고서_2026-10-27_120000_abcdef.json"


def fake(script: str):
    """command_builder: 출력 폴더 경로를 OUT 변수로 받는 파이썬 한 줄 스크립트."""
    return lambda output_dir, options: [sys.executable, "-c", f"OUT = {str(output_dir)!r}\n{script}"]


SUCCESS = fake(
    "import pathlib, time\n"
    "print('[STAGE] agent1 start', flush=True)\n"
    "print('key=sk-secret-123456', flush=True)\n"
    "time.sleep(0.5)\n"
    "print('[STAGE] agent1 done', flush=True)\n"
    f"pathlib.Path(OUT, {REPORT!r}).write_text('{{}}', encoding='utf-8')\n"
    "print('보고서: done', flush=True)\n"
)
FAILURE = fake("import sys\nprint('[STAGE] agent1 start', flush=True)\nprint('[실패] boom', flush=True)\nsys.exit(1)\n")


def collect(manager: JobManager, job_id: str) -> list[dict]:
    async def run():
        return [event async for event in manager.stream(job_id)]
    return asyncio.run(asyncio.wait_for(run(), timeout=20))


runs.OUTPUTS = Path(tempfile.mkdtemp())


def output_dir() -> Path:
    return Path(tempfile.mkdtemp(dir=runs.OUTPUTS))


def test_internal_error_ends_stream() -> None:
    """보고서가 OUTPUTS 밖에 생기는 등 내부 예외가 나도 error로 끝나야 함 (멈춘 화면 방지)."""
    manager = JobManager(SUCCESS, [])
    job = manager.start(Path(tempfile.mkdtemp()), {})  # OUTPUTS 밖 → run_id 인코딩 실패
    events = collect(manager, job.id)
    assert events[-1]["type"] == "error" and manager.current() is None


def test_parse_and_mask() -> None:
    assert parse_line("[STAGE] agent1 done") == {"type": "stage", "node": "agent1", "state": "done"}
    assert parse_line("[1/4] RFP 분석 완료") == {"type": "log", "text": "[1/4] RFP 분석 완료"}
    assert parse_line("[STAGE] agent9 dance")["type"] == "log", "모르는 노드·상태는 일반 로그"


def test_mask_secrets() -> None:
    assert mask("key=sk-secret-123456", ["sk-secret-123456"]) == "key=***"
    assert mask("abc abc", ["abc"]) == "abc abc", "8자 미만 secret은 무시"
    assert mask("no key", ["", "sk-secret-123456"]) == "no key"


def test_busy_and_done() -> None:
    manager = JobManager(SUCCESS, ["sk-secret-123456"])
    job = manager.start(output_dir(), {})
    try:
        manager.start(output_dir(), {})
    except JobBusy:
        pass
    else:
        raise AssertionError("실행 중인데 두 번째 실행이 허용됨")
    events = collect(manager, job.id)
    assert events[-1]["type"] == "done" and runs.decode_run_id(events[-1]["run_id"]).name == REPORT
    assert {"type": "log", "text": "key=***"} in events and "sk-secret" not in str(events)
    assert manager.current() is None and job.status == "done"


def test_failure_releases_lock() -> None:
    manager = JobManager(FAILURE, [])
    job = manager.start(output_dir(), {})
    events = collect(manager, job.id)
    assert events[-1]["type"] == "error" and "boom" in events[-1]["message"]
    assert {"type": "stage", "node": "agent1", "state": "fail"} in events
    assert job.status == "error" and manager.current() is None
    manager.start(output_dir(), {})  # 잠금이 풀려 다시 시작 가능


def test_replay_after_reconnect() -> None:
    manager = JobManager(SUCCESS, [])
    job = manager.start(output_dir(), {})
    first = collect(manager, job.id)
    time.sleep(0.1)
    assert collect(manager, job.id) == first, "끝난 작업을 다시 구독하면 처음부터 같은 이벤트"


def test_build_command() -> None:
    out = Path("/tmp/x")
    cmd = jobs.build_command(out, {"mode": "live", "notice": True, "market_api": True})
    assert cmd[1:] == ["run.py", "--mode", "live", "--rfp", str(out / "rfp.pdf"), "--notice-pdf", str(out / "notice.pdf"),
                       "--allow-market-api", "--output", str(out), "--save-intermediate", "--verbose"]
    offline = jobs.build_command(out, {"mode": "offline", "notice": True, "market_api": False})
    assert offline[1:] == ["-m", "bid_review.web.langgraph_job", "--rfp", str(out / "rfp.pdf"), "--output", str(out),
                           "--notice-pdf", str(out / "notice.pdf")], "offline 웹 실행은 LangGraph(일원화)"
    assert "--notice-pdf" not in jobs.build_command(out, {"mode": "offline", "notice": False, "market_api": False})


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("jobs OK")
