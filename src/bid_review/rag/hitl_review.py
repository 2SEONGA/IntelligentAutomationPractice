"""담당자 검토 응답 검증·수정 반영. interrupt 앞에는 기록·외부 호출을 하지 않는다."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from langgraph.types import interrupt

from ..models import Evidence, RFPAnalysisResult, RFPFieldCandidate

EDITABLE = {
    "budget_krw", "estimated_price_krw", "contract_period", "proposal_deadline",
    "bid_deadline", "proposal_submission_start", "joint_venture_deadline", "joint_venture_policy",
}


def validate_review_response(response: Any, state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise ValueError("검토 응답은 JSON 객체여야 합니다.")
    if set(response) - {"action", "reviewer", "note", "corrections"}:
        raise ValueError("검토 응답에 허용되지 않은 항목이 있습니다.")
    action = response.get("action")
    if action not in {"approve", "edit", "reject"}:
        raise ValueError("action은 approve / edit / reject 중 하나여야 합니다.")
    if action == "approve" and (state.get("case_identity") or {}).get("conflicts"):
        raise ValueError("공고 식별 정보가 불일치합니다. 원문과 공고번호를 확인하고 새 실행을 시작하세요.")
    for key in ("reviewer", "note"):
        if not isinstance(response.get(key), str) or not response[key].strip():
            raise ValueError(f"{key}를 입력해야 합니다.")
    changes = response.get("corrections", {})
    if not isinstance(changes, dict) or (action != "edit" and changes):
        raise ValueError("corrections는 edit에서만 사용할 수 있는 객체입니다.")
    if action == "edit" and not changes:
        raise ValueError("edit에는 최소 한 개의 수정 항목이 필요합니다.")
    if set(changes) - EDITABLE:
        raise ValueError("공고번호·문서 경로 등 실행 식별 정보는 수정할 수 없습니다.")
    clean = {}
    for field, raw in changes.items():
        if not isinstance(raw, dict) or set(raw) != {"value", "source_page", "source_excerpt", "source_document"}:
            raise ValueError(f"{field}: value·source_page·source_excerpt·source_document 네 항목이 필요합니다.")
        value = raw["value"]
        if field in {"budget_krw", "estimated_price_krw"}:
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ValueError("금액은 0 이상의 정수 또는 null이어야 합니다.")
        elif value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{field}: 문자열 또는 null이어야 합니다.")
        if (isinstance(raw["source_page"], bool) or not isinstance(raw["source_page"], int)
                or raw["source_page"] < 1 or not isinstance(raw["source_excerpt"], str)
                or not raw["source_excerpt"].strip()):
            raise ValueError(f"{field}: 실제 원문 페이지와 발췌가 필요합니다.")
        candidate = RFPFieldCandidate.model_validate(raw)
        source_key = "notice_pdf_path" if candidate.source_document == "notice_pdf" else "pdf_path"
        if not state.get(source_key):
            raise ValueError(f"{field}: 해당 근거 문서가 이번 실행 입력에 없습니다.")
        clean[field] = candidate.model_dump(mode="json")
    return {"action": action, "reviewer": response["reviewer"].strip(),
            "note": response["note"].strip(), "corrections": clean}


def pause_for_human_review(state: dict[str, Any]) -> dict[str, Any]:
    # 재개하면 이 Node의 처음부터 실행된다. audit 기록은 interrupt 뒤에서 한 번만 만든다.
    response = interrupt({
        "kind": "bid_review", "run_id": state.get("run_id"),
        "notice_no": state.get("notice_no"), "notice_order": state.get("notice_order"),
        "verification": state.get("verification", {}),
        "basic_info": (state.get("rfp") or {}).get("basic_info", {}),
        "instructions": "승인은 후속 분석 진행 승인입니다. 자격 충족·최종 Go 결정이 아닙니다.",
        "allowed_actions": ["approve", "edit", "reject"], "editable_fields": sorted(EDITABLE),
    })
    answer = validate_review_response(response, state)
    history = deepcopy(state.get("review_history") or [])
    audit = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "action": answer["action"], "reviewer": answer["reviewer"], "note": answer["note"],
             "verification_status": (state.get("verification") or {}).get("status"), "changes": {}}
    update: dict[str, Any] = {"reviewer": answer["reviewer"], "review_note": answer["note"]}
    if answer["action"] == "approve":
        update.update(review_decision="approved", decision="human_approved_for_continuation")
    elif answer["action"] == "reject":
        update.update(review_decision="rejected", decision="human_rejected",
                      routing_reasons=[answer["note"]])
    else:
        rfp = deepcopy(state["rfp"])
        summary_fields = {"budget_krw": "budget_krw", "estimated_price_krw": "estimated_price_krw",
                          "contract_period": "period"}
        for field, candidate in answer["corrections"].items():
            audit["changes"][field] = {"before": deepcopy(rfp["basic_info"][field]), "after": candidate}
            rfp["basic_info"][field] = candidate
            # 기존 후보는 삭제하지 않고 미선택 사유를 남긴다. 선택된 값은 다음 T1.5에서 재검증한다.
            candidates = rfp.setdefault("basic_info_candidates", {}).setdefault(field, [])
            for old in candidates:
                old["invalid_reason"] = "담당자 수정으로 미선택; 변경 이력 참조"
            candidates.append(candidate)
            if field in summary_fields:
                rfp["project_summary"][summary_fields[field]] = candidate["value"]
            if field in {"budget_krw", "estimated_price_krw"}:
                text_key = "budget_original_text" if field == "budget_krw" else "estimated_price_original_text"
                rfp["project_summary"][text_key] = candidate["source_excerpt"]
            source_key = "notice_pdf_path" if candidate["source_document"] == "notice_pdf" else "pdf_path"
            evidence = Evidence(document=Path(state[source_key]).name, source_document=candidate["source_document"],
                                pdf_page=candidate["source_page"], section="담당자 수정 근거",
                                excerpt=candidate["source_excerpt"])
            rfp["project_summary"].setdefault("evidence", []).append(evidence.model_dump(mode="json"))
        if {"proposal_deadline", "bid_deadline"} & set(answer["corrections"]):
            rfp["project_summary"]["submission_deadline"] = (
                rfp["basic_info"]["proposal_deadline"]["value"] or rfp["basic_info"]["bid_deadline"]["value"])
        rfp = RFPAnalysisResult.model_validate(rfp).model_dump(mode="json")
        # 과거 분석·보고서는 수정 후 다시 계산해야 한다. 이전 결과는 checkpoint 이력에 남는다.
        update.update(rfp=rfp, review_decision=None, decision="human_edit_applied", human_override=None,
                      capability=None, company_evidence_candidates={}, similar_projects=[],
                      similar_projects_status="not_run: 담당자 수정 후 재검증 필요", market=None,
                      report=None, report_details=None, report_validation={}, t4_status="blocked")
    history.append(audit)
    update["review_history"] = history
    return update


def route_after_human_review(state: dict[str, Any]) -> Literal["company_match", "reverify", "end"]:
    if state.get("decision") == "human_edit_applied":
        return "reverify"
    if (state.get("decision") == "human_approved_for_continuation"
            and state.get("review_decision") == "approved" and str(state.get("review_note") or "").strip()):
        return "company_match"
    return "end"
