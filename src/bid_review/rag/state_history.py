"""공고 식별자·문서 버전과 Node별 실행 경위를 State에 보존한다."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

# 나라장터 공고번호: 차세대(R26BK01762350) 또는 구 형식(20240112345). 뒤에 차수(-000)가 붙을 수 있다.
# 기관 자체 공고번호(예: 제2026-013호)는 이 형식이 아니다.
G2B_NOTICE_RE = re.compile(r"(R\d{2}[A-Z]{2}\d{8}|20\d{9})(?:\s*-\s*(\d{3}))?")


def parse_g2b_notice(value: Any) -> tuple[str, str | None] | None:
    """문자열에서 나라장터 공고번호와 차수를 찾는다. 없으면 None."""
    match = G2B_NOTICE_RE.search(str(value or "").upper())
    return (match.group(1), match.group(2)) if match else None


def file_identity(path: str | Path) -> dict[str, str]:
    source = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    sha = digest.hexdigest()
    return {"document_id": sha[:16], "sha256": sha, "path": str(source)}


def case_identity(state: dict[str, Any], extracted_notice_no: Any,
                  notice_pdf_number: Any = None) -> dict[str, Any]:
    """입력 공고와 PDF의 공고번호를 따로 보존하고 불일치를 표시한다."""
    rfp = file_identity(state["pdf_path"])
    notice_pdf = (file_identity(state["notice_pdf_path"])
                  if state.get("notice_pdf_path") and Path(state["notice_pdf_path"]).is_file() else None)
    company = (file_identity(state["company_path"])
               if state.get("company_path") and Path(state["company_path"]).is_file() else None)
    def g2b(value: Any) -> str:
        parsed = parse_g2b_notice(value)
        return parsed[0] if parsed else ""

    raw_input = str(state.get("notice_no") or "").strip()
    input_no = g2b(raw_input) or raw_input
    # 문서에서 읽은 번호는 나라장터 형식일 때만 비교·조회에 쓴다. 기관 자체 공고번호(예: 제2026-013호)를
    # 나라장터 번호와 비교하면 같은 공고도 늘 "충돌"이 된다(통합정보시스템 오탐, M37).
    extracted_no, notice_pdf_no = g2b(extracted_notice_no), g2b(notice_pdf_number)
    agency_numbers = [str(raw).strip() for raw in (extracted_notice_no, notice_pdf_number)
                      if str(raw or "").strip() and not g2b(raw)]
    order = str(state.get("notice_order") or "000").zfill(3)
    conflicts = []
    if input_no and extracted_no and input_no != extracted_no:
        conflicts.append(f"입력 공고번호 {input_no}와 RFP 추출 공고번호 {extracted_no}가 다릅니다.")
    effective = input_no or extracted_no or notice_pdf_no or None
    if notice_pdf_no and effective and notice_pdf_no != effective:
        conflicts.append(f"공고문 PDF 추출 공고번호 {notice_pdf_no}가 실행 공고번호 {effective}와 다릅니다.")
    run_id = str(state.get("run_id") or uuid4().hex)
    return {"run_id": run_id, "notice_no": effective, "notice_order": order,
            "case_id": f"{effective}:{order}" if effective else f"document:{rfp['document_id']}",
            "rfp": rfp, "notice_pdf": notice_pdf, "company": company,
            "input_notice_no": input_no or None,
            "extracted_notice_no": extracted_no or None, "notice_pdf_number": notice_pdf_no or None,
            "agency_notice_numbers": agency_numbers,  # 참고용 기관 자체 번호(비교하지 않음)
            "conflicts": conflicts,
            "status": "conflict" if conflicts else "identified"}


def validate_api_identity(identity: dict[str, Any], lookup: dict[str, Any]) -> dict[str, Any]:
    """API 레코드의 공고번호·차수가 요청한 공고와 다르면 분석을 중단한다."""
    updated = {**identity, "conflicts": list(identity.get("conflicts") or [])}
    notice = lookup.get("notice") or {}
    api_no = str(notice.get("bidNtceNo") or "").strip()
    api_order = str(notice.get("bidNtceOrd") or "").strip()
    if api_order.isdigit():
        api_order = api_order.zfill(3)
    if api_no and updated.get("notice_no") and api_no != updated["notice_no"]:
        updated["conflicts"].append(f"조회된 공고번호 {api_no}가 실행 공고번호와 다릅니다.")
    if api_order and api_order != updated.get("notice_order"):
        updated["conflicts"].append(f"조회된 공고 차수 {api_order}가 실행 차수와 다릅니다.")
    updated["api_notice_no"] = api_no or None
    updated["api_notice_order"] = api_order or None
    updated["attachment_version_comparison"] = lookup.get("attachment_version_comparison") or {}
    updated["status"] = "conflict" if updated["conflicts"] else "identified"
    return updated


def source_matches(identity: dict[str, Any], metadata: dict[str, Any], *, document_type: str) -> bool:
    """검색 후보가 이번 실행의 PDF 또는 같은 공고·차수의 공고문에 속하는지 검사."""
    doc_id = metadata.get("document_id")
    notice_no = metadata.get("related_notice_no")
    order = str(metadata.get("related_notice_order") or "").strip()
    if order.isdigit():
        order = order.zfill(3)
    if notice_no and identity.get("notice_no") and str(notice_no) != identity["notice_no"]:
        return False
    if order and order != identity.get("notice_order"):
        return False
    if document_type == "rfp_pdf":
        rfp = identity.get("rfp") or {}
        return (doc_id == rfp.get("document_id") and
                (not metadata.get("document_sha256") or metadata["document_sha256"] == rfp.get("sha256")))
    if document_type == "notice_pdf":
        known = (identity.get("notice_pdf") or {}).get("document_id")
        version = (identity.get("notice_pdf") or {}).get("sha256")
        return bool(notice_no and notice_no == identity.get("notice_no") and
                    (not known or doc_id == known) and
                    (not version or not metadata.get("document_sha256") or metadata["document_sha256"] == version))
    return False


def citation_conflicts(identity: dict[str, Any], verification: dict[str, Any]) -> list[str]:
    """검증에 인용된 원문이 다른 공고/차수로 표시된 경우 차단한다."""
    errors: list[str] = []
    expected_no = identity.get("notice_no")
    expected_order = identity.get("notice_order")
    for category in (verification.get("categories") or {}).values():
        for claim in category.get("claims", []):
            for citation in claim.get("citations", []):
                cited_no = citation.get("notice_no")
                cited_order = str(citation.get("notice_order") or "").strip()
                if cited_order.isdigit():
                    cited_order = cited_order.zfill(3)
                source = citation.get("chunk_id") or citation.get("document_title") or "원문 근거"
                if expected_no and cited_no and str(cited_no) != expected_no:
                    errors.append(f"{source}: 다른 공고번호 {cited_no}가 인용됐습니다.")
                if expected_no and cited_order and cited_order != expected_order:
                    errors.append(f"{source}: 다른 공고 차수 {cited_order}가 인용됐습니다.")
    return list(dict.fromkeys(errors))


def merge_execution_history(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge complete history snapshots from concurrent branches without duplicating old events."""
    merged = [dict(item) for item in left]
    seen = {item.get("event_id") or (item.get("run_id"), item.get("sequence"), item.get("node"), item.get("at"))
            for item in merged}
    for item in right:
        key = item.get("event_id") or (item.get("run_id"), item.get("sequence"), item.get("node"), item.get("at"))
        if key not in seen:
            merged.append(dict(item))
            seen.add(key)
    for sequence, item in enumerate(merged, start=1):
        item["sequence"] = sequence
    return merged


def record_stage(state: dict[str, Any], node: str, update: dict[str, Any],
                 *, duration_seconds: float | None = None) -> dict[str, Any]:
    """Node 반환값에 실행 기록을 추가한다. 큰 원문/개인 검토 메모는 복사하지 않는다."""
    identity = update.get("case_identity") or state.get("case_identity") or {}
    history = list(state.get("execution_history") or [])
    verification = update.get("verification") or {}
    evaluation = update.get("agent23_evaluation") or {}
    plan = update.get("routing_plan") or {}
    reviewed = update.get("review_history") or []
    event: dict[str, Any] = {
        "event_id": uuid4().hex,
        "sequence": len(history) + 1,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "run_id": identity.get("run_id") or state.get("run_id"),
        "case_id": identity.get("case_id"),
        "document_id": (identity.get("rfp") or {}).get("document_id"),
        "document_sha256": (identity.get("rfp") or {}).get("sha256"),
        "company_sha256": (identity.get("company") or {}).get("sha256"),
        "node": node,
    }
    if duration_seconds is not None:
        event["duration_seconds"] = round(duration_seconds, 6)
    if verification:
        event["status"] = verification.get("status")
        event["failure_reasons"] = verification.get("routing_reasons", [])
    elif evaluation:
        event["status"] = evaluation.get("status")
        event["failure_reasons"] = [item.get("check_id") for item in evaluation.get("checks", [])
                                    if item.get("status") == "needs_review"]
    elif plan:
        event["status"] = plan.get("action")
        event["failure_reasons"] = [plan.get("reason")]
        event["targets"] = [{"category": item.get("category"), "field": item.get("field")}
                            for item in plan.get("targets", [])]
    else:
        event["status"] = (update.get("decision") or update.get("similar_projects_status")
                           or update.get("t4_status") or "completed")
    if "retrieval_round" in update:
        event["retrieval_round"] = update["retrieval_round"]
    if "reanalysis_attempts" in update:
        event["reanalysis_attempts"] = update["reanalysis_attempts"]
    if update.get("reanalysis_error"):
        event["failure_reasons"] = [update["reanalysis_error"]]
    if "supplemental_evidence" in update:
        event["evidence_ids"] = [item.get("chunk_id") for item in update["supplemental_evidence"]]
    if "company_evidence_candidates" in update:
        event["evidence_ids"] = [item.get("record_id") for group in update["company_evidence_candidates"].values()
                                 for item in group]
    if "similar_projects" in update:
        event["evidence_ids"] = [item.get("notice_id") for item in update["similar_projects"]]
    if "report_validation" in update:
        event["status"] = update["report_validation"].get("status")
        event["failure_reasons"] = [item.get("check_id") for item in update["report_validation"].get("checks", [])
                                    if item.get("status") != "passed"]
    if "review_history" in update and reviewed:
        last = reviewed[-1]
        event["status"] = last.get("action")
        event["reviewer"] = last.get("reviewer")
        event["correction_fields"] = sorted((last.get("changes") or {}).keys())
        event["review_note"] = last.get("note")
    if identity.get("conflicts") and node in {"t1_extract", "g2b_notice_lookup", "t15_route"}:
        event["identity_conflicts"] = identity["conflicts"]
    return {**update, "execution_history": [*history, event]}
