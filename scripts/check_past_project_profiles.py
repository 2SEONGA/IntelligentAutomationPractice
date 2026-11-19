"""T3 과거 사업 프로필 품질 점검(읽기 전용): 목차·계약 조항·너무 짧은 본문으로 보이는 사업을 표시한다.

    python scripts/check_past_project_profiles.py [--json outputs/eval/past_profile_quality.json]

data/past_rfps 원본과 과거 사업 색인은 고치지 않는다. 표시된 사업은 T3 데이터 정제 담당자가 확인한다.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from bid_review.rag.corpus import text_quality_flags
from bid_review.rag.past_projects import project_profile, rfp_file
from bid_review.settings import PROJECT_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="T3 과거 사업 프로필 품질 점검(읽기 전용)")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    rows = []
    for folder in sorted((PROJECT_ROOT / "data/past_rfps").iterdir()):
        meta_path, source = folder / "meta.json", rfp_file(folder)
        if not meta_path.is_file() or source is None:
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        try:
            profile = project_profile(source, meta.get("bidNtceNm", ""), meta.get("dminsttNm", ""))
        except Exception as exc:  # 손상 파일도 목록에 남긴다
            rows.append({"notice_id": folder.name, "error": type(exc).__name__})
            continue
        body = re.sub(r"^(사업명|수요기관):.*$", "", profile, flags=re.M)
        rows.append({"notice_id": folder.name, "title": meta.get("bidNtceNm"), "file": source.name,
                     **text_quality_flags(body), "excerpt": re.sub(r"\s+", " ", body)[:160]})
    flagged = [row for row in rows if row.get("needs_review") or row.get("error")]
    summary = {"profiles": len(rows), "needs_review": len(flagged),
               "toc_like": sum(bool(r.get("toc_like")) for r in rows),
               "contract_clause_like": sum(bool(r.get("contract_clause_like")) for r in rows),
               "too_short": sum(bool(r.get("too_short")) for r in rows),
               "errors": sum(bool(r.get("error")) for r in rows)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for row in flagged[:40]:
        print(f"- {row['notice_id']} {row.get('title', '')[:40]} "
              f"{[k for k in ('toc_like', 'contract_clause_like', 'too_short', 'error') if row.get(k)]}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
