"""겹쳐 찍은 글자 걸러내기 — agent01_rfp(T1)·past_projects(T3) 공용.

일부 PDF는 굵게 보이려고 같은 글자를 조금씩 비켜 두세 번 찍는다. 그대로 읽으면 "요요구구사사항항"처럼
글자가 겹쳐 나온다(과거 RFP R26BK01550694·R26BK01693931). pypdf도 "사 업 명사 업 명"처럼 겹쳐 읽으므로
겹침이 있는 쪽은 걸러낸 pdfplumber 텍스트를 쓴다.
"""
from __future__ import annotations

OVERPRINT_TOLERANCE = 1  # pt: 이 안에서 같은 글자·글꼴·크기가 겹치면 하나만 남긴다


def dedupe_overprint(page):
    """(겹친 글자를 하나만 남긴 pdfplumber 쪽, 겹침이 있었는지). 실패하면 원래 쪽을 그대로 돌려준다."""
    try:
        deduped = page.dedupe_chars(tolerance=OVERPRINT_TOLERANCE)
        return deduped, len(deduped.chars) < len(page.chars)
    except Exception:
        return page, False
