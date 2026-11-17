"""① 공고 수집 · ② 규칙 필터 · ②-1 담당자 확인 · ③ 첨부 확보 (G2B 입찰공고정보서비스)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unicodedata
from urllib.parse import urlencode

from dotenv import dotenv_values
import requests

from ..notice_filter import VERDICTS, judge
from . import criteria
from ..settings import PROJECT_ROOT, load_config
from . import runs

# Service02 operation names carry a "02" suffix (unsuffixed names answer 12 "서비스 없음/폐기", checked live 2026-11-17).
# PPSSrch02: keyword+date search; it ignores inqryDiv=2, so single-number lookup uses Servc02.
ENDPOINT = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService02/getBidPblancListInfoServcPPSSrch02"
NOTICE_BY_NUMBER_ENDPOINT = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService02/getBidPblancListInfoServc02"
CHANGE_HISTORY_ENDPOINT = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService02/getBidPblancListInfoChgHstryServc02"
LICENSE_LIMIT_ENDPOINT = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService02/getBidPblancListInfoLicenseLimit02"
CHANGE_FIELDS = ("bidNtceOrd", "chgDt", "chgDataDivNm", "chgItemNm", "bfchgVal", "afchgVal")
ATTACHMENT_INFO_ENDPOINT = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService02/getBidPblancListInfoAtchFileInfo02"
ENV_FILE = PROJECT_ROOT / ".env"
KEY_OVERRIDE: str | None = None  # tests only
MAX_WINDOW_DAYS = 7
# Seen in real G2B attachment names: (입찰)공고문, (재)공고서, 입찰공고서
NOTICE_TERMS = ("공고문", "공고서", "입찰공고")
KEPT_FIELDS = ("bidNtceNo", "bidNtceOrd", "bidNtceNm", "ntceInsttNm", "dminsttNm", "asignBdgtAmt", "presmptPrce",
               "bidClseDt", "bidNtceDt", "cntrctCnclsMthdNm", "sucsfbidMthdNm", "pubPrcrmntLrgClsfcNm", "bidNtceDtlUrl")
_store_lock = threading.Lock()


class NoticeError(Exception):
    """User-facing (Korean) collection failure; never contains the service key."""


def STORE() -> Path:  # noqa: N802 — resolved per call so tests can redirect runs.OUTPUTS
    return runs.OUTPUTS / "notices/notices.json"


def _key() -> str:
    if KEY_OVERRIDE is not None:
        return KEY_OVERRIDE
    env = {**dotenv_values(ENV_FILE), **os.environ}
    return env.get("G2B_BID_NOTICE_KEY") or env.get("G2B_SERVICE_KEY") or ""


def notice_id(item: dict) -> str:
    return f"{item['bidNtceNo']}-{item.get('bidNtceOrd') or '000'}"


def classify(names: list[str]) -> dict[str, str]:
    kinds = {}
    for name in names:
        n = unicodedata.normalize("NFC", name)
        pdf = n.lower().endswith(".pdf")
        if pdf and any(t in n for t in NOTICE_TERMS):
            kinds[name] = "notice"
        elif pdf and any(t in n.upper() for t in ("제안요청", "과업", "RFP")):
            kinds[name] = "rfp"
        else:
            kinds[name] = "pdf" if pdf else "other"
    return kinds


def _attachments(item: dict) -> list[dict]:
    pairs = [(item.get(f"ntceSpecFileNm{i}"), item.get(f"ntceSpecDocUrl{i}")) for i in range(1, 11)]
    pairs = [(name, url) for name, url in pairs if name and url]
    kinds = classify([name for name, _ in pairs])
    return [{"name": name, "url": url, "kind": kinds[name]} for name, url in pairs]


def fetch_notice_attachment_info(bid_ntce_no: str, bid_ntce_ord: str = "000", session=None) -> dict:
    """Fetch authoritative attachment metadata for one service notice number/order."""
    bid_ntce_no = str(bid_ntce_no).strip()
    bid_ntce_ord = str(bid_ntce_ord or "000").strip().zfill(3)
    service_key = _key()
    if not service_key:
        raise NoticeError("G2B_BID_NOTICE_KEY(또는 G2B_SERVICE_KEY)가 없어 첨부정보를 조회할 수 없습니다.")
    session = session or requests.Session()
    params = {
        "serviceKey": service_key, "type": "json", "numOfRows": 100, "pageNo": 1,
        "inqryDiv": "2", "bidNtceNo": bid_ntce_no, "bidNtceOrd": bid_ntce_ord,
        "bsnsDivCd": "5",
    }
    try:
        response = session.get(ATTACHMENT_INFO_ENDPOINT, params=params, timeout=60)
    except requests.RequestException as exc:
        raise NoticeError(f"나라장터 첨부정보 조회 연결 실패({type(exc).__name__}).") from None
    try:
        payload = response.json()
    except ValueError:
        reason = re.search(r"<(?:returnAuthMsg|errMsg|resultMsg)>([^<]+)<", getattr(response, "text", "") or "")
        raise NoticeError(f"나라장터 첨부정보 API 오류(HTTP {response.status_code}): "
                          f"{reason.group(1) if reason else '응답을 해석하지 못했어요'}") from None
    envelope = payload.get("response") or next(
        (value for key, value in payload.items() if key.endswith("ResponseError")), None,
    )
    header = (envelope or {}).get("header") or (payload.get("OpenAPI_ServiceResponse") or {}).get("cmmMsgHeader") or {}
    code = str(header.get("resultCode", header.get("returnReasonCode", "99")))
    if code not in ("00", "0"):
        message = header.get("resultMsg") or header.get("returnAuthMsg") or "알 수 없는 오류"
        raise NoticeError(f"나라장터 첨부정보 API 오류 {code}: {message}")
    body = (envelope or {}).get("body") or {}
    page = body.get("items") or []
    page = page.get("item", []) if isinstance(page, dict) else page
    records = [item for item in page if isinstance(item, dict)]
    attachments = []
    for item in records:
        record_no = str(item.get("bidNtceNo") or bid_ntce_no).strip()
        record_order = str(item.get("bidNtceOrd") or bid_ntce_ord).strip().zfill(3)
        if record_no != bid_ntce_no or record_order != bid_ntce_ord:
            continue
        name = next((item.get(key) for key in ("fileNm", "atchFileNm", "atchFileName", "ntceSpecFileNm")
                     if item.get(key)), None)
        url = next((item.get(key) for key in ("fileUrl", "atchFileUrl", "atchFileURL", "ntceSpecDocUrl")
                    if item.get(key)), None)
        document_kind = next((item.get(key) for key in ("docDivNm", "atchDocDivNm", "atchDocNm", "atchDocTypeNm")
                              if item.get(key)), "")
        if not name or not url:
            continue
        kind = classify([str(name)]).get(str(name), "other")
        kind_text = str(document_kind)
        # The file name wins: G2B labels converted RFPs "공고서(변환본)" (seen live on R26BK01751619).
        if kind == "pdf" and any(word in kind_text for word in ("공고", "공고서")):
            kind = "notice"
        elif kind == "pdf" and any(word in kind_text for word in ("제안요청", "과업", "규격")):
            kind = "rfp"
        attachments.append({"name": str(name), "url": str(url), "kind": kind,
                            "document_kind": kind_text, "bidNtceNo": record_no, "bidNtceOrd": record_order})
    return {
        "attachments": attachments,
        "status": "found" if attachments else "empty",
        "lookup_key": {"bidNtceNo": bid_ntce_no, "bidNtceOrd": bid_ntce_ord, "bsnsDivCd": "5"},
        "api_operation": "BidPublicInfoService02/getBidPblancListInfoAtchFileInfo02",
        "api_source_url": "https://www.data.go.kr/data/15129394/openapi.do",
        "retrieved_at": datetime.now(runs.KST).isoformat(timespec="seconds"),
    }


def fetch_notice_change_history(bid_ntce_no: str, session=None) -> dict:
    """Fetch 용역변경이력 rows for one notice number (all orders): 정정 차수, 변경항목, 변경 전/후 값."""
    bid_ntce_no = str(bid_ntce_no).strip()
    if not _key():
        raise NoticeError("G2B_BID_NOTICE_KEY(또는 G2B_SERVICE_KEY)가 없어 변경이력을 조회할 수 없습니다.")
    session = session or requests.Session()
    params = {"serviceKey": _key(), "type": "json", "numOfRows": 999, "pageNo": 1, "inqryDiv": "2", "bidNtceNo": bid_ntce_no}
    try:
        payload = session.get(CHANGE_HISTORY_ENDPOINT, params=params, timeout=60).json()
    except requests.RequestException as exc:
        raise NoticeError(f"나라장터 변경이력 조회 연결 실패({type(exc).__name__}).") from None
    except ValueError:
        raise NoticeError("나라장터 변경이력 API 응답을 해석하지 못했어요.") from None
    envelope = payload.get("response") or next((v for k, v in payload.items() if k.endswith("ResponseError")), None) or {}
    header = envelope.get("header") or {}
    code = str(header.get("resultCode", "99"))
    if code not in ("00", "0"):
        raise NoticeError(f"나라장터 변경이력 API 오류 {code}: {header.get('resultMsg') or '알 수 없는 오류'}")
    page = (envelope.get("body") or {}).get("items") or []
    page = page.get("item", []) if isinstance(page, dict) else page
    changes = [{field: item.get(field) for field in CHANGE_FIELDS}
               for item in page if isinstance(item, dict) and str(item.get("bidNtceNo", "")).strip() == bid_ntce_no]
    changes.sort(key=lambda row: str(row.get("chgDt") or ""))
    return {
        "status": "changes_found" if changes else "no_changes",
        "changes": changes,
        "api_operation": "BidPublicInfoService02/getBidPblancListInfoChgHstryServc02",
        "api_source_url": "https://www.data.go.kr/data/15129394/openapi.do",
        "retrieved_at": datetime.now(runs.KST).isoformat(timespec="seconds"),
    }


def fetch_notice_license_limits(bid_ntce_no: str, bid_ntce_ord: str = "000", session=None) -> dict:
    """Fetch 면허(업종) 제한 rows for one notice number/order (참가자격 교차검증용)."""
    bid_ntce_no, bid_ntce_ord = str(bid_ntce_no).strip(), str(bid_ntce_ord or "000").strip().zfill(3)
    if not _key():
        raise NoticeError("G2B_BID_NOTICE_KEY(또는 G2B_SERVICE_KEY)가 없어 면허제한정보를 조회할 수 없습니다.")
    session = session or requests.Session()
    params = {"serviceKey": _key(), "type": "json", "numOfRows": 100, "pageNo": 1, "inqryDiv": "2",
              "bidNtceNo": bid_ntce_no, "bidNtceOrd": bid_ntce_ord}
    try:
        payload = session.get(LICENSE_LIMIT_ENDPOINT, params=params, timeout=60).json()
    except requests.RequestException as exc:
        raise NoticeError(f"나라장터 면허제한정보 조회 연결 실패({type(exc).__name__}).") from None
    except ValueError:
        raise NoticeError("나라장터 면허제한정보 API 응답을 해석하지 못했어요.") from None
    envelope = payload.get("response") or next((v for k, v in payload.items() if k.endswith("ResponseError")), None) or {}
    header = envelope.get("header") or {}
    if str(header.get("resultCode", "99")) not in ("00", "0"):
        raise NoticeError(f"나라장터 면허제한정보 API 오류 {header.get('resultCode')}: {header.get('resultMsg') or '알 수 없는 오류'}")
    page = (envelope.get("body") or {}).get("items") or []
    page = page.get("item", []) if isinstance(page, dict) else page
    limits = [{"name": str(item.get("lcnsLmtNm") or "").strip(), "group": item.get("lmtGrpNo"),
               "permitted_industries": item.get("permsnIndstrytyList") or ""}
              for item in page if isinstance(item, dict)
              and str(item.get("bidNtceNo", "")).strip() == bid_ntce_no
              and str(item.get("bidNtceOrd", bid_ntce_ord)).strip().zfill(3) == bid_ntce_ord
              and str(item.get("lcnsLmtNm") or "").strip()]
    return {"status": "found" if limits else "empty", "limits": limits,
            "api_operation": "BidPublicInfoService02/getBidPblancListInfoLicenseLimit02",
            "retrieved_at": datetime.now(runs.KST).isoformat(timespec="seconds")}


def compare_notice_pdf_bytes(attachments: list[dict], notice_pdf_path: Path | None, session=None) -> dict:
    """Compare the current G2B notice attachment bytes with the saved local notice PDF."""
    if not notice_pdf_path or not Path(notice_pdf_path).is_file():
        return {"status": "local_pdf_missing"}
    local_path = Path(notice_pdf_path)
    local_sha256 = hashlib.sha256(local_path.read_bytes()).hexdigest()
    candidates = [item for item in attachments if item.get("kind") == "notice"]
    if not candidates:
        return {"status": "g2b_notice_attachment_missing", "local_sha256": local_sha256}
    attachment = candidates[0]
    url = str(attachment.get("url") or "")
    if not url.startswith("https://"):
        return {"status": "invalid_attachment_url", "local_sha256": local_sha256,
                "attachment_name": attachment.get("name")}
    session = session or requests.Session()
    try:
        response = session.get(url, timeout=90)
        content = response.content
    except requests.RequestException as exc:
        return {"status": "download_unavailable", "error": type(exc).__name__,
                "local_sha256": local_sha256, "attachment_name": attachment.get("name"), "source_url": url}
    if len(content) > 50 * 1024 * 1024:
        return {"status": "download_too_large", "local_sha256": local_sha256,
                "attachment_name": attachment.get("name"), "source_url": url}
    if not content.startswith(b"%PDF-"):
        return {"status": "download_not_pdf", "local_sha256": local_sha256,
                "attachment_name": attachment.get("name"), "source_url": url}
    remote_sha256 = hashlib.sha256(content).hexdigest()
    return {
        "status": "byte_match" if local_sha256 == remote_sha256 else "byte_mismatch",
        "attachment_name": attachment.get("name"),
        "document_kind": attachment.get("document_kind"),
        "source_url": url,
        "local_sha256": local_sha256,
        "g2b_sha256": remote_sha256,
        "checked_at": datetime.now(runs.KST).isoformat(timespec="seconds"),
    }


def _fetch(session, keyword: str, begin: date, end: date) -> list[dict]:
    params = {"serviceKey": _key(), "type": "json", "numOfRows": 999, "pageNo": 1, "inqryDiv": "1",
              "inqryBgnDt": begin.strftime("%Y%m%d0000"), "inqryEndDt": end.strftime("%Y%m%d2359"), "bidNtceNm": keyword}
    items: list[dict] = []
    while True:
        try:
            response = session.get(f"{ENDPOINT}?{urlencode(params, safe='%')}", timeout=60)
        except requests.RequestException as exc:
            raise NoticeError(f"나라장터 연결에 실패했어요({type(exc).__name__}).") from None
        try:
            payload = response.json()
        except ValueError:  # requests' JSONDecodeError is a ValueError: data.go.kr answers key errors in XML
            reason = re.search(r"<(?:returnAuthMsg|errMsg|resultMsg)>([^<]+)<", getattr(response, "text", "") or "")
            raise NoticeError(f"나라장터 입찰공고 API 오류(HTTP {response.status_code}): "
                              f"{reason.group(1) if reason else '응답을 해석하지 못했어요'}") from None
        envelope = payload.get("response") or next((v for k, v in payload.items() if k.endswith("ResponseError")), None)
        header = (envelope or {}).get("header") or (payload.get("OpenAPI_ServiceResponse") or {}).get("cmmMsgHeader") or {}
        code = str(header.get("resultCode", header.get("returnReasonCode", "99")))
        if code not in ("00", "0"):
            message = header.get("resultMsg") or header.get("returnAuthMsg") or "알 수 없는 오류"
            raise NoticeError(f"나라장터 입찰공고 API 오류 {code}: {message}")
        body = envelope.get("body") or {}
        page = body.get("items") or []
        page = page.get("item", []) if isinstance(page, dict) else page
        items += [i for i in page if isinstance(i, dict) and i.get("bidNtceNo")]
        if params["pageNo"] * params["numOfRows"] >= int(body.get("totalCount") or 0):
            return items
        params["pageNo"] += 1


def fetch_notice_by_number(bid_ntce_no: str, bid_ntce_ord: str = "000", session=None) -> dict | None:
    """Fetch one G2B notice by its stable number and order, including attachment links.

    This returns the structured record and source metadata; it does not claim
    that attachment contents have been downloaded or reviewed.
    """
    bid_ntce_no = str(bid_ntce_no).strip()
    bid_ntce_ord = str(bid_ntce_ord or "000").strip().zfill(3)
    if not bid_ntce_no:
        raise NoticeError("공고번호가 없어 나라장터 단건 조회를 할 수 없습니다.")
    service_key = _key()
    if not service_key:
        raise NoticeError("G2B_BID_NOTICE_KEY(또는 G2B_SERVICE_KEY)가 없어 나라장터 API를 조회할 수 없습니다.")
    session = session or requests.Session()
    params = {
        "serviceKey": service_key, "type": "json", "numOfRows": 100, "pageNo": 1,
        "inqryDiv": "2", "bidNtceNo": bid_ntce_no, "bidNtceOrd": bid_ntce_ord,
    }
    try:
        response = session.get(NOTICE_BY_NUMBER_ENDPOINT, params=params, timeout=60)
    except requests.RequestException as exc:
        raise NoticeError(f"나라장터 단건 조회 연결 실패({type(exc).__name__}).") from None
    try:
        payload = response.json()
    except ValueError:
        reason = re.search(r"<(?:returnAuthMsg|errMsg|resultMsg)>([^<]+)<", getattr(response, "text", "") or "")
        raise NoticeError(f"나라장터 단건 조회 API 오류(HTTP {response.status_code}): "
                          f"{reason.group(1) if reason else '응답을 해석하지 못했어요'}") from None

    envelope = payload.get("response") or next(
        (value for key, value in payload.items() if key.endswith("ResponseError")), None,
    )
    header = (envelope or {}).get("header") or (payload.get("OpenAPI_ServiceResponse") or {}).get("cmmMsgHeader") or {}
    code = str(header.get("resultCode", header.get("returnReasonCode", "99")))
    if code not in ("00", "0"):
        message = header.get("resultMsg") or header.get("returnAuthMsg") or "알 수 없는 오류"
        raise NoticeError(f"나라장터 단건 조회 API 오류 {code}: {message}")
    body = (envelope or {}).get("body") or {}
    page = body.get("items") or []
    page = page.get("item", []) if isinstance(page, dict) else page
    matches = [item for item in page if isinstance(item, dict)
               and str(item.get("bidNtceNo", "")).strip() == bid_ntce_no
               and str(item.get("bidNtceOrd", "000")).strip().zfill(3) == bid_ntce_ord]
    if not matches:
        return None
    record = matches[0]
    try:
        attachment_lookup = fetch_notice_attachment_info(bid_ntce_no, bid_ntce_ord, session=session)
    except NoticeError as exc:
        attachment_lookup = {
            "status": "unavailable", "error": str(exc),
            "api_operation": "BidPublicInfoService02/getBidPblancListInfoAtchFileInfo02",
            "api_source_url": "https://www.data.go.kr/data/15129394/openapi.do",
        }
        attachments = [{**item, "metadata_source": "notice_list_fallback"} for item in _attachments(record)]
    else:
        attachments = [{**item, "metadata_source": attachment_lookup["api_operation"]}
                       for item in attachment_lookup["attachments"]]
    try:
        license_limits = fetch_notice_license_limits(bid_ntce_no, bid_ntce_ord, session=session)
    except NoticeError as exc:
        license_limits = {"status": "unavailable", "error": str(exc), "limits": []}
    try:
        change_history = fetch_notice_change_history(bid_ntce_no, session=session)
    except NoticeError as exc:
        change_history = {"status": "unavailable", "error": str(exc), "changes": []}
    return {
        "notice": record,
        "attachments": attachments,
        "attachment_lookup": attachment_lookup,
        "change_history": change_history,
        "license_limits": license_limits,
        "source_url": record.get("bidNtceDtlUrl"),
        "api_source_url": "https://www.data.go.kr/data/15129394/openapi.do",
        "api_operation": "BidPublicInfoService02/getBidPblancListInfoServc02",
        "lookup_key": {"bidNtceNo": bid_ntce_no, "bidNtceOrd": bid_ntce_ord},
        "retrieved_at": datetime.now(runs.KST).isoformat(timespec="seconds"),
    }


def load_notices() -> dict:
    path = STORE()
    if not path.is_file():
        return {"runs": [], "notices": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _save(store: dict) -> None:
    path = STORE()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        json.dump(store, handle, ensure_ascii=False, indent=1)
    os.replace(handle.name, path)


def _final_verdict(entry: dict) -> str:
    review = entry.get("review")
    if review:
        return "검토 대상" if review["include"] else "제외"
    return entry["judged"]["verdict"]


def collect(days: int, keywords: list[str], session=None, now: datetime | None = None) -> dict:
    if not _key():
        raise NoticeError(".env에 G2B_BID_NOTICE_KEY(또는 G2B_SERVICE_KEY) 키가 없어요.")
    session = session or requests.Session()
    now = now or datetime.now(runs.KST).replace(tzinfo=None)
    active = criteria.resolve()
    rules = active["notice_filter"]
    fetched: dict[str, dict] = {}
    end, start = now.date(), now.date() - timedelta(days=days)
    # 빈 목록 = 키워드 없이 전체 조회(M27). 공고명 키워드 5개는 규칙 통과 공고의 59%를 놓쳤다(M26).
    for keyword in keywords or [""]:
        window_end = end
        while window_end >= start:
            window_start = max(start, window_end - timedelta(days=MAX_WINDOW_DAYS - 1))
            for item in _fetch(session, keyword, window_start, window_end):
                fetched[notice_id(item)] = item
            window_end = window_start - timedelta(days=1)
    counts = dict.fromkeys(VERDICTS, 0)
    skipped_non_ict = 0
    with _store_lock:  # every network call succeeded; only now touch the store
        store = load_notices()
        for nid, item in fetched.items():
            previous = store["notices"].get(nid, {})
            judged = judge(item, rules, now)
            # ponytail: 전체 수집이면 30일 약 1.4만 건이 ICT 분야가 아니다(M26). 저장하지 않고 건수만 남긴다.
            # 담당자가 이미 손댄 공고(review/case_folder)는 계속 갱신한다.
            if judged["rules"]["field"]["verdict"] == "제외" and not (previous.get("review") or previous.get("case_folder")):
                skipped_non_ict += 1
                counts["제외"] += 1
                continue
            entry = {**{f: item.get(f, "") for f in KEPT_FIELDS}, "attachments": _attachments(item),
                     "judged": {**judged, "criteria_set": active["name"]}, "review": previous.get("review"),
                     "case_folder": previous.get("case_folder")}
            entry["verdict"] = _final_verdict(entry)
            counts[entry["verdict"]] += 1
            store["notices"][nid] = entry
        at = datetime.now(runs.KST).isoformat(timespec="seconds")
        store["runs"].append({"at": at, "days": days, "keywords": keywords, "count": len(fetched),
                              "skipped_non_ict": skipped_non_ict})
        _save(store)
    return {"collected": len(fetched), "total": len(store["notices"]), "by_verdict": counts, "at": at,
            "skipped_non_ict": skipped_non_ict}


def set_review(nid: str, include: bool, reason: str, role: str) -> dict:
    if not reason or not reason.strip():
        raise ValueError("포함/제외 사유를 입력하세요")
    with _store_lock:
        store = load_notices()
        entry = store["notices"][nid]  # KeyError → 404 in the API
        entry["review"] = {"include": include, "reason": reason.strip(), "role": role,
                           "at": datetime.now(runs.KST).isoformat(timespec="seconds")}
        entry["verdict"] = _final_verdict(entry)
        _save(store)
    return entry


def attach_case(nid: str, case_folder: str) -> None:
    with _store_lock:
        store = load_notices()
        store["notices"][nid]["case_folder"] = case_folder
        _save(store)


def fetch_attachments(nid: str, folder: Path, session=None) -> dict:
    session = session or requests.Session()
    entry = load_notices()["notices"][nid]
    pdfs = [a for a in entry["attachments"] if a["kind"] != "other"]
    rfp_candidates = [a for a in pdfs if a["kind"] == "rfp"] + [a for a in pdfs if a["kind"] == "pdf"]
    targets = {"notice.pdf": next((a for a in pdfs if a["kind"] == "notice"), None)}
    result = {"rfp": False, "notice": False, "skipped": [], "rfp_guessed": False}
    for attachment in rfp_candidates:  # first downloadable real PDF wins
        if _download(session, attachment, folder / "rfp.pdf"):
            result["rfp"] = True
            result["rfp_guessed"] = attachment["kind"] == "pdf"  # no 제안요청/과업/RFP hint in the name
            break
        result["skipped"].append(attachment["name"])
    if targets["notice.pdf"]:
        if _download(session, targets["notice.pdf"], folder / "notice.pdf"):
            result["notice"] = True
        else:
            result["skipped"].append(targets["notice.pdf"]["name"])
    return result


def _download(session, attachment: dict, target: Path) -> bool:
    try:
        content = session.get(attachment["url"], timeout=120).content
    except requests.RequestException:
        return False
    if not content.startswith(b"%PDF-"):
        return False
    target.write_bytes(content)
    return True
