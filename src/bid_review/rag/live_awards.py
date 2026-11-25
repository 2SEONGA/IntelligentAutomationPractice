"""남은 업무 5번 A안: T3 뒤에서 나라장터 낙찰정보를 실시간으로 다시 조회해 T3 후보에 붙인다(실험용, 기본 꺼짐).

B안(기본·현재 운영)은 T3 색인을 만들 때 받아 둔 낙찰정보(data/past_rfps/awards.json)만 쓴다.
A안은 같은 후보에 대해 **후보 자신의 공고번호**로 낙찰정보서비스를 다시 조회한다.

연결 규칙(기존 T2·T3 검증과 같음):
- 후보 공고번호와 낙찰 레코드의 공고번호가 정확히 같을 때만 붙인다. 재공고 묶음의 다른 공고번호 낙찰은 붙이지 않는다.
- 레코드 공고명이 후보 사업명과 다르면(공백·기호 무시) 붙이지 않고 '이름 불일치'로 남긴다.
- API 실패·키 미등록이면 색인의 낙찰정보를 그대로 두고 실패 사유만 남긴다(보고서가 멈추지 않는다).
- 서비스 키는 결과·오류 어디에도 남기지 않는다.
"""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any

import requests

AWARD_BASE = "https://apis.data.go.kr/1230000/as/ScsbidInfoService"
AWARD_OPERATION = "getScsbidListSttusServc"  # inqryDiv=4: 입찰공고번호로 조회
DATASET_URL = "https://www.data.go.kr/data/15129397/openapi.do"
MAX_LOOKUPS = 10  # 후보 10건까지만 조회(호출량 관리)
_SQUASH = re.compile(r"[\s\W_]+")


class AwardLookupError(Exception):
    """사용자에게 보여 줄 수 있는 실패 사유(키 없음)."""


def _key() -> str:
    from dotenv import dotenv_values

    from ..settings import PROJECT_ROOT
    env = dotenv_values(PROJECT_ROOT / ".env")
    return env.get("G2B_SERVICE_KEY") or env.get("G2B_BID_NOTICE_KEY") or ""


def fetch_awards_by_notice(bid_ntce_no: str, *, session=None, key: str | None = None) -> list[dict[str, Any]]:
    """한 공고번호의 낙찰 레코드(모든 차수). 실패하면 AwardLookupError(키를 담지 않는다)."""
    key = _key() if key is None else key
    if not key:
        raise AwardLookupError("나라장터 서비스 키가 없습니다.")
    session = session or requests.Session()
    params = {"serviceKey": key, "type": "json", "numOfRows": 50, "pageNo": 1, "inqryDiv": "4", "bidNtceNo": bid_ntce_no}
    try:
        response = session.get(f"{AWARD_BASE}/{AWARD_OPERATION}", params=params, timeout=30)
    except requests.RequestException as exc:
        raise AwardLookupError(f"연결 실패({type(exc).__name__})") from None
    try:
        payload = response.json()
    except ValueError:
        raise AwardLookupError(f"응답 해석 실패(HTTP {getattr(response, 'status_code', '?')})") from None
    header = ((payload.get("OpenAPI_ServiceResponse") or {}).get("cmmMsgHeader")
              or (payload.get("response") or next((v for k, v in payload.items() if k.endswith("ResponseError")), {}) or {}).get("header")
              or {})
    code = str(header.get("resultCode", header.get("returnReasonCode", "00" if "response" in payload else "99")))
    if code not in ("00", "0"):
        message = header.get("returnAuthMsg") or header.get("resultMsg") or "알 수 없는 오류"
        raise AwardLookupError(f"낙찰정보 API 오류 {code}: {message}")
    items = ((payload.get("response") or {}).get("body") or {}).get("items") or []
    items = items.get("item", []) if isinstance(items, dict) else items
    items = [items] if isinstance(items, dict) else items
    return [item for item in items if isinstance(item, dict)]


def _award_fields(record: dict[str, Any]) -> dict[str, Any]:
    keep = ("bidNtceNo", "bidNtceOrd", "bidNtceNm", "bidwinnrNm", "bidwinnrBizno", "sucsfbidAmt", "sucsfbidRate",
            "rlOpengDt", "fnlSucsfDate", "dminsttNm", "prtcptCnum")
    return {key: record.get(key) for key in keep if record.get(key) not in (None, "")}


def refresh_awards(similar_projects: list[dict[str, Any]], *, fetch=fetch_awards_by_notice,
                   max_lookups: int = MAX_LOOKUPS, now: str | None = None) -> dict[str, Any]:
    """T3 후보마다 낙찰정보를 실시간으로 다시 조회한다. 반환: {"similar_projects": [...], "live_award_lookup": {...}}."""
    checked_at = now or datetime.now().astimezone().isoformat(timespec="seconds")
    updated, results, calls = [], [], 0
    failure: str | None = None
    for project in similar_projects:
        project = dict(project)
        notice_id = str(project.get("notice_id") or "")
        number = notice_id.split("-")[0]
        index_award = project.get("award")
        row = {"notice_id": notice_id, "title": project.get("title"), "index_award": bool(index_award)}
        if not number or calls >= max_lookups or failure:
            row["status"] = "not_checked" if not failure else "unavailable"
            row["reason"] = failure or ("공고번호 없음" if not number else f"조회 상한 {max_lookups}건 도달")
            results.append(row)
            updated.append(project)
            continue
        calls += 1
        try:
            records = fetch(number)
        except AwardLookupError as exc:
            failure = str(exc)  # 키 미등록·장애는 모든 후보가 같으므로 더 부르지 않는다
            row.update(status="unavailable", reason=failure)
            results.append(row)
            updated.append(project)
            continue
        same = [r for r in records if str(r.get("bidNtceNo") or "").strip() == number]
        named = [r for r in same if not r.get("bidNtceNm")
                 or _SQUASH.sub("", str(r["bidNtceNm"])) == _SQUASH.sub("", str(project.get("title") or ""))]
        if same and not named:
            row.update(status="name_mismatch", reason="공고번호는 같지만 공고명이 후보 사업명과 달라 연결하지 않음",
                       live_titles=sorted({str(r.get("bidNtceNm")) for r in same})[:3])
        elif not named:
            row["status"] = "no_award_yet" if not index_award else "index_only"
            row["reason"] = "실시간 조회에서 이 공고번호의 낙찰 레코드가 없음" + (" (색인 값은 유지)" if index_award else "")
        else:
            latest = max(named, key=lambda r: (str(r.get("bidNtceOrd") or ""), str(r.get("rlOpengDt") or "")))
            live = _award_fields(latest)
            old = {k: (index_award or {}).get(k) for k in ("bidwinnrNm", "sucsfbidAmt")}
            new = {k: live.get(k) for k in ("bidwinnrNm", "sucsfbidAmt")}
            row["status"] = "new_award" if not index_award else ("same_as_index" if old == new else "updated")
            if row["status"] == "updated":
                row["index_values"], row["live_values"] = old, new
            project.update(award=live, award_notice_id=f"{number}-{str(latest.get('bidNtceOrd') or '000').zfill(3)}",
                           award_source_url=project.get("notice_url") or project.get("award_source_url"),
                           award_live_checked_at=checked_at, award_source="live_api")
        results.append(row)
        updated.append(project)
    summary = {"status": "unavailable" if failure and calls == 1 else "done", "api_calls": calls, "checked_at": checked_at,
               "api_operation": f"ScsbidInfoService/{AWARD_OPERATION}", "api_source_url": DATASET_URL,
               "error": failure, "results": results}
    return {"similar_projects": updated, "live_award_lookup": summary}
