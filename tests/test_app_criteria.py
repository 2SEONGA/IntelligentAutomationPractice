"""판단 기준 세트 API 검사 (권한·수집 적용·검토 스냅샷): `python tests/test_app_criteria.py`."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from bid_review.web import notices, runs  # noqa: E402
from test_app import pdf_bytes  # noqa: E402
from test_app_v2 import DIRECTOR, FILES, FULL, STAFF, client, collect, wait_done  # noqa: E402
from test_notices import FakeSession, item  # noqa: E402

SMALL = item("N5", "정보시스템 소액", amount=1_000_000_000)
LOW = {"values": {"min_amount_krw": 1, "decision": {"go": 1, "review": 0}}}


def notice(c, nid: str) -> dict:
    return next(n for n in c.get("/api/notices").json()["notices"] if n["id"] == nid)


def test_permissions_and_errors() -> None:
    c, _ = client()
    assert c.put("/api/criteria/sets/공격적", json=LOW, headers={}).status_code == 403
    assert c.put("/api/criteria/sets/공격적", json=LOW, headers=STAFF).status_code == 200
    assert c.put("/api/criteria/sets/공격적", json=LOW, headers=DIRECTOR).status_code == 200
    assert c.put("/api/criteria/sets/기본", json=LOW, headers=STAFF).status_code == 400
    assert c.put("/api/criteria/sets/x", json={"values": {"min_amount_krw": -1}}, headers=STAFF).status_code == 400
    assert c.put("/api/criteria/active", json={"name": "공격적"}, headers=STAFF).status_code == 403
    assert c.put("/api/criteria/active", json={"name": "없음"}, headers=DIRECTOR).status_code == 404
    assert c.put("/api/criteria/active", json={"name": "공격적"}, headers=DIRECTOR).status_code == 200
    assert c.delete("/api/criteria/sets/공격적", headers=STAFF).status_code == 409
    assert c.delete("/api/criteria/sets/기본", headers=STAFF).status_code == 400
    assert c.delete("/api/criteria/sets/없음", headers=STAFF).status_code == 404
    assert c.get("/api/criteria?set=없음").status_code == 404
    body = c.get("/api/criteria").json()
    assert body["active"] == body["selected"] == "공격적" and body["notice_filter"]["min_amount_krw"] == 1
    assert [s["name"] for s in body["sets"]] == ["기본", "공격적"] and body["values"]["min_amount_krw"] == 1
    assert c.get("/api/criteria?set=기본").json()["notice_filter"]["min_amount_krw"] == 4_000_000_000


def test_collect_uses_active_set() -> None:
    c, _ = client()
    notices.requests.Session = lambda: FakeSession({"정보시스템": [FULL, SMALL]}, files=FILES)
    collect(c)
    assert notice(c, "N5-000")["verdict"] == "제외" and notice(c, "N5-000")["judged"]["criteria_set"] == "기본"
    c.put("/api/criteria/sets/공격적", json=LOW, headers=STAFF)
    c.put("/api/criteria/active", json={"name": "공격적"}, headers=DIRECTOR)
    collect(c)
    assert notice(c, "N5-000")["verdict"] == "검토 대상" and notice(c, "N5-000")["judged"]["criteria_set"] == "공격적"


def test_review_keeps_set_it_started_with() -> None:
    c, _ = client()
    c.put("/api/criteria/sets/공격적", json=LOW, headers=STAFF)
    c.put("/api/criteria/active", json={"name": "공격적"}, headers=DIRECTOR)
    run_id = wait_done(c, c.post("/api/runs", files={"rfp": ("a.pdf", pdf_bytes())}, data={"mode": "offline"}, headers=STAFF).json()["job_id"])
    before = c.get(f"/api/runs/{run_id}").json()["checklist"]
    assert before["criteria_set"] == "공격적"
    c.put("/api/criteria/active", json={"name": "기본"}, headers=DIRECTOR)
    after = c.get(f"/api/runs/{run_id}").json()["checklist"]
    assert after["criteria_set"] == "공격적" and after["summary"] == before["summary"]
    assert c.put(f"/api/runs/{run_id}/checklist", json={"overrides": {"S5": 2}}, headers=STAFF).status_code == 200


def test_busy_rerun_keeps_snapshot() -> None:
    c, commands = client()
    run_id = wait_done(c, c.post("/api/runs", files={"rfp": ("a.pdf", pdf_bytes())}, data={"mode": "offline"}, headers=STAFF).json()["job_id"])
    c.put(f"/api/runs/{run_id}/workflow", json={"action": "submit"}, headers=STAFF)
    c.put(f"/api/runs/{run_id}/workflow", json={"action": "reject", "reason": "다시", "request": "재실행"}, headers=DIRECTOR)
    snapshot = runs.case_of(run_id) / "criteria.json"
    saved = snapshot.read_text(encoding="utf-8")
    c.put("/api/criteria/sets/공격적", json=LOW, headers=STAFF)
    c.put("/api/criteria/active", json={"name": "공격적"}, headers=DIRECTOR)
    commands.slow_next = True
    busy = c.post("/api/runs", files={"rfp": ("b.pdf", pdf_bytes())}, data={"mode": "offline"}, headers=STAFF)
    assert busy.status_code == 201, busy.text
    assert c.post(f"/api/runs/{run_id}/rerun", json={"mode": "offline"}, headers=STAFF).status_code == 409
    assert snapshot.read_text(encoding="utf-8") == saved, "시작 못 한 다시 실행이 기준 스냅샷을 바꿈"
    assert json.loads(saved)["name"] == "기본"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("app_criteria OK")
