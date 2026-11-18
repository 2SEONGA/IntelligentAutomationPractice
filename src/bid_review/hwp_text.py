"""HWP 5.0 / HWPX 본문 텍스트 추출 (M31) — 표준 라이브러리만 사용.

나라장터 제안요청서는 대부분 HWP·HWPX다(공고함 첨부 중 제안요청 이름 2,279건 중 PDF 254건).
외부에서 받은 파일이므로 압축 해제 크기를 제한하고, 암호·배포용 문서는 거부한다.
표·글상자 안의 문단도 본문 순서대로 나온다. 그림·수식은 버린다.
"""
from __future__ import annotations

import re
import struct
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree

MAX_TEXT_BYTES = 64 * 1024 * 1024  # 압축 해제 후 섹션 합계 상한(압축 폭탄 방지)
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"
_END, _FREE = 0xFFFFFFFE, 0xFFFFFFFF
PARA_TEXT = 67  # HWPTAG_BEGIN(0x10) + 51
# 길이 8(wchar)인 제어 문자: 인라인(4~9, 19, 20)·확장(1~3, 11, 12, 14~18, 21~23). 나머지 < 32는 길이 1.
_WIDE_CONTROLS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


class HwpError(ValueError):
    pass


def hwp_text(path: Path) -> str:
    data = Path(path).read_bytes()
    if data.startswith(ZIP_MAGIC):
        return _hwpx_text(path)
    if data.startswith(OLE_MAGIC):
        return _hwp5_text(data)
    raise HwpError("HWP/HWPX 형식이 아닙니다.")


def _hwpx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        sections = sorted((info for info in archive.infolist() if re.fullmatch(r"Contents/section\d+\.xml", info.filename)),
                          key=lambda info: int(re.search(r"\d+", info.filename.rsplit("/", 1)[1]).group()))
        if not sections:
            raise HwpError("HWPX 본문(section*.xml)이 없습니다.")
        if sum(info.file_size for info in sections) > MAX_TEXT_BYTES:
            raise HwpError("HWPX 본문이 너무 큽니다.")
        paragraphs: list[str] = []
        for info in sections:
            xml = archive.read(info)
            if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:  # 정상 HWPX에는 DTD가 없다 — 엔티티 확장 공격 차단
                raise HwpError("HWPX 본문에 DTD가 있습니다.")
            root = ElementTree.fromstring(xml)
            # 표 안 문단도 hp:p라서 가장 안쪽 문단 단위로 모은다(바깥 문단에 표 글자가 겹치지 않게).
            for para in root.iter():
                if para.tag.endswith("}p") and not any(child.tag.endswith("}p") for child in para.iter() if child is not para):
                    text = "".join(node.text or "" for node in para.iter() if node.tag.endswith("}t"))
                    if text.strip():
                        paragraphs.append(text)
    return "\n".join(paragraphs)


def _hwp5_text(data: bytes) -> str:
    streams = _ole_streams(data)
    header = streams.get("FileHeader", b"")
    if not header.startswith(b"HWP Document File"):
        raise HwpError("HWP 파일 헤더가 없습니다.")
    flags = struct.unpack_from("<I", header, 36)[0]
    if flags & 0b110:
        raise HwpError("암호가 걸렸거나 배포용 HWP 문서입니다.")
    names = sorted((name for name in streams if re.fullmatch(r"BodyText/Section\d+", name)),
                   key=lambda name: int(name[len("BodyText/Section"):]))
    if not names:
        raise HwpError("HWP 본문(BodyText)이 없습니다.")
    budget, paragraphs = MAX_TEXT_BYTES, []
    for name in names:
        raw = streams[name]
        if flags & 1:  # 압축 문서: raw deflate
            inflater = zlib.decompressobj(-15)
            raw = inflater.decompress(raw, budget)
            if inflater.unconsumed_tail:
                raise HwpError("HWP 본문이 너무 큽니다.")
        budget -= len(raw)
        paragraphs += _para_texts(raw)
    return "\n".join(text for text in paragraphs if text.strip())


def _para_texts(raw: bytes) -> list[str]:
    out, pos = [], 0
    while pos + 4 <= len(raw):
        head = struct.unpack_from("<I", raw, pos)[0]
        tag, size, pos = head & 0x3FF, head >> 20, pos + 4
        if size == 0xFFF:
            size, pos = struct.unpack_from("<I", raw, pos)[0], pos + 4
        if tag == PARA_TEXT:
            out.append(_decode_para(raw[pos:pos + size]))
        pos += size
    return out


def _decode_para(body: bytes) -> str:
    chars, i, units = [], 0, len(body) // 2
    while i < units:
        code = struct.unpack_from("<H", body, i * 2)[0]
        if code >= 32:
            chars.append(chr(code))
            i += 1
        elif code in _WIDE_CONTROLS:
            if code == 9:
                chars.append("\t")
            i += 8
        else:
            if code == 10:
                chars.append("\n")
            i += 1
    # 코드 단위를 하나씩 chr()했으므로 BMP 밖 글자(이모지·확장 한자)는 서로게이트 쌍으로 남는다 → 다시 합치고, 짝 없는 것은 �로.
    return "".join(chars).encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def _ole_streams(data: bytes) -> dict[str, bytes]:
    """Compound File Binary 스트림을 '저장소/스트림' 경로로. 사슬 순환·범위 밖 섹터는 오류로 끊는다."""
    if len(data) < 512 or not data.startswith(OLE_MAGIC):
        raise HwpError("OLE 파일이 아닙니다.")
    sector = 1 << struct.unpack_from("<H", data, 0x1E)[0]
    mini_sector = 1 << struct.unpack_from("<H", data, 0x20)[0]
    dir_start, cutoff, minifat_start = struct.unpack_from("<I", data, 0x30)[0], *struct.unpack_from("<II", data, 0x38)
    difat_start, difat_count = struct.unpack_from("<II", data, 0x44)
    total = (len(data) - 512) // sector + 1

    def read_sector(n: int) -> bytes:
        if n >= total:
            raise HwpError("OLE 섹터 범위 오류")
        return data[(n + 1) * sector:(n + 2) * sector]

    fat_sectors = [n for n in struct.unpack_from("<109I", data, 0x4C) if n not in (_END, _FREE)]
    n = difat_start
    for _ in range(difat_count):
        if n in (_END, _FREE):
            break
        entries = struct.unpack(f"<{sector // 4}I", read_sector(n))
        fat_sectors += [s for s in entries[:-1] if s not in (_END, _FREE)]
        n = entries[-1]
    fat = [v for s in fat_sectors for v in struct.unpack(f"<{sector // 4}I", read_sector(s))]

    def chain(start: int, table: list[int]) -> list[int]:
        out = []
        while start not in (_END, _FREE):
            if start >= len(table) or len(out) > len(table):
                raise HwpError("OLE 사슬 오류")
            out.append(start)
            start = table[start]
        return out

    directory = b"".join(read_sector(s) for s in chain(dir_start, fat))
    entries = [directory[i:i + 128] for i in range(0, len(directory) - 127, 128)]
    if not entries:
        raise HwpError("OLE 디렉터리가 없습니다.")
    root_start, root_size = struct.unpack_from("<IQ", entries[0], 116)
    mini_stream = b"".join(read_sector(s) for s in chain(root_start, fat))[:root_size]
    minifat = [v for s in chain(minifat_start, fat) for v in struct.unpack(f"<{sector // 4}I", read_sector(s))]

    def stream(start: int, size: int) -> bytes:
        if size > MAX_TEXT_BYTES:
            raise HwpError("OLE 스트림이 너무 큽니다.")
        if size < cutoff:
            return b"".join(mini_stream[s * mini_sector:(s + 1) * mini_sector] for s in chain(start, minifat))[:size]
        return b"".join(read_sector(s) for s in chain(start, fat))[:size]

    streams: dict[str, bytes] = {}
    seen: set[int] = set()

    def walk(index: int, prefix: str) -> None:
        if index == _FREE or index >= len(entries) or index in seen:
            return
        seen.add(index)
        entry = entries[index]
        name_len = struct.unpack_from("<H", entry, 64)[0]
        name = entry[:max(name_len - 2, 0)].decode("utf-16-le", "replace")
        kind = entry[66]
        left, right, child = struct.unpack_from("<III", entry, 68)
        walk(left, prefix)
        walk(right, prefix)
        if kind == 2:
            start, size = struct.unpack_from("<IQ", entry, 116)
            streams[prefix + name] = stream(start, size & 0xFFFFFFFF if sector == 512 else size)
        elif kind == 1:
            walk(child, prefix + name + "/")

    walk(struct.unpack_from("<I", entries[0], 76)[0], "")
    return streams
