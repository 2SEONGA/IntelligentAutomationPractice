"""Evidence-first T1.5 checks over T1 facts and retrieved primary-source chunks."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..agent01_rfp import RFPAnalysisResult
from ..models import RFPFieldCandidate
from ..money import parse_krw
from ..settings import PROJECT_ROOT
from .retriever import notice_pdf_chunks, RetrievedEvidence, related_notice_number, search_keyword


class VerificationStatus(str, Enum):
    CONFIRMED = "confirmed"
    RETRIEVE_MORE = "retrieve_more"
    CONFLICT = "conflict"
    HUMAN_REVIEW = "human_review"


class EvidenceCitation(BaseModel):
    chunk_id: str
    document_title: str
    document_type: str
    page: int | None = None
    section: str
    quote: str
    source_path: str | None = None
    source_url: str | None = None
    api_source_url: str | None = None
    notice_no: str | None = None
    notice_order: str | None = None
    field_name: str | None = None


class VerificationCheck(BaseModel):
    check_id: str
    label: str
    status: VerificationStatus
    detail: str


class VerifiedClaim(BaseModel):
    category: str
    field: str
    claim: Any = None
    normalized_value: Any = None
    status: VerificationStatus
    checks: list[VerificationCheck] = Field(default_factory=list)
    citations: list[EvidenceCitation] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    retrieval_queries: list[str] = Field(default_factory=list)


class ScopeExcludedItem(BaseModel):
    field: str
    source_page: int | None = None
    item: str
    reason: str


class CategoryVerification(BaseModel):
    category: str
    status: VerificationStatus
    claims: list[VerifiedClaim] = Field(default_factory=list)
    scope_exclusions: list[ScopeExcludedItem] = Field(default_factory=list)
    coverage_complete: bool = False
    coverage_note: str = ""


class CoreInfoVerification(BaseModel):
    document_id: str
    notice_no: str | None = None
    notice_pdf_status: str = "not_provided"
    g2b_lookup_status: str = "not_attempted"
    g2b_lookup_error: str | None = None
    g2b_attachment_lookup_status: str = "not_attempted"
    g2b_attachment_lookup_error: str | None = None
    g2b_attachment_comparison: dict[str, Any] = Field(default_factory=dict)
    g2b_change_history: dict[str, Any] = Field(default_factory=dict)
    linked_attachments: list[dict[str, Any]] = Field(default_factory=list)
    status: VerificationStatus
    categories: dict[str, CategoryVerification]
    routing_reasons: list[str] = Field(default_factory=list)


def _api_citation(notice_record: dict[str, Any], field: str, section: str, quote: str) -> EvidenceCitation:
    return EvidenceCitation(
        chunk_id=f"g2b:{notice_record.get('bidNtceNo')}:{field}", document_title=notice_record.get("bidNtceNm", ""),
        document_type="g2b_notice_metadata", section=section, quote=quote,
        source_url=notice_record.get("bidNtceDtlUrl"), api_source_url="https://www.data.go.kr/data/15129394/openapi.do",
        notice_no=notice_record.get("bidNtceNo"), notice_order=notice_record.get("bidNtceOrd"), field_name=field,
    )


def api_eligibility_claims(notice_record: dict[str, Any] | None, rfp_claims: list[VerifiedClaim],
                           notice_texts: dict[str, str] | None = None) -> list[VerifiedClaim]:
    """Cross-check G2B 업종(면허) 제한·공동수급 방식 against the RFP 참가자격 claims.

    Only exact, API-stated facts are compared; a missing RFP mention is human_review,
    not a pass. ponytail: 면허명 포함 여부(공백 무시) 비교 — 면허 그룹(lmtGrpNo) AND/OR 의미는 해석하지 않음.
    """
    if not notice_record:
        return []
    texts = {claim.field: " ".join([str(claim.claim or "")] + [c.quote for c in claim.citations]) for claim in rfp_claims}
    texts.update(notice_texts or {})  # 공고문 PDF chunk: 참가자격이 RFP가 아니라 공고문에만 있는 경우가 있음
    squash = lambda value: re.sub(r"\s+", "", value)  # noqa: E731
    claims: list[VerifiedClaim] = []

    lookup = notice_record.get("license_limits") or {}
    limits = [item["name"] for item in lookup.get("limits", [])]
    if limits or notice_record.get("indstrytyLmtYn") == "Y":
        checks, unresolved = [], []
        for name in limits:
            core = squash(name.split("/")[0])
            hits = [field for field, text in texts.items() if core and core in squash(text)]
            checks.append(VerificationCheck(
                check_id=f"license_{len(checks) + 1}", label=f"업종 제한 {name}",
                status=VerificationStatus.CONFIRMED if hits else VerificationStatus.HUMAN_REVIEW,
                detail=(f"API 업종 제한이 {', '.join(hits)}에도 있습니다." if hits
                        else "RFP 참가자격 인용과 색인된 공고문에서 이 업종을 찾지 못했습니다. 원문 전체를 확인해 주세요."),
            ))
            if not hits:
                unresolved.append(f"나라장터 업종 제한 '{name}'이 RFP 참가자격에 반영됐는지 확인해 주세요.")
        if not limits:
            checks.append(VerificationCheck(
                check_id="license_lookup", label="면허제한정보 조회", status=VerificationStatus.HUMAN_REVIEW,
                detail=f"공고는 업종 제한(indstrytyLmtYn=Y)이지만 세부 면허를 받지 못했습니다: {lookup.get('error') or lookup.get('status', '미조회')}.",
            ))
            unresolved.append("나라장터 공고 상세에서 업종 제한 내용을 확인해 주세요.")
        claims.append(VerifiedClaim(
            category="eligibility", field="g2b_license_limit", claim=limits or None,
            status=_state_for_checks(checks), checks=checks, unresolved_questions=unresolved,
            citations=[_api_citation(notice_record, "lcnsLmtNm", "업종(면허) 제한", "업종 제한: " + (", ".join(limits) or "세부 미조회"))],
        ))

    method = str(notice_record.get("cmmnSpldmdMethdNm") or "").strip()
    if method:
        joint = {field: text for field, text in texts.items() if re.search(r"공동\s*(?:도급|수급|계약)", text)}
        forbid = re.compile(r"허용하지\s*않|불허|불가|금지|할\s*수\s*없")
        rfp_forbids = [field for field, text in joint.items() if forbid.search(text)]
        rfp_allows = [field for field, text in joint.items() if not forbid.search(text) and re.search(r"허용|가능", text)]
        api_forbids = "불허" in method
        if not joint:
            status, detail = VerificationStatus.HUMAN_REVIEW, f"API 공동수급 방식은 '{method}'이나 RFP 참가자격·공고문에서 공동수급 조항을 찾지 못했습니다."
        elif api_forbids and rfp_forbids and not rfp_allows:
            status, detail = VerificationStatus.CONFIRMED, f"API '{method}'와 {', '.join(rfp_forbids)}의 공동수급 불허가 일치합니다."
        elif api_forbids != bool(rfp_forbids) or (rfp_forbids and rfp_allows):
            status, detail = VerificationStatus.CONFLICT, (f"API 공동수급 방식 '{method}'와 RFP 조항"
                                                           f"({', '.join(sorted(joint))})의 허용 여부가 다릅니다.")
        else:
            status, detail = VerificationStatus.HUMAN_REVIEW, f"API '{method}'와 RFP 모두 공동수급을 허용합니다. 이행방식·구성원 조건을 확인해 주세요."
        claims.append(VerifiedClaim(
            category="eligibility", field="g2b_joint_contract", claim=method, status=status,
            checks=[VerificationCheck(check_id="joint_contract_crosscheck", label="공동수급 허용 여부 교차 확인",
                                      status=status, detail=detail)],
            unresolved_questions=[] if status == VerificationStatus.CONFIRMED else ["공고와 RFP의 공동수급 허용 여부·방식을 확인해 주세요."],
            citations=[_api_citation(notice_record, "cmmnSpldmdMethdNm", "공동수급 방식", f"공동수급 방식: {method}")],
        ))
    return claims


def change_history_reason(history: dict[str, Any] | None) -> str | None:
    """Routing reason when G2B 변경이력 has rows; None when there is nothing to reconcile."""
    changes = (history or {}).get("changes") or []
    if not changes:
        return None
    items = sorted({str(row.get("chgItemNm") or "항목 미상") for row in changes})
    return (f"g2b_change_history: human_review — 나라장터 변경이력 {len(changes)}건({', '.join(items)}). "
            "정정 후 값과 인용한 공고문·RFP 판본을 담당자가 대조해야 합니다.")


DATE_RE = re.compile(
    r"(?P<year>20\d{2})\s*(?:년|[./-])\s*"
    r"(?P<month>\d{1,2})\s*(?:월|[./-])\s*"
    r"(?P<day>\d{1,2})\s*일?"
)
TIME_RE = re.compile(r"(?:(오전|오후)\s*)?(\d{1,2}):(\d{2})")
AMOUNT_KEYWORDS = (
    "사업예산", "사업금액", "사업비", "총사업비", "추정가격", "기초금액", "부가가치세", "부가세", "VAT",
)
ELIGIBILITY_KEYWORDS = (
    "입찰참가자격", "참가자격", "업종", "면허", "등록", "공동수급", "공동이행", "지역제한",
    "중소기업", "대기업", "참여제한", "하도급", "실적", "필수인력", "인증",
)
SCHEDULE_KEYWORDS = (
    "계약기간", "수행기간", "착수일", "준공일", "완료일", "제안서 제출", "입찰서 제출",
    "제안서 마감", "입찰 마감", "공동수급 협정서",
)
AMOUNT_FIELD_QUERIES = {
    "budget_krw": ("사업예산", "사업금액", "사업비", "총사업비", "배정예산"),
    "estimated_price_krw": ("추정가격", "기초금액", "예정가격", "추정금액"),
}
SCHEDULE_FIELD_QUERIES = {
    "contract_period": ("계약기간", "수행기간", "착수일", "완료일"),
    "proposal_submission_start": ("제안서 제출 시작", "제안서 제출기간", "제안서 접수 시작", "제안서 제출일시"),
    "proposal_deadline": ("제안서 제출 마감", "제안서 마감", "제안서 제출기한", "제안서 제출일시"),
    "bid_deadline": ("입찰서 제출 마감", "입찰서 마감", "입찰서 제출기한", "입찰 마감"),
    "joint_venture_deadline": ("공동수급협정서 제출 마감", "공동수급협정서 제출기한", "공동수급 협정서 마감"),
}


def _norm(value: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", value).casefold() if ch.isalnum())


def _document_id(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest[:16]


def _citation(item: RetrievedEvidence) -> EvidenceCitation:
    meta = item.metadata
    return EvidenceCitation(
        chunk_id=item.chunk_id,
        document_title=meta.get("document_title", ""),
        document_type=meta.get("document_type", "unknown"),
        page=meta.get("page"),
        section=meta.get("section", ""),
        quote=item.text,
        source_path=meta.get("source_path"),
        # Link reviewers to the specific notice record first. Keep the API
        # catalogue as separate provenance instead of presenting it as the
        # source record for this particular field value.
        source_url=(meta.get("notice_url") or
                    (meta.get("source_url") if meta.get("document_type") != "g2b_notice_metadata" else None)),
        api_source_url=(meta.get("source_url")
                        if meta.get("document_type") == "g2b_notice_metadata" else None),
        notice_no=meta.get("related_notice_no"),
        notice_order=meta.get("related_notice_order"),
        field_name=meta.get("field_name"),
    )


def _notice_api_citation(record: dict[str, Any], field_name: str, label: str, value: Any) -> EvidenceCitation:
    notice_no = str(record.get("bidNtceNo") or "")
    notice_order = str(record.get("bidNtceOrd") or "000").zfill(3)
    return EvidenceCitation(
        chunk_id=f"g2b:{notice_no}:{notice_order}:{field_name}",
        document_title=str(record.get("bidNtceNm") or f"나라장터 공고 {notice_no}-{notice_order}"),
        document_type="g2b_notice_metadata",
        section=label,
        quote=f"{label}: {value}",
        source_url=record.get("bidNtceDtlUrl"),
        api_source_url="https://www.data.go.kr/data/15129394/openapi.do",
        notice_no=notice_no,
        notice_order=notice_order,
        field_name=field_name,
    )


def _same_source_quote(expected: str, actual: str) -> bool:
    expected_norm, actual_norm = _norm(expected), _norm(actual)
    if len(expected_norm) >= 8 and expected_norm in actual_norm:
        return True
    expected_terms = set(re.findall(r"[가-힣A-Za-z0-9]+", unicodedata.normalize("NFKC", expected).casefold()))
    actual_terms = set(re.findall(r"[가-힣A-Za-z0-9]+", unicodedata.normalize("NFKC", actual).casefold()))
    if len(expected_terms) < 3:
        return False
    return len(expected_terms & actual_terms) / len(expected_terms) >= 0.9


def _find_quote(
    excerpt: str,
    *,
    document_id: str,
    page: int | None,
    notice_no: str | None,
) -> tuple[EvidenceCitation | None, list[str]]:
    query = excerpt.strip()
    if not query:
        return None, []
    queries = [query]
    # If a long source excerpt has layout breaks, search with its first sentence too.
    first_line = next((line.strip() for line in query.splitlines() if len(line.strip()) >= 8), "")
    if first_line and first_line != query:
        queries.append(first_line)
    for candidate_query in queries:
        found = search_keyword(candidate_query, limit=50, document_id=document_id, match_mode="all")
        found += search_keyword(candidate_query, limit=50, document_id=document_id, match_mode="any")
        for result in found:
            if page is not None and result.metadata.get("page") != page:
                continue
            if _same_source_quote(excerpt, result.text):
                return _citation(result), queries
    return None, queries


def _state_for_checks(checks: list[VerificationCheck]) -> VerificationStatus:
    statuses = {check.status for check in checks}
    if VerificationStatus.CONFLICT in statuses:
        return VerificationStatus.CONFLICT
    if VerificationStatus.HUMAN_REVIEW in statuses:
        return VerificationStatus.HUMAN_REVIEW
    if VerificationStatus.RETRIEVE_MORE in statuses:
        return VerificationStatus.RETRIEVE_MORE
    return VerificationStatus.CONFIRMED


def _category_state(claims: list[VerifiedClaim], *, coverage_complete: bool = True) -> VerificationStatus:
    if any(claim.status == VerificationStatus.CONFLICT for claim in claims):
        return VerificationStatus.CONFLICT
    if not coverage_complete or any(claim.status == VerificationStatus.HUMAN_REVIEW for claim in claims):
        return VerificationStatus.HUMAN_REVIEW
    if any(claim.status == VerificationStatus.RETRIEVE_MORE for claim in claims):
        return VerificationStatus.RETRIEVE_MORE
    return VerificationStatus.CONFIRMED


def _candidate_claim(
    category: str,
    field: str,
    candidate: RFPFieldCandidate,
    document_id: str,
    notice_no: str | None,
) -> VerifiedClaim:
    if candidate.source_document == "notice_pdf":
        citation, queries = None, []
    else:
        citation, queries = _find_quote(
            candidate.source_excerpt,
            document_id=document_id,
            page=candidate.source_page,
            notice_no=notice_no,
        )
    source_excerpt = candidate.source_excerpt.strip()
    source_citation = citation or (
        EvidenceCitation(
            chunk_id=(
                f"rfp:{document_id}:{candidate.source_page}:"
                f"{hashlib.sha256(source_excerpt.encode('utf-8')).hexdigest()[:10]}"
            ),
            document_title="나라장터 공고문 PDF (T1 추출 근거)" if candidate.source_document == "notice_pdf" else "RFP 원본 (T1 추출 근거)",
            document_type=candidate.source_document or "rfp_pdf",
            page=candidate.source_page,
            section="T1 추출 인용 — 공고문 PDF에서 담당자 확인 필요" if candidate.source_document == "notice_pdf" else "T1 추출 인용 — 원본 PDF에서 담당자 확인 필요",
            quote=source_excerpt,
            source_path="notice.pdf" if candidate.source_document == "notice_pdf" else None,
        )
        if source_excerpt and candidate.source_page is not None else None
    )
    checks: list[VerificationCheck] = []
    if candidate.source_document == "notice_pdf" and source_citation:
        checks.append(VerificationCheck(
            check_id="notice_pdf_extraction",
            label="공고문 PDF 일정 추출",
            status=VerificationStatus.HUMAN_REVIEW,
            detail="공고문 PDF에서 T1이 추출한 페이지 인용입니다. 원문에서 일정 종류·날짜·시각을 담당자가 확인해야 합니다.",
        ))
    elif citation:
        checks.append(VerificationCheck(
            check_id="primary_source_quote",
            label="원문 페이지·인용 일치",
            status=VerificationStatus.CONFIRMED,
            detail=f"{citation.document_title} {citation.page}쪽의 검색 chunk에서 T1 인용을 확인했습니다.",
        ))
    else:
        checks.append(VerificationCheck(
            check_id="primary_source_quote",
            label="원문 페이지·인용 일치",
            status=VerificationStatus.RETRIEVE_MORE,
            detail="현재 검색으로 T1 인용과 일치하는 원문 chunk를 찾지 못했습니다.",
        ))
    return VerifiedClaim(
        category=category,
        field=field,
        claim=candidate.value,
        normalized_value=candidate.value,
        status=_state_for_checks(checks),
        checks=checks,
        citations=[source_citation] if source_citation else [],
        unresolved_questions=[] if citation else ["원문 페이지에서 추출 인용을 다시 확인해 주세요."],
        retrieval_queries=queries,
    )


class T15Verifier:
    """Run field-specific deterministic checks before any company matching."""

    def __init__(self, project_root: Path = PROJECT_ROOT):
        self.project_root = project_root

    def verify(
        self,
        rfp: RFPAnalysisResult,
        pdf_path: Path,
        notice_no: str | None = None,
        notice_record: dict[str, Any] | None = None,
        g2b_lookup_status: str = "not_attempted",
        g2b_lookup_error: str | None = None,
        g2b_attachment_lookup_status: str = "not_attempted",
        g2b_attachment_lookup_error: str | None = None,
        g2b_attachment_comparison: dict[str, Any] | None = None,
        g2b_change_history: dict[str, Any] | None = None,
        notice_pdf: RFPAnalysisResult | None = None,
        notice_pdf_status: str = "not_provided",
    ) -> CoreInfoVerification:
        pdf_path = Path(pdf_path)
        document_id = _document_id(pdf_path)
        notice_no = notice_no or (str(rfp.basic_info.notice_number.value) if rfp.basic_info.notice_number.value else None)
        notice_no = notice_no or related_notice_number(document_id)
        amount = self._verify_amounts(rfp, document_id, notice_no, notice_record, g2b_lookup_status, g2b_lookup_error)
        eligibility = self._verify_eligibility(rfp, document_id, notice_no, notice_record)
        schedule = self._verify_schedule(rfp, document_id, notice_no, notice_record, g2b_lookup_status, g2b_lookup_error, notice_pdf)
        categories = {
            "amount": amount,
            "eligibility": eligibility,
            "schedule": schedule,
        }
        status = _category_state([
            claim for category in categories.values() for claim in category.claims
        ], coverage_complete=all(category.coverage_complete for category in categories.values()))
        reasons = [
            f"{key}: {category.status.value} — {category.coverage_note}"
            for key, category in categories.items()
            if category.status != VerificationStatus.CONFIRMED
        ]
        change_reason = change_history_reason(g2b_change_history)
        if change_reason:  # 정정이 있으면 인용 판본이 최신인지 사람이 대조하기 전까지 T2로 자동 진행하지 않음
            reasons.append(change_reason)
            if status == VerificationStatus.CONFIRMED:
                status = VerificationStatus.HUMAN_REVIEW
        return CoreInfoVerification(
            document_id=document_id,
            notice_no=notice_no,
            notice_pdf_status=notice_pdf_status,
            g2b_lookup_status=g2b_lookup_status,
            g2b_lookup_error=g2b_lookup_error,
            g2b_attachment_lookup_status=g2b_attachment_lookup_status,
            g2b_attachment_lookup_error=g2b_attachment_lookup_error,
            g2b_attachment_comparison=g2b_attachment_comparison or {},
            g2b_change_history=g2b_change_history or {},
            linked_attachments=(notice_record.get("attachments", []) if notice_record else []),
            status=status,
            categories=categories,
            routing_reasons=reasons,
        )

    def _verify_amounts(
        self, rfp: RFPAnalysisResult, doc_id: str, notice_no: str | None,
        notice_record: dict[str, Any] | None, lookup_status: str, lookup_error: str | None,
    ) -> CategoryVerification:
        claims: list[VerifiedClaim] = []
        for field in ("budget_krw", "estimated_price_krw"):
            candidates = [
                item for item in getattr(rfp.basic_info_candidates, field)
                if item.value is not None and item.invalid_reason is None
            ]
            distinct_values = {item.value for item in candidates}
            if not candidates:
                claim = VerifiedClaim(
                    category="amount", field=field, status=VerificationStatus.RETRIEVE_MORE,
                    checks=[VerificationCheck(
                        check_id="candidate_present", label="금액 후보 존재", status=VerificationStatus.RETRIEVE_MORE,
                        detail="T1에서 확정된 금액 후보가 없어 해당 금액 레이블을 원문에서 다시 검색해야 합니다.",
                    )],
                    unresolved_questions=["이 금액 유형이 원문에 있는지 확인해 주세요."],
                    retrieval_queries=list(AMOUNT_FIELD_QUERIES[field]),
                )
                claims.append(claim)
                continue
            for candidate in candidates:
                claim = _candidate_claim("amount", field, candidate, doc_id, notice_no)
                parsed = parse_krw(candidate.source_excerpt)
                if isinstance(candidate.value, int) and parsed == candidate.value:
                    claim.checks.append(VerificationCheck(
                        check_id="amount_normalization", label="원 단위 변환", status=VerificationStatus.CONFIRMED,
                        detail=f"원문 표기에서 {parsed:,}원을 다시 계산했습니다.",
                    ))
                else:
                    claim.checks.append(VerificationCheck(
                        check_id="amount_normalization", label="원 단위 변환", status=VerificationStatus.CONFLICT,
                        detail=f"원문에서 다시 읽은 값 {parsed!r}과 T1 값 {candidate.value!r}이 일치하지 않습니다.",
                    ))
                    claim.unresolved_questions.append("원문 금액·단위와 추출 숫자를 담당자가 대조해 주세요.")
                raw = candidate.source_excerpt
                if re.search(r"부가가치세\s*(?:포함|포함액)|부가세\s*포함|VAT\s*포함", raw, re.I):
                    vat_state, vat_note = VerificationStatus.CONFIRMED, "원문 발췌에 부가세 포함이 명시되어 있습니다."
                elif re.search(r"부가가치세\s*(?:별도|제외)|부가세\s*별도|VAT\s*별도", raw, re.I):
                    vat_state, vat_note = VerificationStatus.CONFIRMED, "원문 발췌에 부가세 별도가 명시되어 있습니다."
                elif re.search(r"부가가치세|부가세|VAT", raw, re.I):
                    vat_state, vat_note = VerificationStatus.HUMAN_REVIEW, "세금 언급은 있지만 포함/별도 관계가 명확하지 않습니다."
                else:
                    vat_state, vat_note = VerificationStatus.RETRIEVE_MORE, "현재 금액 발췌에서 VAT 조건을 확인하지 못했습니다."
                claim.checks.append(VerificationCheck(
                    check_id="vat_condition", label="부가세 조건", status=vat_state, detail=vat_note,
                ))
                if vat_state != VerificationStatus.CONFIRMED:
                    claim.unresolved_questions.append("부가세 포함/별도 조건을 원문에서 확인해 주세요.")

                if re.search(r"연차별|연도별|매년|년간", raw):
                    scope_state, scope = VerificationStatus.CONFIRMED, "annual_or_periodic"
                elif re.search(r"총액|총사업비|전체\s*사업금액|5년간|\d+\s*년간", raw):
                    scope_state, scope = VerificationStatus.CONFIRMED, "multi_year_or_total"
                else:
                    scope_state, scope = VerificationStatus.RETRIEVE_MORE, "scope_unspecified"
                claim.checks.append(VerificationCheck(
                    check_id="amount_scope", label="총액/연차 범위", status=scope_state,
                    detail=f"금액 범위 판정: {scope}.",
                ))
                if scope_state != VerificationStatus.CONFIRMED:
                    claim.unresolved_questions.append("연차별 금액인지 총사업 금액인지 주변 문맥에서 확인해 주세요.")
                claim.normalized_value = {
                    "amount_krw": candidate.value,
                    "kind": field,
                    "vat_status": "included" if "포함" in vat_note else "excluded" if "별도" in vat_note else "unknown",
                    "scope": scope,
                }
                claim.status = _state_for_checks(claim.checks)
                claims.append(claim)

        if notice_no:
            for field, api_field, label in (
                ("budget_krw", "asignBdgtAmt", "배정예산"),
                ("estimated_price_krw", "presmptPrce", "추정가격"),
            ):
                rfp_values = {
                    item.value for item in getattr(rfp.basic_info_candidates, field)
                    if isinstance(item.value, int) and item.invalid_reason is None
                }
                api = None
                if notice_record is not None:
                    api_value = notice_record.get(api_field)
                    if api_value not in (None, ""):
                        meta = {
                            "document_title": notice_record.get("bidNtceNm", ""),
                            "document_type": "g2b_notice_metadata", "field_name": api_field,
                            "field_value": api_value, "section": label,
                            "related_notice_no": notice_record.get("bidNtceNo"),
                            "related_notice_order": notice_record.get("bidNtceOrd"),
                            "notice_url": notice_record.get("bidNtceDtlUrl"),
                            "source_url": "https://www.data.go.kr/data/15129394/openapi.do",
                        }
                        api = RetrievedEvidence(
                            chunk_id=f"g2b:{notice_no}:{api_field}", text=f"{label}: {api_value}",
                            metadata=meta, rank=0.0,
                        )
                elif lookup_status == "not_attempted":
                    api_chunks = search_keyword(label, limit=50, notice_no=notice_no, match_mode="all")  # 공고문 chunk도 같은 notice_no
                    api = next((item for item in api_chunks if item.metadata.get("field_name") == api_field), None)
                    api_value = api.metadata.get("field_value") if api else None
                else:
                    api_value = None
                if not api or not rfp_values:
                    status = VerificationStatus.HUMAN_REVIEW if lookup_status in {"unavailable", "not_found"} else VerificationStatus.RETRIEVE_MORE
                    reason = lookup_error or ("공고번호로 조회한 나라장터 레코드를 찾지 못했습니다." if lookup_status == "not_found"
                                              else "나라장터 API 조회 결과에 해당 금액 필드가 없습니다.")
                    if lookup_status == "not_attempted":
                        reason = "현재 실행에서 실시간 G2B 단건 조회를 하지 않아 기존 색인으로 확인 가능한 API 근거가 없습니다."
                    if not rfp_values:
                        reason = "RFP에서 비교할 금액 후보를 확인하지 못했습니다. " + reason
                    claims.append(VerifiedClaim(
                        category="amount", field=f"g2b_crosscheck_{field}", status=status,
                        checks=[VerificationCheck(
                            check_id="notice_source_availability", label=f"나라장터 {label} 출처 조회", status=status,
                            detail=reason,
                        )],
                        unresolved_questions=[f"공고별 API에서 {label} 필드와 RFP 금액 종류/VAT 기준을 확인해 주세요."],
                        retrieval_queries=[label],
                    ))
                    continue
                try:
                    api_amount = int(str(api_value).replace(",", ""))
                except (TypeError, ValueError):
                    continue
                citations = [_citation(api)]
                if api_amount in rfp_values:
                    claims.append(VerifiedClaim(
                        category="amount", field=f"g2b_crosscheck_{field}", claim=api_amount,
                        normalized_value={"amount_krw": api_amount, "field": api_field},
                        status=VerificationStatus.CONFIRMED,
                        checks=[VerificationCheck(
                            check_id="notice_crosscheck", label="공고 API 금액 교차 확인",
                            status=VerificationStatus.CONFIRMED,
                            detail=f"RFP 후보와 나라장터 {label} 필드가 일치합니다.",
                        )], citations=citations,
                        retrieval_queries=[label],
                    ))
                elif any(
                    re.search(r"부가가치세\s*(?:별도|제외)|부가세\s*별도|VAT\s*별도", item.source_excerpt, re.I)
                    and api_amount > item.value
                    and abs((api_amount - item.value) / item.value - 0.10) < 0.001
                    for item in getattr(rfp.basic_info_candidates, field)
                    if isinstance(item.value, int) and item.value > 0 and item.invalid_reason is None
                ):
                    base_amount = next(
                        item.value for item in getattr(rfp.basic_info_candidates, field)
                        if isinstance(item.value, int) and item.value > 0 and item.invalid_reason is None
                        and re.search(r"부가가치세\s*(?:별도|제외)|부가세\s*별도|VAT\s*별도", item.source_excerpt, re.I)
                        and api_amount > item.value
                        and abs((api_amount - item.value) / item.value - 0.10) < 0.001
                    )
                    claims.append(VerifiedClaim(
                        category="amount", field=f"g2b_crosscheck_{field}", claim=api_amount,
                        normalized_value={"amount_krw": api_amount, "field": api_field,
                                          "possible_vat_exclusive_base_krw": base_amount},
                        status=VerificationStatus.HUMAN_REVIEW,
                        checks=[VerificationCheck(
                            check_id="notice_crosscheck_vat_basis", label="공고/RFP VAT 기준 교차 확인",
                            status=VerificationStatus.HUMAN_REVIEW,
                            detail=(f"RFP의 VAT 별도 금액 {base_amount:,}원과 나라장터 {label} "
                                    f"{api_amount:,}원의 차이는 약 10%입니다. 두 필드의 VAT 산정 기준과 적용 세율을 원문으로 확인해야 합니다."),
                        )], citations=citations,
                        unresolved_questions=["공고 배정예산이 VAT 포함 기준인지, RFP 금액에 어떤 세율이 적용되는지 확인해 주세요."],
                        retrieval_queries=[label, "부가가치세 별도"],
                    ))
                else:
                    claims.append(VerifiedClaim(
                        category="amount", field=f"g2b_crosscheck_{field}", claim=api_amount,
                        normalized_value={"amount_krw": api_amount, "field": api_field},
                        status=VerificationStatus.CONFLICT,
                        checks=[VerificationCheck(
                            check_id="notice_crosscheck", label="공고 API 금액 교차 확인",
                            status=VerificationStatus.CONFLICT,
                            detail=f"RFP 후보 {sorted(rfp_values)}와 나라장터 {label} {api_amount:,}원이 다릅니다.",
                        )], citations=citations,
                        unresolved_questions=["공고와 RFP의 금액 종류, 정정 여부, VAT 조건을 담당자가 비교해 주세요."],
                        retrieval_queries=[label],
                    ))
        coverage_complete = bool(claims) and all(
            claim.status == VerificationStatus.CONFIRMED for claim in claims
        )
        note = "금액 후보·단위·세금·범위·공고 레코드 대조를 확인했습니다." if coverage_complete else "후보 누락, VAT/범위 모호성 또는 문서 간 차이가 남았습니다."
        return CategoryVerification(
            category="amount", status=_category_state(claims), claims=claims,
            coverage_complete=coverage_complete, coverage_note=note,
        )

    @staticmethod
    def _eligibility_scope_exclusion(requirement) -> str | None:
        """Keep bid-entry qualification separate from forms, scoring, and post-award duties."""
        name = requirement.name or ""
        text = " ".join((name, requirement.details or "", requirement.evidence.excerpt or ""))
        compact = re.sub(r"\s+", "", text)
        is_section_clause = requirement.evidence.section in {"입찰참가자격 절", "공동도급 참가 제한"}
        explicit_qualification = any(term.replace(" ", "") in compact for term in (
            "입찰참가자격", "경쟁입찰 참가자격", "참가자격을 갖춘", "자격을 갖춘 자",
            "참가자격 등록", "입찰참가 등록증", "부정당업자에 해당하지", "입찰에 참여할 수",
            "중소기업자 또는", "소상공인으로서", "입찰 참여를 제한", "입찰 참가 자격 제한",
            "부도", "파산", "해산", "영업정지",
        ))
        # Contract performance/security sanctions are not entry qualifications.
        if "계약업체" in compact and any(term in compact for term in ("정보누출", "정보시스템", "보안서약", "취약점")):
            return "계약 이후 보안/정보보호 의무와 위반 제재로, 입찰 전 참가자격 조건이 아닙니다."
        if "계약금액" in compact and any(term in compact for term in ("%", "배상", "감액", "지체상금")):
            return "계약금액 기준의 사후 배상/제재 조항으로 분류합니다."
        scoring_terms = ("배점", "점수", "평가 결과", "정량평가", "차등점수", "가점", "감점", "평가점수")
        if any(term.replace(" ", "") in compact for term in scoring_terms):
            return "평가·배점 기준이며 입찰 참여 가능 여부를 정하는 자격 조항이 아닙니다."
        form_terms = ("서식", "별지", "작성하여 제출", "기재하여 제출", "해당 없음", "증명서 양식")
        if (not explicit_qualification or not is_section_clause) and (
            any(term.replace(" ", "") in compact for term in form_terms)
            or ("기재" in compact and any(term.replace(" ", "") in compact for term in ("대표자 직인", "작성", "제출용")))
        ):
            return "제출 서식/증빙 양식 자체이며 참가자격 조건 문장이 아닙니다."
        if (not explicit_qualification or not is_section_clause) and any(term.replace(" ", "") in compact for term in (
            "입찰서류 및 제안서 제출", "제안서의 효력", "제출서류", "제출 서류", "협상순서",
        )):
            return "제출 절차/제안서 효력/협상 운영 내용이며 참가자격 조건이 아닙니다."
        return None

    def _verify_eligibility(self, rfp: RFPAnalysisResult, doc_id: str, notice_no: str | None,
                            notice_record: dict[str, Any] | None = None) -> CategoryVerification:
        claims: list[VerifiedClaim] = []
        exclusions: list[ScopeExcludedItem] = []
        for index, requirement in enumerate(rfp.eligibility_requirements, start=1):
            exclusion_reason = self._eligibility_scope_exclusion(requirement)
            if exclusion_reason:
                exclusions.append(ScopeExcludedItem(
                    field=f"condition_{index}",
                    source_page=requirement.evidence.pdf_page,
                    item=(requirement.name or requirement.details)[:400],
                    reason=exclusion_reason,
                ))
                continue
            excerpt = requirement.evidence.excerpt or requirement.details
            claim = _candidate_claim(
                "eligibility", f"condition_{index}",
                RFPFieldCandidate(
                    value=requirement.details or requirement.name,
                    source_page=requirement.evidence.pdf_page,
                    source_excerpt=excerpt,
                    source_document=requirement.evidence.source_document or "rfp_pdf",
                ),
                doc_id,
                notice_no,
            )
            text = " ".join((requirement.name, requirement.details, excerpt))
            dimensions = (
                ("subject", "적용 주체 표기", ("입찰자", "입찰참가자", "업체", "제안사", "사업자", "공동수급", "구성원", "참여인력", "계약업체", "사업수행자")),
                ("condition", "자격/제한 조건 표기", ("자격", "등록", "면허", "업종", "실적", "중소기업", "소기업", "제한", "인증", "제재", "제제", "부도", "파산", "영업정지")),
                ("effect", "의무·허용·배제 효과 표기", ("하여야", "해야", "제출", "보유", "등록된다", "제한", "제외", "금지", "참가할 수", "참가자격")),
            )
            for check_id, label, terms in dimensions:
                matched = [term for term in terms if term in text]
                state = VerificationStatus.CONFIRMED if matched else VerificationStatus.HUMAN_REVIEW
                claim.checks.append(VerificationCheck(
                    check_id=f"eligibility_{check_id}", label=label, status=state,
                    detail=(f"확인된 원문 표지: {', '.join(matched[:8])}." if matched
                            else "인용된 문구에서 이 항목을 분명히 확인하지 못했습니다."),
                ))
                if not matched:
                    claim.unresolved_questions.append(f"{label}를 원문 전체 문맥에서 확인해 주세요.")
            exception_terms = ("다만", "단,", "단 ", "예외", "제외하고", "이외", "그 밖에", "협의에 따라")
            exceptions = [term for term in exception_terms if term in text]
            exception_status = VerificationStatus.HUMAN_REVIEW if exceptions else VerificationStatus.CONFIRMED
            claim.checks.append(VerificationCheck(
                check_id="eligibility_exceptions", label="예외·단서 표기 확인",
                status=exception_status,
                detail=(f"예외/단서 표지가 있어 적용 범위를 사람 확인 대상으로 남깁니다: {', '.join(exceptions[:8])}."
                        if exceptions else "인용 범위에서 명시적 예외·단서 표지를 찾지 못했습니다."),
            ))
            if exceptions:
                claim.unresolved_questions.append("단서 문구의 적용 주체와 예외 범위를 원문 문맥에서 확인해 주세요.")
            negations = [term for term in ("해당되지 아니", "해당하지 아니", "해당되지 않는", "없는 자", "할 수 없다") if term in text]
            claim.checks.append(VerificationCheck(
                check_id="eligibility_negative_condition", label="자격 배제/부정 조건 표기",
                status=VerificationStatus.CONFIRMED,
                detail=(f"원문에서 부정/배제 조건을 확인했습니다: {', '.join(negations)}. 담당자가 실제 적용 범위를 확인해야 합니다."
                        if negations else "인용 범위에서 명시적인 부정/배제 표현을 찾지 못했습니다."),
            ))
            evidence_terms = ("실적증명", "증빙", "증명서", "인증서", "제출서류", "제출 서류", "확인서")
            evidence_hits = [term for term in evidence_terms if term in text]
            claim.checks.append(VerificationCheck(
                check_id="eligibility_proof", label="요구 증빙 표기 확인",
                status=VerificationStatus.CONFIRMED,
                detail=(f"원문에 언급된 증빙 표지: {', '.join(evidence_hits[:8])}." if evidence_hits
                        else "이 인용 범위에는 별도 증빙 서류 표지가 없습니다. 증빙 요구가 다른 조항에 있는지는 전체 범위 검사에 맡깁니다."),
            ))
            claim.status = _state_for_checks(claim.checks)
            claims.append(claim)
        notice_texts: dict[str, str] = {}  # 같은 쪽의 여러 chunk는 이어 붙임 (키 충돌로 덮어쓰지 않게)
        for item in (notice_pdf_chunks(notice_no) if notice_no and notice_record else []):
            key = f"공고문 p.{item.metadata.get('page')}"
            notice_texts[key] = f"{notice_texts.get(key, '')}\n{item.text}".strip()
        claims += api_eligibility_claims(notice_record, claims, notice_texts)

        explicit_section_pages = {
            item.evidence.pdf_page for item in rfp.eligibility_requirements
            if item.evidence.pdf_page is not None
            and item.evidence.section == "입찰참가자격 절"
        }
        unclassified_pages: set[int] = set()
        for item in rfp.unclassified_requirements:
            page = item.source_page
            text = item.requirement_text or ""
            if not page or page in explicit_section_pages:
                continue
            if any(term in text for term in ("배점", "평가 결과", "정량평가", "차등점수", "서식", "별지", "제출서류", "제출 서류")):
                continue
            if any(term in text for term in ("허위", "서약", "이의를 제기", "참가자격 박탈")):
                continue
            relevant_terms = (
                "참가자격을 갖춘", "입찰 참가 자격", "입찰참가자격 등록", "입찰에 참가할 수",
                "입찰참가를 제한", "업종코드", "직접생산", "중소기업자", "소상공인",
                "공동도급은 허용하지 않음", "공동수급은 허용하지 않음",
            )
            if len("".join(text.split())) < 35:
                continue
            if any(term in text for term in relevant_terms):
                unclassified_pages.add(page)
        uncovered_pages = sorted(unclassified_pages)
        complete = bool(explicit_section_pages) and bool(claims) and not uncovered_pages
        if not explicit_section_pages:
            note = "명시적인 입찰참가자격 절을 찾지 못했습니다. 문서 전체에서 해당 절과 요건을 확인해야 합니다."
        elif uncovered_pages:
            note = f"자격 조건으로 보이는 미분류 문구가 남은 페이지: {uncovered_pages[:20]}"
        else:
            note = (f"구조 추출 범위: 명시적 자격 절을 탐지해 조건별 후보를 구조화했습니다. "
                    f"자격 후보 {len(claims)}건을 검증하고 범위 밖 후보 {len(exclusions)}건을 분리했습니다. "
                    "이 표시는 문서 구간 추출 상태이며 자격 충족이나 법적 적합 판정이 아닙니다. "
                    "공동수급·예외의 적용 및 제출 증빙은 담당자 확인 대상으로 남깁니다.")
        if not complete and not claims:
            claims.append(VerifiedClaim(
                category="eligibility", field="eligibility_section_coverage",
                status=VerificationStatus.RETRIEVE_MORE,
                checks=[VerificationCheck(
                    check_id="eligibility_extraction", label="참가자격 조건 발견",
                    status=VerificationStatus.RETRIEVE_MORE, detail=note,
                )], unresolved_questions=["공고문/RFP의 참가자격·제한·예외 조항을 전체 확인해 주세요."],
                retrieval_queries=list(ELIGIBILITY_KEYWORDS),
            ))
        return CategoryVerification(
            category="eligibility", status=_category_state(claims, coverage_complete=complete),
            claims=claims, scope_exclusions=exclusions,
            coverage_complete=complete, coverage_note=note,
        )

    def _verify_schedule(
        self, rfp: RFPAnalysisResult, doc_id: str, notice_no: str | None,
        notice_record: dict[str, Any] | None, lookup_status: str, lookup_error: str | None,
        notice_pdf: RFPAnalysisResult | None = None,
    ) -> CategoryVerification:
        claims: list[VerifiedClaim] = []
        fields = (
            "contract_period", "proposal_submission_start", "proposal_deadline",
            "bid_deadline", "joint_venture_deadline",
        )
        for field in fields:
            candidates = [
                item for item in getattr(rfp.basic_info_candidates, field)
                if item.value is not None and item.invalid_reason is None
                and not self._is_schedule_template(str(item.value))
            ]
            if field == "proposal_deadline" and notice_pdf is not None:
                candidates.extend(
                    item for item in notice_pdf.basic_info_candidates.proposal_deadline
                    if item.value is not None and item.invalid_reason is None
                    and not self._is_schedule_template(str(item.value))
                )
            signatures = {self._schedule_signature(str(item.value), field) for item in candidates}
            if not candidates:
                claims.append(VerifiedClaim(
                    category="schedule", field=field, status=VerificationStatus.RETRIEVE_MORE,
                    checks=[VerificationCheck(
                        check_id="schedule_candidate", label="일정 후보 존재",
                        status=VerificationStatus.RETRIEVE_MORE,
                        detail="T1 확정 후보가 없어 해당 일정 유형을 원문에서 검색해야 합니다.",
                    )], unresolved_questions=["이 일정이 원문에 있는지 확인해 주세요."],
                    retrieval_queries=list(SCHEDULE_FIELD_QUERIES.get(field, ())),
                ))
                continue
            for candidate in candidates:
                claim = _candidate_claim("schedule", field, candidate, doc_id, notice_no)
                if len(signatures) > 1:
                    claim.checks.append(VerificationCheck(
                        check_id="candidate_conflict", label="일정 후보 충돌",
                        status=VerificationStatus.CONFLICT,
                        detail=f"같은 일정 유형에 의미상 다른 날짜/기간 후보가 있습니다: {sorted(map(str, signatures))}",
                    ))
                    claim.unresolved_questions.append("정정 공고 및 날짜 종류를 담당자가 확인해 주세요.")
                raw = str(candidate.value)
                absolute_dates = self._normalize_dates(raw)
                if absolute_dates:
                    claim.normalized_value = {"raw": raw, "absolute_dates": absolute_dates}
                    claim.checks.append(VerificationCheck(
                        check_id="date_normalization", label="날짜 정규화",
                        status=VerificationStatus.CONFIRMED,
                        detail=f"원문 날짜를 읽어 정규화했습니다: {absolute_dates}.",
                    ))
                elif re.search(r"계약.{0,8}(?:후|일로부터)|착수.{0,8}후|개월|주간|일간", raw):
                    claim.normalized_value = {"raw": raw, "relative_period": True}
                    claim.checks.append(VerificationCheck(
                        check_id="date_normalization", label="상대 기간 보존",
                        status=VerificationStatus.CONFIRMED,
                        detail="상대 기준일을 임의의 절대 날짜로 계산하지 않고 원문 표현을 보존했습니다.",
                    ))
                else:
                    claim.checks.append(VerificationCheck(
                        check_id="date_normalization", label="날짜 유형/값 정규화",
                        status=VerificationStatus.HUMAN_REVIEW,
                        detail="절대 날짜 또는 확인된 상대 기간으로 안전하게 정규화할 수 없습니다.",
                    ))
                    claim.unresolved_questions.append("날짜 기준 사건, 시각, 수행기간/제출기한 종류를 확인해 주세요.")
                claim.status = _state_for_checks(claim.checks)
                claims.append(claim)

        if notice_no:
            api_deadline = notice_record.get("bidClseDt") if notice_record else None
            if api_deadline not in (None, ""):
                claims.append(VerifiedClaim(
                    category="schedule", field="g2b_bid_closing_time", claim=api_deadline,
                    normalized_value={"kind": "bid_closing_time", "raw": str(api_deadline),
                                      "is_proposal_deadline": False},
                    status=VerificationStatus.CONFIRMED,
                    checks=[VerificationCheck(
                        check_id="g2b_bid_close_api", label="나라장터 입찰 마감 API 확인",
                        status=VerificationStatus.CONFIRMED,
                        detail="공고별 API의 bidClseDt(입찰 마감) 값입니다. 제안서 제출기한과 같은 일정이라고 간주하지 않습니다.",
                    )],
                    citations=[_notice_api_citation(notice_record, "bidClseDt", "입찰 마감 일시", api_deadline)],
                ))
            else:
                status = VerificationStatus.HUMAN_REVIEW if lookup_status in {"unavailable", "not_found"} else VerificationStatus.RETRIEVE_MORE
                detail = lookup_error or ("공고번호 API 조회에서 입찰 마감 필드를 찾지 못했습니다." if lookup_status == "found"
                                          else "나라장터 공고별 API에서 입찰 마감 일시를 확인하지 못했습니다.")
                claims.append(VerifiedClaim(
                    category="schedule", field="g2b_bid_closing_time", status=status,
                    checks=[VerificationCheck(
                        check_id="g2b_bid_close_api", label="나라장터 입찰 마감 API 확인", status=status,
                        detail=detail,
                    )], unresolved_questions=["공고별 API의 입찰 마감 일시와 정정 공고 여부를 확인해 주세요."],
                ))

        complete = bool(claims) and all(claim.status == VerificationStatus.CONFIRMED for claim in claims)
        note = "일정 유형별 원문 근거와 값을 확인했습니다." if complete else "일정 후보 누락, 원문 근거, 충돌 또는 날짜 해석 확인이 남았습니다."
        return CategoryVerification(
            category="schedule", status=_category_state(claims), claims=claims,
            coverage_complete=complete, coverage_note=note,
        )

    def _keyword_pages(self, doc_id: str, notice_no: str | None, keywords: tuple[str, ...]) -> set[int]:
        pages: set[int] = set()
        for keyword in keywords:
            results = search_keyword(keyword, limit=50, document_id=doc_id, match_mode="all")
            pages.update(
                int(item.metadata["page"]) for item in results
                if item.metadata.get("page") is not None
            )
        return pages

    @staticmethod
    def _normalize_dates(value: str) -> list[str]:
        dates = []
        for match in DATE_RE.finditer(value):
            year, month, day = (int(match.group(name)) for name in ("year", "month", "day"))
            try:
                parsed_date = date(year, month, day)
            except ValueError:
                continue
            normalized = parsed_date.isoformat()
            remainder = value[match.end():match.end() + 24]
            time = TIME_RE.search(remainder)
            if time:
                hour, minute = int(time.group(2)), int(time.group(3))
                if time.group(1) == "오후" and hour < 12:
                    hour += 12
                elif time.group(1) == "오전" and hour == 12:
                    hour = 0
                if hour <= 23 and minute <= 59:
                    normalized += f"T{hour:02d}:{minute:02d}"
            dates.append(normalized)
        return list(dict.fromkeys(dates))

    @staticmethod
    def _is_schedule_template(value: str) -> bool:
        return bool(re.search(r"YYYY|20XX|년\s*월\s*일|기재\s*\)?$|\(\s*\.\s*\.\s*\.", value, re.I))

    @classmethod
    def _schedule_signature(cls, value: str, field: str) -> tuple[str, ...]:
        dates = cls._normalize_dates(value)
        if dates:
            # A contract period often appears once as a five-year phrase and
            # once as an explicit start rule; a shared endpoint is not itself
            # a contradiction. Keep all explicit dates for other schedule types.
            if field == "contract_period":
                return (dates[-1],)
            return tuple(dates)
        compact = _norm(value)
        return (compact,)

