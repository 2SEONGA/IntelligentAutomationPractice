from __future__ import annotations

from typing import Any

from .pdf_context import PAGE_TYPE_LABELS, page_analysis_summary
from .models import CapabilityMatchingResult, MarketResearchResult, RFPAnalysisResult
from .money import format_krw


def _evidence(evidence) -> str:
    return f"{evidence.document} p.{evidence.pdf_page or '?'} / {evidence.section}: {evidence.excerpt}"


def _selection_reason(result: RFPAnalysisResult, field: str) -> str:
    return next((item.reason for item in result.basic_info_selection_reasons if item.field == field), "선택 근거 없음")


def rfp_markdown(result: RFPAnalysisResult) -> str:
    basic = result.basic_info
    fields = [
        ("사업명", basic.project_name), ("발주기관", basic.procuring_entity), ("공고번호", basic.notice_number),
        ("수요기관", basic.demanding_entity), ("사업목적", basic.project_purpose),
        ("제안서 제출기한", basic.proposal_deadline), ("입찰 마감일", basic.bid_deadline),
        ("사업예산", basic.budget_krw), ("사업기간", basic.contract_period), ("공동수급", basic.joint_venture_policy),
    ]
    lines = ["# RFP 사전 구조화 결과", "", f"- 분석 범위: **{result.analysis_scope}**", f"- 시장조사 실행 가능: **{'예' if result.market_research_allowed else '아니오'}**", "", "## 기본정보 후보", "", "| 항목 | 값 | 출처 문서 | 페이지 | 원문 근거 | 신뢰도 |", "|---|---|---|---:|---|---|"]
    for label, candidate in fields:
        value = format_krw(candidate.value) if label == "사업예산" and isinstance(candidate.value, int) else (candidate.value or "미확인")
        lines.append(f"| {label} | {value} | {candidate.source_document} | {candidate.source_page or '-'} | {candidate.source_excerpt or '-'} | {candidate.confidence} |")
    page_summary = page_analysis_summary(result)
    type_counts = page_summary["page_type_counts"]
    ocr_pages = page_summary["ocr_required_pages"]
    basic_pages = page_summary["relevant_pages"]["basic_information"]
    lines += ["", "## PDF 구조 분석 요약", "", f"- 총 페이지: {page_summary['total_pages']}"]
    lines.extend(f"- {PAGE_TYPE_LABELS[page_type]}: {type_counts[page_type]}페이지" for page_type in PAGE_TYPE_LABELS)
    lines.extend([
        f"- OCR 또는 원본 확인 필요 페이지: {', '.join(map(str, ocr_pages)) or '없음'}",
        f"- 기본정보 추출에 사용한 페이지: {', '.join(map(str, basic_pages)) or '미확인'}",
    ])
    lines += ["", "## 기본정보 후보·선택 근거", ""]
    for field, values in result.basic_info_candidates.model_dump().items():
        lines.append(f"### {field}")
        if values:
            lines.extend(f"- 후보: {item['value'] or '미확인'} (p.{item['source_page'] or '?'}, {item['page_type'] or '미분류'}, 점수 {item['score']})" for item in values)
        else:
            lines.append("- 후보 없음")
        lines.append(f"- 선택 근거: {_selection_reason(result, field)}")
    lines += ["", "## 품질 게이트", "", f"- RFP: **{(result.rfp_quality_gate or result.quality_gate).status}**", f"- 공고문: **{result.notice_quality_gate.status if result.notice_quality_gate else 'not_provided'}**", f"- 통합: **{result.integration_quality_gate.status if result.integration_quality_gate else result.quality_gate.status}**", f"- OCR 필요: **{'예' if result.needs_ocr else '아니오'}**", ""]
    gate_issues = (result.integration_quality_gate or result.quality_gate).issues
    if gate_issues:
        lines.extend(f"- **{issue.field}**: {issue.reason} (페이지: {issue.source_page or '미확인'})" for issue in gate_issues)
    else:
        lines.append("- 통과")
    if result.conflicts:
        lines += ["", "## 문서 간 충돌", ""]
        for item in result.conflicts:
            lines.append(f"- **{item.field}**: RFP({item.rfp_value.value}, p.{item.rfp_value.source_page}) / 공고문({item.notice_value.value}, p.{item.notice_value.source_page}) — {item.reason}")

    def structured_section(title, items):
        lines.extend(["", f"## {title}", ""])
        if not items:
            lines.append("- 추출 항목 없음")
            return
        for item in items:
            if hasattr(item, "requirement_text"):
                lines.extend([f"### {item.id}", f"- 실제 조건: {item.requirement_text}", f"- 근거 페이지: {item.source_page}", f"- 회사에서 확인할 증빙: {'; '.join(item.required_company_evidence) or '확인 필요'}", f"- 필수 여부: {'필수' if item.mandatory else '확인 필요'}", ""])
            else:
                lines.extend([f"### {item.name}", f"- 실제 조건: {item.details}", f"- 근거 페이지: {item.evidence.pdf_page or '미확인'}", "- 회사에서 확인할 증빙: 나라장터 등록·관련 면허·실적·제한 여부 원본", f"- 필수 여부: {item.mandatory_status.value}", ""])

    structured_section("참가자격", result.eligibility_requirements)
    structured_section("인력 요건", result.staffing_requirements)
    structured_section("제출서류", result.submission_requirements)
    structured_section("산출물", result.deliverable_requirements)
    structured_section("기술요구", result.technical_requirements)
    structured_section("미분류 항목", result.unclassified_requirements)
    lines += ["## 추출·교차검증 이슈", ""]
    if result.extraction_issues:
        lines.extend(f"- **{issue.issue_type}**: {issue.message}" for issue in result.extraction_issues)
    else:
        lines.append("- 없음")
    return "\n".join(lines) + "\n"


def capability_markdown(result: CapabilityMatchingResult) -> str:
    score = result.fit_score
    lines = ["# 자사 역량 비교 및 적합도 분석", "", f"- 적합도: **{score.value}%**", f"- 산식: {score.formula}", f"- 분자 / 분모: {score.numerator} / {score.denominator}", "", "| 요구사항 | 상태 | 점수 | 증빙/확인 항목 |", "|---|---|---:|---|"]
    if result.company_evidence_notice:
        lines.insert(2, f"- 증빙 유의: {result.company_evidence_notice}")
    for item in result.requirement_matches:
        evidence = "; ".join(
            f"{x.document}/{x.section}: {x.excerpt}"
            + (f" (출처: {x.source_url})" if x.source_url else "")
            + (f" [추가 확인: {x.verification_required}]" if x.verification_required else "")
            for x in item.company_evidence
        ) or "회사 증빙 없음"
        lines.append(f"| {item.requirement_id} | {item.status.value} | {item.score} | {evidence}<br>{item.gap_or_check_item} |")
    lines += ["", "## 참가자격 확인 상태 (기술 적합도 점수와 별도)", ""]
    if result.eligibility_matches:
        lines.extend(f"- **{item.status.value}** {item.requirement_id}: {item.gap_or_check_item}" for item in result.eligibility_matches)
    else:
        lines.append("- 확인 필요: 구조화된 참가자격 항목이 없습니다.")
    lines += ["", "## 인력 요건 확인 상태", ""]
    if result.staffing_matches:
        lines.extend(f"- **{item.status.value}** {item.requirement_id}: {item.gap_or_check_item}" for item in result.staffing_matches)
    else:
        lines.append("- 확인 필요: 구조화된 인력 요건 항목이 없습니다.")
    lines += ["", "## 중요 위험", ""]
    if result.critical_risks:
        lines.extend(f"- {item}" for item in result.critical_risks)
    else:
        lines.append("- 없음")
    lines += ["", "## 사람 검토 항목", ""]
    if result.human_review_items:
        lines.extend(f"- {item}" for item in result.human_review_items)
    else:
        lines.append("- 없음")
    return "\n".join(lines) + "\n"


def market_markdown(result: MarketResearchResult) -> str:
    lines = ["# 유사사업·시장 조사 결과", "", "## 유사 과거사업", ""]
    if result.similar_projects:
        for item in result.similar_projects:
            lines += [f"### {item.project_name}", f"- 발주기관: {item.buyer or '미확인'}", f"- 최종낙찰자: {item.awardee or '미확인'}", f"- 낙찰금액 또는 낙찰률: {item.budget_or_award_amount or '미확인'}", f"- 개찰일: {item.opening_date or '미확인'}", f"- 출처: {item.source_url}", f"- 조회일: {item.checked_at}", f"- 유사성: {item.similarity_rationale}", f"- 비교 한계: {'; '.join(item.comparison_limitations) or '없음'}", ""]
    else:
        lines.append("- 미확인")
    lines += ["", "## 시장 관찰", ""]
    if result.market_observations:
        lines.extend(f"- {item.statement} (출처: {item.source_url}, 조회일: {item.checked_at}; 한계: {'; '.join(item.limitations) or '없음'})" for item in result.market_observations)
    else:
        lines.append("- 미확인")
    lines += ["", "## 미확인·주의", ""]
    if result.unknown_items:
        lines.extend(f"- {item}" for item in result.unknown_items)
    else:
        lines.append("- 없음")
    return "\n".join(lines) + "\n"
