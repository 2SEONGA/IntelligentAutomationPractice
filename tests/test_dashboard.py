"""대시보드 집계 검사 (고정 데이터): `python tests/test_dashboard.py`."""
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bid_review.web.dashboard import compute_dashboard  # noqa: E402

NOW = datetime(2026, 10, 27, 12, 0)


def notice(verdict: str, deadline: str = "2026-11-06 10:00:00", case: str | None = None, name: str = "공고") -> dict:
    return {"bidNtceNm": name, "dminsttNm": "기관", "verdict": verdict, "case_folder": case,
            "judged": {"amount_krw": 5_000_000_000, "deadline": deadline}, "bidClseDt": deadline}


def case(status: str, ai: str, decision: str | None = None, notice_id: str | None = None) -> dict:
    return {"run_id": f"r-{status}-{ai}-{notice_id}", "project_name": "건", "status": status, "ai_recommendation": ai,
            "checklist_recommendation": "보류", "decision": decision, "notice_id": notice_id, "amount_krw": 1}


STORE = {
    "runs": [{"at": "2026-10-27T10:00:00+09:00", "days": 30, "keywords": ["AI"], "count": 3}],
    "notices": {
        "A-000": notice("검토 대상", "2026-11-06 10:00:00", case="20261027_1"),
        "B-000": notice("검토 대상", "2026-10-30 10:00:00", name="급한 공고"),
        "C-000": notice("확인 필요", ""),
        "D-000": notice("제외"),
    },
}
CASES = [
    case("결재 대기", "Go", notice_id="A-000"),
    case("결정 완료", "보류", decision="보류"),
    case("결정 완료", "No-Go", decision="Go"),
    case("검토 완료", "조건부 Go"),
]


def test_before_collection_is_all_zero() -> None:
    board = compute_dashboard({"runs": [], "notices": {}}, [], NOW)
    assert board["cards"]["collected_total"] == 0 and board["cards"]["last_run"] is None
    assert [s["count"] for s in board["funnel"]] == [0, 0, 0, 0, 0] and board["deadlines"] == []


def test_cards_and_funnel() -> None:
    board = compute_dashboard(STORE, CASES, NOW)
    cards = board["cards"]
    assert (cards["collected_recent"], cards["collected_total"], cards["passed"], cards["confirm"]) == (3, 4, 2, 1)
    assert (cards["approvals_pending"], cards["reviews_total"], cards["reviews_linked"]) == (1, 4, 1)
    assert cards["awaiting_start"] == 1, "공고함 배지 = 아직 검토를 시작하지 않은 검토 대상(공고함 탭과 같은 수)"
    assert [s["count"] for s in board["funnel"]] == [4, 2, 1, 1, 0], "흐름 막대는 공고에 연결된 건만"


def test_distribution_and_agreement() -> None:
    board = compute_dashboard(STORE, CASES, NOW)
    assert board["distribution"] == {"Go": 1, "조건부 Go": 1, "보류": 1, "No-Go": 1}, "분포는 모든 건"
    assert board["agreement"] == {"matched": 1, "total": 2}
    assert [c["status"] for c in board["pending"]] == ["결재 대기"]


def test_deadlines_sorted_with_dday() -> None:
    store = {**STORE, "notices": {**STORE["notices"], "E-000": notice("검토 대상", "2026-10-20 10:00:00", name="마감 지남")}}
    board = compute_dashboard(store, CASES, NOW)
    assert [(d["name"], d["d_day"]) for d in board["deadlines"]] == [("급한 공고", 3)], "마감 지남·이미 시작한 공고는 빼기"
    assert board["cards"]["awaiting_start"] == 1, "마감 지난 공고는 공고함 배지에서 빼기"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("dashboard OK")
