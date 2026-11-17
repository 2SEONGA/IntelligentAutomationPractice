"""Go/No-Go checklist auto-assessment (config/checklist.yaml).

Missing evidence stays undecided: knockout items fall back to `확인 필요`, scoring items to None.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any

UNKNOWN = "확인 필요"
EOK = 100_000_000
SIMILAR_NAME_RATIO = 0.6


def evaluate(report: dict, rfp: dict | None, capability: dict | None, checklist: dict, awards: list[dict]) -> list[dict]:
    rfp, capability = rfp or {}, capability or {}
    summary = rfp.get("project_summary") or {}
    scale = report.get("project_scale") or {}
    buyer = _value(rfp.get("basic_info"), "procuring_entity") or summary.get("buyer")
    name = summary.get("project_name") or _value(rfp.get("basic_info"), "project_name") or ""
    previous = _previous_round(buyer, name, awards)
    scoring = {item["id"]: item for item in checklist["scoring"]}

    judged: dict[str, tuple[Any, str, int | None]] = {
        "K1": (*_aggregate(capability.get("eligibility_matches")), None),
        "K2": (UNKNOWN, _scale_note(scale.get("amount_krw")), scale.get("source_page")),
        "K3": (UNKNOWN, "공동수급·지역 조건은 공고문 원문으로 확인", None),
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
