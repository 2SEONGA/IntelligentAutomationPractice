"""Bounded, evidence-first contexts for the live CrewAI path.

The deterministic artifacts remain the audit trail.  This module intentionally
creates a small briefing only; it never serializes page text, all requirements,
or complete matching/market records into an LLM task.
"""
from __future__ import annotations

import re
from typing import Any

from .models import CapabilityMatchingResult, MarketResearchResult, MatchStatus, RFPAnalysisResult
from .project_scale import review_project_scale


def _short(value: str | None, limit: int = 190) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


def _reference(document: str, page: int | None, section: str | None = None) -> str:
    page_label = f" p.{page}" if page else ""
    section_label = f" > {section}" if section and section != "미분류" else ""
    return f"{_short(document, 70)}{page_label}{section_label}"


def _field_item(field) -> dict[str, Any]:
    """A basic-info value with its source page, never the source excerpt."""
    return {
        "value": field.value,
        "reference": _reference(field.source_document, field.source_page, "기본정보"),
    }


def _requirement_rows(items: list, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items[:limit]:
        evidence = getattr(item, "evidence", None)
        if evidence is not None:
            statement = _short(getattr(item, "details", None) or getattr(item, "name", None))
            ref = _reference(evidence.document, evidence.pdf_page, evidence.section)
        else:
            statement = _short(getattr(item, "requirement_text", None))
            ref = _reference(
                "RFP", getattr(item, "source_page", None),
                "요구사항",
            )
        if statement:
            rows.append({"statement": statement, "reference": ref})
    return rows


def _match_rows(matches: list, limit: int, statuses: set[MatchStatus] | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in matches:
        if statuses and match.status not in statuses:
            continue
        item = _short(match.gap_or_check_item or match.score_rationale)
        evidence = match.company_evidence[0] if match.company_evidence else None
        ref = evidence.source_url if evidence and evidence.source_url else (
            f"{evidence.document} > {evidence.section}" if evidence else "회사 원본 증빙 확인 필요"
        )
        if item:
            rows.append({"statement": item, "reference": ref})
        if len(rows) >= limit:
            break
    return rows


def build_compact_summary(
    rfp: RFPAnalysisResult,
    capability: CapabilityMatchingResult,
    market: MarketResearchResult,
) -> dict[str, Any]:
    """Return the sole data shape supplied to live Agent task templates."""
    effective_gate = rfp.integration_quality_gate or rfp.quality_gate
    quality_status = effective_gate.status
    project = rfp.project_summary
    basic = rfp.basic_info
    legal = _requirement_rows(rfp.eligibility_requirements, 5)
    staffing = _requirement_rows(rfp.staffing_requirements, 5)
    technical = _requirement_rows(rfp.technical_requirements or rfp.requirements, 5)
    risks = _match_rows(
        capability.eligibility_matches + capability.staffing_matches + capability.requirement_matches,
        5,
        {MatchStatus.UNMET, MatchStatus.NEEDS_REVIEW},
    )
    conflicts = [
        {
            "statement": _short(f"{item.field}: RFP와 공고문 값이 달라 원문 확인 필요 — {item.reason}"),
            "reference": _reference(item.rfp_value.source_document, item.rfp_value.source_page),
        }
        for item in rfp.conflicts[:3]
    ]
    evidence_references = list(dict.fromkeys(
        [row["reference"] for row in legal + staffing + technical + risks + conflicts]
    ))[:10]
    market_status = "completed" if market.similar_projects or market.market_observations else (
        "skipped" if any("미실행" in value or "실행하지" in value for value in market.unknown_items) else "insufficient_data"
    )
    return {
        "project_summary": {
            "project_name": project.project_name or basic.project_name.value,
            "procuring_entity": basic.procuring_entity.value,
            "demanding_entity": basic.demanding_entity.value,
            "notice_number": basic.notice_number.value,
            "budget_krw": project.budget_krw,
            "estimated_price_krw": project.estimated_price_krw,
            "contract_period": project.period or basic.contract_period.value,
            "proposal_submission_start": basic.proposal_submission_start.value,
            "proposal_deadline": project.submission_deadline or basic.proposal_deadline.value or basic.bid_deadline.value,
            "joint_venture_deadline": basic.joint_venture_deadline.value,
            "contract_method": basic.contract_method.value,
            "evaluation_method": basic.evaluation_method.value,
            "analysis_scope": rfp.analysis_scope,
        },
        "quality_gate_status": quality_status,
        "legal_eligibility_summary": legal,
        "staffing_summary": staffing,
        "technical_summary": technical,
        "top_risks": risks,
        "top_conflicts": conflicts,
        "market_summary": {
            "research_status": market_status,
            "similar_projects_count": len(market.similar_projects),
            "observations": [
                {
                    "statement": _short(item.statement, 190),
                    "reference": item.source_url,
                }
                for item in market.market_observations[:3]
            ],
            "comparable_cases": [
                {
                    "statement": _short(f"{item.project_name} / {item.buyer or '발주기관 미확인'}", 190),
                    "reference": item.source_url,
                }
                for item in market.similar_projects[:3]
            ],
            "unknown_items": [_short(value, 180) for value in market.unknown_items[:3]],
        },
        "evidence_references": evidence_references,
    }


def build_agent_input_summaries(
    rfp: RFPAnalysisResult,
    capability: CapabilityMatchingResult,
    market: MarketResearchResult,
    minimum_project_amount_krw: int = 0,
) -> dict[str, dict[str, Any]]:
    """Build the four deliberately small hand-off inputs used by the MVP.

    The detailed extraction/matching objects remain in memory for validation.
    Nothing in this return value contains PDF page text, requirement IDs, or a
    complete company/market record.
    """
    combined = build_compact_summary(rfp, capability, market)
    basic = rfp.basic_info
    project = combined["project_summary"]
    gate = rfp.integration_quality_gate or rfp.quality_gate
    quality_questions = [
        _short(issue.reason, 180) for issue in gate.issues[:5]
    ] + [row["statement"] for row in combined["top_conflicts"][:3]]

    notice_summary = {
        "internal_project_scale": review_project_scale(rfp, minimum_project_amount_krw).model_dump(),
        "notice_number": _field_item(basic.notice_number),
        "procuring_entity": _field_item(basic.procuring_entity),
        "demanding_entity": _field_item(basic.demanding_entity),
        "budget_krw": _field_item(basic.budget_krw),
        "estimated_price_krw": _field_item(basic.estimated_price_krw),
        "proposal_submission_start": _field_item(basic.proposal_submission_start),
        "bid_or_proposal_deadline": _field_item(basic.proposal_deadline if basic.proposal_deadline.value else basic.bid_deadline),
        "joint_venture_deadline": _field_item(basic.joint_venture_deadline),
        "legal_eligibility_conditions": combined["legal_eligibility_summary"][:5],
        "joint_venture_policy": _short(basic.joint_venture_policy.value, 190),
        "quality_status": gate.status,
        "items_to_verify": list(dict.fromkeys(filter(None, quality_questions)))[:5],
    }
    rfp_summary = {
        "project_name": _field_item(basic.project_name),
        "purpose": _field_item(basic.project_purpose),
        "contract_period": _field_item(basic.contract_period),
        "key_technical_requirements": combined["technical_summary"][:5],
        "key_staffing_requirements": combined["staffing_summary"][:5],
        "key_submission_documents": _requirement_rows(rfp.submission_requirements, 5),
        "key_deliverables": _requirement_rows(rfp.deliverable_requirements, 5),
        "conflicts_or_source_checks": combined["top_conflicts"][:5],
    }
    company_summary = {
        "company_name": capability.company_name,
        "matched_evidence": _match_rows(
            capability.requirement_matches + capability.staffing_matches, 3,
            {MatchStatus.MET, MatchStatus.PARTIAL},
        ),
        "representative_public_records": [item.model_dump() for item in capability.representative_public_records],
        "public_technology_and_certifications": [item.model_dump() for item in capability.public_technology_and_certifications],
        "financial_reference": capability.financial_reference.model_dump() if capability.financial_reference else None,
        "evidence_limitations": capability.evidence_limitations,
    }
    api_status = next((item for item in market.unknown_items if "API" in item or "나라장터" in item), None)
    market_summary = {
        "g2b_api_status": _short(api_status or ("조회 결과 있음" if market.similar_projects or market.market_observations else "조회 근거 부족"), 190),
        "research_status": combined["market_summary"]["research_status"],
        "similar_project_count": combined["market_summary"]["similar_projects_count"],
        "representative_cases": combined["market_summary"]["comparable_cases"][:3],
        "market_observations": combined["market_summary"]["observations"][:3],
        "limitations": combined["market_summary"]["unknown_items"][:5],
    }
    return {
        "notice_summary": notice_summary,
        "rfp_summary": rfp_summary,
        "company_summary": company_summary,
        "market_summary": market_summary,
    }
