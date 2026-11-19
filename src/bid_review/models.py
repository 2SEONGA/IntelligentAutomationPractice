from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator
from .policy import SOURCE_ARTIFACTS


class MandatoryStatus(str, Enum):
    REQUIRED = "필수"
    OPTIONAL = "선택"
    NEEDS_REVIEW = "확인 필요"


class ReviewStatus(str, Enum):
    COMPLETE = "확인 완료"
    NEEDS_REVIEW = "확인 필요"
    CONFLICT = "충돌"


class MatchStatus(str, Enum):
    MET = "충족"
    PARTIAL = "부분 충족"
    UNMET = "미충족"
    NEEDS_REVIEW = "확인 필요"


class Evidence(BaseModel):
    document: str
    source_document: Literal["rfp_pdf", "notice_pdf"] | None = None
    pdf_page: int | None = Field(default=None, ge=1)
    section: str = "미분류"
    excerpt: str = ""

    def is_located(self) -> bool:
        return bool(self.document and self.pdf_page and self.section and self.excerpt)


class ProjectSummary(BaseModel):
    project_name: str | None = None
    buyer: str | None = None
    budget_krw: int | None = Field(default=None, ge=0)
    budget_original_text: str | None = None
    estimated_price_krw: int | None = Field(default=None, ge=0)
    estimated_price_original_text: str | None = None
    period: str | None = None
    submission_deadline: str | None = None
    scope: str | None = None
    deliverables: list[str] = Field(default_factory=list)
    schedule: list[str] = Field(default_factory=list)
    evaluation_criteria: list[str] = Field(default_factory=list)
    performance_recognition_conditions: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class RFPFieldCandidate(BaseModel):
    """A preflight field value retained with its page-level source evidence."""

    value: str | int | None = None
    source_page: int | None = Field(default=None, ge=1)
    source_excerpt: str = ""
    confidence: Literal["high", "medium", "low"] = "low"
    score: float = 0.0
    # The page number is meaningful only together with the PDF it came from.
    source_document: Literal["rfp_pdf", "notice_pdf"] = "rfp_pdf"
    page_type: Literal["cover_page", "table_of_contents", "body_page", "table_heavy_page", "appendix_page"] | None = None
    invalid_reason: str | None = None


class RFPBasicInfo(BaseModel):
    project_name: RFPFieldCandidate = Field(default_factory=RFPFieldCandidate)
    procuring_entity: RFPFieldCandidate = Field(default_factory=RFPFieldCandidate)
    notice_number: RFPFieldCandidate = Field(default_factory=RFPFieldCandidate)
    proposal_deadline: RFPFieldCandidate = Field(default_factory=RFPFieldCandidate)
    bid_deadline: RFPFieldCandidate = Field(default_factory=RFPFieldCandidate)
    proposal_submission_start: RFPFieldCandidate = Field(default_factory=RFPFieldCandidate)
    joint_venture_deadline: RFPFieldCandidate = Field(default_factory=RFPFieldCandidate)
    budget_krw: RFPFieldCandidate = Field(default_factory=RFPFieldCandidate)
    estimated_price_krw: RFPFieldCandidate = Field(default_factory=RFPFieldCandidate)
    contract_period: RFPFieldCandidate = Field(default_factory=RFPFieldCandidate)
    contract_method: RFPFieldCandidate = Field(default_factory=RFPFieldCandidate)
    evaluation_method: RFPFieldCandidate = Field(default_factory=RFPFieldCandidate)
    joint_venture_policy: RFPFieldCandidate = Field(default_factory=RFPFieldCandidate)
    project_purpose: RFPFieldCandidate = Field(default_factory=RFPFieldCandidate)
    demanding_entity: RFPFieldCandidate = Field(default_factory=RFPFieldCandidate)


class RFPBasicInfoCandidates(BaseModel):
    project_name: list[RFPFieldCandidate] = Field(default_factory=list)
    procuring_entity: list[RFPFieldCandidate] = Field(default_factory=list)
    notice_number: list[RFPFieldCandidate] = Field(default_factory=list)
    proposal_deadline: list[RFPFieldCandidate] = Field(default_factory=list)
    bid_deadline: list[RFPFieldCandidate] = Field(default_factory=list)
    proposal_submission_start: list[RFPFieldCandidate] = Field(default_factory=list)
    joint_venture_deadline: list[RFPFieldCandidate] = Field(default_factory=list)
    budget_krw: list[RFPFieldCandidate] = Field(default_factory=list)
    estimated_price_krw: list[RFPFieldCandidate] = Field(default_factory=list)
    contract_period: list[RFPFieldCandidate] = Field(default_factory=list)
    contract_method: list[RFPFieldCandidate] = Field(default_factory=list)
    evaluation_method: list[RFPFieldCandidate] = Field(default_factory=list)
    joint_venture_policy: list[RFPFieldCandidate] = Field(default_factory=list)
    project_purpose: list[RFPFieldCandidate] = Field(default_factory=list)
    demanding_entity: list[RFPFieldCandidate] = Field(default_factory=list)


class FieldSelectionReason(BaseModel):
    field: str
    reason: str


class PageAnalysis(BaseModel):
    page_number: int = Field(ge=1)
    page_type: Literal["cover_page", "table_of_contents", "body_page", "table_heavy_page", "appendix_page"]
    rationale: str
    extraction_methods: list[str] = Field(default_factory=list)
    table_count: int = Field(default=0, ge=0)
    ocr_required: bool = False
    ocr_reason: str | None = None
    # OCR 텍스트를 이 페이지의 추출 텍스트로 채택했는지(기존 텍스트보다 나을 때만).
    ocr_applied: bool = False


class RFPQualityIssue(BaseModel):
    field: str
    reason: str
    source_page: int | None = Field(default=None, ge=1)


class RFPQualityGate(BaseModel):
    status: Literal["passed", "needs_manual_verification", "needs_ocr", "not_provided"] = "needs_manual_verification"
    issues: list[RFPQualityIssue] = Field(default_factory=list)
    message: str | None = None

    @property
    def passed(self) -> bool:
        return self.status == "passed"


class StructuredRequirement(BaseModel):
    id: str
    requirement_text: str
    mandatory: bool = False
    source_page: int = Field(ge=1)
    source_excerpt: str
    source_document: Literal["rfp_pdf", "notice_pdf"] = "rfp_pdf"
    required_company_evidence: list[str] = Field(default_factory=list)


class UnclassifiedRequirement(StructuredRequirement):
    reason: str


class Requirement(BaseModel):
    id: str
    category: str
    name: str
    details: str
    mandatory_status: MandatoryStatus = MandatoryStatus.NEEDS_REVIEW
    evidence: Evidence
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW

    @model_validator(mode="after")
    def unlocated_evidence_cannot_be_final(self) -> "Requirement":
        if not self.evidence.is_located():
            self.mandatory_status = MandatoryStatus.NEEDS_REVIEW
            self.review_status = ReviewStatus.NEEDS_REVIEW
        return self


class EligibilityRequirement(BaseModel):
    name: str
    details: str
    mandatory_status: MandatoryStatus = MandatoryStatus.NEEDS_REVIEW
    evidence: Evidence
    review_status: ReviewStatus = ReviewStatus.NEEDS_REVIEW


class ExtractionIssue(BaseModel):
    issue_type: str
    message: str
    evidence: list[Evidence] = Field(default_factory=list)


class DocumentConflict(BaseModel):
    field: str
    rfp_value: RFPFieldCandidate
    notice_value: RFPFieldCandidate
    reason: str


class RFPAnalysisResult(BaseModel):
    analysis_scope: Literal["rfp_only", "notice_only", "notice_and_rfp"] = "rfp_only"
    source_document: Literal["rfp_pdf", "notice_pdf"] = "rfp_pdf"
    project_summary: ProjectSummary = Field(default_factory=ProjectSummary)
    basic_info: RFPBasicInfo = Field(default_factory=RFPBasicInfo)
    basic_info_candidates: RFPBasicInfoCandidates = Field(default_factory=RFPBasicInfoCandidates)
    basic_info_selection_reasons: list[FieldSelectionReason] = Field(default_factory=list)
    # Extraction-only metadata. Keep it in memory for quality checks and
    # opt-in diagnostics, but never serialize it into standard artifacts or
    # CrewAI Task context.
    page_analysis: list[PageAnalysis] = Field(default_factory=list, exclude=True)
    quality_gate: RFPQualityGate = Field(default_factory=RFPQualityGate)
    rfp_quality_gate: RFPQualityGate | None = None
    notice_quality_gate: RFPQualityGate | None = None
    integration_quality_gate: RFPQualityGate | None = None
    notice_document: str | None = None
    conflicts: list[DocumentConflict] = Field(default_factory=list)
    # Legacy hand-constructed test/artifact instances remain market-capable;
    # the document merger explicitly turns this off for RFP-only reviews.
    market_research_allowed: bool = True
    needs_ocr: bool = False
    eligibility_requirements: list[EligibilityRequirement] = Field(default_factory=list)
    staffing_requirements: list[StructuredRequirement] = Field(default_factory=list)
    submission_requirements: list[StructuredRequirement] = Field(default_factory=list)
    deliverable_requirements: list[StructuredRequirement] = Field(default_factory=list)
    technical_requirements: list[StructuredRequirement] = Field(default_factory=list)
    unclassified_requirements: list[UnclassifiedRequirement] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    extraction_issues: list[ExtractionIssue] = Field(default_factory=list)


class CompanyEvidence(BaseModel):
    document: str
    section: str
    excerpt: str
    record_id: str | None = None
    source_url: str | None = None
    evidence_level: str | None = None
    verification_required: str | None = None


class RetrievedCompanyEvidenceCandidate(BaseModel):
    """RAG retrieval candidates shown separately from deterministic match status."""

    record_id: str
    title: str
    record_type: str
    excerpt: str
    source_id: str | None = None
    source_title: str | None = None
    source_url: str | None = None
    source_section: str | None = None
    evidence_level: str | None = None
    verification_required: str | None = None
    data_freshness: str | None = None
    retrieval_method: Literal["SQLite FTS5 키워드 검색", "키워드+의미 검색", "의미 검색"] = "SQLite FTS5 키워드 검색"
    semantic_score: float | None = None
    interpretation_notice: str = "검색 후보입니다. 적합도 점수나 자격 충족 판정에 자동 반영하지 않으며 원문과 증빙을 확인해야 합니다."


class RequirementMatch(BaseModel):
    requirement_id: str
    status: MatchStatus
    company_evidence: list[CompanyEvidence] = Field(default_factory=list)
    retrieved_candidates: list[RetrievedCompanyEvidenceCandidate] = Field(default_factory=list)
    gap_or_check_item: str
    score: float = Field(ge=0, le=1)
    score_rationale: str
    required_headcount: int | None = Field(default=None, ge=0)
    available_headcount: int | None = Field(default=None, ge=0)


class ScoreWeight(BaseModel):
    category: str
    weight: float


class FitScore(BaseModel):
    value: float = Field(ge=0, le=100)
    formula: str
    numerator: float = 0
    denominator: float = 0
    weights: list["ScoreWeight"] = Field(default_factory=list)
    excluded_items: list[str] = Field(default_factory=list)


class CapabilityMatchingResult(BaseModel):
    company_name: str = "㈜아이티센엔텍(구 쌍용정보통신㈜)"
    company_evidence_notice: str | None = None
    requirement_matches: list[RequirementMatch] = Field(default_factory=list)
    eligibility_matches: list[RequirementMatch] = Field(default_factory=list)
    staffing_matches: list[RequirementMatch] = Field(default_factory=list)
    fit_score: FitScore
    critical_risks: list[str] = Field(default_factory=list)
    human_review_items: list[str] = Field(default_factory=list)
    representative_public_records: list["PublicCompanyEvidence"] = Field(default_factory=list, max_length=3)
    public_technology_and_certifications: list["PublicCompanyEvidence"] = Field(default_factory=list, max_length=5)
    financial_reference: "PublicCompanyEvidence | None" = None
    evidence_limitations: list[str] = Field(default_factory=list, max_length=8)


class PublicCompanyEvidence(BaseModel):
    """Public-source evidence: useful for relevance, never bid-proof by itself."""

    title: str
    source_url: str | None = None
    relevance: str
    evidence_tier: Literal["public_reference"] = "public_reference"
    bid_eligibility_status: Literal["원본 증빙 확인 필요"] = "원본 증빙 확인 필요"
    verification_required: str
    type: str | None = None


class SimilarProject(BaseModel):
    project_name: str
    buyer: str | None = None
    awardee: str | None = None
    budget_or_award_amount: str | None = None
    source_url: str = Field(min_length=1)
    checked_at: str
    opening_date: str | None = None
    similarity_rationale: str
    comparison_limitations: list[str] = Field(default_factory=list)


class MarketObservation(BaseModel):
    statement: str
    source_url: str = Field(min_length=1)
    checked_at: str
    limitations: list[str] = Field(default_factory=list)


class MarketResearchResult(BaseModel):
    similar_projects: list[SimilarProject] = Field(default_factory=list)
    market_observations: list[MarketObservation] = Field(default_factory=list)
    unknown_items: list[str] = Field(default_factory=list)


class DecisionStatus(str, Enum):
    GO = "Go"
    CONDITIONAL = "조건부 Go"
    NO_GO = "No-Go"
    HOLD = "보류"


class ProjectScaleReview(BaseModel):
    amount_krw: int | None = Field(default=None, ge=0)
    minimum_krw: int = Field(ge=0)
    status: Literal["기준 미설정", "기준 충족", "기준 미달", "확인 필요"]
    source_document: str = "rfp_pdf"
    source_page: int | None = None
    source_excerpt: str = ""
    rationale: str


class CompactEvidenceReference(BaseModel):
    """A short pointer to a canonical, rule-based analysis artifact."""

    reference: str = Field(min_length=1, max_length=240)


class CompactReviewItem(BaseModel):
    """A bounded Agent statement which must point back to detailed evidence."""

    statement: str = Field(min_length=1, max_length=360)
    evidence_references: list[CompactEvidenceReference] = Field(min_length=1, max_length=3)


class RFPReviewSummary(BaseModel):
    """Small live-Crew result; detailed RFP evidence stays in 01_rfp_analysis."""

    document_status: Literal["정상", "확인 필요", "충돌", "OCR 필요"]
    project_name: str = Field(min_length=1, max_length=180)
    top_mandatory_conditions: list[CompactReviewItem] = Field(default_factory=list, max_length=5)
    top_conflicts: list[CompactReviewItem] = Field(default_factory=list, max_length=3)
    critical_missing_information: list[CompactReviewItem] = Field(default_factory=list, max_length=5)
    proceed_to_next_stage: bool
    project_scale: ProjectScaleReview | None = None


class CapabilityReviewSummary(BaseModel):
    """Small live-Crew result; detailed matches and score stay in 02."""

    overall_fit: Literal["충족", "부분 충족", "확인 필요", "미충족"]
    top_strengths: list[CompactReviewItem] = Field(default_factory=list, max_length=3)
    top_gaps: list[CompactReviewItem] = Field(default_factory=list, max_length=5)
    critical_evidence_to_verify: list[CompactReviewItem] = Field(default_factory=list, max_length=5)
    blocking_risk: bool
    confirmed_company_evidence: list[CompactReviewItem] = Field(default_factory=list, max_length=3)
    public_reference_strengths: list[CompactReviewItem] = Field(default_factory=list, max_length=5)
    missing_bid_evidence: list[CompactReviewItem] = Field(default_factory=list, max_length=5)


class MarketReviewSummary(BaseModel):
    """Small live-Crew result; source-linked research records stay in 03."""

    research_status: Literal["completed", "skipped", "insufficient_data"]
    comparable_cases_count: int = Field(ge=0, le=999)
    market_observations: list[CompactReviewItem] = Field(default_factory=list, max_length=3)
    search_limitations: list[CompactReviewItem] = Field(default_factory=list, max_length=3)
    evidence_to_verify: list[CompactReviewItem] = Field(default_factory=list, max_length=3)


class ExecutiveDecisionSummary(BaseModel):
    """The Report Agent's bounded recommendation, not the final business decision."""

    recommendation: Literal["Go", "조건부 Go", "No-Go", "보류"]
    one_line_reason: str = Field(min_length=1, max_length=360)
    top_positive_factors: list[CompactReviewItem] = Field(default_factory=list, max_length=3)
    top_blockers_or_risks: list[CompactReviewItem] = Field(default_factory=list, max_length=5)
    required_next_actions: list[CompactReviewItem] = Field(default_factory=list, max_length=7)
    final_decision_owner: Literal["사업 담당자"] = "사업 담당자"


class ReportFinding(BaseModel):
    topic: str
    assessment: str
    evidence_references: list[str] = Field(min_length=1)
    action: str | None = None


class BidReviewReport(BaseModel):
    decision_status: DecisionStatus
    executive_summary: str
    key_findings: list[ReportFinding] = Field(default_factory=list)
    bid_conditions: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    source_artifacts: list[str] = Field(default_factory=list)
    agent_executive_summary: ExecutiveDecisionSummary | None = None
    final_decision: str = "미결정"
    project_scale: ProjectScaleReview | None = None

    @model_validator(mode="after")
    def report_must_only_use_validated_artifacts(self) -> "BidReviewReport":
        allowed_blocked_artifacts = {"debug/01_rfp_analysis.json"}
        if set(self.source_artifacts) != set(SOURCE_ARTIFACTS) and not (
            self.decision_status == DecisionStatus.HOLD and set(self.source_artifacts) == allowed_blocked_artifacts
        ):
            raise ValueError("보고서는 세 개의 검증된 분석 산출물만 source_artifacts로 사용할 수 있습니다.")
        if self.final_decision != "미결정":
            raise ValueError("사업본부장 최종 결정은 AI가 작성할 수 없으며 '미결정'이어야 합니다.")
        return self


class CompanyRecord(BaseModel):
    id: str
    type: str
    title: str
    keywords: list[str] = Field(default_factory=list)
    document: str
    section: str
    excerpt: str
    explicitly_not_supported: bool = False
    buyer: str | None = None
    period: str | None = None
    contract_amount_krw: int | None = Field(default=None, ge=0)
    scope: str | None = None
    role: str | None = None
    # Public sources sometimes state a role or period without the primary
    # contract evidence required for an eligible bid-performance judgment.
    project_role: str | None = None
    contract_period_months: int | None = Field(default=None, ge=0)
    estimated_contract_amount_krw_approx: int | None = Field(default=None, ge=0)
    financial_basis: str | None = None
    revenue_krw: int | None = Field(default=None, ge=0)
    operating_income_krw: int | None = None
    net_income_krw: int | None = None
    total_assets_krw: int | None = Field(default=None, ge=0)
    total_equity_krw: int | None = None
    available_headcount: int | None = Field(default=None, ge=0)
    source_url: str | None = None
    evidence_level: str | None = None
    verification_required: str | None = None


class CompanyProfile(BaseModel):
    company_name: str = "㈜아이티센엔텍(구 쌍용정보통신㈜)"
    is_sample_data: bool = False
    former_name: str | None = None
    evidence_scope: str | None = None
    usage_rule: str | None = None
    entity_boundary_rule: str | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    records: list[CompanyRecord] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_source_linked_public_evidence(cls, value: Any) -> Any:
        """Accept source_id-based public-evidence files without weakening proof rules.

        The internal schema keeps the evidence location on each record.  Public
        evidence files can instead declare a shared ``sources`` list and point
        to it with ``source_id``.  This normalizer copies the source title/URL
        to the record; it never invents missing business scope or proof facts.
        """
        if not isinstance(value, dict):
            return value
        sources = {
            item.get("id"): item
            for item in value.get("sources", [])
            if isinstance(item, dict) and item.get("id")
        }
        normalized = dict(value)
        normalized_records: list[Any] = []
        for item in value.get("records", []):
            if not isinstance(item, dict):
                normalized_records.append(item)
                continue
            record = dict(item)
            source = sources.get(record.get("source_id"), {})
            if not record.get("document") and source.get("title"):
                record["document"] = source["title"]
            if not record.get("source_url") and source.get("url"):
                record["source_url"] = source["url"]
            if not record.get("evidence_level") and source.get("source_type"):
                record["evidence_level"] = source["source_type"]
            if not record.get("buyer") and record.get("client"):
                record["buyer"] = record["client"]
            if not record.get("period"):
                start, end = record.get("contract_date"), record.get("contract_end_date")
                if start and end:
                    record["period"] = f"{start} ~ {end}"
                elif start:
                    record["period"] = str(start)
                elif record.get("contract_period"):
                    record["period"] = str(record["contract_period"])
            if not record.get("role") and record.get("project_role"):
                record["role"] = str(record["project_role"])
            normalized_records.append(record)
        normalized["records"] = normalized_records
        return normalized
