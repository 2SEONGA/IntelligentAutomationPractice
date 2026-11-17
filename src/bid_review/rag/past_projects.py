"""Agent 3 — past public SI projects for similar-project search (M23).

Collection (network): recent 나라장터 용역 notices by the notice-filter keywords → official
첨부파일정보 API → 제안요청서 PDF → a short project profile (공고명·수요기관·사업 개요/목적/범위
excerpt, contact rows removed). 낙찰 results are joined only by an exact 공고번호 match.

Search: one OpenAI embedding per project profile; the current RFP gets the same profile and is
compared by cosine similarity. Similarity is a lead for a human, never a "same project" verdict.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from ..notice_filter import amount_of
from ..settings import PROJECT_ROOT, load_config
from .corpus import _privacy_filter

PAST_DIR = PROJECT_ROOT / "data/past_rfps"
INDEX_DIR = PROJECT_ROOT / "outputs/rag"
AWARD_ENDPOINT = "https://apis.data.go.kr/1230000/as/ScsbidInfoService/getScsbidListSttusServcPPSSrch"
PROFILE_HEADINGS = re.compile(r"사업\s*(?:개요|목적|범위|배경|내용)|추진\s*(?:배경|목적)|과업\s*(?:개요|범위|내용)|제안\s*요청\s*내용")
PROFILE_CHARS = 2500
TOC_LEADER = re.compile(r"[·.…ㆍ‥\-]{5,}\s*\d{0,3}\s*$|[·…]{3,}")  # 목차 점선(····· 3)
# M25 담당자 평가(15건)로 조정: 0.60 → 0.70. 0.78 이상 5건 중 4건 관련, 0.69 이하는 대부분 무관(최적 F1 0.713: 정밀도·재현율 0.83).
MIN_PROJECT_SCORE = 0.70
SAME_PROJECT_SCORE = 0.95  # 이 이상이면 다른 공고번호라도 같은 사업의 재공고일 가능성이 높다


def project_profile(pdf_path: Path, title: str = "", buyer: str = "", max_pages: int = 12) -> str:
    """공고명·수요기관 + RFP 앞쪽에서 사업 개요/목적/범위가 나오는 쪽의 본문(연락처 행 제거)."""
    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages[:max_pages]:
            text, _ = _privacy_filter(page.extract_text() or "")
            lines = text.splitlines()
            leaders = [line for line in lines if TOC_LEADER.search(line)]
            if lines and len(leaders) / len(lines) > 0.3:
                continue  # 목차 쪽 — "사업 개요 ·····1" 제목만 있고 내용이 없다 (M25에서 발견)
            pages.append("\n".join(line for line in lines if not TOC_LEADER.search(line)))
    hits = [text for text in pages if PROFILE_HEADINGS.search(text)]
    body = "\n".join(hits or pages[1:6] or pages)
    if hits:
        first = PROFILE_HEADINGS.search(body)
        body = body[first.start():]  # 목차·표지 대신 첫 개요 제목부터
    return f"사업명: {title}\n수요기관: {buyer}\n{re.sub(r'[ \t]+', ' ', body)}"[:PROFILE_CHARS]


def _award_key() -> str:
    from dotenv import dotenv_values

    env = dotenv_values(PROJECT_ROOT / ".env")
    return env.get("G2B_SERVICE_KEY") or env.get("G2B_BID_NOTICE_KEY") or ""


def fetch_awards(keywords: list[str], begin: date, end: date, session=None) -> tuple[dict[str, dict[str, Any]], int]:
    """낙찰 결과 by 공고번호, and the number of failed windows.

    With a 공고명 keyword the API accepts at most 7 days (15·30 days answer 07 "입력범위값 초과", checked live).
    keyword "" = every 용역 award; a week then exceeds one 999-row page, so pages are followed to totalCount (M28).
    """
    session = session or requests.Session()
    awards: dict[str, dict[str, Any]] = {}
    failed = 0
    cursor = begin
    while cursor <= end:
        window_end = min(end, cursor + timedelta(days=6))
        for keyword in keywords:
            params = {"serviceKey": _award_key(), "type": "json", "numOfRows": 999, "pageNo": 1, "inqryDiv": "1",
                      "inqryBgnDt": cursor.strftime("%Y%m%d0000"), "inqryEndDt": window_end.strftime("%Y%m%d2359"),
                      "bidNtceNm": keyword}
            while True:
                try:
                    payload = session.get(AWARD_ENDPOINT, params=dict(params), timeout=90).json()
                except (requests.RequestException, ValueError):
                    failed += 1
                    break
                envelope = payload.get("response") or {}
                if str((envelope.get("header") or {}).get("resultCode")) not in ("00", "0"):
                    failed += 1  # 오류 응답을 "낙찰 없음"과 구분해 센다
                    break
                body = envelope.get("body") or {}
                items = body.get("items") or []
                for item in (items.get("item", []) if isinstance(items, dict) else items):
                    if isinstance(item, dict) and item.get("bidNtceNo"):
                        awards[f"{item['bidNtceNo']}-{str(item.get('bidNtceOrd') or '000').zfill(3)}"] = {
                            field: item.get(field) for field in ("bidwinnrNm", "sucsfbidAmt", "sucsfbidRate", "prtcptCnum", "rlOpengDt")}
                if params["pageNo"] * params["numOfRows"] >= int(body.get("totalCount") or 0):
                    break
                params["pageNo"] += 1
        cursor = window_end + timedelta(days=1)
    return awards, failed


def _spread_by_week(ids: list[str], dates: dict[str, str], amounts: dict[str, int | None] | None = None) -> list[str]:
    """Newest week first, then one notice per week in turn — so a capped run covers the whole period.

    Inside a week the largest 사업금액 goes first (M29), unknown amounts last, ties newest first. Without a
    floor: a capped day takes the big projects, and resuming on later days works down to the small ones.
    """
    amounts = amounts or {}
    newest_first = sorted(ids, key=lambda nid: dates.get(nid, ""), reverse=True)
    buckets: dict[str, list[str]] = {}
    for nid in sorted(newest_first, key=lambda nid: (amounts.get(nid) is None, -(amounts.get(nid) or 0))):
        stamp = dates.get(nid, "")[:10]
        try:
            week = date.fromisoformat(stamp).isocalendar()[:2]
        except ValueError:
            week = (0, 0)
        buckets.setdefault(str(week), []).append(nid)
    ordered, queues = [], [buckets[key] for key in sorted(buckets, reverse=True)]
    while any(queues):
        for queue in queues:
            if queue:
                ordered.append(queue.pop(0))
    return ordered


def _excluded_contract(record: dict, terms: list[str]) -> bool:
    """Same rule as the notice filter: 수의계약 notices are product specs (상용SW 3자단가 등), not project RFPs (M29)."""
    text = f"{record.get('cntrctCnclsMthdNm') or ''} {record.get('sucsfbidMthdNm') or ''}"
    return any(term in text for term in terms)


def _is_quota_error(message: str) -> bool:
    return "요청제한" in message or "LIMITED_NUMBER" in message or "API 오류 22" in message


def collect_past_projects(days: int | None = None, max_projects: int | None = None, exclude: set[str] | None = None,
                          today: date | None = None, session=None, log=print, max_lookups: int | None = None) -> dict[str, Any]:
    """Download ICT 용역 RFP PDFs spread over the period → data/past_rfps/<no>-<ord>/{rfp.pdf, meta.json}.

    Resumable across days: saved notices and notices already checked (no PDF 제안요청서) are skipped without
    an API call; an API quota error (22) stops the run instead of being counted as "no PDF".
    """
    from ..settings import load_settings
    from ..web import notices

    rules, scope = load_config("notice_filter"), load_settings().get("past_projects", {})
    days = days or scope.get("days", 180)
    max_projects = max_projects or scope.get("max_projects", 200)
    max_lookups = max_lookups or scope.get("max_lookups_per_run", 800)
    # 공고 수집 키워드가 비면(M27) 키워드 없이 전체 조회한다. extra_keywords는 키워드 수집일 때만 보탠다(M28).
    keywords = list(dict.fromkeys(rules["collect_keywords"] + scope.get("extra_keywords", []))) if rules["collect_keywords"] else [""]
    ict = set(rules.get("ict_classes", []))
    excluded_terms = rules.get("exclude_contract_terms", [])
    skipped_contract: set[str] = set()
    session = session or requests.Session()
    end = (today or date.today()) - timedelta(days=1)
    begin = end - timedelta(days=days)
    found: dict[str, dict[str, Any]] = {}
    for keyword in keywords:
        window_end = end
        while window_end >= begin:
            window_start = max(begin, window_end - timedelta(days=notices.MAX_WINDOW_DAYS - 1))
            for item in notices._fetch(session, keyword, window_start, window_end):
                if ict and item.get("pubPrcrmntLrgClsfcNm") not in ict:
                    continue
                if _excluded_contract(item, excluded_terms):
                    skipped_contract.add(notices.notice_id(item))
                    continue
                found[notices.notice_id(item)] = item
            window_end = window_start - timedelta(days=1)
    exclude = exclude or set()
    ordered = _spread_by_week([nid for nid in found if nid.split("-")[0] not in exclude],
                              {nid: str(found[nid].get("bidNtceDt") or "") for nid in found},
                              {nid: amount_of(found[nid]) for nid in found})
    log(f"후보 공고 {len(ordered)}건 (ICT 서비스, {begin}~{end}, {'전체 수집' if keywords == [''] else f'키워드 {len(keywords)}개'}, "
        f"수의계약 제외 {len(skipped_contract)}건, 주 안에서 금액 큰 순)")
    PAST_DIR.mkdir(parents=True, exist_ok=True)
    checked_path = PAST_DIR / "_checked.json"
    checked: dict[str, str] = json.loads(checked_path.read_text(encoding="utf-8")) if checked_path.is_file() else {}
    counts = {"saved": 0, "already_saved": 0, "no_rfp_pdf": 0, "already_checked": 0, "api_error": 0, "download_failed": 0}
    lookups, stopped = 0, None
    for nid in ordered:
        if counts["saved"] + counts["already_saved"] >= max_projects:
            break
        folder = PAST_DIR / nid
        if (folder / "meta.json").is_file():
            counts["already_saved"] += 1
            continue
        if nid in checked:
            counts["already_checked"] += 1
            continue
        if lookups >= max_lookups:
            stopped = f"lookup_cap({max_lookups})"
            break
        item = found[nid]
        lookups += 1
        try:
            info = notices.fetch_notice_attachment_info(item["bidNtceNo"], item.get("bidNtceOrd") or "000", session=session)
        except notices.NoticeError as exc:
            if _is_quota_error(str(exc)):
                stopped = "api_quota_exhausted"  # 다음 날 다시 실행하면 이어서 받는다
                break
            counts["api_error"] += 1
            continue
        rfp = next((a for a in info["attachments"] if a["kind"] == "rfp" and a["name"].lower().endswith(".pdf")), None)
        if not rfp:
            checked[nid] = "no_rfp_pdf"  # HWP뿐이거나 제안요청서 없음 — 다음 실행에서 다시 조회하지 않음
            counts["no_rfp_pdf"] += 1
            continue
        folder.mkdir(parents=True, exist_ok=True)
        if not notices._download(session, rfp, folder / "rfp.pdf"):
            folder.rmdir()
            counts["download_failed"] += 1
            continue
        data = (folder / "rfp.pdf").read_bytes()
        meta = {field: item.get(field) for field in notices.KEPT_FIELDS}
        meta.update({"notice_id": nid, "rfp_attachment": {"name": rfp["name"], "url": rfp["url"],
                                                          "sha256": hashlib.sha256(data).hexdigest()},
                     "api_operation": info["api_operation"], "retrieved_at": info["retrieved_at"]})
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        counts["saved"] += 1
    checked_path.write_text(json.dumps(checked, ensure_ascii=False, indent=2), encoding="utf-8")
    awards, failed = fetch_awards(keywords, begin, end, session=session)
    (PAST_DIR / "awards.json").write_text(json.dumps({"period": [str(begin), str(end)], "awards": awards,
                                                      "failed_windows": failed}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"candidates": len(ordered), "skipped_contract": len(skipped_contract), **counts, "attachment_lookups": lookups, "stopped": stopped,
            "awards": len(awards), "award_failed_windows": failed, "period": [str(begin), str(end)]}


def build_past_project_index(embed=None, log=print) -> dict[str, Any]:
    """Profile every downloaded past RFP, embed the profiles, and join 낙찰 by 공고번호."""
    import numpy as np

    from .semantic import EMBEDDING_MODEL, _normalise, openai_embedder

    awards = json.loads((PAST_DIR / "awards.json").read_text(encoding="utf-8")).get("awards", {}) \
        if (PAST_DIR / "awards.json").is_file() else {}
    projects: list[dict[str, Any]] = []
    latest: dict[str, Path] = {}  # 공고번호별 최신 차수 하나만 (재공고 차수 중복 제거)
    for meta_path in sorted(PAST_DIR.glob("*/meta.json")):
        latest[meta_path.parent.name.split("-")[0]] = meta_path  # 이름순 정렬 → 마지막이 최대 차수
    excluded_terms = load_config("notice_filter").get("exclude_contract_terms", [])
    excluded_contract = 0
    for meta_path in latest.values():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if _excluded_contract(meta, excluded_terms):
            excluded_contract += 1  # 폴더는 남긴다(이어받기 때 재조회 없음); 색인에서만 뺀다 (M29)
            continue
        number = meta["notice_id"].split("-")[0]
        award = next((value for key, value in sorted(awards.items(), reverse=True) if key.split("-")[0] == number), None)
        try:
            profile = project_profile(meta_path.parent / "rfp.pdf", meta.get("bidNtceNm") or "", meta.get("dminsttNm") or "")
        except Exception as exc:  # 손상된 PDF 하나가 전체 색인을 막지 않게
            log(f"프로필 실패 {meta['notice_id']}: {type(exc).__name__}")
            continue
        projects.append({"notice_id": meta["notice_id"], "title": meta.get("bidNtceNm"), "buyer": meta.get("dminsttNm"),
                         "amount_krw": meta.get("asignBdgtAmt") or meta.get("presmptPrce"), "notice_date": meta.get("bidNtceDt"),
                         "notice_url": meta.get("bidNtceDtlUrl"), "rfp_attachment": meta.get("rfp_attachment"),
                         "rfp_path": str((meta_path.parent / "rfp.pdf").relative_to(PROJECT_ROOT)),
                         "award": award, "profile": profile})
    if not projects:
        raise ValueError("data/past_rfps에 수집된 과거 RFP가 없습니다(collect 먼저 실행).")
    # 재공고는 새 공고번호로 나오기도 한다 → 공고명+수요기관이 같으면 한 사업으로 묶는다.
    groups: dict[str, list[dict[str, Any]]] = {}
    for project in projects:
        groups.setdefault(re.sub(r"\s+", "", f"{project['title']}|{project['buyer']}"), []).append(project)
    projects = []
    for members in groups.values():
        members.sort(key=lambda item: str(item.get("notice_date") or ""), reverse=True)
        lead = dict(members[0])
        lead["related_notice_ids"] = [item["notice_id"] for item in members]
        lead["award"] = next((item["award"] for item in members if item["award"]), None)
        projects.append(lead)
    embed = embed or openai_embedder(EMBEDDING_MODEL)
    matrix = _normalise(np.asarray(embed([p["profile"] for p in projects]), dtype=np.float32))
    payload = json.dumps(projects, ensure_ascii=False, indent=2)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    (INDEX_DIR / "past_projects.json").write_text(payload, encoding="utf-8")
    np.save(INDEX_DIR / "past_projects.npy", matrix)
    (INDEX_DIR / "past_projects_meta.json").write_text(json.dumps({
        "model": EMBEDDING_MODEL, "count": len(projects), "excluded_contract": excluded_contract, "profiles_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds")}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"projects": len(projects), "notices": sum(len(p["related_notice_ids"]) for p in projects),
            "with_award": sum(1 for p in projects if p["award"]), "excluded_contract": excluded_contract}


def search_similar_projects(query_profile: str, *, limit: int = 5, exclude_notice_no: str | None = None,
                            query_title: str | None = None, embed=None, index_dir: Path | None = None) -> list[dict[str, Any]]:
    """Top past projects by profile similarity (≥ MIN_PROJECT_SCORE). Raises SemanticUnavailable."""
    import numpy as np

    from .semantic import SemanticUnavailable, _normalise, openai_embedder

    index_dir = Path(index_dir or INDEX_DIR)
    paths = [index_dir / name for name in ("past_projects.json", "past_projects.npy", "past_projects_meta.json")]
    if not all(path.is_file() for path in paths):
        raise SemanticUnavailable("과거 사업 색인이 없습니다(scripts/build_past_projects.py 실행 필요).")
    payload = paths[0].read_text(encoding="utf-8")
    meta = json.loads(paths[2].read_text(encoding="utf-8"))
    if meta.get("profiles_sha256") != hashlib.sha256(payload.encode()).hexdigest():
        raise SemanticUnavailable("과거 사업 색인과 프로필 파일이 맞지 않습니다(재색인 필요).")
    projects, matrix = json.loads(payload), np.load(paths[1])
    try:
        vector = (embed or openai_embedder(meta["model"]))([query_profile])[0]
    except Exception as exc:
        raise SemanticUnavailable(f"과거 사업 검색 임베딩 실패({type(exc).__name__}).") from None
    scores = matrix @ _normalise(np.asarray([vector], dtype=np.float32))[0]
    squash = lambda value: re.sub(r"[\s\W_]+", "", value)  # noqa: E731 — 공백·기호 무시 사업명 비교
    results = []
    for index in np.argsort(-scores):
        project = projects[index]
        if exclude_notice_no and any(nid.split("-")[0] == exclude_notice_no for nid in project.get("related_notice_ids", [project["notice_id"]])):
            continue
        if float(scores[index]) < MIN_PROJECT_SCORE or len(results) >= limit:
            break
        results.append({**{k: v for k, v in project.items() if k != "profile"},
                        "similarity": round(float(scores[index]), 4), "profile_excerpt": project["profile"][:400],
                        "same_project_suspected": float(scores[index]) >= SAME_PROJECT_SCORE
                        or bool(query_title and squash(query_title) == squash(project.get("title") or "")),
                        "interpretation_notice": "사업 프로필 유사도 후보입니다. 같은 사업·유사 실적 인정을 뜻하지 않으며 원문 범위를 비교해야 합니다."})
    return results
