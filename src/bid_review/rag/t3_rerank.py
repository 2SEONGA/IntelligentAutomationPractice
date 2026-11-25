"""T3 LLM 재정렬(Re-ranker) — 남은 업무 3번 할 일 4, M48.

유사도(임베딩) 점수만으로는 업무 분야를 가르지 못한다(M42: 상수도관 질의에 무관 후보가 0.73~0.80).
그래서 임계값(0.70)을 넘은 후보마다 LLM에게 "두 사업이 정말 비슷한가"를 0·1·2로 다시 묻는다.

- 2 = 관련, 1 = 부분, 0 = 분야 다름. **0점 후보도 빼지 않고 "분야 다름"으로 표시만 한다**(사람이 최종 확인).
- 정렬: 재정렬 점수 높은 순 → 같은 점수 안에서는 유사도 높은 순.
- 같은 질문은 SQLite 캐시로 같은 답을 돌려준다(재현성, 비용 절약).
- 키가 없거나 호출이 실패하면 후보를 그대로 두고 상태만 남긴다(흐름을 막지 않음). 첫 실패 뒤에는 더 부르지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from ..settings import PROJECT_ROOT, load_config

_SETTINGS = load_config("settings").get("past_projects", {})
RERANK_ENABLED = bool(_SETTINGS.get("rerank", True))
RERANK_CACHE = PROJECT_ROOT / "outputs/rag/rerank_cache.sqlite3"
PROFILE_CHARS = 2500
LABELS = {2: "관련", 1: "부분", 0: "분야 다름"}
PROMPT = """두 공공 SI 사업이 '유사 과거 사업'으로 참고할 만한지 판정하세요.
기준: 2 = 관련(업무 분야와 주요 과업이 비슷함), 1 = 부분(일부 업무만 겹침), 0 = 무관(분야가 다름).
기관 유형(대학·병원·공기업)이나 'AI·데이터·플랫폼' 같은 일반 단어가 겹치는 것만으로는 관련으로 보지 마세요.
JSON으로만 답하세요: {{"score": 0|1|2, "reason": "한 문장"}}

[검토 중인 사업]
{query}

[과거 사업 후보]
{candidate}
"""

Judge = Callable[[str], dict]  # 프롬프트 → {"score": int, "reason": str}


def rerank_model() -> str:
    return str(_SETTINGS.get("rerank_model") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini")


def openai_judge(model: str) -> Judge:
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv(PROJECT_ROOT / ".env")
    client = None

    def judge(prompt: str) -> dict:
        nonlocal client
        client = client or OpenAI()
        response = client.chat.completions.create(model=model, temperature=0, response_format={"type": "json_object"},
                                                  messages=[{"role": "user", "content": prompt}])
        return json.loads(response.choices[0].message.content)
    return judge


def cached_judge(judge: Judge, model: str, path: Path = RERANK_CACHE) -> Judge:
    """같은 모델·같은 프롬프트면 저장된 답을 쓴다. 캐시 오류는 무시하고 그냥 호출한다."""
    def run(prompt: str) -> dict:
        key = hashlib.sha256(f"{model}\n{prompt}".encode()).hexdigest()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE IF NOT EXISTS rerank (key TEXT PRIMARY KEY, answer TEXT)")
                row = db.execute("SELECT answer FROM rerank WHERE key = ?", (key,)).fetchone()
            if row:
                return json.loads(row[0])
        except (sqlite3.Error, OSError, ValueError):
            pass
        answer = judge(prompt)
        try:
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("INSERT OR REPLACE INTO rerank VALUES (?, ?)", (key, json.dumps(answer, ensure_ascii=False)))
        except (sqlite3.Error, OSError):
            pass
        return answer
    return run


def _profiles(index_dir: Path | None) -> dict[str, str]:
    from .past_projects import INDEX_DIR
    path = Path(index_dir or INDEX_DIR) / "past_projects.json"
    try:
        return {p["notice_id"]: p.get("profile") or "" for p in json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def rerank_projects(query_profile: str, projects: list[dict[str, Any]], *, judge: Judge | None = None,
                    index_dir: Path | None = None) -> tuple[list[dict[str, Any]], str]:
    """후보마다 rerank_score·rerank_label·rerank_reason을 붙이고 재정렬한다. 반환: (후보 목록, 상태 문자열)."""
    if not projects:
        return projects, "not_needed"
    if judge is None and not RERANK_ENABLED:
        return projects, "disabled"
    try:
        judge = judge or cached_judge(openai_judge(rerank_model()), rerank_model())
    except Exception as exc:  # openai 미설치 등
        return projects, f"unavailable: {type(exc).__name__}"
    profiles = _profiles(index_dir)
    ranked, failure = [], None
    for project in projects:
        item = dict(project)
        if failure is None:
            candidate = profiles.get(str(item.get("notice_id"))) or str(item.get("profile_excerpt") or "")
            prompt = PROMPT.format(query=query_profile[:PROFILE_CHARS], candidate=candidate[:PROFILE_CHARS])
            try:
                answer = judge(prompt)
                score = int(answer.get("score"))
                if score not in LABELS:
                    raise ValueError(score)
                item.update(rerank_score=score, rerank_label=LABELS[score], rerank_reason=str(answer.get("reason", ""))[:200])
            except Exception as exc:
                failure = f"{type(exc).__name__}"
        if "rerank_score" not in item:
            item.update(rerank_score=None, rerank_label="재정렬 안 됨", rerank_reason="")
        ranked.append(item)
    order = lambda i: (-(i["rerank_score"] if i["rerank_score"] is not None else 1), -float(i.get("similarity") or 0))  # noqa: E731
    ranked.sort(key=order)
    done = sum(1 for i in ranked if i["rerank_score"] is not None)
    if failure:
        return ranked, f"partial {done}/{len(ranked)}: {failure}"
    return ranked, f"done {done} (분야 다름 {sum(1 for i in ranked if i['rerank_score'] == 0)})"
