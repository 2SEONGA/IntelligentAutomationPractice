from __future__ import annotations

import argparse
import json

from bid_review.rag.company_retriever import search_company_evidence


def main() -> None:
    parser = argparse.ArgumentParser(description="출처와 법인 경계를 보존해 회사 역량 근거를 검색합니다.")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--record-type", action="append", dest="record_types")
    parser.add_argument("--match-mode", choices=("all", "any"), default="any")
    args = parser.parse_args()
    results = search_company_evidence(
        args.query,
        limit=args.limit,
        record_types=args.record_types,
        match_mode=args.match_mode,
    )
    for item in results:
        metadata = item.metadata
        source = metadata.get("source", {})
        print(json.dumps({
            "record_id": item.record_id,
            "record_type": metadata.get("record_type"),
            "title": metadata.get("title"),
            "excerpt": metadata.get("excerpt"),
            "source_id": metadata.get("source_id"),
            "source_title": source.get("title"),
            "source_url": source.get("url"),
            "source_section": metadata.get("section"),
            "evidence_level": metadata.get("evidence_level"),
            "entity_boundary_rule": metadata.get("entity_boundary_rule"),
            "verification_required": metadata.get("verification_required"),
            "data_freshness": metadata.get("data_freshness"),
            "reported_progress_percent": metadata.get("reported_progress_percent"),
            "reported_progress_as_of": metadata.get("reported_progress_as_of"),
            "retrieval_notice": metadata.get("retrieval_notice"),
            "rank": item.rank,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
