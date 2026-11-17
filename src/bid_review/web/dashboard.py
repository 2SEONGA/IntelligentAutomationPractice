"""Dashboard numbers, computed only from stored notices and review cases (no placeholders)."""
from __future__ import annotations

from datetime import datetime

RECOMMENDATIONS = ("Go", "조건부 Go", "보류", "No-Go")
DEADLINE_LIMIT = 5


def _agrees(ai: str | None, decision: str) -> bool:
    return ai == decision or (ai == "조건부 Go" and decision == "Go")


def compute_dashboard(notices: dict, cases: list[dict], now: datetime) -> dict:
    items = list(notices.get("notices", {}).values())
    last_run = (notices.get("runs") or [None])[-1]
    linked = [c for c in cases if c.get("notice_id")]
    decided = [c for c in cases if c.get("decision")]
    passed = [n for n in items if n["verdict"] == "검토 대상"]
    deadlines, expired = [], set()
    for n in passed:
        try:
            deadline = datetime.strptime((n["judged"].get("deadline") or "")[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if deadline < now:  # verdicts are only refreshed on the next collection
            expired.add(id(n))
            continue
        if n.get("case_folder"):
            continue
        deadlines.append({"name": n["bidNtceNm"], "agency": n.get("dminsttNm"), "amount_krw": n["judged"].get("amount_krw"),
                          "deadline": n["judged"]["deadline"], "d_day": (deadline.date() - now.date()).days,
                          "case_folder": n.get("case_folder")})
    return {
        "cards": {
            "collected_recent": last_run["count"] if last_run else 0, "collected_total": len(items), "last_run": last_run,
            "passed": len(passed), "awaiting_start": sum(not n.get("case_folder") and id(n) not in expired for n in passed), "confirm": sum(n["verdict"] == "확인 필요" for n in items),
            "approvals_pending": sum(c["status"] == "결재 대기" for c in cases),
            "reviews_total": len(cases), "reviews_linked": len(linked),
        },
        "funnel": [
            {"stage": "1. 수집", "count": len(items)},
            {"stage": "2. 필터 통과", "count": len(passed)},
            {"stage": "4~7. Agent 검토", "count": len(linked)},
            {"stage": "8. 결재 대기", "count": sum(c["status"] == "결재 대기" for c in linked)},
            {"stage": "9. 결정 완료", "count": sum(c["status"] == "결정 완료" for c in linked)},
        ],
        "distribution": {r: sum(c.get("ai_recommendation") == r for c in cases) for r in RECOMMENDATIONS},
        "agreement": {"matched": sum(_agrees(c.get("ai_recommendation"), c["decision"]) for c in decided), "total": len(decided)},
        "pending": [c for c in cases if c["status"] == "결재 대기"],
        "deadlines": sorted(deadlines, key=lambda d: d["deadline"])[:DEADLINE_LIMIT],
    }
