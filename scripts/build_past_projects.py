"""Agent 3 (M23): collect recent ICT 용역 RFPs from 나라장터 and build the similar-project index.

.venv/bin/python scripts/build_past_projects.py [--days N] [--max N] [--index-only]
기본 범위는 config/settings.yaml의 past_projects. 할당량이 끝나면 멈추고, 다음 날 다시 실행하면 이어서 받는다.
"""
from __future__ import annotations

import argparse
import json

import yaml
from dotenv import load_dotenv

from bid_review.rag.past_projects import build_past_project_index, collect_past_projects
from bid_review.settings import PROJECT_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, help="기본: settings.yaml past_projects.days")
    parser.add_argument("--max", type=int, help="기본: settings.yaml past_projects.max_projects")
    parser.add_argument("--index-only", action="store_true", help="수집 없이 data/past_rfps로 색인만 다시 만든다")
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    if not args.index_only:
        current = {str(case["notice_no"]) for case in yaml.safe_load(
            (PROJECT_ROOT / "config/rag_sources.yaml").read_text(encoding="utf-8"))["cases"]}
        print(json.dumps(collect_past_projects(args.days, args.max, exclude=current), ensure_ascii=False))
    print(json.dumps(build_past_project_index(), ensure_ascii=False))


if __name__ == "__main__":
    main()
