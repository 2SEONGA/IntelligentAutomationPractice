from __future__ import annotations

import argparse
import json

from bid_review.rag.retriever import search_keyword
from bid_review.rag.semantic import search_semantic


def main() -> None:
    parser = argparse.ArgumentParser(description="원문 페이지 인용을 포함해 로컬 RAG 근거를 검색합니다.")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--document-id")
    parser.add_argument("--notice-no")
    parser.add_argument("--match-mode", choices=("all", "any"), default="all")
    parser.add_argument("--mode", choices=("keyword", "semantic"), default="keyword",
                        help="keyword: FTS5 원문 단어 검색, semantic: OpenAI 임베딩 유사도 검색")
    args = parser.parse_args()
    scope = {"limit": args.limit, "document_id": args.document_id, "notice_no": args.notice_no}
    if args.mode == "semantic":
        results = search_semantic(args.query, **scope)
    else:
        results = search_keyword(args.query, match_mode=args.match_mode, **scope)
    for item in results:
        print(json.dumps({
            "rank": item.rank,
            "text": item.text,
            "document": item.metadata["document_title"],
            "page": item.metadata["page"],
            "section": item.metadata["section"],
            "document_type": item.metadata["document_type"],
            "field_name": item.metadata.get("field_name"),
            "source_url": item.metadata.get("source_url"),
            "notice_no": item.metadata.get("related_notice_no"),
            "semantic_score": item.metadata.get("semantic_score"),
            "chunk_id": item.chunk_id,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
