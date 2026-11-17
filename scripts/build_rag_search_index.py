from __future__ import annotations

import argparse

from bid_review.rag.retriever import build_keyword_index
from bid_review.settings import PROJECT_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="개인정보를 제거한 RAG chunk의 로컬 키워드 검색 인덱스를 만듭니다.")
    parser.add_argument("--chunks", default=str(PROJECT_ROOT / "outputs/rag/chunks.jsonl"))
    parser.add_argument("--database", default=str(PROJECT_ROOT / "outputs/rag/retrieval.sqlite3"))
    args = parser.parse_args()
    count = build_keyword_index(args.chunks, args.database)
    print(f"Indexed {count} page-cited chunks into {args.database}")


if __name__ == "__main__":
    main()
