"""남은 업무 5번: A안(낙찰 실시간 재조회)과 B안(T3 색인 낙찰만)을 같은 후보로 비교한다.

    python scripts/compare_market_modes.py [--index-dir outputs/rag] [--limit 10] [--json out.json]

- 후보: 과거 사업 색인에서 공고번호 순으로 낙찰 연결 있는 사업 절반 + 없는 사업 절반(같은 후보로 두 안을 비교하려고 고정).
- B안: 색인 값 그대로(외부 호출 0회). A안: live_awards.refresh_awards(나라장터 낙찰정보서비스, 후보당 1회, 최대 --limit회).
- 결과: 안별 실행시간·API 호출 수·낙찰 연결 수, 후보별 상태(new_award/same_as_index/updated/...), 실패 사유.
- 비용: 공공데이터포털 API는 무료(하루 호출 한도 있음). OpenAI 호출 없음.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from bid_review.rag.live_awards import refresh_awards
from bid_review.settings import PROJECT_ROOT


def pick_candidates(index_dir: Path, limit: int) -> list[dict]:
    projects = json.loads((index_dir / "past_projects.json").read_text(encoding="utf-8"))
    projects.sort(key=lambda p: p["notice_id"])
    awarded = [p for p in projects if p.get("award")][: limit - limit // 2]
    plain = [p for p in projects if not p.get("award")][: limit // 2]
    return [{k: v for k, v in p.items() if k != "profile"} for p in awarded + plain]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index-dir", type=Path, default=PROJECT_ROOT / "outputs/rag")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    candidates = pick_candidates(args.index_dir, args.limit)

    started = time.perf_counter()
    b = {"similar_projects": candidates}  # B안: 색인 그대로
    b_seconds = time.perf_counter() - started

    started = time.perf_counter()
    a = refresh_awards(candidates, max_lookups=args.limit)
    a_seconds = time.perf_counter() - started

    lookup = a["live_award_lookup"]
    report = {
        "candidates": len(candidates),
        "B_index_only": {"seconds": round(b_seconds, 3), "api_calls": 0,
                         "with_award": sum(1 for p in b["similar_projects"] if p.get("award")),
                         "award_freshness": "색인 수집 시점(data/past_rfps/awards.json의 period 끝날)"},
        "A_live_awards": {"seconds": round(a_seconds, 3), "api_calls": lookup["api_calls"], "status": lookup["status"],
                          "error": lookup["error"],
                          "with_award": sum(1 for p in a["similar_projects"] if p.get("award")),
                          "by_status": {s: sum(1 for r in lookup["results"] if r["status"] == s)
                                        for s in dict.fromkeys(r["status"] for r in lookup["results"])},
                          "award_freshness": lookup["checked_at"]},
        "rows": lookup["results"],
    }
    awards_meta = PROJECT_ROOT / "data/past_rfps/awards.json"
    if awards_meta.is_file():
        report["B_index_only"]["awards_period"] = json.loads(awards_meta.read_text(encoding="utf-8")).get("period")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=2))
    for row in report["rows"]:
        print(f"- {row['notice_id']} {str(row.get('title'))[:30]} | 색인 낙찰 {'O' if row['index_award'] else '-'} | A안 {row['status']}"
              f"{' — ' + row['reason'] if row.get('reason') else ''}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
