"""T1.5 실패 원인별 재작업 계획. 자동 재작업은 원본 값을 확정하지 않는다."""
from __future__ import annotations

from typing import Any


# 원문에 있는 값의 T1 변환/유형 해석 문제. 서로 다른 문서의 불일치는 여기에 넣지 않는다.
INTERPRETATION_CHECKS = {"amount_normalization", "date_normalization"}


def _value(status: Any) -> str:
    return str(getattr(status, "value", status))


def plan_verification(state: dict[str, Any]) -> dict[str, Any]:
    """claim 단위로 재검색/재분석/담당자 확인을 분류한다.

    이전 재작업 후에도 동일 오류라면 다음 재작업을 실행하지 않고 담당자에게 보낸다.
    """
    verification = state.get("verification") or {}
    identity = state.get("case_identity") or {}
    if identity.get("conflicts"):
        return {"action": "human_review", "targets": [],
                "reason": "공고 식별 정보 불일치: " + "; ".join(identity["conflicts"])}
    if state.get("reanalysis_error"):
        return {"action": "human_review", "targets": [], "reason": state["reanalysis_error"]}
    if state.get("review_decision") == "approved" or verification.get("status") == "confirmed":
        return {"action": "company_match", "targets": [], "reason": "검증 또는 담당자 진행 승인"}

    attempts = state.get("reanalysis_attempts") or {}
    limit = max(0, min(int(state.get("retrieval_limit", 2)), 3))
    round_no = state.get("retrieval_round", 0)
    interpretation, missing, manual = [], [], []
    for category_name, category in (verification.get("categories") or {}).items():
        for claim in category.get("claims", []):
            status = _value(claim.get("status"))
            if status == "confirmed":
                continue
            target = {"category": category_name, "field": claim.get("field", ""),
                      "status": status, "queries": claim.get("retrieval_queries") or []}
            checks = claim.get("checks") or []
            # 교차 문서 충돌이나 후보 간 상충은 사람이 원본 판본을 확인한다.
            cross_source = any(_value(check.get("status")) == "conflict"
                               and check.get("check_id") not in INTERPRETATION_CHECKS for check in checks)
            if cross_source or status == "human_review" and not any(
                    c.get("check_id") in INTERPRETATION_CHECKS and _value(c.get("status")) != "confirmed"
                    for c in checks):
                manual.append(target)
            elif any(c.get("check_id") in INTERPRETATION_CHECKS
                     and _value(c.get("status")) != "confirmed" for c in checks):
                interpretation.append(target)
            elif status == "retrieve_more":
                missing.append(target)
            else:
                manual.append(target)

    # 충돌은 다른 항목의 자동 검색 결과로 해소할 수 없다.
    if manual:
        return {"action": "human_review", "targets": manual,
                "reason": "원문 충돌 또는 담당자 판단이 필요한 항목"}
    fresh = [item for item in interpretation
             if attempts.get(f"{item['category']}:{item['field']}", 0) < 1]
    if fresh:
        return {"action": "reanalyze", "targets": fresh, "reason": "T1 원문 값 해석 오류"}
    if interpretation:
        return {"action": "human_review", "targets": interpretation,
                "reason": "재분석 뒤에도 원문 해석 오류가 남음"}
    if missing and round_no < limit and any(item["queries"] for item in missing):
        return {"action": "retrieve_more", "targets": missing, "reason": "원문 근거 부족"}
    return {"action": "human_review", "targets": missing,
            "reason": "재검색 횟수 초과 또는 자동 확인 가능한 원문 근거 없음"}
