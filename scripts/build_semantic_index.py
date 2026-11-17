"""Embed outputs/rag/chunks.jsonl with the OpenAI embeddings API (M4). Rerun after build_rag_corpus.py."""
from __future__ import annotations

import json

from bid_review.rag.semantic import build_semantic_index


def main() -> None:
    print(json.dumps(build_semantic_index(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
