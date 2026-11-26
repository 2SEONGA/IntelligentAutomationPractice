"""T3 과거 사업 확장 — 비슷한 과거 사업이 부족하면 나라장터에서 더 찾아 색인에 덧붙인다.

과거 사업 색인(110건)은 미리 모아 둔 것이라, 새 공고가 그 안에 없는 분야면 T3가 비슷한 사업을 못 찾는다.
그럴 때(재정렬 기준 '관련·부분' 후보가 min_related건 미만) 웹 실행에서만 다음을 한다.

1. 검색어 만들기: 새 공고의 사업명·프로필로 나라장터 공고명 검색어 몇 개를 정한다(LLM, 실패하면 사업명 규칙).
2. 공고 찾기: 최근 days일 동안 그 검색어가 공고명에 들어간 ICT 용역 공고(수의계약 제외)를 찾는다.
3. 제안요청서 받기: 새 공고부터 최대 max_new건을 data/past_rfps/에 저장한다(첨부정보 API 호출 max_lookups회 이하).
4. 색인에 덧붙이기: 받은 사업만 임베딩해 과거 사업 색인 뒤에 붙인다(전체 재색인 없음).

T3는 그다음 같은 검색·재정렬을 한 번 더 한다. 어느 단계가 실패해도 T3 결과는 원래대로 남고 흐름은 계속된다.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
import re
import time
from typing import Any, Callable

import requests

from ..settings import load_config
from . import past_projects as pp

SETTINGS: dict[str, Any] = {"enabled": True, "min_related": 2, "days": 180, "max_new": 5, "max_lookups": 10, "keywords": 3,
                            **load_config("settings").get("past_projects", {}).get("expand", {})}
# 공고명에 흔히 붙어 검색어로 쓸모없는 말
STOPWORDS = {"용역", "사업", "구축", "개발", "시스템", "고도화", "운영", "유지보수", "유지관리", "위탁", "기반", "관련", "및", "연동",
             "소프트웨어", "정보시스템", "플랫폼", "통합", "차세대", "재공고", "긴급", "AI", "데이터", "분석", "서비스", "도입", "개선"}
KEYWORD_PROMPT = """아래 공공 SI 사업과 같은 분야의 과거 나라장터 공고를 '공고명'으로 검색하려고 합니다.
공고명에 실제로 들어갈 만한 분야 핵심어 {count}개를 고르세요.
- 2~8글자 명사 하나씩 (예: 상수도, 관망, 임상, 학사)
- '시스템·구축·용역·AI·데이터·플랫폼·고도화' 같은 일반어는 빼세요.
JSON으로만 답하세요: {{"keywords": ["...", "..."]}}

{profile}
"""


def rule_keywords(title: str, count: int = 3) -> list[str]:
    """사업명에서 일반어를 뺀 2글자 이상 낱말을 긴 것부터 고른다(LLM을 못 쓸 때)."""
    words = [w for w in re.findall(r"[가-힣A-Za-z0-9]+", title) if len(w) >= 2 and w not in STOPWORDS and not w.isdigit()]
    return sorted(dict.fromkeys(words), key=len, reverse=True)[:count]


def query_keywords(title: str, profile: str, *, judge: Callable[[str], dict] | None = None, count: int = 3) -> tuple[list[str], str]:
    """반환: (검색어 목록, 만든 방법 'llm' 또는 'rule')."""
    try:
        if judge is None:
            from .t3_rerank import cached_judge, openai_judge, rerank_model
            judge = cached_judge(openai_judge(rerank_model()), rerank_model())
        answer = judge(KEYWORD_PROMPT.format(count=count, profile=profile[:2000]))
        words = [str(w).strip() for w in answer.get("keywords", []) if isinstance(w, str)]
        words = [w for w in dict.fromkeys(words) if 2 <= len(w) <= 12 and w not in STOPWORDS]
        if words:
            return words[:count], "llm"
    except Exception:
        pass
    return rule_keywords(title, count), "rule"


def find_candidates(keywords: list[str], *, exclude_numbers: set[str], days: int, session=None,
                    fetch: Callable | None = None, today: date | None = None) -> tuple[list[dict], int]:
    """검색어마다 7일 단위로 공고를 찾는다(병렬). 반환: (후보 공고 — 최신순, 실패한 조회 수)."""
    from ..web import notices

    fetch = fetch or notices._fetch
    session = session or requests.Session()
    rules = load_config("notice_filter")
    ict, excluded_terms = set(rules.get("ict_classes", [])), rules.get("exclude_contract_terms", [])
    end = (today or date.today()) - timedelta(days=1)
    windows, cursor = [], end
    while cursor > end - timedelta(days=days):
        windows.append((max(end - timedelta(days=days - 1), cursor - timedelta(days=notices.MAX_WINDOW_DAYS - 1)), cursor))
        cursor -= timedelta(days=notices.MAX_WINDOW_DAYS)
    jobs = [(keyword, start, stop) for keyword in keywords for start, stop in windows]

    def run(job):
        try:
            return fetch(session, *job)
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        pages = list(pool.map(run, jobs))
    found: dict[str, dict] = {}
    for page in pages:
        for item in page or []:
            nid = notices.notice_id(item)
            if (ict and item.get("pubPrcrmntLrgClsfcNm") not in ict) or pp._excluded_contract(item, excluded_terms):
                continue
            if str(item.get("bidNtceNo")) in exclude_numbers:
                continue
            found[nid] = item
    ordered = sorted(found.values(), key=lambda item: str(item.get("bidNtceDt") or ""), reverse=True)
    return ordered, sum(1 for page in pages if page is None)


def rank_candidates(candidates: list[dict], title: str, keywords: list[str], embed=None) -> tuple[list[dict], str]:
    """받을 순서를 정한다: 같은 사업명(재공고)은 하나만 남기고, 점수 높은 순으로 정렬한다.

    점수 = 드문 검색어 점수 + 사업명 의미 유사도.
    - 드문 검색어 점수: 후보 사업명에 든 검색어마다 1/(그 검색어가 걸린 후보 수). '항만'처럼 적게 걸린 검색어는
      분야를 잘 가리키고, '자동화'처럼 많이 걸린 검색어는 여러 분야에 흔하므로 낮게 친다.
    - 사업명 의미 유사도: 새 공고 사업명과 후보 사업명의 임베딩 코사인(1회 호출). 못 하면 0.
    반환: (정렬된 후보, 정렬 방법 설명)
    """
    unique: dict[str, dict] = {}
    for item in candidates:  # 최신순으로 들어오므로 같은 사업명이면 최신 공고가 남는다
        unique.setdefault(re.sub(r"\(?\s*(?:재공고|긴급|재입찰)\s*\)?|[\s\W_]+", "", str(item.get("bidNtceNm") or "")), item)
    items = list(unique.values())
    names = [str(c.get("bidNtceNm") or "") for c in items]
    hits = {k: sum(1 for n in names if k in n) for k in keywords}
    rarity = [sum(1 / hits[k] for k in keywords if hits[k] and k in n) for n in names]
    method = "rare_keywords"
    similarity = [0.0] * len(items)
    if len(items) > 1:
        try:
            import numpy as np

            from .semantic import _normalise, openai_embedder

            if embed is None:
                embed = openai_embedder(json.loads((pp.INDEX_DIR / "past_projects_meta.json").read_text(encoding="utf-8"))["model"])
            vectors = _normalise(np.asarray(embed([title] + names), dtype=np.float32))
            similarity = [float(s) for s in vectors[1:] @ vectors[0]]
            method = "rare_keywords+title_similarity"
        except Exception:
            pass
    order = sorted(range(len(items)), key=lambda i: -(rarity[i] + similarity[i]))
    return [items[i] for i in order], method


def expand_past_projects(title: str, profile: str, exclude_notice_no: str | None, *, embed=None,
                         judge: Callable[[str], dict] | None = None, session=None, fetch: Callable | None = None,
                         save: Callable | None = None, settings: dict | None = None, today: date | None = None) -> dict[str, Any]:
    """과거 사업을 더 찾아 색인에 덧붙인다. 반환값은 실행 기록(state['t3_expansion'])으로 남는다."""
    from ..web import notices
    from .hitl_runtime import _exclusive

    cfg = {**SETTINGS, **(settings or {})}
    started = time.perf_counter()
    report: dict[str, Any] = {"status": "", "keywords": [], "keyword_source": "", "candidates": 0, "lookups": 0,
                              "saved": [], "added": [], "skipped": {}}
    try:
        index = json.loads((pp.INDEX_DIR / "past_projects.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {**report, "status": "unavailable: 과거 사업 색인 없음"}
    known = {nid.split("-")[0] for p in index for nid in p.get("related_notice_ids", [p["notice_id"]])}
    checked_path = pp.PAST_DIR / "_checked.json"
    checked = json.loads(checked_path.read_text(encoding="utf-8")) if checked_path.is_file() else {}
    skip = known | {nid.split("-")[0] for nid in checked} | ({str(exclude_notice_no)} if exclude_notice_no else set())

    report["keywords"], report["keyword_source"] = query_keywords(title, profile, judge=judge, count=int(cfg["keywords"]))
    if not report["keywords"]:
        return {**report, "status": "no_keywords", "seconds": round(time.perf_counter() - started, 2)}
    session = session or requests.Session()
    candidates, failed = find_candidates(report["keywords"], exclude_numbers=skip, days=int(cfg["days"]),
                                         session=session, fetch=fetch, today=today)
    report["candidates"], report["failed_searches"] = len(candidates), failed
    candidates, report["candidate_order"] = rank_candidates(candidates, title, report["keywords"], embed)
    save = save or pp.save_notice_rfp
    lock_path = pp.INDEX_DIR / ".past_projects.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file, _exclusive(lock_file):  # 동시에 도는 웹 실행이 색인을 같이 고치지 않게
        for item in candidates:
            nid = notices.notice_id(item)
            if len(report["saved"]) >= int(cfg["max_new"]) or report["lookups"] >= int(cfg["max_lookups"]):
                break
            if (pp.PAST_DIR / nid / "meta.json").is_file():
                report["saved"].append(nid)  # 이미 받아 두었지만 색인에 없던 사업
                continue
            report["lookups"] += 1
            status = save(item, session=session)
            if status == "api_quota_exhausted":
                report["stopped"] = status
                break
            if status in ("no_rfp", "rfp_unreadable"):
                checked[nid] = status
            elif status.startswith("saved_"):
                report["saved"].append(nid)
        if checked:
            checked_path.parent.mkdir(parents=True, exist_ok=True)
            checked_path.write_text(json.dumps(checked, ensure_ascii=False, indent=2), encoding="utf-8")
        if report["saved"]:
            try:
                result = pp.append_to_past_project_index(report["saved"], embed=embed)
                report["added"], report["skipped"] = result["added"], result["skipped"]
            except Exception as exc:
                report["status"] = f"index_failed: {type(exc).__name__}"
    if not report["status"]:
        report["status"] = f"expanded {len(report['added'])}" if report["added"] else (
            "no_candidates" if not candidates else "nothing_added")
    report["seconds"] = round(time.perf_counter() - started, 2)
    return report


def related_count(projects: list[dict[str, Any]]) -> int:
    """재정렬 '관련·부분'(1·2점) 후보 수. 재정렬이 안 된 후보는 관련으로 셈한다(확장을 과하게 하지 않도록)."""
    return sum(1 for p in projects if p.get("rerank_score") in (None, 1, 2))
