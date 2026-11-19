"""Go/No-Go checklist auto-assessment (config/checklist.yaml).

Missing evidence stays undecided: knockout items fall back to `확인 필요`, scoring items to None.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any

from .notice_filter import normalized_amount

UNKNOWN = "확인 필요"
EOK = 100_000_000
SIMILAR_NAME_RATIO = 0.6


def evaluate(report: dict, rfp: dict | None, capability: dict | None, checklist: dict, awards: list[dict],
             core_review: dict | None = None) -> list[dict]:
    """core_review: LangGraph T1.5 결과(langgraph_review.json). 있으면 K2·K3를 확인된 나라장터 값으로 판정한다."""
    rfp, capability = rfp or {}, capability or {}
    summary = rfp.get("project_summary") or {}
    scale = report.get("project_scale") or {}
    buyer = _value(rfp.get("basic_info"), "procuring_entity") or summary.get("buyer")
    name = summary.get("project_name") or _value(rfp.get("basic_info"), "project_name") or ""
    previous = _previous_round(buyer, name, awards)
    scoring = {item["id"]: item for item in checklist["scoring"]}

    k2_rule = next(item for item in checklist["knockout"] if item["id"] == "K2")
    judged: dict[str, tuple[Any, str, int | None]] = {
        "K1": (*_aggregate(capability.get("eligibility_matches")), None),
        "K2": _large_firm_limit(core_review, k2_rule) or (UNKNOWN, _scale_note(scale.get("amount_krw")), scale.get("source_page")),
        "K3": _joint_and_region(core_review) or (UNKNOWN, "공동수급·지역 조건은 공고문 원문으로 확인", None),
        "K4": (*_aggregate(capability.get("staffing_matches")), None),
        "S1": (_amount_points(scale.get("amount_krw")), _scale_note(scale.get("amount_krw")), scale.get("source_page")),
        "S2": _business_type(f"{name} {summary.get('scope') or ''}", scoring["S2"]["keywords"]),
        "S3": _agency_points(buyer, awards, scoring["S3"]["core_agencies"]),
        "S4": (None, "발주기관 또는 사업명 미확인", None) if not (buyer and name) else
              (2, f"직전 차수: {previous['bidNtceNm']} ({previous.get('fnlSucsfDate', '')})", None) if previous else
              (0, "같은 기관의 유사 사업 수주 이력 없음", None),
        "S5": (None, "발주기관 또는 사업명 미확인", None) if not (buyer and name) else
              (_competition_points(previous.get("prtcptCnum")), f"직전 차수 참여 {previous.get('prtcptCnum')}개사", None) if previous else
              (None, "직전 차수 없음", None),
    }
    items = []
    for kind, entries in (("knockout", checklist["knockout"]), ("scoring", checklist["scoring"])):
        for entry in entries:
            ai, evidence, page = judged[entry["id"]]
            items.append({"id": entry["id"], "title": entry["title"], "kind": kind, "ai": ai, "value": ai,
                          "overridden": False, "evidence": evidence, "source_page": page})
    return items


def apply_overrides(items: list[dict], overrides: dict[str, Any]) -> list[dict]:
    return [{**item, "value": overrides[item["id"]], "overridden": overrides[item["id"]] != item["ai"]}
            if item["id"] in overrides else dict(item) for item in items]


def summarize(items: list[dict], checklist: dict) -> dict:
    knockout = [item["value"] for item in items if item["kind"] == "knockout"]
    points = [item["value"] for item in items if item["kind"] == "scoring"]
    score = sum(p for p in points if p is not None)
    undetermined = sum(p is None for p in points)
    score_max = score + 2 * undetermined  # every undecided item could still score 2
    decision = checklist["decision"]

    def band(total: int) -> str:
        return "Go 권고" if total >= decision["go"] else "사업본부장 검토" if total >= decision["review"] else "No-Go 권고"

    result = "No-Go" if "미충족" in knockout else "보류" if UNKNOWN in knockout else "통과"
    if result == "No-Go":
        recommendation = "No-Go 권고"
    elif result == "보류" or band(score) != band(score_max):  # undecided items could change the band
        recommendation = "보류"
    else:
        recommendation = band(score)
    return {"knockout_result": result, "knockout_passed": knockout.count("충족"), "score": score, "score_max": score_max,
            "undetermined": undetermined, "recommendation": recommendation}


def _claims(core_review: dict | None) -> dict[str, dict]:
    categories = ((core_review or {}).get("verification") or {}).get("categories") or {}
    return {claim["field"]: claim for category in categories.values() for claim in category.get("claims", [])}


def _g2b_notice(core_review: dict | None) -> dict:
    return ((core_review or {}).get("g2b_notice_lookup") or {}).get("notice") or {}


def _large_firm_limit(core_review: dict | None, rule: dict) -> tuple[str, str, None] | None:
    """K2: 나라장터 금액이 RFP와 일치(T1.5 confirmed)할 때만 대기업 참여 하한과 비교한다. 아니면 None(기존 확인 필요)."""
    claims, notice = _claims(core_review), _g2b_notice(core_review)
    crosschecked = any(claims.get(field, {}).get("status") == "confirmed"
                       for field in ("g2b_crosscheck_budget_krw", "g2b_crosscheck_estimated_price_krw"))
    if not (crosschecked and notice):
        return None
    minimum, basis = int(rule.get("min_amount_krw", 40 * EOK)), rule.get("amount_vat_basis", "included")
    amount, _, field = normalized_amount(notice, {"min_amount_vat_basis": basis})
    if amount is None:
        return None
    label = "배정예산" if field == "asignBdgtAmt" else "추정가격"
    shown = f"나라장터 {label} 환산 {amount / EOK:,.2f}억 원({'VAT 포함' if basis == 'included' else 'VAT 별도'}, RFP와 일치)"
    clause = next((str(c["claim"]) for c in claims.values()
                   if c.get("category") == "eligibility" and "대기업" in str(c.get("claim")) and "제한" in str(c.get("claim"))), None)
    clause_note = f" · 공고 조항: '{clause}'" if clause else ""
    if amount < minimum:
        return "미충족", f"{shown} < 대기업 참여 하한 {minimum // EOK}억 원{clause_note}", None
    if clause:  # 하한 이상인데 제한 조항이 있으면 예외 인정 여부를 사람이 본다.
        return UNKNOWN, f"{shown} ≥ 하한 {minimum // EOK}억 원이나 대기업 참여 제한 조항 있음{clause_note}", None
    return "충족", f"{shown} ≥ 대기업 참여 하한 {minimum // EOK}억 원", None


def _joint_and_region(core_review: dict | None) -> tuple[str, str, None] | None:
    """K3: 공동수급 방식이 나라장터·RFP에서 일치(confirmed)할 때 판정한다. 지역 제한이 보이면 사람이 본다."""
    joint, notice = _claims(core_review).get("g2b_joint_contract"), _g2b_notice(core_review)
    if not (joint and joint.get("status") == "confirmed" and notice):
        return None
    method = str(joint.get("claim") or "")
    region = notice.get("rgnLmtBidLocplcJdgmBssNm") or notice.get("cmmnSpldmdCorpRgnLmtYn") == "Y" \
        or notice.get("rgnDutyJntcontrctRt") or any(notice.get(f"jntcontrctDutyRgnNm{i}") for i in (1, 2, 3))
    if region:
        return UNKNOWN, f"공동수급 '{method}'(나라장터·RFP 일치) · 지역 제한·지역 의무공동도급 표시 있음 — 본사 소재지 확인", None
    if "의무" in method:
        return UNKNOWN, f"공동수급 '{method}' — 의무 공동수급 구성원 확인", None
    # ponytail: 단독 참여 전제. 하도급 제한은 API에 없어 원문 확인으로 남긴다.
    return "충족", f"공동수급 '{method}'(나라장터·RFP 일치), 단독 참여와 충돌 없음 · 지역 제한 표시 없음(나라장터) · 하도급 제한은 원문 확인", None


def _value(container: dict | None, field: str) -> Any:
    return ((container or {}).get(field) or {}).get("value")


def _aggregate(matches: list[dict] | None) -> tuple[str, str]:
    statuses = [m.get("status") for m in matches or []]
    if not statuses:
        return UNKNOWN, "역량 매칭 항목 없음"
    counts = ", ".join(f"{s} {statuses.count(s)}" for s in dict.fromkeys(statuses))
    if "미충족" in statuses:
        return "미충족", counts
    return ("충족" if all(s == "충족" for s in statuses) else UNKNOWN), counts


def _scale_note(amount: int | None) -> str:
    return "사업금액 원문 확인 필요" if amount is None else f"사업금액 {amount / EOK:,.1f}억 원 (과거 낙찰 하한 40억 원)"


def _amount_points(amount: int | None) -> int | None:
    if amount is None:
        return None
    return 2 if amount >= 100 * EOK else 1 if amount >= 40 * EOK else 0


def _business_type(text: str, keywords: dict) -> tuple[int | None, str, None]:
    for points in (2, 1):
        hit = next((word for word in keywords[points] if word in text), None)
        if hit:
            return points, f"키워드 '{hit}'", None
    return None, "주력 유형 키워드 없음", None


def _agency_points(buyer: str | None, awards: list[dict], core: list[str]) -> tuple[int | None, str, None]:
    if not buyer:
        return None, "발주기관 미확인", None
    if any(agency in buyer for agency in core):
        return 2, f"핵심 기관: {buyer}", None
    count = sum(1 for award in awards if _same_agency(buyer, award.get("dminsttNm", "")))
    return (1, f"3년 내 수주 {count}건", None) if count else (0, "3년 내 수주 이력 없음", None)


def _same_agency(buyer: str, agency: str) -> bool:
    return bool(agency) and (buyer in agency or agency in buyer)


def _core_name(name: str) -> str:
    return re.sub(r"[\d’‘'~년도차\-_()·ㆍ\s]|단계|사업|용역", "", name)


def _previous_round(buyer: str | None, name: str, awards: list[dict]) -> dict | None:
    if not buyer or not name:
        return None
    core = _core_name(name)
    matches = [a for a in awards if _same_agency(buyer, a.get("dminsttNm", ""))
               and SequenceMatcher(None, _core_name(a.get("bidNtceNm", "")), core).ratio() > SIMILAR_NAME_RATIO]
    return max(matches, key=lambda a: a.get("fnlSucsfDate", ""), default=None)


def _competition_points(participants: str | int | None) -> int | None:
    try:
        count = int(participants)
    except (TypeError, ValueError):
        return None
    return 2 if count <= 1 else 1 if count == 2 else 0
