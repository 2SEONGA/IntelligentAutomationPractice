"""M31: HWP 5.0 / HWPX 본문 추출과 Agent 3 수집의 HWP 제안요청서 사용."""
from __future__ import annotations

import io
import json
import shutil
import struct
import sys
import tempfile
import zipfile
import zlib
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bid_review.hwp_text import HwpError, hwp_text  # noqa: E402

END, FREE = 0xFFFFFFFE, 0xFFFFFFFF


def _record(tag: int, body: bytes) -> bytes:
    return struct.pack("<I", tag | (len(body) << 20)) + body


def _para(*parts) -> bytes:
    """문자열은 그대로, 정수는 제어 문자(넓은 제어는 8 wchar 차지)."""
    out = b""
    for part in parts:
        out += part.encode("utf-16-le") if isinstance(part, str) else struct.pack("<8H", part, 0, 0, 0, 0, 0, 0, part)
    return out


def make_hwp(path: Path, flags: int = 1) -> None:
    """512바이트 섹터, mini stream 기준값 0(모든 스트림을 일반 FAT에)인 최소 Compound File."""
    body = _record(66, b"\0" * 22) + _record(67, _para("1. 사업 개요", 9, "1억")) + _record(67, _para("표 안 문단", 11, "끝"))
    section = zlib.compress(body)[2:-4] if flags & 1 else body  # raw deflate
    file_header = b"HWP Document File".ljust(32, b"\0") + struct.pack("<II", 0x05000000, flags) + b"\0" * 216
    sec_sectors = -(-len(section) // 512)

    def entry(name: str, kind: int, left=FREE, right=FREE, child=FREE, start=END, size=0) -> bytes:
        raw = (name + "\0").encode("utf-16-le")
        return (raw.ljust(64, b"\0") + struct.pack("<HBB3I", len(raw), kind, 1, left, right, child)
                + b"\0" * 36 + struct.pack("<IQ", start, size))

    directory = (entry("Root Entry", 5, child=1) + entry("FileHeader", 2, right=2, start=2, size=256)
                 + entry("BodyText", 1, child=3) + entry("Section0", 2, start=3, size=len(section)))
    fat = [0xFFFFFFFD, END, END] + [3 + i + 1 for i in range(sec_sectors - 1)] + [END]
    fat += [FREE] * (128 - len(fat))
    header = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 16 + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6)
              + b"\0" * 10 + struct.pack("<8I", 1, 1, 0, 0, END, 0, END, 0)
              + struct.pack("<109I", 0, *([FREE] * 108)))
    path.write_bytes(header + struct.pack("<128I", *fat) + directory + file_header.ljust(512, b"\0")
                     + section.ljust(sec_sectors * 512, b"\0"))


def make_hwpx(path: Path, extra: str = "") -> None:
    ns = 'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"'
    xml = (f'<?xml version="1.0"?>{extra}<hs:sec xmlns:hs="x" {ns}>'
           '<hp:p><hp:run><hp:t>사업 개요</hp:t><hp:t> 및 목적</hp:t></hp:run></hp:p>'
           '<hp:p><hp:run><hp:tbl><hp:tr><hp:tc><hp:p><hp:run><hp:t>표 안 문단</hp:t></hp:run></hp:p></hp:tc></hp:tr></hp:tbl></hp:run></hp:p>'
           '</hs:sec>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
        archive.writestr("Contents/section0.xml", xml)
    path.write_bytes(buffer.getvalue())


def test_hwp5_text_and_controls() -> None:
    work = Path(tempfile.mkdtemp())
    try:
        make_hwp(work / "a.hwp")
        assert hwp_text(work / "a.hwp") == "1. 사업 개요\t1억\n표 안 문단끝", repr(hwp_text(work / "a.hwp"))
        make_hwp(work / "plain.hwp", flags=0)
        assert "표 안 문단" in hwp_text(work / "plain.hwp"), "압축하지 않은 문서"
        import bid_review.hwp_text as ht
        saved_cap = ht.MAX_TEXT_BYTES
        try:
            ht.MAX_TEXT_BYTES = 300  # FileHeader(256) + 압축 섹션 → 합계 상한 초과
            try:
                hwp_text(work / "a.hwp")
                raise AssertionError("스트림 합계 상한")
            except HwpError:
                pass
        finally:
            ht.MAX_TEXT_BYTES = saved_cap
        make_hwp(work / "locked.hwp", flags=1 | 2)
        try:
            hwp_text(work / "locked.hwp")
            raise AssertionError("암호 문서는 거부")
        except HwpError:
            pass
    finally:
        shutil.rmtree(work)


def test_hwp5_surrogates_are_valid_text() -> None:
    from bid_review.hwp_text import _decode_para
    pair = "😀".encode("utf-16-le")
    assert _decode_para(b"A\0" + pair + b"B\0") == "A😀B", "서로게이트 쌍은 한 글자로"
    assert _decode_para(b"\x00\xd8C\0") == "\ufffdC", "짝 없는 서로게이트는 대체 문자"
    "".join(_decode_para(b"\x00\xd8")).encode("utf-8")  # 임베딩 API로 보낼 수 있어야 함


def test_hwpx_text_and_dtd_rejected() -> None:
    work = Path(tempfile.mkdtemp())
    try:
        make_hwpx(work / "a.hwpx")
        assert hwp_text(work / "a.hwpx") == "사업 개요 및 목적\n표 안 문단", "표 글자가 바깥 문단에 겹치지 않음"
        make_hwpx(work / "bomb.hwpx", '<!DOCTYPE x [<!ENTITY a "aaaa">]>')
        try:
            hwp_text(work / "bomb.hwpx")
            raise AssertionError("DTD가 있는 HWPX는 거부")
        except HwpError:
            pass
        utf16 = work / "utf16.hwpx"  # UTF-16으로 쓴 DTD는 바이트 검사를 피하므로 UTF-8이 아니면 거부
        with zipfile.ZipFile(utf16, "w") as archive:
            archive.writestr("Contents/section0.xml", '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE x [<!ENTITY a "b">]><x/>'.encode("utf-16"))
        try:
            hwp_text(utf16)
            raise AssertionError("UTF-16 HWPX는 거부")
        except HwpError:
            pass
        (work / "x.hwp").write_bytes(b"not hwp")
        try:
            hwp_text(work / "x.hwp")
            raise AssertionError("형식 오류")
        except HwpError:
            pass
    finally:
        shutil.rmtree(work)


def test_past_collection_uses_hwp_rfp_and_rechecks_old_no_pdf() -> None:
    import bid_review.rag.past_projects as pp
    from bid_review.settings import PROJECT_ROOT
    from bid_review.web import notices

    work = Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "outputs"))
    sample = work / "sample.hwpx"
    make_hwpx(sample)
    attachments = {
        "OLD": [{"name": "제안요청서.hwpx", "url": "https://www.g2b.go.kr/a", "kind": "other", "document_kind": ""}],
        "PDF": [{"name": "과업지시서.hwp", "url": "https://www.g2b.go.kr/b", "kind": "other", "document_kind": ""},
                {"name": "제안요청서.pdf", "url": "https://www.g2b.go.kr/c", "kind": "rfp", "document_kind": ""}],
        "NONE": [{"name": "공고문.hwp", "url": "https://www.g2b.go.kr/d", "kind": "other", "document_kind": "공고서"}],
        "BAD": [{"name": "붙임 제안요청서.hwp", "url": "https://www.g2b.go.kr/e", "kind": "other", "document_kind": ""}],
    }
    looked: list[str] = []

    def fake_download(session, attachment, target, magic=b"%PDF-"):
        content = {".pdf": b"%PDF-1.4", ".hwpx": sample.read_bytes(), ".hwp": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1broken"}[target.suffix]
        assert content.startswith(magic), (target, magic)
        target.write_bytes(content)
        return True

    saved = (notices._fetch, notices.fetch_notice_attachment_info, notices._download, pp.PAST_DIR, pp.fetch_awards)
    try:
        notices._fetch = lambda session, keyword, b, e: [
            {"bidNtceNo": no, "bidNtceOrd": "000", "bidNtceNm": f"{no} 사업", "bidNtceDt": "2026-11-10",
             "pubPrcrmntLrgClsfcNm": "ICT 서비스", "cntrctCnclsMthdNm": "일반경쟁"} for no in attachments]
        notices.fetch_notice_attachment_info = lambda no, order, session=None: looked.append(no) or {
            "attachments": attachments[no], "api_operation": "op", "retrieved_at": "t"}
        notices._download = fake_download
        pp.PAST_DIR = work / "past"
        pp.PAST_DIR.mkdir()
        (pp.PAST_DIR / "_checked.json").write_text(json.dumps({"OLD-000": "no_rfp_pdf"}), encoding="utf-8")
        pp.fetch_awards = lambda *a, **k: ({}, 0)
        result = pp.collect_past_projects(days=7, max_projects=10, today=date(2026, 11, 17), log=lambda *_: None)
        assert "OLD" in looked, "M31 전 'PDF 없음' 기록은 다시 조회"
        assert (result["saved_hwpx"], result["saved_pdf"], result["no_rfp"], result["rfp_unreadable"]) == (1, 1, 1, 1), result
        assert pp.rfp_file(pp.PAST_DIR / "OLD-000").name == "rfp.hwpx"
        assert pp.rfp_file(pp.PAST_DIR / "PDF-000").name == "rfp.pdf", "PDF가 있으면 PDF 우선"
        assert not (pp.PAST_DIR / "BAD-000").exists(), "읽을 수 없는 HWP는 저장하지 않음"
        checked = json.loads((pp.PAST_DIR / "_checked.json").read_text(encoding="utf-8"))
        assert checked == {"NONE-000": "no_rfp", "BAD-000": "rfp_unreadable"}, checked
        profile = pp.project_profile(pp.PAST_DIR / "OLD-000/rfp.hwpx", "OLD 사업")
        assert profile.startswith("사업명: OLD 사업") and "사업 개요 및 목적" in profile
        looked.clear()
        again = pp.collect_past_projects(days=7, max_projects=10, today=date(2026, 11, 17), log=lambda *_: None)
        assert looked == [] and again["already_checked"] == 2 and again["already_saved"] == 2, again
    finally:
        notices._fetch, notices.fetch_notice_attachment_info, notices._download, pp.PAST_DIR, pp.fetch_awards = saved
        shutil.rmtree(work, ignore_errors=True)


def test_hwp_toc_tab_lines_are_skipped() -> None:
    import bid_review.rag.past_projects as pp
    assert pp.TOC_LEADER.search("   가. 추진배경 및 필요성\t1") and pp.TOC_LEADER.search("     1. 사업개요\t 1")
    assert not pp.TOC_LEADER.search("○ 사업기간\t: 계약 후 12개월")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
