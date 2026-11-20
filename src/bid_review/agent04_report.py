"""Decision-oriented report synthesis.

Detailed extraction and matching artifacts remain auditable intermediate
outputs. This module turns them into an ID-free presales decision brief.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import re
from typing import Any

from .models import BidReviewReport, CapabilityMatchingResult, DecisionStatus, ExecutiveDecisionSummary, MarketResearchResult, MatchStatus, ReportFinding, RFPAnalysisResult
from .money import format_krw
from .policy import SOURCE_ARTIFACTS, VISIBLE_COMPANY_NAME
from .project_scale import review_project_scale


UNCONFIRMED = "원문 확인 필요"


def _compact(value: str | None, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


def _cell(value: str | None) -> str:
    return (value or UNCONFIRMED).replace("|", "／").replace("\n", " ")


def _effective_gate(rfp: RFPAnalysisResult):
    return rfp.integration_quality_gate or rfp.quality_gate


def _document_status(rfp: RFPAnalysisResult) -> str:
    return "정상" if _effective_gate(rfp).passed and not rfp.needs_ocr else "일부 원문 확인 필요"


def _analysis_scope(rfp: RFPAnalysisResult) -> str:
    return "공고문 + RFP 통합 검토" if rfp.analysis_scope == "notice_and_rfp" else "RFP 기반 사전 검토"


def _status_counts(matches) -> Counter:
    return Counter(match.status.value for match in matches)


def _aggregate_status(matches) -> str:
    if not matches:
        return "확인 필요"
    statuses = {match.status for match in matches}
    if MatchStatus.UNMET in statuses:
        return "미충족"
    if statuses == {MatchStatus.MET}:
        return "충족"
    if MatchStatus.PARTIAL in statuses:
        return "부분 충족"
    return "확인 필요"


def _counts_label(matches) -> str:
    counts = _status_counts(matches)
    parts = [f"{status} {counts[status]}건" for status in ("충족", "부분 충족", "미충족", "확인 필요") if counts[status]]
    return ", ".join(parts) or "검토 항목 없음"


def _requirement_lookup(rfp: RFPAnalysisResult) -> dict[str, str]:
    values = {item.id: item.name or item.details for item in rfp.requirements}
    values.update({item.id: item.requirement_text for item in rfp.staffing_requirements})
    return values


def _representative_evidence(capability: CapabilityMatchingResult, rfp: RFPAnalysisResult, limit: int = 3, include_partial: bool = False) -> list[str]:
    requirements = _requirement_lookup(rfp)
    rows: list[str] = []
    seen: set[tuple[str, str]] = set()
    for match in capability.requirement_matches + capability.staffing_matches:
        allowed_statuses = {MatchStatus.MET, MatchStatus.PARTIAL} if include_partial else {MatchStatus.MET}
        if match.status not in allowed_statuses:
            continue
        for evidence in match.company_evidence:
            key = (evidence.document, evidence.section)
            if key in seen:
                continue
            seen.add(key)
            condition = _compact(requirements.get(match.requirement_id, "관련 요구사항"), 62)
            proof_limit = " 입찰 제출용 원본 증빙 확인 필요." if evidence.verification_required else ""
            rows.append(f"{condition}: {evidence.document} / {evidence.section}에서 관련 근거 확인.{proof_limit}")
            if len(rows) == limit:
                return rows
    return rows


def _public_strength_lines(capability: CapabilityMatchingResult, limit: int = 3) -> list[str]:
    """Render public references as leads, never as bid-eligibility proof."""
    rows = capability.representative_public_records + capability.public_technology_and_certifications
    lines: list[str] = []
    seen: set[str] = set()
    for item in rows:
        if item.title in seen:
            continue
        seen.add(item.title)
        source = item.source_url or "공식 출처 URL 원문 확인 필요"
        lines.append(f"- [{item.title}]: {item.relevance} / 출처: {source}\n  - 한계: {item.verification_required}")
        if len(lines) >= limit:
            break
    return lines


def _risk_label(value: str) -> str | None:
    text = value.replace(" ", "")
    if "나라장터" in text or "경쟁입찰참가자격" in text or "조달청" in text:
        return "나라장터 경쟁입찰참가자격 및 관련 등록 요건"
    if "부정당" in text or "영업정지" in text or "입찰참가제한" in text:
        return "입찰참가 제한·부정당업자 해당 여부"
    if "중소기업" in text or "소상공인" in text:
        return "중소기업·소상공인 확인서 및 기업구분 요건"
    if "공동수급" in text or "공동이행" in text:
        return "공동수급체 구성 및 출자비율 요건"
    if "가격입찰서" in text or "제안서" in text:
        return "제안서·가격입찰서 제출 및 유효성 요건"
    if "실적" in text:
        return "유사실적 인정 기준 및 증빙 요건"
    return None


def _requirement_summary(value: str) -> str:
    """Keep report risks decision-readable; raw requirement tables stay in debug artifacts."""
    text = re.sub(r"\s+", " ", value)
    if "전담인력" in text:
        headcount = re.search(r"전담인력(?:은)?\s*(\d+)명", text)
        areas = re.search(r"(\d+)개\s*권역\s*이상", text)
        parts = [f"전담인력 {headcount.group(1)}명" if headcount else "전담인력 운영"]
        if areas:
            parts.append(f"{areas.group(1)}개 권역 이상·권역별 1명 이상")
        if "사전 승인" in text:
            parts.append("인력 교체 시 발주기관 사전 승인")
        return "; ".join(parts) + " 확인 필요"
    if any(word in text for word in ("장애", "유지관리", "네트워크", "스마트기기")):
        return "네트워크·스마트기기 유지관리 수행방안 및 장애 대응체계 확인 필요"
    return _compact(text, 110)


def _legal_condition_summaries(rfp: RFPAnalysisResult) -> list[str]:
    """Derive concise eligibility checks from extracted clauses; never infer company compliance."""
    text = " ".join(item.details or item.name for item in rfp.eligibility_requirements)
    items: list[str] = []
    software = re.search(r"소프트웨어사업자[^\d]{0,80}?(\d{4})", text)
    if software:
        items.append(f"소프트웨어사업자(컴퓨터관련서비스사업) {software.group(1)} 등록")
    ict = re.search(r"정보통신공사업[^\d]{0,80}?(\d{4})", text)
    if ict:
        items.append(f"정보통신공사업 {ict.group(1)} 등록")
    direct = re.search(r"직접생산\s*확인\s*증명서[^\d]{0,120}?(\d{10})", text)
    if direct:
        items.append(f"직접생산확인증명서(정보시스템유지관리서비스 {direct.group(1)})")
    if "중소기업" in text or "소상공인" in text:
        items.append("중소기업·소상공인 확인서")
    if "대기업" in text or "중견기업" in text:
        items.append("대기업·중견기업 SW사업자 참여 제한 해당 여부")
    if "상호출자제한" in text:
        items.append("상호출자제한기업집단 소속 여부")
    return list(dict.fromkeys(items))


def _top_risks(rfp: RFPAnalysisResult, capability: CapabilityMatchingResult, limit: int = 5) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen_items: set[str] = set()
    for item, match in zip(rfp.eligibility_requirements, capability.eligibility_matches):
        if match.status == MatchStatus.MET:
            continue
        label = _risk_label(item.details or item.name)
        # Submission mechanics are tracked as a process checklist, not as a
        # company capability or legal-eligibility failure.
        if label == "제안서·가격입찰서 제출 및 유효성 요건":
            continue
        if not label or label in seen_items:
            continue
        seen_items.add(label)
        rows.append({
            "priority": "높음",
            "item": label,
            "why": "법적 참가자격 충족 여부에 직접 영향을 줄 수 있습니다.",
            "action": "공고문 원본·나라장터 등록정보·관련 등록/제한 증빙 확인",
        })
        if len(rows) >= limit:
            return rows
    staffing_by_id = {item.requirement_id: item for item in capability.staffing_matches}
    for item in rfp.staffing_requirements:
        match = staffing_by_id.get(item.id)
        if not match or match.status == MatchStatus.MET:
            continue
        raw_text = re.sub(r"\s+", " ", item.requirement_text).strip()
        # PMR/PSR 분류표·여러 셀이 붙은 평가표는 인력 요건으로 확정하지 않는다.
        if "|" in raw_text and re.search(r"\b(?:PMR|PSR|COR|QUR)(?:-\d+)?\b", raw_text, re.IGNORECASE):
            continue
        # 실질적인 인력·경력 조건이 없는 경우 보류 사유로 출력하지 않는다.
        if not re.search(r"투입\s*인력|참여\s*인력|전담\s*인력|책임\s*기술자|참여\s*기술자|기술\s*인력|기술등급|경력|재직|상주|인력\s*(?:구성|배치)|(?<![A-Za-z])(?:PM|PL)(?![A-Za-z])", raw_text, re.IGNORECASE):
            continue
        label = ("인력·경력·기술등급 관련 조건(해당 원문 페이지 확인 필요)"
                 if "|" in raw_text or len(raw_text) > 180
                 else _requirement_summary(raw_text))
        if label in seen_items:
            continue
        seen_items.add(label)
        rows.append({
            "priority": "높음" if item.mandatory else "중간",
            "item": label,
            "why": "핵심 인력의 제안평가 또는 수행 가능성에 영향을 줄 수 있습니다.",
            "action": "경력증명·재직증명·투입 가능 인력계획 확인",
        })
        if len(rows) >= limit:
            return rows
    if rfp.needs_ocr and len(rows) < limit:
        rows.append({
            "priority": "중간",
            "item": "표 기반 조건 및 일부 추출 텍스트",
            "why": "자동 추출 품질 문제가 있어 조건을 확정하지 않았습니다.",
            "action": "공고문·RFP 원문에서 해당 조건 확인",
        })
    return rows[:limit]


def _market_status(rfp: RFPAnalysisResult, market: MarketResearchResult) -> tuple[str, str, str]:
    if not rfp.market_research_allowed:
        return "미실행", "공고문 기본정보 또는 통합 품질 게이트가 확인되지 않아 시장조사를 실행하지 않았습니다.", "공고문 원본 확인 후 재실행"
    if market.similar_projects or market.market_observations:
        return "확인됨", f"출처가 있는 유사사업 {len(market.similar_projects)}건과 시장 관찰 {len(market.market_observations)}건을 확인했습니다.", "출처 원문과 사업 유사성 검토"
    raw_reason = market.unknown_items[0] if market.unknown_items else ""
    if "API 조회 실패" in raw_reason or "네트워크" in raw_reason:
        reason = "나라장터 API 조회가 완료되지 않아 URL 기반 시장 근거를 확보하지 못했습니다."
    else:
        reason = _compact(raw_reason, 130) or "출처가 있는 시장 근거가 확인되지 않았습니다."
    return "시장 근거 제한", reason, "나라장터·발주기관의 URL 근거 확보"


def _checklist(rfp: RFPAnalysisResult, capability: CapabilityMatchingResult, market: MarketResearchResult) -> list[str]:
    items: list[str] = []
    if _aggregate_status(capability.eligibility_matches) != "충족":
        items.append("공고문 원본 기준의 법적 참가자격 및 나라장터 등록 상태 확인")
    if any(match.status != MatchStatus.MET for match in capability.requirement_matches if "실적" in match.requirement_id.upper()):
        items.append("유사실적 인정 가능 여부를 발주처 실적증명서로 확인")
    if _aggregate_status(capability.staffing_matches) != "충족":
        items.append("PM 및 핵심인력의 경력·재직·투입 가능 여부 확인")
    if rfp.basic_info.joint_venture_policy.value is not None:
        items.append("공동수급 또는 하도급 허용 조건과 구성 요건 확인")
    if rfp.needs_ocr:
        items.append("표 기반 조건과 자동 추출 불명확 항목을 공고문·RFP 원문으로 대조")
    if market.unknown_items:
        items.append("나라장터·발주기관 URL 근거로 시장·유사사업 정보 보강")
    items.append("제출기한·제출서식·필수 첨부증빙을 최종 점검")
    return list(dict.fromkeys(items))[:7]


def _source_references(rfp: RFPAnalysisResult, capability: CapabilityMatchingResult) -> list[str]:
    references: list[str] = []
    for evidence in rfp.project_summary.evidence[:4]:
        references.append(f"{evidence.document} p.{evidence.pdf_page or '?'} / {evidence.section}")
    for match in capability.requirement_matches + capability.staffing_matches:
        for evidence in match.company_evidence:
            references.append(f"{evidence.document} / {evidence.section}")
            if len(references) >= 7:
                return list(dict.fromkeys(references))
    return list(dict.fromkeys(references))


def build_report_agent_context(rfp: RFPAnalysisResult, capability: CapabilityMatchingResult, market: MarketResearchResult) -> dict[str, Any]:
    """Compact, ID-free input contract for the Report Writing Agent."""
    risks = _top_risks(rfp, capability)
    market_status, market_reason, _ = _market_status(rfp, market)
    category_counts: dict[str, Counter] = defaultdict(Counter)
    requirement_categories = {item.id: item.category for item in rfp.requirements}
    for match in capability.requirement_matches:
        category_counts[requirement_categories.get(match.requirement_id, "기타")][match.status.value] += 1
    return {
        "project_summary": {
            "project_name": rfp.project_summary.project_name,
            "procuring_entity": rfp.basic_info.procuring_entity.value or rfp.project_summary.buyer,
            "demanding_entity": rfp.basic_info.demanding_entity.value,
            "notice_number": rfp.basic_info.notice_number.value,
            "budget_krw": rfp.project_summary.budget_krw,
            "estimated_price_krw": rfp.project_summary.estimated_price_krw,
            "period": rfp.project_summary.period,
            "proposal_submission_start": rfp.basic_info.proposal_submission_start.value,
            "proposal_deadline": rfp.project_summary.submission_deadline,
            "joint_venture_deadline": rfp.basic_info.joint_venture_deadline.value,
            "contract_method": rfp.basic_info.contract_method.value,
            "evaluation_method": rfp.basic_info.evaluation_method.value,
            "joint_venture_policy": rfp.basic_info.joint_venture_policy.value,
            "analysis_scope": _analysis_scope(rfp),
            "document_status": _document_status(rfp),
        },
        "decision_summary": {
            "legal_eligibility": _aggregate_status(capability.eligibility_matches),
            "technical_fit_score": capability.fit_score.value,
            "technical_requirement_counts": _counts_label(capability.requirement_matches),
            "staffing": _aggregate_status(capability.staffing_matches),
            "market_status": market_status,
        },
        "top_strengths": _representative_evidence(capability, rfp),
        "top_blockers": [row["item"] for row in risks if row["priority"] == "높음"],
        "top_risks": risks,
        "capability_summary_by_category": {key: dict(value) for key, value in category_counts.items()},
        "representative_evidence": _representative_evidence(capability, rfp),
        "market_summary": {"status": market_status, "summary": market_reason},
        "action_checklist": _checklist(rfp, capability, market),
        "source_references": _source_references(rfp, capability),
    }


class ReportWriter:
    """Creates a decision brief from validated, already-structured artifacts."""

    def __init__(self, settings: dict | None = None):
        self.minimum_krw = (settings or {}).get("minimum_project_amount_krw", 0)

    def write(
        self,
        rfp: RFPAnalysisResult,
        capability: CapabilityMatchingResult,
        market: MarketResearchResult,
        executive_summary: ExecutiveDecisionSummary | None = None,
    ) -> BidReviewReport:
        preliminary_only = not rfp.market_research_allowed
        legal_status = _aggregate_status(capability.eligibility_matches)
        scale = review_project_scale(rfp, self.minimum_krw)
        mandatory_ids = {item.id for item in rfp.requirements if item.mandatory_status.value == "필수"}
        mandatory_ids.update(item.id for item in rfp.staffing_requirements if item.mandatory)
        mandatory_matches = [item for item in capability.requirement_matches if item.requirement_id in mandatory_ids]
        blocked = [item.requirement_id for item in mandatory_matches if item.status == MatchStatus.UNMET]
        unresolved = any(item.status in {MatchStatus.NEEDS_REVIEW, MatchStatus.PARTIAL} for item in mandatory_matches)
        if preliminary_only:
            status = DecisionStatus.HOLD
        elif legal_status == "미충족":
            status = DecisionStatus.NO_GO
        elif blocked or scale.status == "기준 미달":
            status = DecisionStatus.NO_GO
        elif legal_status != "충족":
            status = DecisionStatus.HOLD
        elif unresolved or scale.status == "확인 필요":
            status = DecisionStatus.HOLD
        elif capability.human_review_items or market.unknown_items:
            status = DecisionStatus.CONDITIONAL
        else:
            status = DecisionStatus.GO

        risks = _top_risks(rfp, capability)
        market_status, market_reason, market_action = _market_status(rfp, market)
        conditions = [row["item"] for row in risks]
        if blocked:
            conditions.append("필수 수행조건 미충족: " + ", ".join(blocked))
        if scale.status in {"기준 미달", "확인 필요"}:
            conditions.append(scale.rationale)
        if preliminary_only:
            conditions.insert(0, "공고문 원본 확인 전에는 법적 참가자격과 최종 입찰 참여 여부를 확정할 수 없습니다.")
        if market.unknown_items:
            conditions.append("출처가 있는 시장·경쟁 근거가 충분히 확보되지 않았습니다.")
        actions = _checklist(rfp, capability, market)
        if scale.status == "기준 미달":
            actions.insert(0, "내부 최소 금액 미달 사업의 전략적 참여 예외를 사업본부장이 재검토")
        one_line = "공고문 원본 기준의 참가자격과 핵심 증빙을 확인한 뒤 참여 여부를 결정해야 합니다." if status in {DecisionStatus.HOLD, DecisionStatus.NO_GO} else "핵심 증빙과 제출 요건을 보완하면 입찰 참여를 검토할 수 있습니다."
        if status == DecisionStatus.NO_GO:
            one_line = "확인된 필수조건 미충족 또는 내부 사업규모 기준 미달로 원칙적 No-Go 검토 대상입니다. 최종 판단은 사업본부장이 수행합니다."
        findings = [
            ReportFinding(topic="분석 범위", assessment=_analysis_scope(rfp), evidence_references=["debug/01_rfp_analysis.json > quality_gate"]),
            ReportFinding(topic="문서 품질", assessment=_document_status(rfp), evidence_references=["debug/01_rfp_analysis.json > quality_gate"]),
            ReportFinding(topic="종합 판단", assessment=one_line, evidence_references=["debug/01_rfp_analysis.json", "debug/02_capability_matching.json"]),
            ReportFinding(topic="핵심 적합 요인", assessment="\n".join(_representative_evidence(capability, rfp)) or "제공된 증빙에서 입찰용으로 확정 가능한 대표 강점을 확인하지 못했습니다.", evidence_references=["debug/02_capability_matching.json"]),
            ReportFinding(topic="핵심 리스크", assessment="\n".join(row["item"] for row in risks) or "현재 구조화된 핵심 리스크 없음", evidence_references=["debug/01_rfp_analysis.json", "debug/02_capability_matching.json"]),
            ReportFinding(topic="시장·경쟁 환경", assessment=market_reason, evidence_references=["debug/03_market_research.json"], action=market_action),
        ]
        return BidReviewReport(
            decision_status=status,
            executive_summary=one_line,
            key_findings=findings,
            bid_conditions=list(dict.fromkeys(conditions)),
            next_actions=actions,
            source_artifacts=list(SOURCE_ARTIFACTS),
            agent_executive_summary=executive_summary,
            final_decision="미결정",
            project_scale=scale,
        )

    def write_quality_gate_hold(self, rfp: RFPAnalysisResult) -> BidReviewReport:
        issues = [item.reason for item in rfp.quality_gate.issues]
        return BidReviewReport(
            decision_status=DecisionStatus.HOLD,
            executive_summary="기본정보 품질 게이트를 통과하지 못해 입찰 참여 여부를 판단하지 않았습니다. 공고문과 RFP 원문을 보완한 뒤 재검토가 필요합니다.",
            key_findings=[ReportFinding(topic="문서 품질", assessment=_document_status(rfp), evidence_references=["debug/01_rfp_analysis.json > quality_gate"])],
            bid_conditions=issues or ["사업 기본정보와 원문 근거 확인 필요"],
            next_actions=["공고문 원본에서 사업명·발주기관·공고번호·제출기한 확인", "텍스트 또는 표 추출이 불명확한 원문 확인"],
            source_artifacts=["debug/01_rfp_analysis.json"], final_decision="미결정",
        )

    def write_notice_gate_hold(self, rfp: RFPAnalysisResult) -> BidReviewReport:
        """RFP requirements may be structurally available, but no bid decision is safe."""
        issues = [item.reason for item in (rfp.integration_quality_gate or rfp.quality_gate).issues]
        return BidReviewReport(
            decision_status=DecisionStatus.HOLD,
            executive_summary="공고문 기본정보가 신뢰 기준을 충족하지 않아 통합 입찰 판단을 실행하지 않았습니다.",
            key_findings=[ReportFinding(topic="공고문 기본정보", assessment="확인 필요", evidence_references=["공고문 원문"] )],
            bid_conditions=issues or ["공고번호·발주기관·마감일 원문 확인 필요"],
            next_actions=["공고문 원본에서 공고번호·발주기관·제출기한 확인", "확인 후 나라장터 조회와 4-Agent 검토 재실행"],
            source_artifacts=["debug/01_rfp_analysis.json"], final_decision="미결정",
        )


def briefing_lines(agent) -> list[str]:
    """CrewAI 의사결정 요약(ExecutiveDecisionSummary)을 보고서 MD 줄로. LangGraph T4 보고서도 같은 표기를 쓴다."""
    lines = ["", "### CrewAI 의사결정 요약", "", f"- Agent 권고: {agent.recommendation}", f"- 사유: {_compact(agent.one_line_reason, 260)}"]
    for item in agent.top_positive_factors:
        lines.append(f"- Agent 긍정 요인: {_compact(item.statement, 180)} ({'; '.join(ref.reference for ref in item.evidence_references)})")
    for item in agent.top_blockers_or_risks:
        lines.append(f"- Agent 리스크: {_compact(item.statement, 180)} ({'; '.join(ref.reference for ref in item.evidence_references)})")
    lines.append("- 위 Agent 권고는 보조 의견이며, 보고서 권고와 최종 결정은 규칙 기반 검증 및 사업 담당자 확인을 따릅니다.")
    return lines


def _overview_rows(rfp: RFPAnalysisResult) -> list[tuple[str, str]]:
    basic = rfp.basic_info
    return [
        ("사업명", str(rfp.project_summary.project_name or basic.project_name.value or UNCONFIRMED)),
        ("입찰공고·계약 담당", str(basic.procuring_entity.value or rfp.project_summary.buyer or UNCONFIRMED)),
        ("수요기관", str(basic.demanding_entity.value or UNCONFIRMED)),
        ("공고번호", str(basic.notice_number.value or UNCONFIRMED)),
        ("예산", format_krw(rfp.project_summary.budget_krw) if rfp.project_summary.budget_krw is not None else UNCONFIRMED),
        ("추정가격", format_krw(rfp.project_summary.estimated_price_krw) if rfp.project_summary.estimated_price_krw is not None else UNCONFIRMED),
        ("사업기간", str(rfp.project_summary.period or basic.contract_period.value or UNCONFIRMED)),
        ("계약방법", str(basic.contract_method.value or UNCONFIRMED)),
        ("평가방법", str(basic.evaluation_method.value or UNCONFIRMED)),
        ("제안서·가격입찰서 제출 시작", str(basic.proposal_submission_start.value or UNCONFIRMED)),
        ("제안서 제출기한", str(rfp.project_summary.submission_deadline or basic.proposal_deadline.value or basic.bid_deadline.value or UNCONFIRMED)),
        ("공동수급협정서 제출기한", str(basic.joint_venture_deadline.value or UNCONFIRMED)),
        ("공동수급 조건", _compact(str(basic.joint_venture_policy.value or UNCONFIRMED), 120)),
    ]


def report_markdown(report: BidReviewReport, rfp: RFPAnalysisResult | None = None, capability: CapabilityMatchingResult | None = None, market: MarketResearchResult | None = None) -> str:
    """Render a concise business report; source details stay in 01/02/03 artifacts."""
    if rfp and not (capability and market):
        basic = rfp.basic_info
        fields = [
            ("사업명", basic.project_name), ("발주기관/수요기관", basic.procuring_entity if basic.procuring_entity.value else basic.demanding_entity),
            ("공고번호", basic.notice_number), ("제출기한", basic.proposal_deadline if basic.proposal_deadline.value else basic.bid_deadline),
        ]
        issues = {item.field: item for item in _effective_gate(rfp).issues}
        lines = ["# 문서 입력 확인 결과", "", "기본정보 품질 게이트를 통과하지 못해 입찰 참여 판단·나라장터 시장조사·CrewAI Agent 2~4를 실행하지 않았습니다.", "", "## 신뢰 가능한 기본정보", "", "| 항목 | 값 | 출처 페이지 |", "|---|---|---|"]
        for label, candidate in fields:
            value = candidate.value if candidate.value is not None else "확인 필요"
            page = f"p.{candidate.source_page}" if candidate.value is not None and candidate.source_page else "-"
            lines.append(f"| {label} | {_cell(str(value))} | {page} |")
        lines += ["", "## 미확인 항목 및 확인 필요 사유", "", "| 항목 | 사유 | 확인 페이지 |", "|---|---|---|"]
        for label, candidate in fields:
            field = {"사업명": "project_name", "발주기관/수요기관": "procuring_entity", "공고번호": "notice_number", "제출기한": "proposal_deadline"}[label]
            issue = issues.get(field) or issues.get("bid_deadline")
            reason = issue.reason if issue else candidate.invalid_reason or "원문 확인 필요"
            page = issue.source_page if issue and issue.source_page else candidate.source_page
            lines.append(f"| {label} | {_cell(reason)} | {f'p.{page}' if page else '-'} |")
        lines += ["", "## 실행 제한", "", "- 나라장터 API: 신뢰 가능한 공고 기본정보 미확인으로 미실행", "- CrewAI Agent 1~4: 문서 입력 확인 결과만 생성하고 입찰 판단을 실행하지 않음", "- 후속 조치: 공고문 원본의 공고번호·발주기관·제출기한과 RFP 사업명·요구사항을 확인 후 재실행"]
        return "\n".join(lines) + "\n"
    if not (rfp and capability and market):
        return "\n".join(["# 입찰 참여 사전 검토 보고서", "", f"> **권고: {report.decision_status.value}**", "", report.executive_summary, "", "## 담당자 확인사항", "", *[f"- [ ] {item}" for item in report.next_actions]]) + "\n"

    project_name = rfp.project_summary.project_name or rfp.basic_info.project_name.value or "사업명 원문 확인 필요"
    legal_status = _aggregate_status(capability.eligibility_matches)
    staff_status = _aggregate_status(capability.staffing_matches)
    company_data_missing = "회사 역량 자료 미제공" in (capability.company_evidence_notice or "")
    technical_status = ("자료 미제공" if company_data_missing
                        else "공개 근거 확인" if capability.public_technology_and_certifications else "확인 필요")
    capability_status = ("자료 미제공" if company_data_missing
                         else "공개 근거 확인" if capability.representative_public_records else "확인 필요")
    capability_basis = ("회사 증빙 JSON 미제공" if company_data_missing
                        else f"공개 수행실적 {len(capability.representative_public_records)}건, 입찰용 원본 확인 전")
    tech_basis = ("회사 증빙 JSON 미제공" if company_data_missing
                  else f"공개 기술·인증 {len(capability.public_technology_and_certifications)}건, 입찰용 원본 확인 전")
    market_status, market_reason, market_action = _market_status(rfp, market)
    strengths = _representative_evidence(capability, rfp)
    preliminary_evidence = _representative_evidence(capability, rfp, include_partial=True)
    risks = _top_risks(rfp, capability)
    checklist = _checklist(rfp, capability, market)
    references = _source_references(rfp, capability)
    lines = [
        f"# {project_name} 입찰 참여 사전 검토 보고서", "",
        f"검토 대상 법인: {VISIBLE_COMPANY_NAME}", f"검토 기준일: {date.today().isoformat()}",
        f"분석 범위: {_analysis_scope(rfp)}", f"문서 상태: {_document_status(rfp)}", "",
        "## 검토 결론", "", f"> **권고: {report.decision_status.value}**", ">", f"> 한 줄 요약: {report.executive_summary}", "",
        "### 핵심 판단 근거",
    ]
    if strengths:
        lines.extend(f"- 긍정: {item}" for item in strengths[:3])
    else:
        lines.append("- 긍정: 제공된 증빙에서 입찰용으로 확정 가능한 대표 강점을 확인하지 못했습니다.")
    if risks:
        lines.extend(f"- 보완 필요: {row['item']}" for row in risks[:3])
    else:
        lines.append("- 보완 필요: 최종 참여 전 법적 참가자격과 제출증빙을 원문으로 확인해야 합니다.")

    if report.agent_executive_summary:
        lines += briefing_lines(report.agent_executive_summary)

    lines += ["", "## 1. 사업 개요", "", "| 항목 | 내용 |", "|---|---|"]
    
    lines.extend(
        f"| {label} | {_cell(value)} |"
        for label, value in _overview_rows(rfp)
        if value != UNCONFIRMED
    )

    if rfp.analysis_scope == "rfp_only":
        lines.append(
            "- 별도 공고문 미제공: 발주기관·공고번호·제출기한 등은 공고문으로 확인해야 합니다."
        )

    if report.project_scale:
        scale = report.project_scale
        lines += ["", "### 내부 사업금액 기준 검토", "",
                  f"- 사업금액: {format_krw(scale.amount_krw)}",
                  f"- 내부 최소 기준: {format_krw(scale.minimum_krw)}",
                  f"- 규모 판정: **{scale.status}**", f"- 판단 근거: {scale.rationale}"]
        if scale.source_page and scale.source_excerpt:
            lines.append(f"- 원문: {scale.source_document} PDF {scale.source_page}쪽 / {scale.source_excerpt}")
        lines.append("- 세금 포함 여부·연차별 금액·전체 사업기간은 위 원문 조건을 기준으로 담당자가 확인합니다.")


    legal_conditions = _legal_condition_summaries(rfp)
    if legal_conditions:
        lines += ["", "### 법적 참가자격 핵심 체크", ""]
        lines.extend(f"- 확인 필요: {item} — 회사 원본 증빙·등록 상태 확인" for item in legal_conditions)

    lines += ["", "## 2. 종합 판단", "", "| 검토 영역 | 상태 | 판단 근거 | 필요한 조치 |", "|---|---|---|---|"]
    lines += [
        f"| 법적 참가자격 | {legal_status} | 내부 사전분류 결과: {_counts_label(capability.eligibility_matches)} | 공고문 원본·나라장터 등록정보 확인 |",
        f"| 회사 역량·실적 | {capability_status} | {capability_basis} | 실적증명서의 발주처·기간·금액·범위 확인 |",
        f"| 기술·인증 | {technical_status} | {tech_basis} | 인증 범위·유효기간·실제 적용 경험 확인 |",
        f"| 투입인력 | {staff_status} | 공개자료만으로 인력 충족 여부 확정 불가 | PM·핵심인력의 경력·가용 여부 확인 |",
        f"| 제출 절차 | 확인 필요 | 제안서·가격입찰서·제출기한·서식은 공고 절차 항목 | 제출 전 나라장터 및 제출서류 체크리스트 확인 |",
        f"| 시장·경쟁환경 | {market_status} | {_cell(market_reason)} | {_cell(market_action)} |",
    ]

    lines += ["", "## 3. 핵심 적합 요인", ""]
    if strengths:
        lines.extend(f"- {item}" for item in strengths)
    else:
        if not capability.representative_public_records and not capability.public_technology_and_certifications:
            lines.append("- 제공된 회사 증빙에서 입찰용 원본으로 확정 가능한 대표 강점을 확인하지 못했습니다.")
        else:
            lines.append("- 공개 근거로 확인된 관련 실적·기술역량은 있으나, 입찰용 원본 실적증명서·인력·인증서 확인 전에는 필수 조건 충족으로 확정하지 않았습니다.")
    public_strengths = _public_strength_lines(capability)
    if public_strengths:
        lines += ["", "### 공개 근거 기반 사전 강점", *public_strengths]
    if capability.company_evidence_notice:
        lines.append(f"- 증빙 유의: {_compact(capability.company_evidence_notice, 160)}")

    lines += ["", "## 4. 핵심 리스크 및 확인 필요 사항", "", "| 우선순위 | 확인할 내용 | 왜 중요한가 | 필요한 증빙/담당 |", "|---|---|---|---|"]
    if risks:
        lines.extend(f"| {row['priority']} | {_cell(row['item'])} | {_cell(row['why'])} | {_cell(row['action'])} |" for row in risks)
    else:
        lines.append("| 중간 | 최종 제출증빙 확인 | AI 사전 검토만으로 참여 자격을 확정할 수 없음 | 사업기획·프리세일즈 담당 확인 |")

    lines += ["", "## 5. 회사 역량 및 유사실적 검토", "", "- 아래 항목은 공식 공개자료에 기반한 사전 검토이며, 요구사항별 점수나 충족 판정으로 사용하지 않습니다."]
    if strengths:
        lines.extend(f"- 대표 매칭 근거: {item}" for item in strengths[:3])
    else:
        if capability.representative_public_records:
            lines.append(f"- 공개 대표 수행실적: {len(capability.representative_public_records)}건 (입찰용 원본 실적증명서 확인 필요)")
            lines.extend(f"- {item.title}: {item.relevance}" for item in capability.representative_public_records)
        else:
            lines.append("- 대표 유사실적: 제공된 자료만으로는 입찰용 원본 실적증명서를 확인하지 못했습니다.")
    if capability.public_technology_and_certifications:
        lines.append(f"- 공개 기술·인증 참고: {len(capability.public_technology_and_certifications)}건 (원본 인증서·적용 범위 확인 필요)")
    if capability.financial_reference:
        lines.append(f"- 재무 참고: {capability.financial_reference.title} / {capability.financial_reference.relevance}")
    lines.append("- 공개 또는 제공 자료는 사전 검토 근거이며, 제출 전 원본 증빙으로 대체·확인해야 합니다.")

    lines += ["", "## 6. 시장·경쟁 환경 검토", ""]
    if market_status == "확인됨":
        lines.append(f"- {market_reason}")
        for project in market.similar_projects[:3]:
            lines.append(f"- 유사사업 참고: {_compact(project.project_name, 100)} / 발주기관: {project.buyer or UNCONFIRMED} / 출처: {project.source_url}")
    else:
        lines.append(f"- {market_reason}")
    lines.append("- 과거 낙찰·개찰 이력은 이번 입찰의 참여사 또는 경쟁사를 의미하지 않습니다.")

    lines += ["", "## 7. 입찰 전 담당자 확인 체크리스트", ""]
    lines.extend(f"- [ ] {item}" for item in checklist)

    lines += ["", "## 8. 분석 한계 및 근거 문서", "", "- 본 보고서는 AI 기반 사전 검토 결과이며 최종 입찰 결정은 담당자가 수행해야 합니다.", "- 공고문/RFP 원본 또는 회사 증빙이 누락된 항목은 확정 판단하지 않았습니다."]
    if references:
        lines.append(f"- 주요 근거: {'; '.join(references)}")
    lines.append("- 상세 요구사항·원문 발췌는 debug/01_rfp_analysis.json, debug/02_capability_matching.json, debug/03_market_research.json에서 확인합니다.")
    return "\n".join(lines) + "\n"
