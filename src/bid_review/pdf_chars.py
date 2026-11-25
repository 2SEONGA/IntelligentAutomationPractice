"""겹쳐 찍은 글자 걸러내기 — agent01_rfp(T1)·past_projects(T3) 공용.

일부 PDF는 굵게 보이려고 같은 글자를 조금씩 비켜 두세 번 찍는다. 그대로 읽으면 "요요구구사사항항"처럼
글자가 겹쳐 나온다(과거 RFP R26BK01550694·R26BK01693931). pypdf도 "사 업 명사 업 명"처럼 겹쳐 읽으므로
겹침이 있는 쪽은 걸러낸 pdfplumber 텍스트를 쓴다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

OVERPRINT_TOLERANCE = 1  # pt: 이 안에서 같은 글자·글꼴·크기가 겹치면 하나만 남긴다
# 남은 업무 4번: T1이 이미 연 쪽의 plain 텍스트(dedupe 후 extract_text)를 T3 질의 프로필이 다시 계산하지 않게 둔다.
# 키는 파일 내용 SHA-256. 값은 쪽별 텍스트(그 쪽 추출이 실패했으면 None → T3가 예전처럼 직접 읽는다).
_PLAIN_TEXTS: dict[str, list[str | None]] = {}
_PLAIN_TEXTS_MAX = 8


def file_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def remember_plain_texts(digest: str, texts: list[str | None]) -> None:
    if len(_PLAIN_TEXTS) >= _PLAIN_TEXTS_MAX and digest not in _PLAIN_TEXTS:
        _PLAIN_TEXTS.pop(next(iter(_PLAIN_TEXTS)))
    _PLAIN_TEXTS[digest] = list(texts)


def recalled_plain_texts(digest: str) -> list[str | None] | None:
    return _PLAIN_TEXTS.get(digest)


def plain_text(page) -> str | None:
    """T3(past_projects._rfp_pages)와 같은 방식의 쪽 텍스트. 실패하면 None."""
    try:
        return dedupe_overprint(page)[0].extract_text() or ""
    except Exception:
        return None


def dedupe_overprint(page):
    """(겹친 글자를 하나만 남긴 pdfplumber 쪽, 겹침이 있었는지). 실패하면 원래 쪽을 그대로 돌려준다."""
    try:
        deduped = page.dedupe_chars(tolerance=OVERPRINT_TOLERANCE)
        if len(deduped.chars) < len(page.chars):
            return deduped, True
        return page, False  # 겹침이 없으면 원래 쪽을 쓴다(걸러낸 사본으로 레이아웃·표를 다시 계산하지 않게)
    except Exception:
        return page, False
