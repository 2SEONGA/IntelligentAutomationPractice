"""남은 업무 3번 보조 실험: T3 판정 풀(시연 3건 × 상위 10건)에 LLM 재정렬(Re-ranker)을 붙여 유사도 임계값과 비교한다.

    python scripts/rerank_t3_candidates.py [--model gpt-4o-mini]

- 입력: outputs/eval/t3_threshold_items.json(판정표 후보), outputs/rag/past_projects.json(후보 프로필),
  판정표의 판정 열(정답). 질의 프로필은 판정표를 만들 때와 같이 다시 만든다(나라장터 조회 무료).
- LLM에게 두 사업 프로필을 주고 관련도 0(무관)·1(부분)·2(관련)를 JSON으로 받는다. temperature 0.
- 비교: (a) 유사도 ≥ 0.70, (b) 재정렬 ≥ 1, (c) 유사도 ≥ 0.70 그리고 재정렬 ≥ 1 — 판정표 기준 정밀도·재현율·F1.
- 비용: 후보 30건 × 1회(gpt-4o-mini, 1회 약 2천 토큰) → 1센트 안팎.
- 주의: 판정표 정답이 AI 대리 판정이면 같은 계열 모델끼리 비교하는 셈이라 재정렬 성능이 부풀려질 수 있다. 사람 판정으로 다시 확인할 것.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import runpy

from bid_review.rag import past_projects as pp
from bid_review.settings import PROJECT_ROOT
from bid_review.web import notices

ITEMS = PROJECT_ROOT / "outputs/eval/t3_threshold_items.json"
SHEET = PROJECT_ROOT / "docs/final_project/회의_개선점/261118/T3_임계값_재평가_판정표.md"
OUT = PROJECT_ROOT / "outputs/eval/t3_rerank.json"
PROMPT = """두 공공 SI 사업이 '유사 과거 사업'으로 참고할 만한지 판정하세요.
기준: 2 = 관련(업무 분야와 주요 과업이 비슷함), 1 = 부분(일부 업무만 겹침), 0 = 무관(분야가 다름).
기관 유형(대학·병원·공기업)이나 'AI·데이터·플랫폼' 같은 일반 단어가 겹치는 것만으로는 관련으로 보지 마세요.
JSON으로만 답하세요: {{"score": 0|1|2, "reason": "한 문장"}}

[검토 중인 사업]
{query}

[과거 사업 후보]
{candidate}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model")
    args = parser.parse_args()
    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv(PROJECT_ROOT / ".env")
    model = args.model or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
    client = OpenAI()
    payload = json.loads(ITEMS.read_text(encoding="utf-8"))
    items = payload["items"]
    profiles = {p["notice_id"]: p["profile"] for p in json.loads((pp.INDEX_DIR / "past_projects.json").read_text(encoding="utf-8"))}
    sheet = runpy.run_path(str(PROJECT_ROOT / "scripts/evaluate_thresholds.py"))
    labels = sheet["parse_t3_sheet"](SHEET.read_text(encoding="utf-8"))

    build = runpy.run_path(str(PROJECT_ROOT / "scripts/build_t3_threshold_sheet.py"))
    queries = {}
    for case in build["demo_cases"]():
        notice = (notices.fetch_notice_by_number(case["notice_no"], case["notice_order"]) or {}).get("notice") or {}
        queries[case["title_alias"]] = pp.project_profile(case["pdf"], notice.get("bidNtceNm") or case["title_alias"],
                                                          notice.get("dminsttNm") or "")
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    for item in items:
        response = client.chat.completions.create(
            model=model, temperature=0, response_format={"type": "json_object"},
            messages=[{"role": "user", "content": PROMPT.format(query=queries[item["case"]][:2500],
                                                                 candidate=profiles[item["candidate_notice_id"]][:2500])}])
        usage["prompt_tokens"] += response.usage.prompt_tokens
        usage["completion_tokens"] += response.usage.completion_tokens
        answer = json.loads(response.choices[0].message.content)
        item["rerank_score"], item["rerank_reason"] = int(answer.get("score", 0)), str(answer.get("reason", ""))[:200]

    def metrics(selected) -> dict:
        kept = [i for i in items if selected(i)]
        relevant = [i for i in items if labels.get(i["id"]) in ("관련", "부분")]
        hits = [i for i in kept if labels.get(i["id"]) in ("관련", "부분")]
        p = len(hits) / len(kept) if kept else None
        r = len(hits) / len(relevant) if relevant else None
        f1 = 2 * p * r / (p + r) if p and r else 0.0
        return {"kept": len(kept), "precision": None if p is None else round(p, 3), "recall": None if r is None else round(r, 3),
                "f1": round(f1, 3), "irrelevant_included": [f"{i['id']} {i['title'][:25]}" for i in kept if labels.get(i["id"]) == "무관"],
                "missed_relevant": [f"{i['id']} {i['title'][:25]}" for i in relevant if i not in kept]}
    rules = {"similarity>=0.70": lambda i: i["similarity"] >= 0.70,
             "rerank>=1": lambda i: i["rerank_score"] >= 1,
             "similarity>=0.70 & rerank>=1": lambda i: i["similarity"] >= 0.70 and i["rerank_score"] >= 1,
             "rerank>=2": lambda i: i["rerank_score"] >= 2}
    report = {"model": model, "usage": usage, "label_source": "판정표 판정 열", "rules": {k: metrics(v) for k, v in rules.items()},
              "agreement_with_labels": sum(1 for i in items if {"관련": 2, "부분": 1, "무관": 0}.get(labels.get(i["id"])) == i["rerank_score"]),
              "items": [{k: i[k] for k in ("id", "case", "title", "similarity", "rerank_score", "rerank_reason")} | {"label": labels.get(i["id"])}
                        for i in items]}
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "items"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
