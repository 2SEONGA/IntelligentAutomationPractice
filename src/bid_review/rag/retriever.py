"""Local exact-term retrieval over page-cited RAG chunks (SQLite FTS5)."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..settings import PROJECT_ROOT


TOKEN_PATTERN = re.compile(r"[가-힣A-Za-z0-9]+")


@dataclass(frozen=True)
class RetrievedEvidence:
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    rank: float


def _fts_expression(query: str, match_mode: str) -> str:
    tokens = list(dict.fromkeys(TOKEN_PATTERN.findall(query)))
    if not tokens:
        return ""
    if match_mode not in {"all", "any"}:
        raise ValueError("match_mode must be 'all' or 'any'")
    operator = " AND " if match_mode == "all" else " OR "
    return operator.join('"' + token.replace('"', '""') + '"' for token in tokens)


def build_keyword_index(
    chunks_path: Path | None = None,
    database_path: Path | None = None,
) -> int:
    """Rebuild the local FTS index from the privacy-filtered corpus JSONL."""
    chunks_path = chunks_path or PROJECT_ROOT / "outputs/rag/chunks.jsonl"
    database_path = database_path or PROJECT_ROOT / "outputs/rag/retrieval.sqlite3"
    chunks_path = Path(chunks_path)
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("DROP TABLE IF EXISTS evidence_fts")
        connection.execute(
            "CREATE VIRTUAL TABLE evidence_fts USING fts5("
            "chunk_id UNINDEXED, text, metadata UNINDEXED, "
            "document_id UNINDEXED, notice_no UNINDEXED, tokenize='unicode61')"
        )
        rows = []
        with chunks_path.open(encoding="utf-8") as handle:
            for line in handle:
                chunk = json.loads(line)
                rows.append((
                    chunk["chunk_id"],
                    chunk["text"],
                    json.dumps(chunk["metadata"], ensure_ascii=False, sort_keys=True),
                    chunk["metadata"].get("document_id", ""),
                    chunk["metadata"].get("related_notice_no", "") or "",
                ))
        connection.executemany(
            "INSERT INTO evidence_fts(chunk_id, text, metadata, document_id, notice_no) "
            "VALUES (?, ?, ?, ?, ?)", rows
        )
        connection.commit()
        return len(rows)
    finally:
        connection.close()


def search_keyword(
    query: str,
    *,
    limit: int = 8,
    document_id: str | None = None,
    notice_no: str | None = None,
    match_mode: str = "all",
    database_path: Path | None = None,
) -> list[RetrievedEvidence]:
    """Search exact Korean/Latin terms and return source-addressable chunks."""
    if limit < 1 or limit > 50:
        raise ValueError("limit must be between 1 and 50")
    expression = _fts_expression(query, match_mode)
    if not expression:
        return []
    database_path = database_path or PROJECT_ROOT / "outputs/rag/retrieval.sqlite3"
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    conditions = ["evidence_fts MATCH ?"]
    parameters: list[Any] = [expression]
    if document_id:
        conditions.append("document_id = ?")
        parameters.append(document_id)
    if notice_no:
        conditions.append("notice_no = ?")
        parameters.append(notice_no)
    parameters.append(limit)
    try:
        rows = connection.execute(
            "SELECT chunk_id, text, metadata, bm25(evidence_fts) AS rank "
            f"FROM evidence_fts WHERE {' AND '.join(conditions)} ORDER BY rank LIMIT ?",
            parameters,
        ).fetchall()
    finally:
        connection.close()

    results: list[RetrievedEvidence] = []
    for row in rows:
        metadata = json.loads(row["metadata"])
        results.append(RetrievedEvidence(
            chunk_id=row["chunk_id"],
            text=row["text"],
            metadata=metadata,
            rank=float(row["rank"]),
        ))
    return results


def related_notice_number(document_id: str, database_path: Path | None = None) -> str | None:
    """Return a unique notice number attached to indexed chunks of this PDF."""
    database_path = database_path or PROJECT_ROOT / "outputs/rag/retrieval.sqlite3"
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT DISTINCT notice_no FROM evidence_fts "
            "WHERE document_id = ? AND notice_no != '' LIMIT 2",
            (document_id,),
        ).fetchall()
    finally:
        connection.close()
    return str(rows[0][0]) if len(rows) == 1 else None


def notice_pdf_chunks(notice_no: str, database_path: Path | None = None) -> list[RetrievedEvidence]:
    """All indexed 공고문 PDF chunks for one notice (M18), in page order."""
    database_path = database_path or PROJECT_ROOT / "outputs/rag/retrieval.sqlite3"
    if not Path(database_path).is_file():
        return []
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT chunk_id, text, metadata FROM evidence_fts WHERE notice_no = ?", (notice_no,),
        ).fetchall()
    finally:
        connection.close()
    chunks = [RetrievedEvidence(chunk_id=row[0], text=row[1], metadata=json.loads(row[2]), rank=0.0) for row in rows]
    chunks = [item for item in chunks if item.metadata.get("document_type") == "notice_pdf"]
    return sorted(chunks, key=lambda item: item.metadata.get("page") or 0)
