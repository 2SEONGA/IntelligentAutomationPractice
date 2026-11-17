from __future__ import annotations

import argparse
import json
from pathlib import Path

from bid_review.rag.corpus import write_local_corpus
from bid_review.settings import PROJECT_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="원본 RFP PDF에서 개인정보를 제외한 페이지 인용형 RAG 코퍼스를 생성합니다.")
    parser.add_argument("--input-dir", type=Path, default=PROJECT_ROOT / "data/input")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "outputs/rag")
    args = parser.parse_args()
    manifest = write_local_corpus(args.output_dir, args.input_dir)
    print(json.dumps({
        "source_count": manifest["source_count"],
        "chunk_count": manifest["chunk_count"],
        "privacy_rows_removed": manifest["privacy_rows_removed"],
        "errors": manifest["errors"],
        "manifest": str(args.output_dir / "manifest.json"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
