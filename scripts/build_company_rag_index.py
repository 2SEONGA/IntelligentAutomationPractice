from __future__ import annotations

import argparse

from bid_review.rag.company_retriever import build_company_index
from bid_review.settings import PROJECT_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="아이티센엔텍 공개 역량 근거의 로컬 FTS 검색 인덱스를 만듭니다.")
    parser.add_argument("--source", default=str(PROJECT_ROOT / "data/company/company"))
    parser.add_argument("--database", default=str(PROJECT_ROOT / "outputs/rag/company_retrieval.sqlite3"))
    args = parser.parse_args()
    count = build_company_index(args.source, args.database)
    print(f"Indexed {count} company evidence records into {args.database}")


if __name__ == "__main__":
    main()
