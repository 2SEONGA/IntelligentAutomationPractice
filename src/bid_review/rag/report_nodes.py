"""LangGraph T4 보고서 생성. 보고서 내용 검증은 다음 Node에서 수행한다."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from ..agent04_report import ReportWriter
from ..models import CapabilityMatchingResult, DecisionStatus, ReportFinding, RFPAnalysisResult
from ..settings import load_settings
from .report_adapter import build_market_result


def g2b_overview(state: dict[str, Any]) -> dict[str, Any] | None:
    """나라장터 공고 조회값 중 보고서 개요에 쓰는 값. RFP 요약을 덮어쓰지 않고 출처를 붙여 따로 둔다."""
    lookup = state.get("g2b_notice_lookup") or {}
    notice = lookup.get("notice") or {}
    if lookup.get("status") != "found" or not notice:
        return None
    return {"buyer": notice.get("dminsttNm") or notice.get("ntceInsttNm") or None,
            "bid_closing": notice.get("bidClseDt") or None,  # 입찰 마감. 제안서 제출기한과 같다고 보지 않는다.
            "source_url": notice.get("bidNtceDtlUrl") or None,
            "api_fields": {"buyer": "dminsttNm", "bid_closing": "bidClseDt"}}


def generate_t4_report(state: dict[str, Any]) -> dict[str, Any]:
    """T2 완료·T1.5 통과 여부를 재확인하고 보고서 초안을 State에 남긴다."""
    verification = state.get("verification") or {}
    human_approved = (
        state.get("review_decision") == "approved"
        and bool(str(state.get("review_note") or "").strip())
    )
    t2_complete = state.get("decision") in {
        "company_matching_complete", "human_approved_company_matching_complete",
    }
    if not t2_complete or (verification.get("status") != "confirmed" and not human_approved):
        # T4를 직접 호출해도 기존 검증 게이트를 건너뛰지 못하게 한다.
        return {"report": None, "report_details": None, "market": None, "t4_status": "blocked",
                "errors": [*state.get("errors", []), "T4: T1.5 검토 또는 T2 완료 조건 미충족"]}

    try:
        if not state.get("rfp"):
            raise ValueError("T1 RFP 분석 결과가 없습니다.")
        rfp = RFPAnalysisResult.model_validate(state.get("rfp") or {})
        capability = CapabilityMatchingResult.model_validate(state.get("capability") or {})
        # 이 시각은 후보를 보고서 입력으로 확인·변환한 시각이며, 외부 재조회 시각이 아니다.
        market = build_market_result(
            state.get("similar_projects") or [],
            state.get("similar_projects_status", "not_run"),
            checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        report = ReportWriter(load_settings()).write(rfp, capability, market)
    except (ValidationError, ValueError) as exc:
        return {"report": None, "report_details": None, "market": None, "t4_status": "failed",
                "errors": [*state.get("errors", []), f"T4 입력·생성 오류: {exc}"]}

    # 기존 Writer의 시장 요약이 검색 후보를 확정 사실처럼 표현하지 않게 보완한다.
    if market.similar_projects:
        for finding in report.key_findings:
            if finding.topic == "시장·경쟁 환경":
                finding.assessment = (
                    f"유사사업 검색 후보 {len(market.similar_projects)}건. "
                    "원문 유사성과 낙찰정보 연결은 확인 필요."
                    if rfp.market_research_allowed else
                    "시장분석 허용 조건 미충족. T3 검색 후보는 별도 참고자료이며 판단에 사용하지 않았습니다."
                )
    report.key_findings.append(ReportFinding(
        topic="LangGraph 보고서 검증 상태",
        assessment="T4 초안 생성 완료. 보고서 내용 검증은 아직 수행하지 않았습니다.",
        evidence_references=["State.rfp", "State.capability", "State.market"],
        action="후속 보고서 검증 Node에서 값·근거·확인 필요 사항을 대조",
    ))
    if human_approved and verification.get("status") != "confirmed":
        report.bid_conditions.append(
            "담당자 승인으로 후속 분석을 진행했으며, T1.5의 미확인 항목이 자동 확정된 것은 아닙니다."
        )
    agent23 = state.get("agent23_evaluation")
    if agent23 is not None:
        pending = [item for item in agent23.get("checks", []) if item.get("status") == "needs_review"]
        report.key_findings.append(ReportFinding(
            topic="T2·T3 후속 분석 검증",
            assessment=f"검증 상태: {agent23.get('status', '확인 필요')}. 담당자 확인 항목 {len(pending)}건.",
            evidence_references=["State.capability", "State.market"],
            action="State.agent23_evaluation의 항목별 근거와 원본 자료 확인",
        ))
        if pending:
            report.decision_status = DecisionStatus.HOLD
            report.executive_summary = "T2·T3 분석의 근거 확인이 필요합니다. " + report.executive_summary
            report.bid_conditions.append("T2·T3 결과에 확인 필요 항목이 남아 있습니다. 최종 참여 판단의 확정 근거로 사용하지 마세요.")
    return {"market": market.model_dump(mode="json"),
            "report": report.model_dump(mode="json"),
            # 요약문에 생략될 수 있는 값과 원문 근거를 구조화 보고서에 함께 보존한다.
            "report_details": {
                "project_summary": rfp.project_summary.model_dump(mode="json"),
                "basic_info": rfp.basic_info.model_dump(mode="json"),
                "eligibility_requirements": [x.model_dump(mode="json") for x in rfp.eligibility_requirements],
                "company_analysis": capability.model_dump(mode="json"),
                "market_analysis": market.model_dump(mode="json"),
                **({"agent23_evaluation": deepcopy(agent23)} if agent23 is not None else {}),
                **({"g2b_overview": g2b_overview(state)} if g2b_overview(state) else {}),
            },
            "t4_status": "generated_pending_validation"}
