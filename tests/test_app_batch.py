"""전체 검토 시작 (검토 대상 공고 일괄 실행) 검사: `python tests/test_app_batch.py`."""
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_app_v2 import DIRECTOR, STAFF, client, collect  # noqa: E402


def wait_batch(c, timeout: float = 30) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        state = c.get("/api/notices/batch").json()
        if not state["running"]:
            return state
        time.sleep(0.2)
    raise AssertionError("일괄 검토가 끝나지 않음")


def notice(c, nid: str) -> dict:
    return next(n for n in c.get("/api/notices").json()["notices"] if n["id"] == nid)


def test_start_all_runs_ready_notices_and_leaves_upload_ones_waiting() -> None:
    c, commands = client()
    collect(c)
    assert c.post("/api/notices/start-all", json={"mode": "offline"}, headers=DIRECTOR).status_code == 403
    started = c.post("/api/notices/start-all", json={"mode": "offline"}, headers=STAFF)
    assert started.status_code == 200, started.text
    assert started.json()["total"] == 2, "검토 대상 2건(확인 필요 공고는 제외)"
    state = wait_batch(c)
    assert [d["id"] for d in state["done"]] == ["N1-000"], state
    assert [w["id"] for w in state["waiting"]] == ["N2-000"], state
    assert state["failed"] == [] and state["total"] == 2
    assert notice(c, "N1-000")["run_id"], "실행한 공고는 보고서와 연결"
    waiting = notice(c, "N2-000")
    assert waiting["case_folder"] and not waiting["run_id"], "RFP가 없는 공고는 업로드 대기로 남음"
    assert all(o["mode"] == "offline" for o in commands.options)
    again = c.post("/api/notices/start-all", json={"mode": "offline"}, headers=STAFF)
    assert again.status_code == 409, "시작할 공고가 없으면 거절"


def test_start_all_refuses_while_busy_and_bad_mode() -> None:
    c, commands = client()
    collect(c)
    assert c.post("/api/notices/start-all", json={"mode": "batch"}, headers=STAFF).status_code == 400, "잘못된 실행 방식"
    commands.slow_next = True
    c.post("/api/notices/start-all", json={"mode": "offline"}, headers=STAFF)
    assert c.post("/api/notices/start-all", json={"mode": "offline"}, headers=STAFF).status_code == 409, "이미 진행 중"
    assert c.post("/api/notices/N1-000/start", json={"mode": "offline"}, headers=STAFF).status_code == 409, "일괄 실행 중 개별 시작"
    wait_batch(c)


def test_stop_finishes_current_then_stops() -> None:
    c, commands = client()
    collect(c)
    commands.slow_next = True
    c.post("/api/notices/start-all", json={"mode": "offline"}, headers=STAFF)
    assert c.post("/api/notices/batch/stop", headers=DIRECTOR).status_code == 403
    assert c.post("/api/notices/batch/stop", headers=STAFF).status_code == 200
    state = wait_batch(c)
    assert state["stopped"] is True and len(state["done"]) + len(state["waiting"]) + len(state["failed"]) < state["total"], state


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("app_batch OK")
