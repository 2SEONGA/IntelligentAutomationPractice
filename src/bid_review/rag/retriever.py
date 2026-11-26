"""Local exact-term retrieval over page-cited RAG chunks (SQLite FTS5).

검색 방식(M33):
  1) 후보 찾기 — 공백을 없앤 정규화 본문에 대한 trigram FTS 색인(`evidence_compact`)에서 부분 문자열로 찾는다.
     unicode61 토크나이저는 한국어를 띄어쓰기 단위로만 잘라 "사 업 금 액", "계약기간은", "원(부가가치세"를
     "사업금액"·"계약기간"·"부가가치세"로 찾지 못했다. 이 색인이 없는 예전 DB는 기존 bm25 검색으로 동작한다.
  2) 검색어 확장 — 같은 뜻의 표기(부가가치세·부가세·VAT, 공동수급·공동도급 등)를 OR로 묶는다.
  3) 재정렬 — 검색어 출현 수 + "검색어 바로 뒤에 값(금액·날짜·허용/불허 등)이 오는가"에 가점,
     목차·빈 서식·표지 chunk에 감점. 지우지는 않으므로 본문이 없으면 여전히 반환된다.
  4) 중복 제거 — 같은 쪽의 같은 문장(본문 chunk와 표 chunk)은 하나만 남긴다.
반환 형식(RetrievedEvidence, rank 낮을수록 관련)은 그대로다.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..settings import PROJECT_ROOT


TOKEN_PATTERN = re.compile(r"[가-힣A-Za-z0-9]+")
INDEX_SCHEMA_VERSION = 2
# 같은 뜻으로 쓰이는 표기. 검색어 토큰이 왼쪽 값과 같으면 오른쪽 표기 중 하나만 있어도 일치로 본다.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "부가가치세": ("부가가치세", "부가세", "vat"),
    "부가세": ("부가가치세", "부가세", "vat"),
    "vat": ("부가가치세", "부가세", "vat"),
    "공동수급": ("공동수급", "공동도급", "공동계약"),
    "공동도급": ("공동수급", "공동도급", "공동계약"),
    "계약기간": ("계약기간", "사업기간", "과업기간", "수행기간", "용역기간"),
    "사업기간": ("계약기간", "사업기간", "과업기간", "수행기간", "용역기간"),
    "수행기간": ("계약기간", "사업기간", "과업기간", "수행기간", "용역기간"),
    "과업기간": ("계약기간", "사업기간", "과업기간", "수행기간", "용역기간"),
    "사업금액": ("사업금액", "사업예산", "사업비", "총사업비"),
    "사업예산": ("사업금액", "사업예산", "사업비", "총사업비"),
    "사업비": ("사업금액", "사업예산", "사업비", "총사업비"),
    "참가자격": ("참가자격", "참여자격"),
}
ROLE_PENALTY = {"toc": 6.0, "form_template": 4.0, "cover": 1.0}
VALUE_AFTER_TERM = re.compile(r"^.{0,40}?(?:\d[\d,]{2,}|20\d{2}|허용|불허|불가|아니|않|포함|별도|제외|이상|이하|이내|까지)")
MAX_CANDIDATES = 400


@dataclass(frozen=True)
class RetrievedEvidence:
    chunk_id: str
    text: str
    metadata: dict[str, Any]
    rank: float


def compact_text(text: str) -> str:
    """색인·검색 공통 정규화: NFKC·소문자, 한글·영숫자만 남기고 공백·기호 제거("사 업 금 액" → "사업금액")."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text).casefold() if ch.isalnum())


def _query_groups(query: str) -> list[tuple[str, ...]]:
    """검색어 토큰마다 같은 뜻 표기 묶음. 토큰 순서는 유지, 중복 제거."""
    groups: list[tuple[str, ...]] = []
    for token in dict.fromkeys(compact_text(token) for token in TOKEN_PATTERN.findall(query)):
        if token:
            groups.append(SYNONYMS.get(token, (token,)))
    return groups


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
        connection.execute("DROP TABLE IF EXISTS evidence_compact")
        connection.execute(
            "CREATE VIRTUAL TABLE evidence_compact USING fts5("
            "chunk_id UNINDEXED, compact, document_id UNINDEXED, notice_no UNINDEXED, tokenize='trigram')"
        )
        connection.executemany(
            "INSERT INTO evidence_compact(chunk_id, compact, document_id, notice_no) VALUES (?, ?, ?, ?)",
            [(row[0], compact_text(row[1]), row[3], row[4]) for row in rows],
        )
        connection.execute("CREATE TABLE IF NOT EXISTS index_info(key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT OR REPLACE INTO index_info VALUES ('schema_version', ?)", (str(INDEX_SCHEMA_VERSION),))
        connection.commit()
        return len(rows)
    finally:
        connection.close()


def _has_compact_index(connection: sqlite3.Connection) -> bool:
    return bool(connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_compact'").fetchone())


def _compact_candidates(connection: sqlite3.Connection, groups: list[tuple[str, ...]], match_mode: str,
                        document_id: str | None, notice_no: str | None) -> list[sqlite3.Row]:
    clauses, parameters = [], []
    for group in groups:
        clauses.append("(" + " OR ".join("c.compact LIKE ?" for _ in group) + ")")
        parameters.extend(f"%{alt.replace('%', '').replace('_', '')}%" for alt in group)
    joined = (" AND " if match_mode == "all" else " OR ").join(clauses)
    conditions = [f"({joined})"]
    if document_id:
        conditions.append("c.document_id = ?")
        parameters.append(document_id)
    if notice_no:
        conditions.append("c.notice_no = ?")
        parameters.append(notice_no)
    parameters.append(MAX_CANDIDATES)
    return connection.execute(
        "SELECT f.chunk_id, f.text, f.metadata, c.compact FROM evidence_compact c "
        "JOIN evidence_fts f ON f.chunk_id = c.chunk_id "
        f"WHERE {' AND '.join(conditions)} LIMIT ?", parameters,
    ).fetchall()


def _relevance(compact: str, groups: list[tuple[str, ...]], metadata: dict[str, Any]) -> float:
    """높을수록 관련. 검색어 출현·값 동반 여부·검색어 묶음 충족 비율에서 chunk 역할 감점을 뺀다."""
    score, matched, value_bonus = 0.0, 0, 0.0
    for group in groups:
        count = sum(compact.count(alt) for alt in group)
        if count:
            matched += 1
            score += 1.0 + math.log1p(min(count, 5))
            for alt in group:
                start = compact.find(alt)
                while start >= 0 and value_bonus < 3.0:
                    if VALUE_AFTER_TERM.match(compact[start + len(alt):start + len(alt) + 48]):
                        value_bonus += 1.5
                        break
                    start = compact.find(alt, start + 1)
    coverage = matched / len(groups) if groups else 0.0
    score += 4.0 * coverage + value_bonus
    role = metadata.get("retrieval_role") or ("toc" if metadata.get("page_type") == "table_of_contents" else "body")
    score -= ROLE_PENALTY.get(role, 0.0)
    # 길이 정규화(아주 긴 chunk가 출현 수만으로 앞서지 않게), 짧은 chunk는 그대로.
    return score / (1.0 + max(0, len(compact) - 1200) / 2400)


def _dedupe(results: list[RetrievedEvidence]) -> list[RetrievedEvidence]:
    """같은 문서·같은 쪽에서 내용이 같거나 한쪽이 다른 쪽에 포함되는 chunk는 앞선 것 하나만 남긴다."""
    kept: list[tuple[RetrievedEvidence, str]] = []
    for item in results:
        body = compact_text(item.text.replace("표 행:", ""))
        key = (item.metadata.get("document_id"), item.metadata.get("page"))
        if any((other.metadata.get("document_id"), other.metadata.get("page")) == key
               and body and (body in other_body or other_body in body)
               for other, other_body in kept):
            continue
        kept.append((item, body))
    return [item for item, _ in kept]


def search_keyword(
    query: str,
    *,
    limit: int = 8,
    document_id: str | None = None,
    notice_no: str | None = None,
    match_mode: str = "all",
    database_path: Path | None = None,
    document_type: str | None = None,
    document_version: str | None = None,
) -> list[RetrievedEvidence]:
    """Search exact Korean/Latin terms and return source-addressable chunks.

    document_type(rfp_pdf/notice_pdf/g2b_notice_metadata)·document_version(공고 차수)으로 같은 공고번호의
    RFP·공고문·API 레코드와 판본을 나눠 검색할 수 있다.
    """
    if limit < 1 or limit > 50:
        raise ValueError("limit must be between 1 and 50")
    if match_mode not in {"all", "any"}:
        raise ValueError("match_mode must be 'all' or 'any'")
    groups = _query_groups(query)
    if not groups:
        return []
    database_path = database_path or PROJECT_ROOT / "outputs/rag/retrieval.sqlite3"
    if not Path(database_path).is_file():  # 색인 없음: 검색 결과 없이 계속(T1.5는 담당자 확인으로 넘긴다)
        return []
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        if _has_compact_index(connection):
            rows = _compact_candidates(connection, groups, match_mode, document_id, notice_no)
            scored = []
            for row in rows:
                metadata = json.loads(row["metadata"])
                scored.append((-_relevance(row["compact"], groups, metadata), row, metadata))
        else:  # 예전 색인: 기존 unicode61 bm25 검색 후 같은 재정렬
            expression = _fts_expression(query, match_mode)
            conditions = ["evidence_fts MATCH ?"]
            parameters: list[Any] = [expression]
            if document_id:
                conditions.append("document_id = ?")
                parameters.append(document_id)
            if notice_no:
                conditions.append("notice_no = ?")
                parameters.append(notice_no)
            parameters.append(MAX_CANDIDATES)
            rows = connection.execute(
                "SELECT chunk_id, text, metadata, bm25(evidence_fts) AS rank "
                f"FROM evidence_fts WHERE {' AND '.join(conditions)} ORDER BY rank LIMIT ?",
                parameters,
            ).fetchall()
            scored = []
            for row in rows:
                metadata = json.loads(row["metadata"])
                scored.append((float(row["rank"]) - _relevance(compact_text(row["text"]), groups, metadata), row, metadata))
    finally:
        connection.close()

    results: list[RetrievedEvidence] = []
    for rank, row, metadata in sorted(scored, key=lambda item: (item[0], item[2].get("page") or 0)):
        if document_type and metadata.get("document_type") != document_type:
            continue
        if document_version and str(metadata.get("document_version") or metadata.get("related_notice_order") or "") != document_version:
            continue
        results.append(RetrievedEvidence(chunk_id=row["chunk_id"], text=row["text"], metadata=metadata, rank=float(rank)))
    return _dedupe(results)[:limit]


def related_notice_number(document_id: str, database_path: Path | None = None) -> str | None:
    """Return a unique notice number attached to indexed chunks of this PDF."""
    database_path = database_path or PROJECT_ROOT / "outputs/rag/retrieval.sqlite3"
    if not Path(database_path).is_file():  # 색인을 아직 안 만든 PC: 공고번호 없이 계속(실행을 멈추지 않음)
        return None
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
