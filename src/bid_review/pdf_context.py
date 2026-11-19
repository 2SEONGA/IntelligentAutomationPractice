"""Compact presentation and Agent-context views of page-level PDF metadata.

Page analysis remains on ``RFPAnalysisResult`` during execution. It is an
extraction diagnostic, not normal review content or LLM task context.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from .models import RFPAnalysisResult


PAGE_TYPE_LABELS = {
    "cover_page": "표지",
    "table_of_contents": "목차",
    "body_page": "본문",
    "table_heavy_page": "표 중심 페이지",
    "appendix_page": "별첨",
}


def _effective_quality_status(result: RFPAnalysisResult) -> str:
    return (result.integration_quality_gate or result.quality_gate).status


def _pages(values: list[int | None]) -> list[int]:
    return sorted({value for value in values if value is not None})


def page_analysis_summary(result: RFPAnalysisResult) -> dict[str, Any]:
    """Return only the small, reviewer-safe view of internal diagnostics."""
    counts = Counter(page.page_type for page in result.page_analysis)
    basic_pages = _pages([
        candidate.get("source_page")
        for candidate in result.basic_info.model_dump().values()
        if candidate.get("value") is not None
    ])
    eligibility_pages = _pages([item.evidence.pdf_page for item in result.eligibility_requirements])
    return {
        "total_pages": len(result.page_analysis),
        "page_type_counts": {page_type: counts.get(page_type, 0) for page_type in PAGE_TYPE_LABELS},
        "ocr_required_pages": _pages([page.page_number for page in result.page_analysis if page.ocr_required]),
        "ocr_applied_pages": _pages([page.page_number for page in result.page_analysis if page.ocr_applied]),
        "ocr_attempted_pages": _pages([page.page_number for page in result.page_analysis if page.ocr_attempted]),
        "ocr_failed_pages": _pages([page.page_number for page in result.page_analysis
                                    if page.ocr_attempted and not page.ocr_succeeded]),
        "ocr_seconds": round(sum(page.ocr_seconds for page in result.page_analysis), 3),
        "relevant_pages": {
            "basic_information": basic_pages,
            "eligibility_requirements": eligibility_pages,
        },
    }


def document_quality_context(result: RFPAnalysisResult) -> dict[str, Any]:
    """The only page-structure information allowed into CrewAI inputs."""
    summary = page_analysis_summary(result)
    return {
        "document_quality_status": _effective_quality_status(result),
        "document_quality_summary": "목차와 표지 제외 후 본문·표 페이지를 중심으로 분석함"
        + (f"; {len(summary['ocr_applied_pages'])}개 페이지는 OCR로 읽은 텍스트로 분석함" if summary["ocr_applied_pages"] else ""),
        "ocr_required_pages": summary["ocr_required_pages"],
        "ocr_applied_pages": summary["ocr_applied_pages"],
        "relevant_pages": summary["relevant_pages"],
    }


def artifact_payload(result: RFPAnalysisResult) -> dict[str, Any]:
    """Normal JSON artifact: omit debug-only page-by-page details."""
    return result.model_dump(exclude={"page_analysis"})


def agent_context_payload(result: RFPAnalysisResult) -> dict[str, Any]:
    """RFP context used by Agents; it cannot contain ``page_analysis``."""
    payload = artifact_payload(result)
    payload["document_context"] = document_quality_context(result)
    return payload


def agent_context_json(result: RFPAnalysisResult) -> str:
    return json.dumps(agent_context_payload(result), ensure_ascii=False)


def debug_page_analysis_payload(result: RFPAnalysisResult) -> dict[str, Any]:
    """Explicit opt-in diagnostic file, separate from normal artifacts."""
    return {
        "document_quality_context": document_quality_context(result),
        "page_analysis": [page.model_dump() for page in result.page_analysis],
    }


def json_size(value: Any) -> int:
    """UTF-8 byte length used for transparent Agent-context diagnostics."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
