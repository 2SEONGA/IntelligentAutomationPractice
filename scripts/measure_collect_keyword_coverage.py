"""공고 수집 키워드가 놓치는 공고 측정 (M26). 읽기 전용: outputs/notices/notices.json에 쓰지 않는다.

같은 기간을 ① 키워드 없이 전체 조회, ② 수집 키워드별 조회로 받아 현재 규칙 필터를 똑같이 적용하고,
규칙 통과(검토 대상·확인 필요) 공고 중 키워드 조회에 없던 공고를 센다.

.venv/bin/python scripts/measure_collect_keyword_coverage.py [--days 30] [--keywords 정보시스템 차세대 ...] [--out 경로.json]
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta

import requests

from bid_review.notice_filter import judge
from bid_review.settings import PROJECT_ROOT
from bid_review.web import criteria, notices, runs


def fetch(session, keyword: str, days: int, today) -> dict[str, dict]:
    got, end, start = {}, today, today - timedelta(days=days)
    while end >= start:
        window_start = max(start, end - timedelta(days=notices.MAX_WINDOW_DAYS - 1))
        for item in notices._fetch(session, keyword, window_start, end):
            got[notices.notice_id(item)] = item
        end = window_start - timedelta(days=1)
    return got


def summarize(rows: list[dict]) -> dict:
    passed = [r for r in rows if r["verdict"] != "제외"]
    unique = lambda rs: len({(r["name"].strip(), r["inst"]) for r in rs})  # noqa: E731 — 같은 사업의 분할·재게시 묶음
    hit = [r for r in passed if r["in_kw"]]
    return {"collected_all": len(rows), "collected_kw": sum(r["in_kw"] for r in rows),
            "passed_rows": len(passed), "passed_unique": unique(passed),
            "passed_kw_unique": unique(hit), "missed_unique": unique(passed) - unique(hit)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--keywords", nargs="*", help="기본값: 현재 판단 기준의 collect_keywords")
    parser.add_argument("--out", default=str(PROJECT_ROOT / "outputs/notices/keyword_coverage.json"))
    args = parser.parse_args()
    now = datetime.now(runs.KST).replace(tzinfo=None)
    rules = criteria.resolve()["notice_filter"]
    keywords = args.keywords or rules["collect_keywords"]
    session = requests.Session()
    everything = fetch(session, "", args.days, now.date())
    by_keyword: dict[str, dict] = {}
    for keyword in keywords:
        by_keyword.update(fetch(session, keyword, args.days, now.date()))
    rows = []
    for nid, item in everything.items():
        rows.append({"id": nid, "name": item.get("bidNtceNm") or "", "inst": item.get("dminsttNm") or item.get("ntceInsttNm") or "",
                     "cls": item.get("pubPrcrmntLrgClsfcNm"), "amt": item.get("asignBdgtAmt") or item.get("presmptPrce"),
                     "contract": f'{item.get("cntrctCnclsMthdNm")}/{item.get("sucsfbidMthdNm")}',
                     "verdict": judge(item, rules, now)["verdict"], "in_kw": nid in by_keyword})
    result = {"measured_at": now.isoformat(timespec="seconds"), "days": args.days,
              "window": [str(now.date() - timedelta(days=args.days)), str(now.date())], "keywords": keywords,
              "kw_not_in_all": len(set(by_keyword) - set(everything)), "summary": summarize(rows), "rows": rows}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False)
    with open(args.out.removesuffix(".json") + "_통과공고.csv", "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["id"])
        writer.writeheader()
        writer.writerows(r for r in rows if r["verdict"] != "제외")
    print(json.dumps({k: result[k] for k in ("window", "keywords", "kw_not_in_all", "summary")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
