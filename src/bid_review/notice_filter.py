"""Rule filter for collected G2B bid notices (config/notice_filter.yaml).

Strongest verdict wins: 제외 > 확인 필요 > 검토 대상. A missing value never excludes; it asks a person.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .vat import BASIS_LABELS, G2B_FIELD_BASIS, convert

VERDICTS = ("검토 대상", "확인 필요", "제외")
EOK = 100_000_000


AMOUNT_FIELDS = ("asignBdgtAmt", "presmptPrce")


def amount_source(notice: dict) -> tuple[int, str] | None:
    """(금액, API 필드명). 배정예산을 먼저 쓰고 없으면 추정가격."""
    for field in AMOUNT_FIELDS:
        try:
            value = int(float(notice.get(field) or 0))
        except ValueError:
            continue
        if value > 0:
            return value, field
    return None


def amount_of(notice: dict) -> int | None:
    source = amount_source(notice)
    return source[0] if source else None


def criterion_basis(rules: dict) -> str:
    """40억 기준의 VAT 기준(included/excluded). 설정이 없거나 잘못되면 unknown."""
    basis = rules.get("min_amount_vat_basis", "included")
    return basis if basis in ("included", "excluded") else "unknown"


def amount_label(rules: dict) -> str:
    """화면·사유에 쓰는 기준 표기. 예: '40억 원 이상 (VAT 포함)'."""
    return f"{rules['min_amount_krw'] // EOK}억 원 이상 ({BASIS_LABELS[criterion_basis(rules)]})"


def normalized_amount(notice: dict, rules: dict) -> tuple[int | None, str, str]:
    """(기준 VAT로 환산한 금액, 원래 필드의 VAT 기준, 필드명). 환산할 수 없으면 금액은 None."""
    source = amount_source(notice)
    if source is None:
        return None, "unknown", ""
    amount, field = source
    field_basis = {**G2B_FIELD_BASIS, **(rules.get("amount_field_vat_basis") or {})}.get(field, "unknown")
    if field_basis not in ("included", "excluded"):
        field_basis = "unknown"
    return convert(amount, field_basis, criterion_basis(rules)), field_basis, field


def _amount(notice: dict, rules: dict) -> tuple[str, str]:
    amount = amount_of(notice)
    minimum = rules["min_amount_krw"]
    if amount is None:
        return "확인 필요", "사업금액 없음"
    target = criterion_basis(rules)
    if target == "unknown":
        return "확인 필요", "40억 기준의 VAT 포함/별도 설정 확인 필요"
    normalized, field_basis, field = normalized_amount(notice, rules)
    label = "배정예산" if field == "asignBdgtAmt" else "추정가격"
    if normalized is None:
        # VAT 포함 여부를 모르는 금액은 임의로 포함/별도 처리하지 않는다.
        return "확인 필요", f"{label} {amount / EOK:,.1f}억의 VAT 포함 여부 확인 필요"
    shown = f"{normalized / EOK:,.1f}억 {BASIS_LABELS[target]}"
    if field_basis != target:
        shown += f", {label} {amount / EOK:,.1f}억 {BASIS_LABELS[field_basis]} 환산"
    if normalized < minimum:
        return "제외", f"{minimum // EOK}억 미만({shown})"
    return "검토 대상", f"사업금액 {shown}"


def _deadline(notice: dict, rules: dict, now: datetime) -> tuple[str, str]:
    raw = (notice.get("bidClseDt") or "").strip()
    try:
        deadline = datetime.strptime(raw[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return "확인 필요", "마감일 없음"
    if deadline < now:
        return "제외", "마감 지남"
    if deadline - now <= timedelta(days=rules["deadline_warn_days"]):
        return "확인 필요", f"마감 임박({raw[:10]})"
    return "검토 대상", f"마감 {raw[:10]}"


def _field(notice: dict, rules: dict) -> tuple[str, str]:
    category = (notice.get("pubPrcrmntLrgClsfcNm") or "").strip()
    if category:
        return ("검토 대상", category) if category in rules["ict_classes"] else ("제외", f"ICT 분야 아님({category})")
    hit = next((k for k in rules["field_keywords"] if k in (notice.get("bidNtceNm") or "")), None)
    return ("검토 대상", f"공고명 키워드 '{hit}'") if hit else ("제외", "분류 없음·키워드 불일치")


def _contract(notice: dict, rules: dict) -> tuple[str, str]:
    text = f"{notice.get('cntrctCnclsMthdNm') or ''} {notice.get('sucsfbidMthdNm') or ''}"
    hit = next((t for t in rules["exclude_contract_terms"] if t in text), None)
    return ("제외", f"계약방법 {hit}") if hit else ("검토 대상", (notice.get("cntrctCnclsMthdNm") or "").strip())


def _conditions(notice: dict, rules: dict) -> tuple[str, str]:
    found = [t for t in rules["confirm_contract_terms"] if t in (notice.get("cntrctCnclsMthdNm") or "")]
    for flag in rules["confirm_flags"]:
        value = (notice.get(flag["field"]) or "").strip()
        if (flag["when"] == "nonempty" and value) or value == flag["when"]:
            found.append(flag["label"])
    return ("확인 필요", f"{'·'.join(found)} — 공고문 원문 확인") if found else ("검토 대상", "참가 조건 제한 없음")


def judge(notice: dict, rules: dict, now: datetime) -> dict:
    results = {
        "amount": _amount(notice, rules),
        "deadline": _deadline(notice, rules, now),
        "field": _field(notice, rules),
        "contract": _contract(notice, rules),
        "conditions": _conditions(notice, rules),
    }
    verdict = max((v for v, _ in results.values()), key=VERDICTS.index)
    reasons = [reason for v, reason in results.values() if v != "검토 대상"] or ["모든 규칙 통과"]
    basis_amount, field_basis, field = normalized_amount(notice, rules)
    return {"verdict": verdict, "reasons": reasons,
            "rules": {name: {"verdict": v, "reason": r} for name, (v, r) in results.items()},
            # amount_krw: 공고 API 원금액(기존 필드, 호환 유지) · amount_basis_krw: 40억 기준과 같은 VAT 기준으로 환산한 금액
            # (선별·정렬·화면 표시는 이 값을 쓴다). VAT 기준을 모르면 None — 임의로 환산하지 않는다.
            "amount_krw": amount_of(notice), "amount_source_field": field or None, "amount_vat_basis": field_basis,
            "amount_basis_krw": basis_amount, "amount_criterion_basis": criterion_basis(rules),
            "amount_criterion": amount_label(rules), "deadline": (notice.get("bidClseDt") or "").strip() or None}
