"""T3 임계값 재평가용 판정표 만들기(남은 업무 3번).

    python scripts/build_t3_threshold_sheet.py

- 질의: 시연 RFP 3건(config/rag_sources.yaml)의 project_profile. 사업명·수요기관은 실제 T3처럼 나라장터 공고 값을 쓴다.
- 후보: 최종 과거 사업 색인에서 유사도 0.60 이상 상위 10건(자기 공고번호 제외 — 실제 T3와 같음).
- 출력:
  - docs/final_project/회의_개선점/261118/T3_임계값_재평가_판정표.md  (사람이 '판정' 열에 관련/부분/무관을 적는다)
  - outputs/eval/t3_threshold_items.json  (후보 ID·점수·근거; evaluate_thresholds.py --t3가 읽는다)
- 판정은 비워 둔다. AI가 관련/무관을 정하지 않는다.
- 비용: 나라장터 공고 조회(무료) 사례당 4회, OpenAI 질의 임베딩 3회(text-embedding-3-small, 1원 미만).
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import yaml

from bid_review.rag import past_projects as pp
from bid_review.settings import PROJECT_ROOT
from bid_review.web import notices

SHEET = PROJECT_ROOT / "docs/final_project/회의_개선점/261118/T3_임계값_재평가_판정표.md"
ITEMS = PROJECT_ROOT / "outputs/eval/t3_threshold_items.json"
POOL_MIN, POOL_SIZE = 0.60, 10
THRESHOLDS = (0.70, 0.80, 0.90)


def demo_cases() -> list[dict]:
    cases = yaml.safe_load((PROJECT_ROOT / "config/rag_sources.yaml").read_text(encoding="utf-8"))["cases"]
    inputs = sorted((PROJECT_ROOT / "data/input").glob("*.pdf"))
    out = []
    for case in cases:
        alias = case["title_alias"].replace(" ", "")
        pdf = next(p for p in inputs if alias[:6] in p.stem.replace(" ", "") or alias in p.stem.replace(" ", ""))
        out.append({**case, "pdf": pdf})
    return out


def collect() -> list[dict]:
    items: list[dict] = []
    saved = pp.MIN_PROJECT_SCORE
    pp.MIN_PROJECT_SCORE = POOL_MIN  # 판정 후보 풀만 넓힌다(운영 값은 바꾸지 않음)
    try:
        for case in demo_cases():
            lookup = notices.fetch_notice_by_number(case["notice_no"], case["notice_order"]) or {}
            notice = lookup.get("notice") or {}
            title = notice.get("bidNtceNm") or case["title_alias"]
            buyer = notice.get("dminsttNm") or ""
            query = pp.project_profile(case["pdf"], title, buyer)
            results = pp.search_similar_projects(query, limit=POOL_SIZE, exclude_notice_no=case["notice_no"], query_title=title)
            for rank, row in enumerate(results, start=1):
                excerpt = row["profile_excerpt"].split("\n", 2)[-1]
                items.append({"id": f"T3-{len(items) + 1:03d}", "case": case["title_alias"], "case_notice_no": case["notice_no"],
                              "rank": rank, "candidate_notice_id": row["notice_id"], "title": row["title"], "buyer": row["buyer"],
                              "similarity": row["similarity"], "award": bool(row.get("award")),
                              "same_project_suspected": row["same_project_suspected"],
                              "evidence_excerpt": " ".join(excerpt.split())[:160]})
    finally:
        pp.MIN_PROJECT_SCORE = saved
    return items


def write_sheet(items: list[dict], index_meta: dict) -> None:
    head = [
        "# T3 임계값 재평가용 판정표",
        "",
        f"- 생성: {datetime.now():%Y-%m-%d %H:%M}, `scripts/build_t3_threshold_sheet.py`",
        f"- 색인: 과거 사업 {index_meta.get('count')}건, 프로필 규칙 v{index_meta.get('profile_version')}, "
        f"모델 {index_meta.get('model')}, 색인 시각 {index_meta.get('built_at')}",
        f"- 질의: 시연 RFP 3건의 `project_profile`(사업명·수요기관은 나라장터 공고 값), 유사도 {POOL_MIN:.2f} 이상 최대 {POOL_SIZE}건, 자기 공고 제외",
        f"- 비교 임계값: {', '.join(f'{t:.2f}' for t in THRESHOLDS)} (열 'O' = 그 임계값이면 T3 결과에 나옴)",
        "- **판정 열은 사람이 적는다.** `관련` / `부분`(일부 업무만 겹침) / `무관`. AI는 판정하지 않았다.",
        "- 판정 후: `python scripts/evaluate_thresholds.py --t3 " + str(SHEET.relative_to(PROJECT_ROOT)).replace("\\", "/") + "`",
        "- 원문 근거: 후보 사업 프로필(색인에 들어간 본문) 앞부분. 전체는 `outputs/rag/past_projects.json`의 `profile`.",
        "",
        "| ID | 시연 공고 | 순위 | 후보 사업 (공고번호) | 수요기관 | 유사도 | 0.70 | 0.80 | 0.90 | 원문 근거(프로필 앞부분) | 판정 |",
        "|---|---|---:|---|---|---:|:---:|:---:|:---:|---|---|",
    ]
    rows = []
    for item in items:
        marks = ["O" if item["similarity"] >= t else "" for t in THRESHOLDS]
        rows.append(f"| {item['id']} | {item['case']} | {item['rank']} | {item['title']} ({item['candidate_notice_id']}) | "
                    f"{item['buyer']} | {item['similarity']:.3f} | {' | '.join(marks)} | "
                    f"{item['evidence_excerpt'].replace('|', '/')} |  |")
    SHEET.write_text("\n".join(head + rows) + "\n", encoding="utf-8")


def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    items = collect()
    index_meta = json.loads((pp.INDEX_DIR / "past_projects_meta.json").read_text(encoding="utf-8"))
    ITEMS.parent.mkdir(parents=True, exist_ok=True)
    ITEMS.write_text(json.dumps({"index": index_meta, "pool_min": POOL_MIN, "pool_size": POOL_SIZE,
                                 "thresholds": THRESHOLDS, "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    write_sheet(items, index_meta)
    for case in dict.fromkeys(item["case"] for item in items):
        scores = [item["similarity"] for item in items if item["case"] == case]
        print(case, len(scores), "건", " / ".join(f"{t:.2f}이상 {sum(s >= t for s in scores)}" for t in THRESHOLDS))
    print("판정표:", SHEET.relative_to(PROJECT_ROOT))


if __name__ == "__main__":
    main()
