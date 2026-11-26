"""웹 실행 색인 자동 확장: T1.5 검색 색인에 새 문서 추가(live_index), T3 과거 사업 확장(past_project_expansion).

모두 임시 폴더와 가짜 임베딩·가짜 나라장터 응답으로 확인한다(외부 호출 없음, 실제 색인 불변).
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bid_review.rag import live_index, past_project_expansion as expansion, past_projects as pp  # noqa: E402
from bid_review.rag.retriever import build_keyword_index, search_keyword  # noqa: E402
from bid_review.rag.semantic import build_semantic_index, search_semantic  # noqa: E402
from bid_review.settings import PROJECT_ROOT  # noqa: E402

NEW_RFP = next(p for p in (PROJECT_ROOT / "data/input").glob("*.pdf") if "상수도관" in p.name)
SAMPLE_PAST = PROJECT_ROOT / "data/past_rfps/R26BK01589082-000"  # 실제 제안요청서(HWP) — 프로필이 만들어지는 원문


def fake_embed(texts):
    return [[1.0 + len(t) % 7, 1.0 + t.count("사업") % 5, 1.0 + t.count("시스템") % 3] for t in texts]


def broken_embed(texts):
    raise RuntimeError("no key")


def small_rag(folder: Path) -> Path:
    chunks = folder / "chunks.jsonl"
    rows = [("old-a", "기존 문서 사업예산", "olddoc"), ("old-b", "기존 문서 계약기간", "olddoc")]
    chunks.write_text("".join(json.dumps({"chunk_id": cid, "text": text, "metadata": {
        "document_id": doc, "document_type": "rfp_pdf", "page": 1, "section": "개요"}}, ensure_ascii=False) + "\n"
        for cid, text, doc in rows), encoding="utf-8")
    build_keyword_index(chunks, folder / "retrieval.sqlite3")
    build_semantic_index(chunks, folder, embed=fake_embed, model="fake")
    return chunks


def test_new_document_is_added_to_keyword_and_semantic_index() -> None:
    folder = Path(tempfile.mkdtemp())
    try:
        small_rag(folder)
        status = live_index.ensure_indexed(NEW_RFP, related={"related_notice_no": "R26BK01762350"}, embed=fake_embed, rag_dir=folder)
        assert status.startswith("indexed ") and "semantic: added" in status, status
        assert live_index.ensure_indexed(NEW_RFP, embed=fake_embed, rag_dir=folder) == "already_indexed"
        doc_id = __import__("hashlib").sha256(NEW_RFP.read_bytes()).hexdigest()[:16]
        assert search_keyword("상수관로", document_id=doc_id, database_path=folder / "retrieval.sqlite3", limit=3)
        hits = search_semantic("사업 기간", document_id=doc_id, embed=fake_embed, rag_dir=folder)
        assert hits and all(h.metadata["document_id"] == doc_id for h in hits)
        assert search_semantic("사업예산", document_id="olddoc", embed=fake_embed, rag_dir=folder), "기존 문서 검색 유지"
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_embedding_failure_keeps_keyword_and_old_semantic_search() -> None:
    folder = Path(tempfile.mkdtemp())
    try:
        small_rag(folder)
        status = live_index.ensure_indexed(NEW_RFP, embed=broken_embed, rag_dir=folder)
        assert "embedding_failed" in status, status
        # 새 문서는 키워드 검색만, 기존 문서의 의미 검색은 그대로(벡터가 있는 앞부분만 사용)
        assert search_semantic("사업예산", document_id="olddoc", embed=fake_embed, rag_dir=folder)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_no_index_is_skipped() -> None:
    folder = Path(tempfile.mkdtemp())
    try:
        assert live_index.ensure_indexed(NEW_RFP, rag_dir=folder).startswith("skipped")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def test_rule_keywords_drop_generic_words() -> None:
    words = expansion.rule_keywords("AI 영상 기반 상수도관 노후도 분석 및 GIS 연동 소프트웨어 개발 용역")
    assert words == ["상수도관", "노후도", "GIS"], words


def test_rank_prefers_rare_keyword_and_drops_reannouncements() -> None:
    items = [{"bidNtceNm": "AI 자율실증랩 자동화 시스템 구축"}, {"bidNtceNm": "AI 자율실증랩 자동화 시스템 구축(재공고)"},
             {"bidNtceNm": "발파 설계 자동화"}, {"bidNtceNm": "항만시설장비 관리시스템 기능개선"}]
    ranked, method = expansion.rank_candidates(items, "항만자동화 통합운영시스템", ["항만", "자동화"], embed=broken_embed)
    assert method == "rare_keywords" and len(ranked) == 3, (method, ranked)  # 재공고 1건 제거, 임베딩 실패 → 검색어 점수만
    assert ranked[0]["bidNtceNm"].startswith("항만"), ranked


def test_related_count() -> None:
    assert expansion.related_count([{"rerank_score": 0}, {"rerank_score": 1}, {"rerank_score": None}, {"rerank_score": 2}]) == 3


def test_expansion_downloads_and_appends_to_index() -> None:
    work = Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "outputs"))
    index_dir, past_dir = work / "rag", work / "past"
    old_index, old_past = pp.INDEX_DIR, pp.PAST_DIR
    try:
        past_dir.mkdir(parents=True)
        pp.INDEX_DIR, pp.PAST_DIR = index_dir, past_dir
        pp._write_index([{"notice_id": "R00OLD-000", "title": "기존 사업", "buyer": "기관", "related_notice_ids": ["R00OLD-000"],
                          "profile": "기존 사업 프로필"}], np.ones((1, 3), dtype=np.float32), {"model": "fake"}, index_dir=index_dir)
        items = [
            {"bidNtceNo": "R99NEW1", "bidNtceOrd": "000", "bidNtceNm": "상수도 관망 GIS 고도화", "dminsttNm": "수도사업소",
             "pubPrcrmntLrgClsfcNm": "ICT 서비스", "bidNtceDt": "2026-11-01"},
            {"bidNtceNo": "R99OPEN", "bidNtceOrd": "000", "bidNtceNm": "상수도 SW 구매", "pubPrcrmntLrgClsfcNm": "ICT 서비스",
             "cntrctCnclsMthdNm": "수의계약", "bidNtceDt": "2026-11-02"},
            {"bidNtceNo": "R99GOODS", "bidNtceOrd": "000", "bidNtceNm": "상수도 계량기", "pubPrcrmntLrgClsfcNm": "물품"},
            {"bidNtceNo": "R00OLD", "bidNtceOrd": "000", "bidNtceNm": "이미 색인", "pubPrcrmntLrgClsfcNm": "ICT 서비스"},
            {"bidNtceNo": "R99SELF", "bidNtceOrd": "000", "bidNtceNm": "자기 공고", "pubPrcrmntLrgClsfcNm": "ICT 서비스"},
        ]
        searched = []

        def fetch(session, keyword, begin, end):
            searched.append(keyword)
            return items

        def save(item, *, session):
            folder = past_dir / f"{item['bidNtceNo']}-000"
            folder.mkdir()
            shutil.copy(next(SAMPLE_PAST.glob("rfp.*")), folder / next(SAMPLE_PAST.glob("rfp.*")).name)
            meta = {k: item.get(k) for k in ("bidNtceNo", "bidNtceOrd", "bidNtceNm", "dminsttNm", "bidNtceDt")}
            (folder / "meta.json").write_text(json.dumps({**meta, "notice_id": f"{item['bidNtceNo']}-000",
                                                          "rfp_attachment": {"name": "제안요청서.hwp"}}, ensure_ascii=False), encoding="utf-8")
            return "saved_hwp"

        report = expansion.expand_past_projects("상수도관 노후도 분석", "프로필", "R99SELF", embed=fake_embed,
                                                judge=lambda prompt: {"keywords": ["상수도", "시스템"]},
                                                fetch=fetch, save=save, settings={"days": 14})
        assert report["keywords"] == ["상수도"] and report["keyword_source"] == "llm", report  # 일반어 '시스템' 제외
        assert set(searched) == {"상수도"} and len(searched) == 2, searched  # 14일 = 7일 창 2개
        assert report["candidates"] == 1 and report["saved"] == ["R99NEW1-000"], report  # 수의계약·물품·색인·자기 공고 제외
        assert report["added"] == ["R99NEW1-000"] and report["status"] == "expanded 1", report
        projects = json.loads((index_dir / "past_projects.json").read_text(encoding="utf-8"))
        assert [p["notice_id"] for p in projects] == ["R00OLD-000", "R99NEW1-000"]
        assert np.load(index_dir / "past_projects.npy").shape == (2, 3)
        # 다시 돌리면 이미 색인에 있어 아무것도 받지 않는다
        again = expansion.expand_past_projects("상수도관", "프로필", "R99SELF", embed=fake_embed,
                                               judge=lambda prompt: {"keywords": ["상수도"]}, fetch=fetch, save=save,
                                               settings={"days": 7})
        assert again["added"] == [] and again["status"] == "no_candidates", again
    finally:
        pp.INDEX_DIR, pp.PAST_DIR = old_index, old_past
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("live growth OK")
