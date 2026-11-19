"""기존 PDF 텍스트 추출(pypdf·pdfplumber)과 OCR 결과를 페이지별로 비교한다.

OCR 적용 범위(config/settings.yaml rfp.ocr.mode: off/fallback/always)를 정하기 위한 근거 자료용.
    python scripts/compare_ocr_extraction.py data/input/*.pdf [--json out.json]
OCR 엔진(pytesseract + tesseract kor)이 없으면 기존 추출 결과와 'OCR 필요' 판정만 출력한다.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

from bid_review import ocr
from bid_review.agent01_rfp import RFPExtractor, _clean
from bid_review.settings import load_settings


def compare(pdf_path: Path, extractor: RFPExtractor) -> list[dict]:
    options = ocr.config(extractor.settings)
    rows: list[dict] = []
    reader = PdfReader(str(pdf_path))
    with pdfplumber.open(str(pdf_path)) as layout_pdf:
        for number, page in enumerate(reader.pages, start=1):
            layout_page = layout_pdf.pages[number - 1]
            simple = _clean(page.extract_text() or "")
            try:
                layout = _clean(layout_page.extract_text(layout=True) or "")
            except Exception:
                layout = ""
            best = layout if len(layout) > len(simple) else simple
            tables = extractor._table_rows(layout_page)
            page_type, _ = extractor._classify_page(number, best, tables)
            reason = extractor._ocr_reason(best, page_type, tables)
            started = time.perf_counter()
            ocr_text = ocr.ocr_page(layout_page, options["lang"], int(options["resolution"]))
            seconds = round(time.perf_counter() - started, 3)
            before = ocr.text_metrics(best)
            after = ocr.text_metrics(ocr_text or "") if ocr_text is not None else None
            adopted, why = ocr.choose(best, ocr_text) if ocr_text is not None else (False, "OCR 엔진 없음 또는 실패")
            rows.append({
                "page": number, "page_type": page_type, "ocr_needed": reason is not None, "ocr_reason": reason,
                "pdf_text": {k: before[k] for k in ("meaningful_chars", "garbled_ratio", "sentence_lines", "clauses", "vat")}
                | {"amounts": len(before["amounts"]), "dates": len(before["dates"])},
                "ocr_text": None if after is None else (
                    {k: after[k] for k in ("meaningful_chars", "garbled_ratio", "sentence_lines", "clauses", "vat")}
                    | {"amounts": len(after["amounts"]), "dates": len(after["dates"])}),
                "ocr_attempted": ocr_text is not None or ocr.engine_available(), "ocr_succeeded": bool(ocr_text),
                "ocr_adopted": adopted, "decision": why, "seconds": seconds,
            })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="기존 PDF 추출과 OCR 결과를 페이지별로 비교합니다.")
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, help="비교 결과를 JSON으로 저장")
    args = parser.parse_args()
    extractor = RFPExtractor(load_settings())
    print(f"OCR 엔진: {'사용 가능' if ocr.engine_available() else '없음 (pytesseract + tesseract kor 설치 필요)'}")
    report = {}
    for pdf in args.pdfs:
        rows = compare(pdf, extractor)
        report[str(pdf)] = rows
        needed = [r["page"] for r in rows if r["ocr_needed"]]
        adopted = [r["page"] for r in rows if r["ocr_adopted"]]
        failed = [r["page"] for r in rows if r["ocr_attempted"] and not r["ocr_succeeded"]]
        print(f"- {pdf.name}: {len(rows)}쪽, OCR 필요 {needed or '없음'}, OCR 채택 {adopted or '없음'}, "
              f"OCR 실패 {failed or '없음'}, OCR 시간 {sum(r['seconds'] for r in rows):.1f}초")
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
