"""PDF 페이지 OCR(선택 기능).

pypdf·pdfplumber 텍스트가 짧거나 깨진 페이지(스캔본·이미지 표)는 페이지 이미지를 OCR로 다시 읽고,
기존 텍스트보다 나은 경우에만 그 텍스트를 추출·요약(→ LLM 전달 JSON)에 쓴다.
OCR 엔진(pytesseract + tesseract, 한국어 kor 데이터)이 없으면 아무것도 하지 않고 기존 방식대로 동작한다.

설정(config/settings.yaml → rfp.ocr):
  mode: off | fallback(기본, OCR 필요 페이지만) | always(모든 페이지를 OCR과 비교)
  lang: kor+eng, resolution: 300, max_pages: 문서당 OCR 페이지 상한
"""
from __future__ import annotations

import importlib.util
import re
import shutil
from typing import Any

DEFAULTS: dict[str, Any] = {"mode": "fallback", "lang": "kor+eng", "resolution": 300, "max_pages": 30}


def config(settings: dict) -> dict[str, Any]:
    return {**DEFAULTS, **((settings.get("rfp") or {}).get("ocr") or {})}


def engine_available() -> bool:
    return bool(importlib.util.find_spec("pytesseract") and shutil.which("tesseract"))


def ocr_page(layout_page, lang: str = "kor+eng", resolution: int = 300) -> str | None:
    """pdfplumber 페이지 1장을 이미지로 렌더링해 OCR한다. 실패하면 None(추출을 멈추지 않는다)."""
    if not engine_available():
        return None
    try:
        import pytesseract  # noqa: PLC0415 - 선택 의존성

        image = layout_page.to_image(resolution=resolution).original
        return pytesseract.image_to_string(image, lang=lang) or None
    except Exception:
        return None


def _quality(text: str) -> float:
    """한글·영숫자 글자 수. 기호·반복 문자가 많은 깨진 텍스트일수록 낮다."""
    compact = re.sub(r"\s+", "", text or "")
    return float(sum(char.isalnum() or "가" <= char <= "힣" for char in compact))


def is_better(original: str, ocr_text: str | None) -> bool:
    """OCR 결과가 기존 추출보다 의미 있는 글자를 20% 이상(최소 20자) 더 담을 때만 채택한다."""
    if not ocr_text:
        return False
    before, after = _quality(original), _quality(ocr_text)
    return after >= 20 and after > before * 1.2
