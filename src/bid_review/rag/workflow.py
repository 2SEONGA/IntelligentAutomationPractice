"""LangGraph workflow for evidence-backed public SI bid review.

The graph deliberately separates T1 extraction, T1.5 source verification,
additional retrieval, human review, and T2 company matching. It never lets an
unverified RFP silently flow into an automated fit conclusion.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from ..agent01_rfp import RFPExtractor
from ..agent02_match import CapabilityMatcher, _tokens as _requirement_tokens
from ..crew import BidReviewCrew
from ..settings import load_settings
from ..web.notices import NoticeError, compare_notice_pdf_bytes, fetch_notice_by_number
from .company_retriever import (
    administrative_clause, clean_requirement_query, company_record_metadata, company_semantic_scores, search_company_evidence,
)
from .past_projects import project_profile, search_similar_projects
from .semantic import MIN_SCORE, SemanticUnavailable, search_semantic

# M25 담당자 평가(36건)로 조정: 0.35 → 0.36 (정밀도 0.36 → 0.50, 재현율 0.56 → 0.44). 점수 구분력이 약해 표본을 늘려 재평가할 것.
COMPANY_MIN_SCORE = 0.36
from .retriever import RetrievedEvidence, search_keyword
from ..models import RFPAnalysisResult, RetrievedCompanyEvidenceCandidate
from .verification import (
    CoreInfoVerification, EvidenceCitation, T15Verifier, VerificationCheck,
    VerificationStatus, _category_state,
)


class BidReviewState(TypedDict, total=False):
    pdf_path: str
    notice_pdf_path: str | None
    company_path: str | None
    notice_no: str | None
    notice_order: str | None
    rfp: dict[str, Any]
    notice_pdf_analysis: dict[str, Any] | None
    notice_pdf_status: str
    g2b_notice_lookup: dict[str, Any]
    verification: dict[str, Any]
    supplemental_evidence: list[dict[str, Any]]
    company_evidence_candidates: dict[str, list[dict[str, Any]]]
    company_retrieval_note: str
    company_retrieval_skipped: dict[str, str]  # requirement id → 행정 조항 사유 (M22)
    retrieval_round: int
    retrieval_limit: int
    review_decision: Literal["approved", "rejected"]
    review_note: str
    reviewer: str
    capability: dict[str, Any]
    semantic_retrieval_status: str
    similar_projects: list[dict[str, Any]]  # Agent 3 과거 유사 사업 후보 (M23)
    similar_projects_status: str
    human_override: dict[str, Any] | None  # 승인 감사 기록 — State에 없으면 LangGraph가 노드 반환값을 버림
    decision: str
    routing_reasons: list[str]
    errors: list[str]


def _model_from_state(value: dict[str, Any], model):
    return model.model_validate(value)


def create_bid_review_graph():
    """Compile the MVP workflow graph (T1 → T1.5 → retrieval/HITL → T2)."""

    def extract_rfp(state: BidReviewState) -> dict[str, Any]:
        path = Path(state["pdf_path"])
        result = RFPExtractor(load_settings()).analyze(path, document_kind="rfp_pdf")
        notice_pdf_path = state.get("notice_pdf_path")
        notice_result = None
        if notice_pdf_path and Path(notice_pdf_path).is_file():
            notice_result = RFPExtractor(load_settings()).analyze(
                Path(notice_pdf_path), document_kind="notice_pdf",
            )
        notice_no = state.get("notice_no") or (
            str(result.basic_info.notice_number.value)
            if result.basic_info.notice_number.value else None
        )
        return {"rfp": result.model_dump(mode="json"),
                "notice_pdf_analysis": notice_result.model_dump(mode="json") if notice_result else None,
                "notice_pdf_status": "analyzed" if notice_result else ("unavailable" if notice_pdf_path else "not_provided"),
                "notice_no": notice_no,
                "notice_order": state.get("notice_order") or "000",
                "retrieval_round": 0, "retrieval_limit": state.get("retrieval_limit", 2),
                "supplemental_evidence": [], "errors": []}

    def lookup_g2b_notice(state: BidReviewState) -> dict[str, Any]:
        notice_no = state.get("notice_no")
        if not notice_no:
            return {"g2b_notice_lookup": {
                "status": "not_requested", "error": "공고번호가 없어 나라장터 단건 조회를 생략했습니다.",
            }}
        try:
            lookup = fetch_notice_by_number(notice_no, state.get("notice_order") or "000")
        except NoticeError as exc:
            return {"g2b_notice_lookup": {"status": "unavailable", "error": str(exc)}}
        if lookup is None:
            return {"g2b_notice_lookup": {
                "status": "not_found", "error": "공고번호와 차수에 일치하는 나라장터 레코드가 없습니다.",
            }}
        attachment_info = lookup.get("attachment_lookup") or {}
        if attachment_info.get("status") == "found":
            comparison = compare_notice_pdf_bytes(
                attachment_info.get("attachments", []),
                Path(state["notice_pdf_path"]) if state.get("notice_pdf_path") else None,
            )
        else:
            comparison = {"status": "attachment_metadata_unavailable" if attachment_info.get("status") == "unavailable"
                          else "g2b_notice_attachment_missing"}
        lookup["attachment_version_comparison"] = comparison
        return {"g2b_notice_lookup": {"status": "found", **lookup}}

    def verify_core_info(state: BidReviewState) -> dict[str, Any]:
        rfp = _model_from_state(state["rfp"], RFPAnalysisResult)
        lookup = state.get("g2b_notice_lookup", {})
        notice_record = lookup.get("notice")
        if notice_record:
            notice_record = {**notice_record, "attachments": lookup.get("attachments", []),
                             "license_limits": lookup.get("license_limits", {})}
        result = T15Verifier().verify(
            rfp, Path(state["pdf_path"]), state.get("notice_no"),
            notice_pdf=_model_from_state(state["notice_pdf_analysis"], RFPAnalysisResult)
            if state.get("notice_pdf_analysis") else None,
            notice_pdf_status=state.get("notice_pdf_status", "not_provided"),
            notice_record=notice_record,
            g2b_lookup_status=lookup.get("status", "not_attempted"),
            g2b_lookup_error=lookup.get("error"),
            g2b_attachment_lookup_status=(lookup.get("attachment_lookup") or {}).get("status", "not_attempted"),
            g2b_attachment_lookup_error=(lookup.get("attachment_lookup") or {}).get("error"),
            g2b_attachment_comparison=lookup.get("attachment_version_comparison", {}),
            g2b_change_history=lookup.get("change_history", {}),
        )
        return {"verification": result.model_dump(mode="json"),
                "notice_no": result.notice_no,
                "routing_reasons": result.routing_reasons}

    def retrieve_targeted_evidence(state: BidReviewState) -> dict[str, Any]:
        verification = _model_from_state(state["verification"], CoreInfoVerification)
        queries: list[str] = []
        for category in verification.categories.values():
            for claim in category.claims:
                if claim.status == VerificationStatus.RETRIEVE_MORE:
                    queries.extend(claim.retrieval_queries)
        seen: set[str] = set()
        semantic_status, semantic_note = "not_needed", ""
        results: list[RetrievedEvidence] = []
        query_by_chunk: dict[str, list[str]] = {}
        for query in queries:
            key = query.strip().casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            matches = search_keyword(query, limit=3, document_id=verification.document_id,
                                     match_mode="all")
            if verification.notice_no:  # 같은 공고의 공고문 PDF (RFP가 "입찰공고문 참조"로 미루는 일정·금액)
                matches += [item for item in search_keyword(query, limit=10, notice_no=verification.notice_no,
                                                             match_mode="all")
                            if item.metadata.get("document_type") == "notice_pdf"][:3]
            if not matches and semantic_status != "unavailable":  # 원문과 다른 표현일 때만 의미 검색 보조 (M4)
                try:
                    matches = [item for item in search_semantic(query, limit=2, document_id=verification.document_id)
                               + (search_semantic(query, limit=2, notice_no=verification.notice_no, document_type="notice_pdf")
                                  if verification.notice_no else [])
                               if item.metadata["semantic_score"] >= MIN_SCORE]
                    semantic_status = "used"
                except SemanticUnavailable as exc:
                    semantic_status, semantic_note = "unavailable", str(exc)
            results.extend(matches)
            for item in matches:
                associated = query_by_chunk.setdefault(item.chunk_id, [])
                if query not in associated:
                    associated.append(query)
            if len(results) >= 40:
                break
        unique: dict[str, RetrievedEvidence] = {item.chunk_id: item for item in results}
        additions = [{"chunk_id": item.chunk_id, "text": item.text,
                      "metadata": item.metadata, "rank": item.rank,
                      "retrieval_queries": query_by_chunk[item.chunk_id]}
                     for item in unique.values()]
        return {"supplemental_evidence": additions,
                "semantic_retrieval_status": f"{semantic_status}: {semantic_note}" if semantic_note else semantic_status,
                "retrieval_round": state.get("retrieval_round", 0) + 1}

    def reverify_after_retrieval(state: BidReviewState) -> dict[str, Any]:
        # A newly found chunk is surfaced for a human to interpret. It cannot
        # silently become a T1 field value or an automatic pass.
        update = verify_core_info(state)
        verification = _model_from_state(update["verification"], CoreInfoVerification)
        by_query: dict[str, list[dict[str, Any]]] = {}
        for item in state.get("supplemental_evidence", []):
            for query in item.get("retrieval_queries", []):
                by_query.setdefault(query.strip().casefold(), []).append(item)
        for category in verification.categories.values():
            for claim in category.claims:
                if claim.status != VerificationStatus.RETRIEVE_MORE:
                    continue
                matches: list[dict[str, Any]] = []
                for query in claim.retrieval_queries:
                    matches.extend(by_query.get(query.strip().casefold(), []))
                if not matches:
                    continue
                evidence = matches[0]
                meta = evidence["metadata"]
                claim.citations.append(EvidenceCitation(
                    chunk_id=evidence["chunk_id"],
                    document_title=meta.get("document_title", ""),
                    document_type=meta.get("document_type", "unknown"),
                    page=meta.get("page"), section=meta.get("section", ""),
                    quote=evidence["text"], source_path=meta.get("source_path"),
                    source_url=(meta.get("notice_url") or
                                (meta.get("source_url") if meta.get("document_type") != "g2b_notice_metadata" else None)),
                    api_source_url=(meta.get("source_url")
                                    if meta.get("document_type") == "g2b_notice_metadata" else None),
                    notice_no=meta.get("related_notice_no"),
                    notice_order=meta.get("related_notice_order"),
                    field_name=meta.get("field_name"),
                ))
                claim.checks.append(VerificationCheck(
                    check_id="targeted_retrieval_hit", label="추가 검색 근거 발견",
                    status=VerificationStatus.HUMAN_REVIEW,
                    detail=(f"의미 검색(유사도 {meta['semantic_score']:.2f})으로 찾은 후보입니다. 원문 표현이 달라 키워드로는 찾지 못했으므로 "
                            "관련 여부와 T1 값 반영을 사람이 확인해야 합니다." if "semantic_score" in meta else
                            "추가 검색에서 관련 근거 후보를 찾았으나 이 문구를 T1 값으로 구조화할지 사람이 확인해야 합니다."),
                ))
                claim.unresolved_questions.append("검색된 원문 후보에서 항목명·값·예외를 확인하고 T1 후보로 반영할지 검토해 주세요.")
                claim.status = VerificationStatus.HUMAN_REVIEW
            category.status = _category_state(category.claims, coverage_complete=category.coverage_complete)
        verification.status = _category_state(
            [claim for category in verification.categories.values() for claim in category.claims],
            coverage_complete=all(category.coverage_complete for category in verification.categories.values()),
        )
        reasons = [
            f"{key}: {category.status.value} — {category.coverage_note}"
            for key, category in verification.categories.items()
            if category.status != VerificationStatus.CONFIRMED
        ]
        return {**update, "verification": verification.model_dump(mode="json"),
                "routing_reasons": reasons}

    def route_after_verification(state: BidReviewState) -> Literal["retrieve_more", "company_match", "human_review"]:
        if state.get("review_decision") == "approved":
            return "company_match"
        verification = _model_from_state(state["verification"], CoreInfoVerification)
        if verification.status == VerificationStatus.CONFIRMED:
            return "company_match"
        can_retry = (
            state.get("retrieval_round", 0) < state.get("retrieval_limit", 2)
            and any(category.status == VerificationStatus.RETRIEVE_MORE
                    for category in verification.categories.values())
        )
        if can_retry:
            return "retrieve_more"
        return "human_review"

    def retrieve_company_evidence(state: BidReviewState) -> dict[str, Any]:
        """Retrieve source-linked company candidates only after the T1.5 gate."""
        rfp = _model_from_state(state["rfp"], RFPAnalysisResult)
        company_path = state.get("company_path")
        if not company_path:
            return {"company_evidence_candidates": {},
                    "company_retrieval_note": "회사 자료 경로가 없어 Agent 2 검색을 수행하지 않았습니다."}
        profile = BidReviewCrew._load_profile(company_path)
        if "아이티센엔텍" not in profile.company_name:
            return {"company_evidence_candidates": {},
                    "company_retrieval_note": "분석 대상 법인과 회사 자료 법인이 일치하지 않아 검색을 생략했습니다."}

        settings = load_settings()
        performance_terms = settings.get("capability", {}).get("performance_terms", [])
        evidence_by_requirement: dict[str, list[dict[str, Any]]] = {}
        requested: list[tuple[str, str, str]] = [
            (item.id, item.category, f"{item.category} {item.name} {item.details}")
            for item in rfp.requirements
        ]
        requested.extend(
            (item.id, "필수 인력", f"필수 인력 {item.requirement_text}")
            for item in rfp.staffing_requirements
        )
        plans: list[tuple[str, list[str], str, list[str]]] = []  # (requirement id, record types, query text, keyword terms)
        skipped: dict[str, str] = {}
        for requirement_id, category, text in requested:
            lowered = text.casefold()
            if category == "필수 인력" or "인력" in category:
                # General company material cannot establish named staff or availability.
                continue
            if any(term.casefold() in lowered for term in performance_terms):
                types = ["project_reference"]
            elif any(term in text for term in ("인증", "ISO", "CMMI", "보안인증")):
                types = ["certification"]
            elif any(term in text for term in ("재무", "매출", "자본", "신용등급")):
                types = ["financial_reference"]
            else:
                types = ["business_capability", "project_reference", "certification"]
            clause = administrative_clause(requirement_id, text)
            if clause:  # 회사 역량과 대조할 대상이 아닌 행정 조항 (M22)
                skipped[requirement_id] = clause
                continue
            query_terms = sorted(_requirement_tokens(text))[:12]
            if query_terms:
                plans.append((requirement_id, types, text, query_terms))

        # M21: 요구사항 전체를 한 번에 임베딩해 키워드 후보와 의미 후보를 합친 뒤 유사도로 거른다.
        records = company_record_metadata(Path(company_path))
        try:
            scores, record_ids = company_semantic_scores(
                [clean_requirement_query(text) for _, _, text, _ in plans], Path(company_path))
            semantic_note = ""
        except SemanticUnavailable as exc:
            scores, record_ids, semantic_note = None, [], str(exc)
        for row, (requirement_id, types, _, query_terms) in enumerate(plans):
            keyword_ids = [item.record_id for item in search_company_evidence(
                " ".join(query_terms), limit=5, record_types=types, match_mode="any",
                source_path=Path(company_path))]
            if scores is None:  # 의미 검색 불가 → 기존 키워드 후보 3건 그대로
                picked = [(record_id, None, "SQLite FTS5 키워드 검색") for record_id in keyword_ids[:3]]
            else:
                by_id = {record_id: float(scores[row][i]) for i, record_id in enumerate(record_ids)
                         if records[record_id]["record_type"] in types}
                semantic_ids = sorted(by_id, key=lambda record_id: -by_id[record_id])[:3]
                pool = [record_id for record_id in dict.fromkeys(keyword_ids + semantic_ids)
                        if by_id.get(record_id, 0.0) >= COMPANY_MIN_SCORE]
                pool.sort(key=lambda record_id: -by_id[record_id])
                picked = [(record_id, round(by_id[record_id], 4),
                           "키워드+의미 검색" if record_id in keyword_ids else "의미 검색") for record_id in pool[:3]]
            candidates: list[dict[str, Any]] = []
            for record_id, score, method in picked:
                metadata = records[record_id]
                source = metadata.get("source", {})
                candidates.append(RetrievedCompanyEvidenceCandidate(
                    record_id=record_id,
                    title=metadata.get("title", ""),
                    record_type=metadata.get("record_type", "other"),
                    excerpt=metadata.get("excerpt", ""),
                    source_id=metadata.get("source_id"),
                    source_title=source.get("title"),
                    source_url=source.get("url"),
                    source_section=metadata.get("section"),
                    evidence_level=metadata.get("evidence_level"),
                    verification_required=metadata.get("verification_required"),
                    data_freshness=metadata.get("data_freshness"),
                    retrieval_method=method,
                    semantic_score=score,
                ).model_dump(mode="json"))
            evidence_by_requirement[requirement_id] = candidates
        note = (f"키워드(FTS5) 후보와 OpenAI 임베딩 의미 검색 후보를 합쳐 유사도 {COMPANY_MIN_SCORE} 이상만 남겼습니다."
                if scores is not None else f"의미 검색을 쓸 수 없어 키워드(FTS5) 후보만 사용했습니다: {semantic_note}")
        if skipped:
            note += f" 행정 조항 {len(skipped)}건(보안서약·지식재산권·실적 인정 규칙 등)은 회사 자료 검색에서 제외했습니다."
        return {"company_evidence_candidates": evidence_by_requirement,
                "company_retrieval_skipped": skipped,
                "company_retrieval_note": note + " 검색 후보는 규칙 기반 적합도·자격 판정에 자동 합산되지 않습니다."}

    def company_match(state: BidReviewState) -> dict[str, Any]:
        # Guard duplicated here so direct graph resumes cannot bypass T1.5.
        verification = _model_from_state(state["verification"], CoreInfoVerification)
        approved_by_human = state.get("review_decision") == "approved" and bool(state.get("review_note", "").strip())
        if verification.status != VerificationStatus.CONFIRMED and not approved_by_human:
            return {"decision": "human_review_required",
                    "routing_reasons": ["T1.5 핵심정보 검증 미통과로 T2 진입을 차단했습니다."]}
        rfp = _model_from_state(state["rfp"], RFPAnalysisResult)
        profile = BidReviewCrew._load_profile(state.get("company_path"))
        candidates = {
            key: [RetrievedCompanyEvidenceCandidate.model_validate(item) for item in items]
            for key, items in state.get("company_evidence_candidates", {}).items()
        }
        capability = CapabilityMatcher(load_settings()).analyze(
            rfp, profile, retrieved_candidates_by_requirement=candidates,
        )
        return {"capability": capability.model_dump(mode="json"),
                "decision": "human_approved_company_matching_complete" if approved_by_human else "company_matching_complete",
                "human_override": ({"reviewer": state.get("reviewer"), "note": state.get("review_note"),
                                    "verification_status": verification.status.value}
                                   if approved_by_human else None)}

    def similar_projects(state: BidReviewState) -> dict[str, Any]:
        """Agent 3: 과거 나라장터 사업 중 사업 프로필이 비슷한 후보. 실패해도 흐름을 막지 않는다."""
        if state.get("decision") not in ("company_matching_complete", "human_approved_company_matching_complete"):
            return {"similar_projects_status": "not_run: T2 미실행"}
        rfp = _model_from_state(state["rfp"], RFPAnalysisResult)
        notice = (state.get("g2b_notice_lookup") or {}).get("notice") or {}  # 공식 공고명·수요기관 우선, T1 값은 대체용
        title = str(notice.get("bidNtceNm") or rfp.basic_info.project_name.value or Path(state["pdf_path"]).stem)
        try:
            buyer = str(notice.get("dminsttNm") or rfp.basic_info.procuring_entity.value or "")
            results = search_similar_projects(project_profile(Path(state["pdf_path"]), title, buyer),
                                              exclude_notice_no=state.get("notice_no"), query_title=title)
        except (SemanticUnavailable, OSError, ValueError) as exc:
            return {"similar_projects": [], "similar_projects_status": f"unavailable: {exc}"}
        return {"similar_projects": results,
                "similar_projects_status": f"found {len(results)}" if results else "none_above_threshold"}

    def human_review(state: BidReviewState) -> dict[str, Any]:
        if state.get("review_decision") == "rejected":
            return {"decision": "human_rejected", "routing_reasons": [state.get("review_note", "담당자가 후속 분석을 반려했습니다.")]}
        verification = _model_from_state(state["verification"], CoreInfoVerification)
        return {"decision": "human_review_required",
                "routing_reasons": verification.routing_reasons or [
                    "핵심정보 검증 결과를 담당자가 확인해야 합니다."
                ]}

    graph = StateGraph(BidReviewState)
    graph.add_node("t1_extract", extract_rfp)
    graph.add_node("g2b_notice_lookup", lookup_g2b_notice)
    graph.add_node("t15_verify", verify_core_info)
    graph.add_node("targeted_retrieval", retrieve_targeted_evidence)
    graph.add_node("t15_reverify", reverify_after_retrieval)
    graph.add_node("t2_company_rag_retrieval", retrieve_company_evidence)
    graph.add_node("t2_company_match", company_match)
    graph.add_node("t3_similar_projects", similar_projects)
    graph.add_node("human_review", human_review)
    graph.add_edge(START, "t1_extract")
    graph.add_edge("t1_extract", "g2b_notice_lookup")
    graph.add_edge("g2b_notice_lookup", "t15_verify")
    graph.add_conditional_edges("t15_verify", route_after_verification, {
        "retrieve_more": "targeted_retrieval",
        "company_match": "t2_company_rag_retrieval",
        "human_review": "human_review",
    })
    graph.add_edge("targeted_retrieval", "t15_reverify")
    graph.add_conditional_edges("t15_reverify", route_after_verification, {
        "retrieve_more": "targeted_retrieval",
        "company_match": "t2_company_rag_retrieval",
        "human_review": "human_review",
    })
    graph.add_edge("t2_company_rag_retrieval", "t2_company_match")
    graph.add_edge("t2_company_match", "t3_similar_projects")
    graph.add_edge("t3_similar_projects", END)
    graph.add_edge("human_review", END)
    return graph.compile()


def run_bid_review(pdf_path: str | Path, company_path: str | Path | None = None,
                   notice_no: str | None = None, retrieval_limit: int = 2,
                   review_decision: Literal["approved", "rejected"] | None = None,
                   review_note: str = "", reviewer: str | None = None,
                   notice_order: str | None = None,
                   notice_pdf_path: str | Path | None = None) -> BidReviewState:
    """Run a single bid document through the compiled LangGraph workflow."""
    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    company = Path(company_path).expanduser().resolve() if company_path else None
    if company is not None and not company.is_file():
        raise FileNotFoundError(company)
    notice_pdf = Path(notice_pdf_path).expanduser().resolve() if notice_pdf_path else None
    return create_bid_review_graph().invoke({
        "pdf_path": str(path),
        "notice_pdf_path": str(notice_pdf) if notice_pdf else None,
        "company_path": str(company) if company else None,
        "notice_no": notice_no,
        "notice_order": notice_order or "000",
        "retrieval_limit": max(0, min(retrieval_limit, 3)),
        **({"review_decision": review_decision, "review_note": review_note, "reviewer": reviewer}
           if review_decision else {}),
    })
