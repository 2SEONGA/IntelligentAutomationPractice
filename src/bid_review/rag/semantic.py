"""Semantic (embedding) retrieval over the same page-cited chunks as the FTS index (M4).

Vectors come from the OpenAI embeddings API and are stored next to the keyword
index: `outputs/rag/embeddings.npy` (L2-normalised float32, one row per chunk) and
`embeddings_meta.json` (model, chunk ids, corpus hash). Chunk text and metadata stay
in `chunks.jsonl`, so citations are identical to keyword hits.

ponytail: brute-force cosine over a numpy matrix — fine for ~10k chunks; switch to a
vector store (e.g. sqlite-vec / LlamaIndex) if the corpus grows by orders of magnitude.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np

from ..settings import PROJECT_ROOT
from .retriever import RetrievedEvidence

EMBEDDING_MODEL = "text-embedding-3-small"
# M25 담당자 평가(30건): 0.35~0.55 구간 어디서나 정밀도 약 0.42로 점수가 관련성을 가르지 못해 0.40을 유지한다.
# 임계값보다 후보 수·질의 품질을 손봐야 하는 지점 — 결과는 항상 담당자 검토로만 붙는다.
MIN_SCORE = 0.40
BATCH = 100
RAG_DIR = PROJECT_ROOT / "outputs/rag"
Embedder = Callable[[list[str]], list[list[float]]]


class SemanticUnavailable(Exception):
    """Semantic search cannot run (no index, stale index, or API failure); callers fall back to keywords."""


EMBEDDING_CACHE = RAG_DIR / "embedding_cache.sqlite3"
EMBEDDING_CACHE_VERSION = 1  # 벡터를 만드는 방식(입력 가공 등)이 바뀌면 올린다 → 예전 캐시는 자동으로 안 맞는다


def embedding_cache_key(model: str, text: str) -> str:
    """캐시 키 = SHA-256(캐시 버전 + 모델 + 텍스트). 모델이나 텍스트가 한 글자라도 다르면 다른 키."""
    return hashlib.sha256(f"v{EMBEDDING_CACHE_VERSION}\n{model}\n{text}".encode("utf-8")).hexdigest()


def cached_embedder(embed: Embedder, model: str, path: Path | None = None) -> Embedder:
    """같은 모델·같은 텍스트는 처음 받은 벡터를 그대로 돌려준다(M37: OpenAI 점수가 소수 넷째 자리에서 흔들림).

    벡터는 받은 값 그대로(float64) 저장해 첫 호출과 이후 호출이 완전히 같다. 캐시에 없는 텍스트만 API로 보낸다.
    캐시 파일을 못 쓰면(권한 등) 캐시 없이 동작한다.
    """
    import sqlite3

    def connect():
        target = Path(path or EMBEDDING_CACHE)
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(target, timeout=30)
        connection.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, model TEXT, dims INTEGER, vector BLOB)")
        return connection

    def run(texts: list[str]) -> list[list[float]]:
        keys = [embedding_cache_key(model, text) for text in texts]
        found: dict[str, list[float]] = {}
        try:
            with connect() as connection:
                for start in range(0, len(keys), 500):
                    chunk = keys[start:start + 500]
                    rows = connection.execute(f"SELECT key, vector FROM vectors WHERE key IN ({','.join('?' * len(chunk))})", chunk)
                    found.update({key: np.frombuffer(blob, dtype=np.float64).tolist() for key, blob in rows})
        except (sqlite3.Error, OSError):
            found = {}
        missing = list(dict.fromkeys(text for text, key in zip(texts, keys) if key not in found))
        if missing:
            fresh = embed(missing)
            new = {embedding_cache_key(model, text): vector for text, vector in zip(missing, fresh)}
            found.update(new)
            try:
                with connect() as connection:
                    connection.executemany("INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?)",
                                           [(key, model, len(vector), np.asarray(vector, dtype=np.float64).tobytes())
                                            for key, vector in new.items()])
            except (sqlite3.Error, OSError):
                pass
        return [found[key] for key in keys]
    return run


def openai_embedder(model: str = EMBEDDING_MODEL, cache: bool = True) -> Embedder:
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv(PROJECT_ROOT / ".env")
    client = None

    def embed(texts: list[str]) -> list[list[float]]:
        nonlocal client
        client = client or OpenAI()  # 캐시로 모두 해결되면 키 없이도 동작한다
        response = client.embeddings.create(model=model, input=texts)
        from .usage_tracking import record_embedding_usage
        record_embedding_usage(getattr(response, "usage", None))
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]
    return cached_embedder(embed, model) if cache else embed


def _normalise(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return (vectors / np.where(norms == 0, 1, norms)).astype(np.float32)


def _load_chunks(chunks_path: Path) -> list[dict[str, Any]]:
    with Path(chunks_path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _embed_text(chunk: dict[str, Any]) -> str:
    meta = chunk["metadata"]
    # 절 제목만 붙인다. 문서 제목을 넣으면 같은 문서 chunk끼리 벡터가 비슷해져 구분력이 떨어졌다(M4 실험).
    return f"{meta.get('section', '')}\n{chunk['text']}"


def build_semantic_index(chunks_path: Path | None = None, out_dir: Path | None = None,
                         embed: Embedder | None = None, model: str = EMBEDDING_MODEL) -> dict[str, Any]:
    chunks_path = Path(chunks_path or RAG_DIR / "chunks.jsonl")
    out_dir = Path(out_dir or RAG_DIR)
    chunks = _load_chunks(chunks_path)
    embed = embed or openai_embedder(model)
    rows: list[list[float]] = []
    texts = [_embed_text(chunk) for chunk in chunks]
    for start in range(0, len(texts), BATCH):
        rows.extend(embed(texts[start:start + BATCH]))
    matrix = _normalise(np.asarray(rows, dtype=np.float32))
    np.save(out_dir / "embeddings.npy", matrix)
    meta = {
        "model": model, "dimensions": int(matrix.shape[1]), "count": len(chunks),
        "chunk_ids": [chunk["chunk_id"] for chunk in chunks],
        "chunks_sha256": hashlib.sha256(chunks_path.read_bytes()).hexdigest(),
        "built_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
    }
    (out_dir / "embeddings_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {key: value for key, value in meta.items() if key != "chunk_ids"}


def _load_index(rag_dir: Path) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, Any]]:
    meta_path, matrix_path, chunks_path = rag_dir / "embeddings_meta.json", rag_dir / "embeddings.npy", rag_dir / "chunks.jsonl"
    if not (meta_path.is_file() and matrix_path.is_file() and chunks_path.is_file()):
        raise SemanticUnavailable("의미 검색 색인이 없습니다(scripts/build_semantic_index.py 실행 필요).")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("chunks_sha256") != hashlib.sha256(chunks_path.read_bytes()).hexdigest():
        # 코퍼스가 바뀌었는데 벡터가 그대로면 행-chunk 대응이 틀어지므로 쓰지 않는다.
        raise SemanticUnavailable("코퍼스가 바뀌어 의미 검색 색인이 오래됐습니다(재색인 필요).")
    return np.load(matrix_path), _load_chunks(chunks_path), meta


def search_semantic(query: str, *, limit: int = 8, document_id: str | None = None, notice_no: str | None = None,
                    document_type: str | None = None, embed: Embedder | None = None,
                    rag_dir: Path | None = None) -> list[RetrievedEvidence]:
    """Cosine-similarity search; rank = 1 - similarity (lower is better, like bm25)."""
    if not query.strip():
        return []
    matrix, chunks, meta = _load_index(Path(rag_dir or RAG_DIR))
    try:
        vector = (embed or openai_embedder(meta["model"]))([query])[0]
    except Exception as exc:  # network / auth / quota — never leak the key, just the error type
        raise SemanticUnavailable(f"임베딩 API 호출 실패({type(exc).__name__}).") from None
    scores = matrix @ _normalise(np.asarray([vector], dtype=np.float32))[0]
    results: list[RetrievedEvidence] = []
    for index in np.argsort(-scores):
        chunk_meta = chunks[index]["metadata"]
        if document_id and chunk_meta.get("document_id") != document_id:
            continue
        if notice_no and chunk_meta.get("related_notice_no") != notice_no:
            continue
        if document_type and chunk_meta.get("document_type") != document_type:
            continue
        results.append(RetrievedEvidence(chunk_id=chunks[index]["chunk_id"], text=chunks[index]["text"],
                                         metadata={**chunk_meta, "semantic_score": round(float(scores[index]), 4)},
                                         rank=1.0 - float(scores[index])))
        if len(results) >= limit:
            break
    return results

