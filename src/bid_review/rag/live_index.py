"""실행 중인 공고 문서를 T1.5 검색 색인에 자동으로 추가한다.

T1.5는 RFP 원문을 검색 색인(outputs/rag/)에서 찾아 근거로 붙인다.
- 원문 인용 확인(verification._find_quote): 키워드 검색
- 근거가 부족한 항목 재검색(workflow.retrieve_targeted_evidence): 키워드 검색 + 의미 검색

예전에는 이 색인을 scripts/build_rag_corpus.py 등으로 data/input의 시연 RFP만 미리 만들어 두었기 때문에,
새 공고는 색인에 없어 T1.5가 원문 근거를 찾지 못했다. 이 모듈은 T1.5 시작 전에 이번 문서가 색인에 있는지 보고,
없으면 그 문서만 쪼개(chunk) 세 곳에 덧붙인다(전체 재색인 없음).

1. chunks.jsonl            — 원문 조각과 출처(쪽·절)
2. retrieval.sqlite3       — 키워드 검색 색인(FTS5)
3. embeddings.npy / _meta  — 의미 검색 색인(OpenAI 임베딩). 실패하면 키워드 검색 색인만 추가된다.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import numpy as np

from .corpus import index_pdf
from .retriever import build_keyword_index, compact_text
from .semantic import RAG_DIR, _embed_text, _load_chunks, _normalise, openai_embedder


def ensure_indexed(path: str | Path, *, document_type: str = "rfp_pdf", related: dict[str, Any] | None = None,
                   title: str | None = None, embed=None, rag_dir: Path | None = None) -> str:
    """문서가 색인에 없으면 추가한다. 반환: 상태 문자열(already_indexed / indexed N (semantic: ...) / skipped: ...)."""
    from .hitl_runtime import _exclusive

    path, rag_dir = Path(path), Path(rag_dir or RAG_DIR)
    chunks_path, database = rag_dir / "chunks.jsonl", rag_dir / "retrieval.sqlite3"
    if not chunks_path.is_file() or not database.is_file():
        return "skipped: 검색 색인이 없음(scripts/build_rag_corpus.py·build_rag_search_index.py 먼저 실행)"
    document_id = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    with (rag_dir / ".index.lock").open("a+") as lock_file, _exclusive(lock_file):  # 동시에 도는 웹 실행 대비
        if _has_document(database, document_id):
            return "already_indexed"
        try:
            chunks, _ = index_pdf(path, related or {}, document_type=document_type, title=title)
        except Exception as exc:  # 프로젝트 폴더 밖 파일·읽기 실패
            return f"skipped: 문서를 조각내지 못함({type(exc).__name__})"
        if not chunks:
            return "skipped: 글자가 있는 쪽이 없음"
        previous = _load_chunks(chunks_path)
        with chunks_path.open("a", encoding="utf-8") as handle:
            for chunk in chunks:
                handle.write(json.dumps(chunk, ensure_ascii=False, sort_keys=True) + "\n")
        try:
            _append_keyword_rows(database, chunks)
        except sqlite3.Error:
            build_keyword_index(chunks_path, database)  # 색인 구조가 예전 것이면 전체를 다시 만든다
        semantic = _append_semantic(rag_dir, previous, chunks, chunks_path, embed)
    return f"indexed {len(chunks)} (semantic: {semantic})"


def _has_document(database: Path, document_id: str) -> bool:
    with closing(sqlite3.connect(database)) as connection:
        return bool(connection.execute("SELECT 1 FROM evidence_fts WHERE document_id = ? LIMIT 1", (document_id,)).fetchone())


def _append_keyword_rows(database: Path, chunks: list[dict[str, Any]]) -> None:
    rows = [(c["chunk_id"], c["text"], json.dumps(c["metadata"], ensure_ascii=False, sort_keys=True),
             c["metadata"].get("document_id", ""), c["metadata"].get("related_notice_no", "") or "") for c in chunks]
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executemany("INSERT INTO evidence_fts(chunk_id, text, metadata, document_id, notice_no) VALUES (?, ?, ?, ?, ?)", rows)
        connection.executemany("INSERT INTO evidence_compact(chunk_id, compact, document_id, notice_no) VALUES (?, ?, ?, ?)",
                               [(row[0], compact_text(row[1]), row[3], row[4]) for row in rows])


def _append_semantic(rag_dir: Path, previous: list[dict], chunks: list[dict], chunks_path: Path, embed) -> str:
    """의미 검색 색인 뒤에 새 조각의 벡터를 붙인다. 기존 색인이 앞부분과 맞지 않으면 건드리지 않는다."""
    meta_path, matrix_path = rag_dir / "embeddings_meta.json", rag_dir / "embeddings.npy"
    if not (meta_path.is_file() and matrix_path.is_file()):
        return "no_semantic_index"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("chunk_ids") != [c["chunk_id"] for c in previous[:len(meta.get("chunk_ids") or [])]]:
        return "stale_semantic_index"
    if len(meta["chunk_ids"]) != len(previous):
        return "behind: 이전 문서의 벡터가 빠져 있어 덧붙이지 않음(scripts/build_semantic_index.py로 다시 만들면 해결)"
    try:
        vectors = _normalise(np.asarray((embed or openai_embedder(meta["model"]))([_embed_text(c) for c in chunks]), dtype=np.float32))
    except Exception as exc:
        return f"embedding_failed({type(exc).__name__}) — 키워드 검색만 추가"
    np.save(matrix_path, np.vstack([np.load(matrix_path), vectors]))
    meta.update(count=meta["count"] + len(chunks), chunk_ids=meta["chunk_ids"] + [c["chunk_id"] for c in chunks],
                chunks_sha256=hashlib.sha256(chunks_path.read_bytes()).hexdigest())
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return "added"
