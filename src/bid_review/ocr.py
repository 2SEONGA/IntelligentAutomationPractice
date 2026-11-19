"""PDF 페이지 OCR(선택 기능).

pypdf·pdfplumber 텍스트가 짧거나 깨진 페이지(스캔본·이미지 표)는 페이지 이미지를 OCR로 다시 읽고,
기존 텍스트보다 나은 경우에만 그 텍스트를 추출·요약(→ LLM 전달 JSON)에 쓴다.
OCR 엔진(pytesseract + tesseract, 한국어 kor 데이터)이 없으면 아무것도 하지 않고 기존 방식대로 동작한다.

설정(config/settings.yaml → rfp.ocr):
  mode: off | fallback(기본, OCR 필요 페이지만) | always(모든 페이지를 OCR과 비교)
  lang: kor+eng, resolution: 300, psm: 4(tesseract 페이지 분할), max_pages: 문서당 OCR "시도" 페이지 상한(채택 수가 아니라 실행 수)
"""
from __future__ import annotations

import importlib.util
import re
import shutil
from typing import Any

# psm 4(한 단 문서로 위→아래 읽기): 시연 RFP 실측에서 기본 psm 3(자동 분할)은 박스·표 레이아웃을 잘게 쪼개
# 글자 일치율 0.17~0.34였고, psm 4는 0.955~0.994였다.
DEFAULTS: dict[str, Any] = {"mode": "fallback", "lang": "kor+eng", "resolution": 300, "max_pages": 30, "psm": 4}


def config(settings: dict) -> dict[str, Any]:
    return {**DEFAULTS, **((settings.get("rfp") or {}).get("ocr") or {})}


def engine_available() -> bool:
    return bool(importlib.util.find_spec("pytesseract") and shutil.which("tesseract"))


def ocr_page(layout_page, lang: str = "kor+eng", resolution: int = 300, psm: int = 4) -> str | None:
    """pdfplumber 페이지 1장을 이미지로 렌더링해 OCR한다. 실패하면 None(추출을 멈추지 않는다)."""
    if not engine_available():
        return None
    try:
        import pytesseract  # noqa: PLC0415 - 선택 의존성

        image = layout_page.to_image(resolution=resolution).original.convert("L")
        return pytesseract.image_to_string(image, lang=lang, config=f"--psm {int(psm)}") or None
    except Exception:
        return None


AMOUNT_RE = re.compile(r"\d{1,3}(?:,\d{3}){2,}|\d{7,}|\d+(?:\.\d+)?\s*억")
DATE_RE = re.compile(r"20\d{2}\s*[.년/\-]\s*\d{1,2}\s*[.월/\-]\s*\d{1,2}")
CLAUSE_RE = re.compile(r"제\s*\d+\s*조|^\s*(?:[가-하]|\d{1,2}|[ⅠⅡⅢⅣⅤ]+)\s*[.)]", re.M)
VAT_RE = re.compile(r"부가가치세|부가세|VAT", re.I)
HANGUL_WORD = re.compile(r"[가-힣]{2,}")


def _quality(text: str) -> float:
    """한글·영숫자 글자 수(비교 스크립트 호환용)."""
    compact = re.sub(r"\s+", "", text or "")
    return float(sum(char.isalnum() or "가" <= char <= "힣" for char in compact))


def _digits(value: str) -> str:
    return re.sub(r"[^\d]", "", value)


def text_metrics(text: str) -> dict[str, Any]:
    """OCR 채택 판단용 텍스트 품질 지표.

    garbled_ratio: 기호·대체문자·한 글자짜리 토큰의 비율(높을수록 깨짐)
    sentence_lines: 한글 낱말이 3개 이상인 줄 수(문장 구조)
    amounts/dates/clauses/vat: 금액·날짜·조항 번호·VAT 표기 보존 여부 확인용
    """
    text = text or ""
    compact = re.sub(r"\s+", "", text)
    symbols = sum(1 for ch in compact if not (ch.isalnum() or "가" <= ch <= "힣")) + compact.count("\ufffd")
    tokens = text.split()
    single = sum(1 for token in tokens if len(token) == 1 and not token.isdigit())
    garbled = (symbols / len(compact) if compact else 1.0) * 0.6 + (single / len(tokens) if tokens else 1.0) * 0.4
    return {
        "meaningful_chars": int(_quality(text)),
        "garbled_ratio": round(garbled, 3),
        "sentence_lines": sum(1 for line in text.splitlines() if len(HANGUL_WORD.findall(line)) >= 3),
        "amounts": sorted({_digits(m) for m in AMOUNT_RE.findall(text) if _digits(m)}),
        "dates": sorted({_digits(m) for m in DATE_RE.findall(text)}),
        "clauses": len(CLAUSE_RE.findall(text)),
        "vat": bool(VAT_RE.search(text)),
    }


def _score(metrics: dict[str, Any]) -> float:
    return (metrics["meaningful_chars"] * (1.0 - min(metrics["garbled_ratio"], 1.0))
            + 15 * metrics["sentence_lines"] + 20 * (len(metrics["amounts"]) + len(metrics["dates"]))
            + 10 * metrics["clauses"] + 10 * metrics["vat"])


def choose(original: str, ocr_text: str | None) -> tuple[bool, str]:
    """OCR 결과를 쓸지 판단하고 그 사유를 돌려준다. 단순 글자 수가 아니라 깨짐·문장·핵심 정보 보존을 본다.

    채택 조건(모두 만족):
      1) OCR 텍스트가 비지 않고 의미 있는 글자 20자 이상
      2) OCR 깨짐 비율 0.35 미만
      3) 기존 추출에 있던 금액·날짜를 OCR이 모두 보존(하나라도 빠지면 기존 유지 — 금액·날짜 누락이 더 위험)
      4) 종합 점수(글자×(1-깨짐) + 문장·금액·날짜·조항·VAT 가점)가 기존보다 10% 이상 높음
    """
    if not ocr_text or not ocr_text.strip():
        return False, "OCR 결과 없음"
    before, after = text_metrics(original), text_metrics(ocr_text)
    if after["meaningful_chars"] < 20:
        return False, f"OCR 의미 글자 {after['meaningful_chars']}자로 너무 짧음"
    if after["garbled_ratio"] >= 0.35:
        return False, f"OCR 텍스트 깨짐 비율 {after['garbled_ratio']:.2f}"
    lost_amounts = [value for value in before["amounts"] if value not in after["amounts"]]
    lost_dates = [value for value in before["dates"] if value not in after["dates"]]
    if before["garbled_ratio"] < 0.35 and (lost_amounts or lost_dates):
        return False, f"기존 추출의 금액 {len(lost_amounts)}개·날짜 {len(lost_dates)}개가 OCR에서 빠짐"
    score_before, score_after = _score(before), _score(after)
    if score_after <= score_before * 1.1:
        return False, f"품질 점수 개선 부족(기존 {score_before:.0f} → OCR {score_after:.0f})"
    return True, (f"품질 점수 {score_before:.0f} → {score_after:.0f}, 깨짐 {before['garbled_ratio']:.2f} → "
                  f"{after['garbled_ratio']:.2f}, 금액 {len(after['amounts'])}·날짜 {len(after['dates'])}·조항 {after['clauses']}")


def is_better(original: str, ocr_text: str | None) -> bool:
    """이전 호출부 호환용: choose()의 채택 여부."""
    return choose(original, ocr_text)[0]
