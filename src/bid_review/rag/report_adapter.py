"""T3 검색 후보를 T4 입력으로 변환한다. 외부 조회나 사실 확정은 하지 않는다."""
from __future__ import annotations

from math import isfinite
from typing import Any
from urllib.parse import urlsplit

from ..models import MarketResearchResult, SimilarProject


def build_market_result(
    projects: list[dict[str, Any]],
    retrieval_status: str,
    *,
    checked_at: str,
) -> MarketResearchResult:
    """checked_at에는 호출자가 이번 검색 실행의 실제 확인 시각을 전달한다."""
    if not checked_at.strip():
        raise ValueError("검색 확인 시각 checked_at이 필요합니다.")

    result = MarketResearchResult()
    status = retrieval_status.strip()
    if not status.startswith("found ") and status != "none_above_threshold":
        result.unknown_items.append(f"T3 검색 미완료 또는 실패: {status or '상태 없음'}")
        return result
    if not projects:
        result.unknown_items.append(
            "이번 검색에서 유사사업 후보가 반환되지 않았습니다. 유사사업이 없다는 뜻은 아닙니다."
        )
        return result

    for index, item in enumerate(projects, start=1):
        label = f"T3 후보 {index}"
        if not isinstance(item, dict):
            result.unknown_items.append(f"{label}: 후보 형식이 올바르지 않습니다.")
            continue
        title = str(item.get("title") or "").strip()
        if item.get("same_project_suspected"):
            # 재공고를 별개의 비교 사업으로 세면 유사사업 근거가 과장된다.
            result.unknown_items.append(f"{label} {title}: 동일 사업·재공고 의심으로 비교 목록에서 제외했습니다.")
            continue

        url = str(item.get("notice_url") or "").strip()
        try:
            parsed = urlsplit(url)
            valid_url = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        except ValueError:
            valid_url = False
        if not title or not valid_url:
            result.unknown_items.append(f"{label}: 사업명 또는 공고 출처 URL이 없어 보고서 후보에서 제외했습니다.")
            continue

        score = item.get("similarity")
        if (isinstance(score, bool) or not isinstance(score, (int, float))
                or not isfinite(score) or not 0 <= score <= 1):
            result.unknown_items.append(f"{label} {title}: 유사도 값이 올바르지 않아 제외했습니다.")
            continue

        limitations = [
            "공고 URL은 사업 출처이며, 본문 인용·유사성·실적 인정은 별도 검토가 필요합니다.",
            "검색 유사도는 회사 수행실적 또는 참가자격 충족을 뜻하지 않습니다.",
            # 기존 색인은 재공고 그룹의 다른 차수 낙찰정보를 합칠 수 있다.
            "낙찰정보의 공고·차수·출처 연결을 별도 검증하지 않아 낙찰자·낙찰금액은 반영하지 않았습니다.",
        ]
        excerpt = str(item.get("profile_excerpt") or "").strip()
        if not excerpt:
            limitations.append("사업 본문 발췌가 없어 내용 비교 근거를 확인해야 합니다.")
            result.unknown_items.append(f"{label} {title}: 사업 본문 발췌 확인 필요")
        notice = str(item.get("interpretation_notice") or "").strip()
        if notice:
            limitations.append(notice)
        rerank = item.get("rerank_score")
        if rerank == 0:  # M48: 빼지 않고 표시만 한다
            limitations.append("AI 재정렬 판정: 분야 다름 — 유사도는 높지만 업무 분야가 달라 참고 가치가 낮을 수 있습니다.")
        rerank_text = (f" AI 재정렬 판정: {item.get('rerank_label')}"
                       + (f"({item.get('rerank_reason')})" if item.get("rerank_reason") else "") + "."
                       if rerank in (0, 1, 2) else "")

        result.similar_projects.append(SimilarProject(
            project_name=title,
            buyer=str(item.get("buyer") or "").strip() or None,
            source_url=url,
            checked_at=checked_at,
            similarity_rationale=(
                f"사업 프로필 유사도 {score:.4f}의 검색 후보입니다." + rerank_text
                + (f" 검색에 사용된 본문 발췌: {excerpt}" if excerpt else "")
            ),
            comparison_limitations=limitations,
        ))

    result.unknown_items.append("유사사업 후보의 원문 범위와 낙찰정보 연결 여부는 확인이 필요합니다.")
    result.unknown_items = list(dict.fromkeys(result.unknown_items))
    return result
