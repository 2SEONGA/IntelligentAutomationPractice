from __future__ import annotations

from datetime import date
from pathlib import Path
import json

from .agent03_g2b import G2BApiError, G2BAwardClient
from .models import MarketObservation, MarketResearchResult, RFPAnalysisResult, SimilarProject
from .policy import G2B_COMPANY_SEARCH_TERMS


class MarketResearcher:
    """Combines reviewed market files with source-linked G2B award evidence."""
    def __init__(self, market_file: str | Path | None = None, settings: dict | None = None, g2b_client: G2BAwardClient | None = None):
        self.market_file = Path(market_file) if market_file else None
        self.settings = (settings or {}).get("market", {})
        self.g2b_client = g2b_client

    def _records(self) -> dict:
        if not self.market_file or not self.market_file.exists():
            return {}
        with self.market_file.open(encoding="utf-8") as handle:
            return json.load(handle)

    def analyze(self, rfp: RFPAnalysisResult) -> MarketResearchResult:
        if not rfp.market_research_allowed:
            return MarketResearchResult(unknown_items=[
                "시장조사 미실행 사유: 공고문에서 확인할 공고번호 또는 발주기관이 없어, 잘못된 검색어에 의한 부정확한 나라장터 조회를 방지하기 위해 실행하지 않았습니다."
            ])
        if not rfp.quality_gate.passed:
            return MarketResearchResult(unknown_items=["RFP 기본정보 품질 게이트 미통과로 나라장터 API와 시장조사를 실행하지 않았습니다."])
        project_name = rfp.basic_info.project_name.value
        buyer_or_notice = rfp.basic_info.procuring_entity.value or rfp.basic_info.notice_number.value
        if not project_name or not buyer_or_notice:
            return MarketResearchResult(unknown_items=["정상화된 사업명과 발주기관 또는 공고번호가 없어 나라장터 API와 시장조사를 실행하지 않았습니다."])
        raw = self._records()
        today = date.today().isoformat()
        relevant = _keywords(rfp)
        projects: list[SimilarProject] = []
        for item in raw.get("similar_projects", []):
            text = " ".join(str(item.get(key, "")) for key in ("project_name", "buyer", "keywords", "scope"))
            if not (relevant & _keywords_text(text)):
                continue
            url = item.get("source_url")
            if not url:
                continue  # external claim without a link is not reportable evidence
            projects.append(SimilarProject(
                project_name=item.get("project_name", "미확인"), buyer=item.get("buyer"), awardee=item.get("awardee"),
                budget_or_award_amount=item.get("budget_or_award_amount"), source_url=url,
                checked_at=item.get("checked_at", today),
                similarity_rationale=item.get("similarity_rationale", "키워드 기반 1차 유사성. 사람이 사업범위·기간·금액을 비교해야 합니다."),
                comparison_limitations=item.get("comparison_limitations", ["제공된 외부 근거 범위 내 비교"]),
            ))
        observations: list[MarketObservation] = []
        for item in raw.get("market_observations", []):
            if item.get("source_url"):
                observations.append(MarketObservation(statement=item["statement"], source_url=item["source_url"], checked_at=item.get("checked_at", today), limitations=item.get("limitations", [])))
        unknown = list(raw.get("unknown_items", []))
        g2b = self.g2b_client or G2BAwardClient(self.settings)
        if g2b.enabled:
            try:
                records, source_url = g2b.search_service_awards(rfp.project_summary.project_name, rfp.project_summary.buyer)
                observations.append(MarketObservation(
                    statement=f"나라장터 낙찰정보서비스에서 용역 개찰결과 {len(records)}건을 조회했습니다.",
                    source_url=source_url, checked_at=today,
                    limitations=["개찰결과와 최종 계약·낙찰 확정 여부는 별도 확인이 필요합니다.", "API 조회 조건과 기간 내 결과에 한정됩니다."],
                ))
                for record in records:
                    project = _to_similar_project(record, source_url, today, rfp, relevant)
                    if project:
                        projects.append(project)
                for company_term in G2B_COMPANY_SEARCH_TERMS:
                    company_records, company_url = g2b.search_company_awards(company_term)
                    observations.append(MarketObservation(
                        statement=f"나라장터 낙찰정보서비스에서 참여 법인 검색어 '{company_term}'로 {len(company_records)}건을 조회했습니다.",
                        source_url=company_url, checked_at=today,
                        limitations=["검색어 기준의 과거 낙찰·개찰 이력이며, 이번 입찰의 참여·경쟁 여부를 뜻하지 않습니다."],
                    ))
                    for record in company_records:
                        project = _to_similar_project(record, company_url, today, rfp, relevant, company_term)
                        if project:
                            projects.append(project)
            except G2BApiError as exc:
                unknown.append(f"나라장터 낙찰정보 API 조회 실패(미확인): {exc}")
        projects = _deduplicate_projects(projects)
        if not projects:
            unknown.append("신뢰 가능한 URL이 있는 유사 과거사업·낙찰 정보가 제공/확인되지 않았습니다(미확인).")
        if not observations:
            unknown.append("시장·경쟁 참고자료가 확인되지 않았습니다. 과거 낙찰사가 이번 참여사·예상 경쟁사라는 의미는 아닙니다.")
        return MarketResearchResult(similar_projects=projects, market_observations=observations, unknown_items=unknown)


def _keywords(rfp: RFPAnalysisResult) -> set[str]:
    return _keywords_text(" ".join(filter(None, [rfp.project_summary.project_name, rfp.project_summary.buyer, rfp.project_summary.scope] + [x.name for x in rfp.requirements])))


def _keywords_text(text: str) -> set[str]:
    import re
    return {x.lower() for x in re.findall(r"[A-Za-z가-힣0-9]{2,}", text)}


def _first(record: dict, *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _won(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return f"{int(float(value)):,}원"
    except ValueError:
        return value


def _opening_first_bidder(record: dict) -> str | None:
    """Bid-opening records carry the first-ranked bidder as `opengCorpInfo`: "업체명^사업자번호^대표자"."""
    info = _first(record, "opengCorpInfo")
    return info.split("^")[0].strip() or None if info else None


def _similarity(title: str, rfp: RFPAnalysisResult) -> str:
    overlap = sorted(_keywords_text(title) & _keywords(rfp))
    return f"사업명 기준 공통 키워드: {', '.join(overlap) if overlap else '없음'}. 사업범위·금액·기간은 원문으로 추가 비교가 필요합니다."


def _to_similar_project(record: dict, source_url: str, checked_at: str, rfp: RFPAnalysisResult, relevant: set[str], company_term: str | None = None) -> SimilarProject | None:
    title = _first(record, "bidNtceNm", "bidNtceNmCn")
    if not title:
        return None
    overlap = _keywords_text(title) & relevant
    # 법인명 조회 결과도 RFP 주제와 공통 키워드가 있어야 유사사업으로 표시합니다.
    if not overlap:
        return None
    qualifier = f"참여 법인 검색어 '{company_term}' 결과이며, " if company_term else ""
    return SimilarProject(
        project_name=title,
        buyer=_first(record, "dminsttNm", "ntceInsttNm"),
        awardee=_first(record, "bidwinnrNm") or _opening_first_bidder(record),
        budget_or_award_amount=_won(_first(record, "sucsfbidAmt")),
        source_url=source_url, checked_at=checked_at,
        opening_date=_first(record, "opengDt", "rlOpengDt", "bidNtceDt"),
        similarity_rationale=qualifier + _similarity(title, rfp),
        comparison_limitations=["나라장터 개찰결과 기반이며 계약 체결·최종낙찰 확정 여부는 별도 확인이 필요합니다.", "사업범위·요구사항·공동수급 조건은 원문 공고와 대조해야 합니다."],
    )


def _deduplicate_projects(projects: list[SimilarProject]) -> list[SimilarProject]:
    deduplicated: dict[tuple[str, str | None, str | None], SimilarProject] = {}
    for project in projects:
        deduplicated.setdefault((project.project_name, project.buyer, project.opening_date), project)
    return list(deduplicated.values())
