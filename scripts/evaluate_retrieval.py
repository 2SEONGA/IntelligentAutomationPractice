"""키워드 검색(FTS) 정확도 평가: Evidence Recall@K · Precision@K · Citation Correctness · 문서 혼동.

    python scripts/evaluate_retrieval.py [--k 3 5] [--json out.json]

정답 기준(자동 도출, 사람 검토 필요):
  정답 라벨을 사람이 직접 만든 것이 아니라, 항목마다 "실제 조건 문구" 정규식을 정하고(예: 사업금액 = 레이블 + 숫자 금액)
  그 문구를 담은 검색 결과를 관련 있는 근거로 본다. 목차("사업 개요 ····· 3")나 서식 안내는 값이 없어 관련으로 세지 않는다.
  원문 PDF에 그 문구가 아예 없는 문서-항목 쌍은 평가에서 빼고 "정답 없음"으로 따로 센다.
  정규식이 놓치거나 잘못 잡는 경우가 있을 수 있으므로 결과 JSON의 hits를 사람이 확인해야 한다.

질의는 T1.5가 실제로 쓰는 재검색어(verification.AMOUNT/SCHEDULE_FIELD_QUERIES 등)와 같은 말을 쓴다.
검색 범위는 workflow.retrieve_targeted_evidence와 같이 document_id(해당 PDF 한 개)로 좁힌다.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

import pdfplumber

from bid_review.rag.retriever import search_keyword
from bid_review.settings import PROJECT_ROOT

AMOUNT = r"[\d,]{7,}\s*원|\d+(?:\.\d+)?\s*억"
NEEDS: dict[str, dict] = {
    "사업금액": {"queries": ["사업금액", "사업예산", "사업비"],
             "evidence": rf"(?:사업\s*금액|사업\s*예산|사업비|기초\s*금액|추정\s*가격)\s*(?:\([^)]*\))?\s*[:：|]?\s*(?:금\s*)?(?:{AMOUNT})"},
    "VAT 조건": {"queries": ["부가가치세", "부가가치세 포함", "부가가치세 별도"],
               "evidence": rf"(?:{AMOUNT})[^\n]{{0,40}}(?:부가가치세|부가세|VAT)\s*(?:포함|별도|제외)"},
    "계약기간": {"queries": ["계약기간", "사업기간", "수행기간"],
             "evidence": r"(?:계약|사업|과업|수행)\s*기간\s*(?:은|:|：|\|)?[^\n]{0,30}20\d{2}\s*[년.]"},
    "공동도급": {"queries": ["공동수급", "공동도급", "공동수급 허용"],
             "evidence": r"공동\s*(?:수급|도급|계약)[^\n]{0,40}(?:허용|불허|불가|아니함|않음)"},
    "참가자격": {"queries": ["입찰참가자격", "참가자격"],
             "evidence": r"(?:소프트웨어\s*사업자|업종|중소기업|직접생산|등록(?:을|한|된)|제\s*\d+\s*조)"},
}


def _norm(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", text).casefold() if ch.isalnum())


def _pdf_pages(path: Path) -> dict[int, str]:
    pages: dict[int, str] = {}
    with pdfplumber.open(str(path)) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            parts = [page.extract_text() or ""]
            try:
                parts.append(page.extract_text(layout=True) or "")
            except Exception:
                pass
            for table in page.extract_tables() or []:
                parts.extend(" | ".join(str(cell or "") for cell in row) for row in table)
            pages[number] = _norm("\n".join(parts))
    return pages


def _citation_ok(hit, pages: dict[int, str]) -> bool:
    """검색 결과가 가리키는 쪽에 그 원문이 실제로 있는지(줄 단위로 80% 이상 포함)."""
    page_text = pages.get(hit.metadata.get("page"))
    if page_text is None:
        return False
    lines = [_norm(line.replace("표 행:", "")) for line in hit.text.splitlines()]
    lines = [line for line in lines if len(line) >= 4]
    if not lines:
        return True
    return sum(line in page_text for line in lines) / len(lines) >= 0.8


def evaluate(ks: list[int]) -> dict:
    manifest = json.loads((PROJECT_ROOT / "outputs/rag/manifest.json").read_text(encoding="utf-8"))
    sources = [item for item in manifest["sources"] if item.get("document_type") in {"rfp_pdf", "notice_pdf"}]
    max_k = max(ks)
    rows, no_gold, hits_log = [], [], []
    with (PROJECT_ROOT / "outputs/rag/chunks.jsonl").open(encoding="utf-8") as handle:
        all_chunks = [json.loads(line) for line in handle if line.strip()]
    citations = {"checked": 0, "correct": 0}
    for source in sources:
        pages = _pdf_pages(PROJECT_ROOT / source["path"])
        for need, spec in NEEDS.items():
            evidence = re.compile(spec["evidence"], re.I)
            gold_pages = sorted({number for number, text in _pdf_pages_raw(PROJECT_ROOT / source["path"]).items()
                                 if evidence.search(text)})
            if not gold_pages:
                no_gold.append({"document": source["title"], "need": need})
                continue
            # 색인 전체에서 정답 조건을 담은 chunk 수 → 정밀도 상한(min(관련 수, K)/K) 계산용
            relevant_in_index = sum(
                1 for chunk in all_chunks
                if chunk["metadata"].get("document_id") == source["document_id"]
                and chunk["metadata"].get("page") in gold_pages and evidence.search(chunk["text"]))
            for query in spec["queries"]:
                results = search_keyword(query, limit=max_k, document_id=source["document_id"], match_mode="all")
                relevant = [bool(evidence.search(hit.text)) and hit.metadata.get("page") in gold_pages for hit in results]
                for hit in results:
                    citations["checked"] += 1
                    citations["correct"] += _citation_ok(hit, pages)
                row = {"document": source["title"], "document_type": source["document_type"], "need": need,
                       "query": query, "gold_pages": gold_pages, "relevant_in_index": relevant_in_index}
                for k in ks:
                    top = relevant[:k]
                    row[f"recall@{k}"] = 1.0 if any(top) else 0.0
                    row[f"precision@{k}"] = sum(top) / k
                    row[f"returned@{k}"] = len(top)
                    row[f"max_precision@{k}"] = min(relevant_in_index, k) / k
                rows.append(row)
                hits_log.append({**row, "hits": [{"page": hit.metadata.get("page"), "page_type": hit.metadata.get("page_type"),
                                                  "relevant": rel, "role": hit.metadata.get("retrieval_role"), "text": hit.text[:600]}
                                                 for hit, rel in zip(results, relevant)]})
    summary = {"chunks_in_index": len(all_chunks), "documents": len(sources), "needs_evaluated": len({(r["document"], r["need"]) for r in rows}),
               "queries_evaluated": len(rows), "no_gold": no_gold,
               "citation_correctness": round(citations["correct"] / citations["checked"], 3) if citations["checked"] else None,
               "citations_checked": citations["checked"]}
    for k in ks:
        summary[f"recall@{k}"] = round(sum(r[f"recall@{k}"] for r in rows) / len(rows), 3) if rows else None
        summary[f"precision@{k}"] = round(sum(r[f"precision@{k}"] for r in rows) / len(rows), 3) if rows else None
        summary[f"max_precision@{k}"] = round(sum(r[f"max_precision@{k}"] for r in rows) / len(rows), 3) if rows else None
    by_need = {}
    for need in NEEDS:
        subset = [r for r in rows if r["need"] == need]
        if subset:
            by_need[need] = {f"recall@{k}": round(sum(r[f"recall@{k}"] for r in subset) / len(subset), 3) for k in ks} | {
                f"precision@{k}": round(sum(r[f"precision@{k}"] for r in subset) / len(subset), 3) for k in ks} | {"queries": len(subset)}
    summary["by_need"] = by_need
    summary["version_mixing"] = _version_mixing(sources)
    return {"summary": summary, "rows": hits_log}


_RAW_CACHE: dict[Path, dict[int, str]] = {}


def _pdf_pages_raw(path: Path) -> dict[int, str]:
    """정답 위치 판단용 원문(공백 정리만 한 페이지 텍스트)."""
    if path not in _RAW_CACHE:
        pages = {}
        with pdfplumber.open(str(path)) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                for table in page.extract_tables() or []:
                    text += "\n" + "\n".join(" | ".join(str(cell or "") for cell in row) for row in table)
                pages[number] = re.sub(r"[ \t]+", " ", text)
        _RAW_CACHE[path] = pages
    return _RAW_CACHE[path]


def _version_mixing(sources: list[dict]) -> dict:
    """같은 공고번호로 검색했을 때 RFP·공고문·API 레코드가 한 결과에 섞이는지(문서 유형을 지정한 경우와 비교)."""
    report = {}
    for notice_no in sorted({s.get("related_notice_no") for s in sources if s.get("related_notice_no")}):
        mixed = search_keyword("사업", limit=10, notice_no=notice_no, match_mode="any")
        types = sorted({hit.metadata.get("document_type") for hit in mixed})
        entry = {"types_without_filter": types}
        try:
            only = search_keyword("사업", limit=10, notice_no=notice_no, match_mode="any", document_type="notice_pdf")
            entry["types_with_document_type_filter"] = sorted({hit.metadata.get("document_type") for hit in only})
        except TypeError:
            entry["types_with_document_type_filter"] = "unsupported"
        report[notice_no] = entry
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 키워드 검색 정확도를 평가합니다(정답: 원문 조건 문구 정규식, 사람 검토 필요).")
    parser.add_argument("--k", type=int, nargs="+", default=[3, 5])
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    report = evaluate(args.k)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
