"""M48 T3 LLM 재정렬: 점수 부여, 정렬, '분야 다름' 표시(삭제 안 함), 실패 처리, 캐시."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bid_review.rag import t3_rerank  # noqa: E402
from bid_review.rag.report_adapter import build_market_result  # noqa: E402

PROJECTS = [
    {"notice_id": "A-000", "title": "바다로시스템", "similarity": 0.78, "profile_excerpt": "해양"},
    {"notice_id": "B-000", "title": "상수도 관망", "similarity": 0.71, "profile_excerpt": "상수도"},
    {"notice_id": "C-000", "title": "스마트도시", "similarity": 0.72, "profile_excerpt": "도시"},
]
SCORES = {"해양": 0, "상수도": 2, "도시": 1}


def fake_judge(prompt: str) -> dict:
    candidate = prompt.split("[과거 사업 후보]")[1]
    return {"score": next(v for k, v in SCORES.items() if k in candidate), "reason": "테스트"}


def test_scores_sort_and_keep_unrelated() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ranked, status = t3_rerank.rerank_projects("상수도관 노후도", PROJECTS, judge=fake_judge, index_dir=Path(tmp))
    assert [p["title"] for p in ranked] == ["상수도 관망", "스마트도시", "바다로시스템"], ranked
    assert len(ranked) == 3, "분야 다름 후보도 빼지 않는다"
    assert ranked[-1]["rerank_label"] == "분야 다름" and ranked[0]["rerank_label"] == "관련"
    assert status == "done 3 (분야 다름 1)", status
    assert "rerank_score" not in PROJECTS[0], "입력 목록은 바꾸지 않는다"


def test_failure_keeps_candidates_and_stops_calling() -> None:
    calls = []

    def broken(prompt: str) -> dict:
        calls.append(1)
        raise RuntimeError("no key")
    with tempfile.TemporaryDirectory() as tmp:
        ranked, status = t3_rerank.rerank_projects("q", PROJECTS, judge=broken, index_dir=Path(tmp))
    assert len(calls) == 1 and status.startswith("partial 0/3"), status
    assert [p["title"] for p in ranked] == ["바다로시스템", "스마트도시", "상수도 관망"], "실패하면 유사도 순서 유지"
    assert all(p["rerank_score"] is None for p in ranked)


def test_cache_returns_same_answer_without_call() -> None:
    calls = []

    def judge(prompt: str) -> dict:
        calls.append(prompt)
        return {"score": 1, "reason": "x"}
    with tempfile.TemporaryDirectory() as tmp:
        cached = t3_rerank.cached_judge(judge, "m", Path(tmp) / "c.sqlite3")
        assert cached("p") == cached("p") == {"score": 1, "reason": "x"}
    assert len(calls) == 1


def test_report_marks_unrelated() -> None:
    item = {"notice_id": "A-000", "title": "바다로시스템", "similarity": 0.78, "notice_url": "https://www.g2b.go.kr/x",
            "profile_excerpt": "해양", "rerank_score": 0, "rerank_label": "분야 다름", "rerank_reason": "분야 다름"}
    market = build_market_result([item], "found 1", checked_at="2026-11-25T00:00:00+09:00")
    project = market.similar_projects[0]
    assert "AI 재정렬 판정: 분야 다름" in project.similarity_rationale
    assert any("분야 다름" in note for note in project.comparison_limitations)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("t3 rerank OK")
