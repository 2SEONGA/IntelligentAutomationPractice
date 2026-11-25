"""문서 처리 보완(남은 업무 1): 겹친 글자 거르기 · 별첨/별표/양식 서식 쪽 · HWP·HWPX 공고문.

`python tests/test_document_processing.py` (네트워크 없음).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from bid_review.agent01_rfp import RFPExtractor  # noqa: E402
from bid_review.rag.past_projects import _rfp_pages  # noqa: E402
from bid_review.settings import load_settings  # noqa: E402
from bid_review.web import jobs, notices, runs  # noqa: E402
from test_hwp_text import make_hwp, make_hwpx  # noqa: E402

EXTRACTOR = RFPExtractor(load_settings())
HWPX_MAGIC, HWP_MAGIC = b"PK\x03\x04", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def make_overprinted_pdf(path: Path, words: list[str], shift: float = 0.3) -> None:
    """굵은 글씨처럼 같은 줄을 0.3pt 비켜 두 번 찍은 최소 PDF(Helvetica)."""
    lines = []
    for index, word in enumerate(words):
        y = 720 - 20 * index
        for dx in (0, shift):
            lines.append(f"BT /F1 12 Tf {72 + dx} {y} Td ({word}) Tj ET")
    stream = "\n".join(lines).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    path.write_bytes(out)


def test_overprinted_chars_are_read_once() -> None:
    """겹쳐 찍은 글자는 T1(pypdf·pdfplumber 모두)과 T3 추출에서 한 번만 읽는다."""
    pdf = Path(tempfile.mkdtemp()) / "bold.pdf"
    make_overprinted_pdf(pdf, ["Requirement", "Budget 100"])
    pages, _, analyses, _ = EXTRACTOR._extract_pages(pdf)
    assert "Requirement" in pages[0].text and "RReeqq" not in pages[0].text, pages[0].text
    assert "Budget 100" in pages[0].text, pages[0].text
    assert "pdfplumber_dedupe_chars" in analyses[0].extraction_methods
    t3 = _rfp_pages(pdf, 5)[0]
    assert "Requirement" in t3 and "RReeqq" not in t3, t3


def test_past_rfps_with_bold_overprint_read_normally() -> None:
    """과거 RFP 2건(굵은 글씨 겹침): 표지가 '한한국국수수출출입입은은행행'이 아니라 정상 문장으로 나온다."""
    kexim = _rfp_pages(ROOT / "data/past_rfps/R26BK01550694-000/rfp.pdf", 1)[0]
    assert "한국수출입은행" in kexim and "한한국국" not in kexim and "구축" in kexim, kexim[:200]
    other = "\n".join(_rfp_pages(ROOT / "data/past_rfps/R26BK01693931-000/rfp.pdf", 3))
    assert other.strip() and "요요구구" not in other and "사사업업" not in other, other[:300]


def test_appendix_labels_only_at_page_top() -> None:
    """별첨·별표·양식은 쪽 머리 표제일 때만 서식 쪽. 본문 인용("[별첨]의 평가기준")은 본문 그대로."""
    classify = EXTRACTOR._classify_page
    for head in ("<별표 2> 보안 위약금 부과 기준", "[별첨 1] 제안서 평가기준", "【양식 3】 참여인력 현황", "별표 4. 차등점수"):
        assert classify(5, f"{head}\n1. 위규 수준별로 차등 부과", [])[0] == "appendix_page", head
    for body in ("나. 평가항목은 [별첨]의 “제안서 평가기준 및 작성양식”에 따른다",
                 "제안서는 정해진 양식에 따라 작성\n별첨으로 제출하여야 함",
                 "1. 사업 개요\n가. 사업명\n나. 사업기간\n다. 산정 기준은 [별표1] 참고"):
        assert classify(5, body, [])[0] == "body_page", body
    assert classify(5, "<별표 1> 처리기준", ["a | b", "c | d", "e | f"])[0] == "table_heavy_page", "표 쪽은 표 행으로 읽는다"
    assert classify(5, "[별지 제1호 서식] 확약서", [])[0] == "appendix_page", "기존 표식 유지"


def test_short_cover_is_not_ocr_but_empty_cover_is() -> None:
    """표지는 원래 짧다(통합정보 표지 38자). 글자가 하나도 없는 표지(스캔본)만 OCR 대상."""
    assert EXTRACTOR._ocr_reason("통합정보시스템 고도화 구축 제안요청서 2026. 09", "cover_page", []) is None
    assert EXTRACTOR._ocr_reason("", "cover_page", []) is not None
    assert EXTRACTOR._ocr_reason("짧은 본문", "body_page", []) is not None, "본문은 기존 기준(40자) 그대로"


def test_hwp_notice_is_analyzed() -> None:
    """HWP·HWPX 공고문도 T1 공고문 분석(document_kind=notice_pdf)에 들어간다."""
    work = Path(tempfile.mkdtemp())
    make_hwpx(work / "notice.hwpx")
    make_hwp(work / "notice.hwp")
    for path in (work / "notice.hwpx", work / "notice.hwp"):
        result = EXTRACTOR.analyze(path, document_kind="notice_pdf")
        assert result.analysis_scope == "notice_only"
        assert [a.extraction_methods for a in result.page_analysis] == [["hwp_text"]], path.name
        assert not result.needs_ocr, "HWP는 OCR 판정 대상이 아님"


def test_hwpx_contract_notice_fields() -> None:
    """실제 HWPX 수의계약 공고(R26BK01847617) 형식: '계 약 명'·'추정금액' 표제, 첫 50줄을 표지로 보지 않음."""
    import io
    import zipfile
    lines = ["수의계약 공고", "1. 계 약 명 : 27년 ADMS 플랫폼 DB 이중화 SW 위탁 유지보수",
             "2. 계약기간 : 2027.01.01 ~ 2027.12.31.", "3. 추정금액 : 48,305,939원(부가세 포함)"]
    paragraphs = "".join(f"<hp:p><hp:run><hp:t>{line}</hp:t></hp:run></hp:p>" for line in lines)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/section0.xml", '<?xml version="1.0"?><hs:sec xmlns:hs="x" '
                         f'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">{paragraphs}</hs:sec>')
    path = Path(tempfile.mkdtemp()) / "notice.hwpx"
    path.write_bytes(buffer.getvalue())
    result = EXTRACTOR.analyze(path, document_kind="notice_pdf")
    assert result.page_analysis[0].page_type != "cover_page"
    assert result.basic_info.project_name.value == "27년 ADMS 플랫폼 DB 이중화 SW 위탁 유지보수", result.basic_info.project_name
    assert result.basic_info.budget_krw.value == 48_305_939
    assert result.basic_info.contract_period.value == "2027.01.01 ~ 2027.12.31."


def test_notice_download_accepts_hwp_and_prefers_pdf() -> None:
    assert notices.classify(["(공고문)A.hwp", "입찰공고서.hwpx", "제안요청서.hwp"]) == {
        "(공고문)A.hwp": "notice", "입찰공고서.hwpx": "notice", "제안요청서.hwp": "other"}

    class Session:
        def __init__(self, files):
            self.files = files

        def get(self, url, **kwargs):
            from test_notices import Response
            if url not in self.files:
                raise notices.requests.ConnectionError()
            return Response(content=self.files[url])

    hwpx = HWPX_MAGIC + b"notice"
    entry = {"attachments": [{"name": "(공고문)A.hwpx", "url": "https://www.g2b.go.kr/f/x", "kind": "notice"},
                             {"name": "(공고문)A.pdf", "url": "https://www.g2b.go.kr/f/p", "kind": "notice"}]}
    saved = notices.load_notices
    notices.load_notices = lambda: {"notices": {"N-000": entry}}
    try:
        both = Path(tempfile.mkdtemp())
        result = notices.fetch_attachments("N-000", both, session=Session({"https://www.g2b.go.kr/f/x": hwpx,
                                                                         "https://www.g2b.go.kr/f/p": b"%PDF-1.4"}))
        assert result["notice"] and result["notice_attachment"]["name"] == "(공고문)A.pdf", "PDF가 있으면 PDF"
        assert runs.notice_file(both) == both / "notice.pdf"
        only_hwpx = Path(tempfile.mkdtemp())
        result = notices.fetch_attachments("N-000", only_hwpx, session=Session({"https://www.g2b.go.kr/f/x": hwpx}))
        assert result["notice"] and result["skipped"] == ["(공고문)A.pdf"], result
        assert (only_hwpx / "notice.hwpx").read_bytes() == hwpx and runs.notice_file(only_hwpx) == only_hwpx / "notice.hwpx"
        broken = Path(tempfile.mkdtemp())
        result = notices.fetch_attachments("N-000", broken, session=Session({"https://www.g2b.go.kr/f/x": b"<html>"}))
        assert not result["notice"] and runs.notice_file(broken) is None, "형식이 안 맞으면 저장하지 않음"
    finally:
        notices.load_notices = saved

    command = jobs.build_command(only_hwpx, {"notice": True})
    assert command[command.index("--notice-pdf") + 1] == str(only_hwpx / "notice.hwpx"), "HWPX 공고문이 실행에 넘어감"


def test_hwp_notice_byte_comparison() -> None:
    """저장한 HWP·HWPX 공고문을 나라장터 첨부와 바이트 대조한다. 같은 형식의 첨부를 고른다."""
    from test_notices import Response

    class Session:
        def __init__(self, files):
            self.files = files

        def get(self, url, **kwargs):
            return Response(content=self.files[url])

    hwp = HWP_MAGIC + b"notice body"
    attachments = [{"name": "(공고문)A.pdf", "url": "https://www.g2b.go.kr/f/p", "kind": "notice"},
                   {"name": "(공고문)A.hwp", "url": "https://www.g2b.go.kr/f/h", "kind": "notice"}]
    local = Path(tempfile.mkdtemp()) / "notice.hwp"
    local.write_bytes(hwp)
    files = {"https://www.g2b.go.kr/f/p": b"%PDF-1.4", "https://www.g2b.go.kr/f/h": hwp}
    matched = notices.compare_notice_pdf_bytes(attachments, local, session=Session(files))
    assert matched["status"] == "byte_match" and matched["attachment_name"] == "(공고문)A.hwp", matched
    changed = notices.compare_notice_pdf_bytes(attachments, local, session=Session({**files, "https://www.g2b.go.kr/f/h": hwp + b"!"}))
    assert changed["status"] == "byte_mismatch"
    wrong = notices.compare_notice_pdf_bytes(attachments[1:], local, session=Session({"https://www.g2b.go.kr/f/h": b"<html>"}))
    assert wrong["status"] == "download_not_pdf", "저장 파일과 다른 형식(오류 페이지)은 대조하지 않음"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("document processing OK")
