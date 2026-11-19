"""T4 구조화 보고서의 값 전달·근거 보존 검사. 원문의 진위 판정은 하지 않는다."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from ..models import BidReviewReport, CapabilityMatchingResult, MarketResearchResult, RFPAnalysisResult
from ..policy import SOURCE_ARTIFACTS
from .report_adapter import build_market_result
from .report_nodes import g2b_overview


def validate_t4_report(state: dict[str, Any]) -> dict[str, Any]:
    """불일치는 failed, 근거·판단 보완 필요는 needs_review로 별도 기록한다."""
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    checks: list[dict[str, str]] = []

    def add(check_id: str, status: str, detail: str) -> None:
        checks.append({"check_id": check_id, "status": status, "detail": detail})

    def finish(status: str, report: BidReviewReport | None = None) -> dict[str, Any]:
        update: dict[str, Any] = {"report_validation": {
            "status": status, "checked_at": checked_at, "checks": checks,
            "scope": "구조화 데이터 일치·근거 보존 검사. 자연어 전체의 의미·원문 진위·입찰 자격 확정은 범위 밖.",
        }}
        if status != "not_run":
            update["t4_status"] = {
                "failed": "generated_validation_failed",
                "needs_review": "generated_review_required",
                "passed": "generated_consistency_checked",
            }[status]
        if report is not None:
            for finding in report.key_findings:
                if finding.topic == "LangGraph 보고서 검증 상태":
                    finding.assessment = {
                        "failed": "구조화 보고서 불일치 발견. 최종본으로 사용하기 전에 수정이 필요합니다.",
                        "needs_review": "구조화 값 대조 완료. 근거·판단 보완이 필요한 항목이 남아 있습니다.",
                        "passed": "구조화 값·근거 보존 검사 통과. 원문 사실과 최종 판단은 담당자가 확인합니다.",
                    }[status]
                    finding.action = "State.report_validation의 항목별 결과와 원문 근거 확인"
            update["report"] = report.model_dump(mode="json")
        return update

    if state.get("t4_status") in {"blocked", "failed"}:
        add("generation", "not_run", "T4 생성이 차단되거나 실패하여 내용 검증을 생략했습니다.")
        return finish("not_run")

    try:
        for key in ("rfp", "capability", "market", "report", "report_details"):
            if not isinstance(state.get(key), dict) or (not state[key] and key != "market"):
                raise ValueError(f"필수 입력 {key} 누락 또는 잘못된 형식")
        rfp = RFPAnalysisResult.model_validate(state["rfp"])
        capability = CapabilityMatchingResult.model_validate(state["capability"])
        market = MarketResearchResult.model_validate(state["market"])
        report = BidReviewReport.model_validate(state["report"])
    except (ValidationError, ValueError) as exc:
        add("schema", "failed", str(exc))
        return finish("failed")
    add("schema", "passed", "기존 분석·보고서 모델 형식 확인")

    expected = {
        "project_summary": rfp.project_summary.model_dump(mode="json"),
        "basic_info": rfp.basic_info.model_dump(mode="json"),
        "eligibility_requirements": [x.model_dump(mode="json") for x in rfp.eligibility_requirements],
        "company_analysis": capability.model_dump(mode="json"),
        "market_analysis": market.model_dump(mode="json"),
    }
    for key, value in expected.items():
        same = key in state["report_details"] and state["report_details"][key] == value
        add(key, "passed" if same else "failed", f"{key}: 앞 단계 결과와 보고서 상세 값·근거 대조")
    if "agent23_evaluation" in state:
        evaluation = state["agent23_evaluation"]
        same = state["report_details"].get("agent23_evaluation") == evaluation
        add("agent23_transfer", "passed" if same else "failed", "T2·T3 평가 결과의 보고서 상세 전달 일치")
        add("agent23_evidence", "passed" if evaluation.get("status") == "passed" else "needs_review",
            "T2·T3 근거 평가 상태: " + str(evaluation.get("status", "누락")))

    overview = g2b_overview(state)
    if overview or "g2b_overview" in state["report_details"]:
        same = state["report_details"].get("g2b_overview") == overview
        add("g2b_overview", "passed" if same else "failed", "보고서 개요의 나라장터 발주기관·입찰 마감과 공고 조회값 대조")

    amount = rfp.project_summary.budget_krw
    scale_ok = report.project_scale is not None and report.project_scale.amount_krw == amount
    add("summary_budget", "passed" if scale_ok else "failed", "요약 보고서의 사업규모 금액과 T1 사업금액 대조")
    if report.project_scale is not None:
        field = rfp.basic_info.budget_krw
        scale = report.project_scale
        same_source = (scale.source_document, scale.source_page, scale.source_excerpt) == (
            field.source_document, field.source_page, field.source_excerpt)
        add("budget_citation", "passed" if same_source else "failed", "사업금액 근거 문서·페이지·발췌 보존 확인")
    if amount is None or not (rfp.basic_info.budget_krw.source_page and rfp.basic_info.budget_krw.source_excerpt.strip()):
        add("budget_evidence", "needs_review", "사업금액 또는 원문 페이지·발췌 확인 필요")
    if amount is not None and rfp.basic_info.budget_krw.value != amount:
        add("t1_budget_consistency", "needs_review", "T1 사업 개요와 기본정보 금액이 다르거나 한쪽이 미확인입니다.")

    if not rfp.eligibility_requirements or any(not x.evidence.is_located() for x in rfp.eligibility_requirements):
        add("eligibility_evidence", "needs_review", "참가자격 항목 또는 위치가 특정된 원문 근거 확인 필요")

    # State.market과 보고서가 함께 바뀐 경우에도 원래 T3 후보와 다시 대조한다.
    expected_market = build_market_result(
        state.get("similar_projects") or [], state.get("similar_projects_status", "not_run"),
        checked_at=checked_at,
    ).model_dump(mode="json")
    actual_market = market.model_dump(mode="json")
    for payload in (expected_market, actual_market):
        for item in payload["similar_projects"]:
            item.pop("checked_at", None)  # 변환 시각은 사실 내용 비교 대상이 아니다.
    add("t3_candidates", "passed" if expected_market == actual_market else "failed",
        "공고 출처·유사사업 후보·미확인 사항을 원래 T3 결과와 대조")
    if market.unknown_items:
        add("market_evidence", "needs_review", " / ".join(market.unknown_items))

    verification_status = (state.get("verification") or {}).get("status")
    if verification_status != "confirmed":
        approved = state.get("review_decision") == "approved" and bool(str(state.get("review_note") or "").strip())
        add("t15_gate", "needs_review" if approved else "failed",
            "담당자 승인 후 진행했으나 미확인 항목은 유지됩니다." if approved else "T1.5 미통과·승인 기록 없음")
    if state.get("decision") not in {"company_matching_complete", "human_approved_company_matching_complete"}:
        add("t2_gate", "failed", "T2 완료 상태가 아닙니다.")
    if capability.human_review_items or capability.evidence_limitations:
        add("company_evidence", "needs_review", "회사 분석의 담당자 확인사항·근거 한계가 남아 있습니다.")
    matches = capability.requirement_matches + capability.eligibility_matches + capability.staffing_matches
    if not matches or any(
        x.status.value != "충족" or not x.company_evidence or any(
            not (e.document.strip() and e.section.strip() and e.excerpt.strip()) or e.verification_required
            for e in x.company_evidence
        ) for x in matches
    ):
        add("company_matches", "needs_review", "회사 요건별 판단·증빙에 확인이 필요한 항목이 있습니다.")

    topics = {x.topic for x in report.key_findings}
    required_topics = {"핵심 적합 요인", "핵심 리스크", "시장·경쟁 환경", "LangGraph 보고서 검증 상태"}
    add("report_sections", "passed" if required_topics <= topics else "failed", "필수 요약 영역 누락 검사")
    allowed = set(SOURCE_ARTIFACTS) | {"State.rfp", "State.capability", "State.market"}
    refs_ok = all(ref.split(" > ", 1)[0] in allowed
                  for finding in report.key_findings for ref in finding.evidence_references)
    add("source_references", "passed" if refs_ok else "failed",
        "보고서 참조 식별자 검사. 실제 debug 파일 저장·존재 여부는 아직 검사하지 않습니다.")
    states = {x["status"] for x in checks}
    status = "failed" if "failed" in states else "needs_review" if "needs_review" in states else "passed"
    return finish(status, report)
