from __future__ import annotations

import re

from .models import (
    CapabilityMatchingResult,
    CompanyEvidence,
    CompanyProfile,
    Evidence,
    FitScore,
    MandatoryStatus,
    MatchStatus,
    PublicCompanyEvidence,
    Requirement,
    RequirementMatch,
    RetrievedCompanyEvidenceCandidate,
    RFPAnalysisResult,
    ReviewStatus,
    ScoreWeight,
)
from .policy import SAMPLE_EVIDENCE_NOTICE, TARGET_LEGAL_ENTITY


def _tokens(value: str) -> set[str]:
    ignored = {
        "및", "또는", "요구", "사항", "사업", "시스템", "구축", "수행", "대한", "필수",
        "기재되어", "위한", "공식", "관련", "내용", "업무", "자료", "경우", "대상",
        "계약을", "대표", "기반", "사업을", "계약", "운영", "제공", "통해", "등의",
    }
    return {
        token.lower() for token in re.findall(r"[A-Za-z가-힣0-9]{2,}", value)
        if token not in ignored and not token.isdigit()
    }


class CapabilityMatcher:
    def __init__(self, settings: dict):
        self.settings = settings

    def _find_evidence(self, requirement: Requirement, profile: CompanyProfile):
        wanted = _tokens(" ".join((requirement.category, requirement.name, requirement.details)))
        matches = []
        denials = []
        performance_required = any(term in requirement.details for term in self.settings["capability"]["performance_terms"])
        for record in profile.records:
            if performance_required and not self._is_verified_performance(record):
                continue
            corpus = " ".join((record.title, " ".join(record.keywords), record.excerpt))
            overlap = wanted & _tokens(corpus)
            if overlap:
                if record.explicitly_not_supported:
                    denials.append(record)
                else:
                    matches.append((record, overlap))
        return matches, denials

    @staticmethod
    def _is_verified_performance(record) -> bool:
        """A project record counts as similar performance only with all requested proof fields."""
        return (
            record.type == "수행실적"
            and not record.verification_required
            and bool(record.buyer and record.title and record.period and record.contract_amount_krw is not None and record.scope)
            and bool(record.document and record.section and record.excerpt)
        )

    @staticmethod
    def _public_evidence_notice(profile: CompanyProfile) -> str | None:
        """Expose evidence limits so public sources are never mistaken for bid proof."""
        notices: list[str] = []
        if profile.evidence_scope:
            notices.append(f"증빙 범위: {profile.evidence_scope}")
        if profile.usage_rule:
            notices.append(profile.usage_rule)
        if profile.entity_boundary_rule:
            notices.append(profile.entity_boundary_rule)
        if profile.missing_evidence:
            notices.append("별도 확인 필요: " + "; ".join(profile.missing_evidence))
        if TARGET_LEGAL_ENTITY not in profile.company_name:
            notices.append(f"제공 자료의 법인명이 분석 대상 {TARGET_LEGAL_ENTITY}와 다릅니다. 참여 법인·실적 귀속을 확인하세요.")
        return " ".join(notices) or None

    @staticmethod
    def _personnel_counts(requirement: Requirement, records) -> tuple[int | None, int | None]:
        if not any(token in requirement.details.upper() for token in ("PM", "PL", "개발", "인프라")):
            return None, None
        needed = re.search(r"(\d+)\s*명", requirement.details)
        required = int(needed.group(1)) if needed else None
        counts = [record.available_headcount for record, _ in records if record.available_headcount is not None]
        available = sum(counts) if counts else None
        return required, available

    def _weight(self, requirement: Requirement) -> float:
        return float(self.settings["scoring"]["category_weights"].get(
            requirement.category, self.settings["scoring"]["default_weight"]
        ))

    @staticmethod
    def _public_evidence(record, rfp: RFPAnalysisResult) -> PublicCompanyEvidence:
        rfp_text = " ".join(
            [rfp.project_summary.project_name or "", rfp.project_summary.scope or ""]
            + [item.name + " " + item.details for item in rfp.requirements]
        )
        overlap = _tokens(rfp_text) & _tokens(" ".join([record.title, *record.keywords, record.excerpt]))
        relevance = (
            f"RFP와 공통 키워드: {', '.join(sorted(overlap)[:4])}"
            if overlap else "직접 매칭 근거 부족; 공공 정보시스템 구축·운영 관련 공개 사례로 사전 참고"
        )
        if record.financial_basis:
            figures = [
                f"매출 {record.revenue_krw:,}원" if record.revenue_krw is not None else None,
                f"총자산 {record.total_assets_krw:,}원" if record.total_assets_krw is not None else None,
                f"자본 {record.total_equity_krw:,}원" if record.total_equity_krw is not None else None,
            ]
            relevance = f"{record.financial_basis}; {', '.join(value for value in figures if value)}"
        return PublicCompanyEvidence(
            title=record.title,
            source_url=record.source_url,
            relevance=relevance,
            verification_required=record.verification_required or "입찰용 원본 증빙 확인 필요",
            type=record.type,
        )

    def _public_company_summary(self, rfp: RFPAnalysisResult, profile: CompanyProfile):
        records = list(profile.records)
        rfp_text = " ".join([rfp.project_summary.project_name or "", rfp.project_summary.scope or ""] + [r.name + " " + r.details for r in rfp.requirements])
        score = lambda record: len(_tokens(rfp_text) & _tokens(" ".join([record.title, *record.keywords, record.excerpt])))
        public_projects = [record for record in records if record.type in {"공개수행실적", "수행실적"}]
        public_projects.sort(key=score, reverse=True)
        technology = [record for record in records if record.type in {"사업역량", "공개기술역량", "인증및지식재산"}]
        technology.sort(key=score, reverse=True)
        finance = next((record for record in records if record.type == "재무참고정보" or record.financial_basis), None)
        limitations = list(dict.fromkeys(filter(None, [
            profile.usage_rule,
            "공개자료는 입찰용 원본 실적증명서·인증서·인력증빙을 대체하지 않습니다.",
            *[record.verification_required for record in records if record.verification_required],
        ])))[:8]
        return (
            [self._public_evidence(record, rfp) for record in public_projects[:3]],
            [self._public_evidence(record, rfp) for record in technology[:5]],
            self._public_evidence(finance, rfp) if finance else None,
            limitations,
        )

    def analyze(
        self,
        rfp: RFPAnalysisResult,
        profile: CompanyProfile,
        retrieved_candidates_by_requirement: dict[str, list[RetrievedCompanyEvidenceCandidate]] | None = None,
    ) -> CapabilityMatchingResult:
        retrieved_candidates_by_requirement = retrieved_candidates_by_requirement or {}
        matches: list[RequirementMatch] = []
        critical: list[str] = []
        review_items: list[str] = []
        numerator = denominator = 0.0
        excluded: list[str] = []
        weights: dict[str, float] = {}
        ratios = self.settings["scoring"]["status_ratios"]
        excluded_statuses = set(self.settings["scoring"]["exclude_statuses"])
        staffing_requirements = [
            Requirement(
                id=item.id, category="필수 인력", name=item.requirement_text, details=item.requirement_text,
                mandatory_status=MandatoryStatus.REQUIRED if item.mandatory else MandatoryStatus.NEEDS_REVIEW,
                evidence=Evidence(document="RFP", pdf_page=item.source_page, section="인력 요건", excerpt=item.source_excerpt),
                review_status=ReviewStatus.COMPLETE,
            )
            for item in rfp.staffing_requirements
        ]
        all_requirements = list(rfp.requirements) + staffing_requirements
        eligibility_matches: list[RequirementMatch] = []
        staffing_matches: list[RequirementMatch] = []
        # 법적 참가자격은 기술 적합도 점수와 분리해 별도 결과로 남깁니다.
        for index, item in enumerate(rfp.eligibility_requirements, start=1):
            eligibility_requirement = Requirement(
                id=f"ELIG-{index:03d}", category="참가자격", name=item.name, details=item.details,
                mandatory_status=item.mandatory_status, evidence=item.evidence, review_status=item.review_status,
            )
            eligibility_matches.append(self._match_requirement(eligibility_requirement, profile, ratios, include_score=False))
        for eligibility in eligibility_matches:
            if eligibility.status != MatchStatus.MET:
                critical.append(f"{eligibility.requirement_id}: {eligibility.status.value} — {eligibility.gap_or_check_item}")
                review_items.append(f"{eligibility.requirement_id}: {eligibility.gap_or_check_item}")
        for requirement in all_requirements:
            item_match = self._match_requirement(
                requirement, profile, ratios, include_score=True,
                retrieved_candidates=retrieved_candidates_by_requirement.get(requirement.id, []),
            )
            status = item_match.status
            ratio = item_match.score
            weight = self._weight(requirement)
            weights[requirement.id] = weight
            if status.value in excluded_statuses:
                excluded.append(f"{requirement.id}: {status.value}")
            else:
                numerator += weight * ratio
                denominator += weight
            item_match.score_rationale = f"상태 {status.value}의 비율 {ratio} × 가중치 {weight}"
            matches.append(item_match)
            if requirement.id.startswith("STAFF-"):
                staffing_matches.append(item_match)
            is_critical = requirement.category == "필수 인력" or "PM" in requirement.details or "PL" in requirement.details
            if is_critical and status != MatchStatus.MET:
                critical.append(f"{requirement.id} ({requirement.name[:60]}): {status.value} — {item_match.gap_or_check_item}")
            if status == MatchStatus.NEEDS_REVIEW:
                review_items.append(f"{requirement.id}: {item_match.gap_or_check_item}")
        value = round((numerator / denominator * 100) if denominator else 0.0, 2)
        formula = "적합도(%) = Σ(가중치 × 상태비율) / Σ(가중치; 제외항목 제외) × 100"
        public_records, public_technology, financial_reference, limitations = self._public_company_summary(rfp, profile)
        return CapabilityMatchingResult(
            company_name=profile.company_name,
            company_evidence_notice=SAMPLE_EVIDENCE_NOTICE if profile.is_sample_data else self._public_evidence_notice(profile),
            requirement_matches=matches,
            eligibility_matches=eligibility_matches,
            staffing_matches=staffing_matches,
            fit_score=FitScore(value=value, formula=formula, numerator=round(numerator, 4), denominator=round(denominator, 4), weights=[ScoreWeight(category=key, weight=value) for key, value in weights.items()], excluded_items=excluded),
            critical_risks=critical,
            human_review_items=review_items,
            representative_public_records=public_records,
            public_technology_and_certifications=public_technology,
            financial_reference=financial_reference,
            evidence_limitations=limitations,
        )

    def _match_requirement(
        self,
        requirement: Requirement,
        profile: CompanyProfile,
        ratios: dict,
        include_score: bool,
        retrieved_candidates: list[RetrievedCompanyEvidenceCandidate] | None = None,
    ) -> RequirementMatch:
        found, denials = self._find_evidence(requirement, profile)
        company_evidence = [
            CompanyEvidence(
                document=r.document, section=r.section, excerpt=r.excerpt, record_id=r.id,
                source_url=r.source_url, evidence_level=r.evidence_level,
                verification_required=r.verification_required,
            )
            for r, _ in found[:3]
        ]
        if denials and not found:
            status = MatchStatus.UNMET
            gap = "제공된 회사 자료에 해당 조건을 충족하지 못한다는 명시적 증거가 있습니다. 최신 사실 여부를 확인하세요."
        elif not found:
            status = MatchStatus.NEEDS_REVIEW
            gap = "제공된 회사 자료에서 직접 증빙을 찾지 못했습니다. 증빙 부재만으로 미충족으로 판단하지 않습니다."
        else:
            strongest = max(len(overlap) for _, overlap in found)
            has_final_evidence = any(not record.verification_required for record, _ in found)
            status = MatchStatus.MET if strongest >= 2 and has_final_evidence else MatchStatus.PARTIAL
            if not has_final_evidence:
                limits = "; ".join(dict.fromkeys(record.verification_required for record, _ in found if record.verification_required))
                gap = f"공개 근거와 요구사항 키워드는 일치하지만 입찰 제출용 원본 증빙이 필요합니다. {limits}"
            else:
                gap = "직접 증빙 확인" if status == MatchStatus.MET else "키워드 일부만 일치합니다. 요구 수준·기간·인력 등 정량 조건을 확인하세요."
        if requirement.review_status.value != "확인 완료":
            status = MatchStatus.NEEDS_REVIEW
            gap = "RFP 근거 위치 또는 추출 상태가 확정되지 않아 자사 적합도도 확정할 수 없습니다."
        required, available = self._personnel_counts(requirement, found)
        if required is not None and requirement.review_status == ReviewStatus.COMPLETE:
            if available is None:
                if status != MatchStatus.UNMET:
                    status = MatchStatus.NEEDS_REVIEW
                gap = f"{gap} 역할별 필요 인원 {required}명 대비 가용 인원 증빙을 확인하세요."
            elif available < required:
                status = MatchStatus.UNMET
                gap = f"필요 인원 {required}명 대비 가용 인원 {available}명으로 {required - available}명 부족합니다. 역할·기간·중복 배치와 충원 가능 여부는 사람이 확인해야 합니다."
        ratio = float(ratios.get(status.value, 0.0)) if include_score else 0.0
        return RequirementMatch(
            requirement_id=requirement.id, status=status, company_evidence=company_evidence,
            retrieved_candidates=retrieved_candidates or [],
            gap_or_check_item=gap, score=ratio, score_rationale=f"상태 {status.value}",
            required_headcount=required, available_headcount=available,
        )
