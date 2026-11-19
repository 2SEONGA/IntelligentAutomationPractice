"""부가가치세(VAT) 포함/별도 판정과 기준 환산.

같은 사업금액도 문서에 따라 'VAT 별도'(예: 107,000,000원)와 'VAT 포함'(117,700,000원)으로 다르게 적힌다.
숫자만 비교하면 충돌로 보이므로, 먼저 각 금액의 VAT 기준을 확인하고 같은 기준으로 환산한 뒤 비교한다.
VAT 기준을 모르는 금액은 임의로 포함/별도로 가정하지 않고 '확인 필요'(unknown)로 남긴다.
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

VatBasis = Literal["included", "excluded", "unknown"]
VAT_RATE = Decimal("0.10")
BASIS_LABELS = {"included": "VAT 포함", "excluded": "VAT 별도", "unknown": "VAT 기준 확인 필요"}

_TAX = r"(?:부가가치세|부가세|VAT)"
INCLUDED_RE = re.compile(rf"{_TAX}\s*(?:포함|포함액|포함금액|포함가)|{_TAX}\s*\(?\s*포함", re.I)
EXCLUDED_RE = re.compile(rf"{_TAX}\s*(?:별도|제외|미포함|불포함)|{_TAX}\s*\(?\s*(?:별도|제외)", re.I)
TAX_MENTION_RE = re.compile(_TAX, re.I)

# 나라장터 입찰공고 API 금액 필드의 VAT 기준. 추정가격은 국가계약법 시행령 제2조상 부가가치세를 제외한 금액이고,
# 배정예산은 부가가치세를 포함한 예산액이다(수집 데이터 예: 배정예산 770,000,000 = 추정가격 700,000,000 × 1.1).
G2B_FIELD_BASIS: dict[str, VatBasis] = {"asignBdgtAmt": "included", "presmptPrce": "excluded"}


def vat_basis(text: str | None) -> VatBasis:
    """원문 발췌에서 VAT 포함/별도를 읽는다. 둘 다 적혀 있거나 아무 말이 없으면 unknown."""
    if not text:
        return "unknown"
    included, excluded = bool(INCLUDED_RE.search(text)), bool(EXCLUDED_RE.search(text))
    if included == excluded:
        return "unknown"
    return "included" if included else "excluded"


def mentions_tax(text: str | None) -> bool:
    return bool(text and TAX_MENTION_RE.search(text))


def convert(amount: int, basis: VatBasis, target: VatBasis) -> int | None:
    """금액을 target 기준으로 환산한다. 기준을 모르면 None(임의 환산하지 않음)."""
    if basis == "unknown" or target == "unknown":
        return None
    if basis == target:
        return amount
    value = Decimal(amount) * (1 + VAT_RATE) if target == "included" else Decimal(amount) / (1 + VAT_RATE)
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def _close(left: int, right: int) -> bool:
    # 원 단위 절사·반올림 차이(최대 10원 또는 0.01%)는 같은 금액으로 본다.
    return abs(left - right) <= max(10, max(left, right) // 10_000)


def looks_like_vat_gap(left: int, right: int) -> bool:
    """한쪽이 다른 쪽의 정확히 1.1배인지(기준이 안 적혀 있어도 VAT 차이로 의심할 수 있는지)."""
    small, large = sorted((left, right))
    return small > 0 and _close(convert(small, "excluded", "included") or 0, large)


def compare(left: int, left_basis: VatBasis, right: int, right_basis: VatBasis) -> tuple[str, str]:
    """두 금액을 VAT 기준을 맞춘 뒤 비교한다.

    반환 상태:
      - "same": 숫자와 VAT 기준이 모두 같다(양쪽 기준이 문서에 명시됨) → 일치.
      - "same_basis_unknown": 숫자는 같지만 양쪽 모두 VAT 기준이 명시되지 않았다 → 숫자 일치, VAT 기준은 미확인.
      - "same_after_vat": 숫자는 다르지만 VAT 포함/별도 기준을 맞추면 같다 → 충돌 아님.
      - "vat_unknown": 한쪽만 VAT 기준이 명시돼 숫자가 같아도 같은 금액인지 알 수 없거나,
        약 10% 차이지만 VAT 기준을 확인하지 못했다 → 확인 필요. 명시되지 않은 기준은 추정하지 않는다.
      - "basis_mismatch": 숫자는 같지만 VAT 포함/별도 표기가 서로 반대다 → 확인 필요.
      - "different": 기준을 맞춰도 다르다 → 실제 금액 불일치.
    """
    if left == right:
        if left_basis != right_basis and "unknown" not in (left_basis, right_basis):
            return "basis_mismatch", (f"같은 숫자 {left:,}원이 한쪽은 {BASIS_LABELS[left_basis]}, "
                                      f"다른 쪽은 {BASIS_LABELS[right_basis]} 표기라 VAT 기준을 확인해야 합니다.")
        if left_basis == right_basis == "unknown":
            return "same_basis_unknown", (f"두 금액이 {left:,}원으로 같지만 양쪽 모두 VAT 포함/별도가 명시되지 않아 "
                                          "VAT 기준은 확인되지 않았습니다.")
        if "unknown" in (left_basis, right_basis):
            return "vat_unknown", (f"두 금액이 {left:,}원으로 같지만 한쪽({BASIS_LABELS[left_basis]} / "
                                   f"{BASIS_LABELS[right_basis]})의 VAT 기준이 명시되지 않아 같은 금액인지 확인이 필요합니다.")
        return "same", f"두 금액이 {left:,}원({BASIS_LABELS[left_basis]})으로 같습니다."
    if "unknown" not in (left_basis, right_basis):
        if left_basis != right_basis:
            converted = convert(right, right_basis, left_basis)
            if converted is not None and _close(left, converted):
                return "same_after_vat", (
                    f"{left:,}원({BASIS_LABELS[left_basis]})과 {right:,}원({BASIS_LABELS[right_basis]})은 "
                    f"부가세 10%를 적용하면 같은 금액입니다. VAT 표기 차이이며 금액 충돌이 아닙니다.")
        return "different", (f"{left:,}원({BASIS_LABELS[left_basis]})과 {right:,}원({BASIS_LABELS[right_basis]})은 "
                             "VAT 기준을 맞춰도 다릅니다.")
    if looks_like_vat_gap(left, right):
        return "vat_unknown", (f"{left:,}원({BASIS_LABELS[left_basis]})과 {right:,}원({BASIS_LABELS[right_basis]})은 "
                               "약 10% 차이로 VAT 표기 차이일 수 있지만 VAT 포함/별도가 확인되지 않아 원문 확인이 필요합니다.")
    return "different", f"{left:,}원과 {right:,}원이 다릅니다."
