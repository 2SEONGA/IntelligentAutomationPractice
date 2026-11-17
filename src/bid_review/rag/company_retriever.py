"""Keyword retrieval over public company capability records with provenance."""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..settings import PROJECT_ROOT


TARGET_COMPANY = "㈜아이티센엔텍"
TOKEN_PATTERN = re.compile(r"[가-힣A-Za-z0-9]+")
RECORD_TYPE_MAP = {
    "법인기본정보": "identity",
    "사업역량": "business_capability",
    "인증및지식재산": "certification",
    "공개수행실적": "project_reference",
    "공개기술역량": "business_capability",
    "재무참고정보": "financial_reference",
}


@dataclass(frozen=True)
class RetrievedCompanyEvidence:
    record_id: str
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


def _searchable_text(record: dict[str, Any]) -> str:
    fields = [
        record.get("title"),
        " ".join(record.get("keywords", [])),
        record.get("section"),
        record.get("excerpt"),
        record.get("client"),
        record.get("project_role"),
        record.get("contract_period"),
        record.get("period"),
        record.get("financial_basis"),
        record.get("bid_use"),
        record.get("verification_required"),
        record.get("progress_interpretation"),
        record.get("reported_progress_as_of"),
    ]
    fields.extend(
        f"{key} {value}"
        for key, value in record.items()
        if isinstance(value, (str, int, float)) and key not in {"id", "title", "excerpt"}
    )
    return "\n".join(str(value) for value in fields if value not in (None, ""))


def build_company_index(
    source_path: Path | None = None,
    database_path: Path | None = None,
) -> int:
    """Build a separate FTS index; does not modify the RFP evidence index."""
    source_path = Path(source_path or PROJECT_ROOT / "data/company/company")
    database_path = Path(database_path or PROJECT_ROOT / "outputs/rag/company_retrieval.sqlite3")
    company_data = json.loads(source_path.read_text(encoding="utf-8"))
    company_name = company_data.get("company_name")
    if company_name != TARGET_COMPANY:
        raise ValueError(f"Expected company data for {TARGET_COMPANY}; got {company_name!r}")

    metadata_by_id = company_record_metadata(source_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("DROP TABLE IF EXISTS company_evidence_fts")
        connection.execute(
            "CREATE VIRTUAL TABLE company_evidence_fts USING fts5("
            "record_id UNINDEXED, text, metadata UNINDEXED, "
            "company_entity UNINDEXED, record_type UNINDEXED, "
            "tokenize='unicode61')"
        )
        rows = []
        for record in company_data.get("records", []):
            metadata = metadata_by_id[record["id"]]
            rows.append((
                record["id"],
                _searchable_text(record),
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                company_name,
                metadata["record_type"],
            ))
        connection.executemany(
            "INSERT INTO company_evidence_fts "
            "(record_id, text, metadata, company_entity, record_type) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
        return len(rows)
    finally:
        connection.close()


def company_record_metadata(source_path: Path | None = None) -> dict[str, dict[str, Any]]:
    """record_id → metadata (same shape the FTS index stores), only for the target legal entity."""
    source_path = Path(source_path or PROJECT_ROOT / "data/company/company")
    company_data = json.loads(source_path.read_text(encoding="utf-8"))
    company_name = company_data.get("company_name")
    if company_name != TARGET_COMPANY:
        raise ValueError(f"Expected company data for {TARGET_COMPANY}; got {company_name!r}")
    sources = {item["id"]: item for item in company_data.get("sources", [])}
    return {record["id"]: {
        **record,
        "company_entity": company_name,
        "record_type": RECORD_TYPE_MAP.get(record.get("type"), "other"),
        "source": sources.get(record.get("source_id"), {}),
        "source_id": record.get("source_id"),
        "entity_boundary_rule": company_data.get("entity_boundary_rule"),
        "retrieval_notice": "검색 후보이며 실적 인정·자격 충족을 뜻하지 않습니다. 원문과 요구 증빙을 확인하세요.",
    } for record in company_data.get("records", [])}


def _semantic_text(record: dict[str, Any]) -> str:
    # 짧은 표현(제목·키워드·발췌)이 전체 필드보다 요구사항 간 구분력이 높았다(M21 실험: 0.10~0.49 vs 0.12~0.53 압축).
    return f"{record.get('title', '')}\n{' '.join(record.get('keywords', []))}\n{record.get('excerpt', '')}"


# 회사 역량과 대조할 대상이 아닌 RFP 행정 조항 (M22). 이름은 화면·기록에 남는 제외 사유다.
# ponytail: 정규식 목록 — 세 RFP에서 확인한 문구 기준. 새 유형이 나오면 여기에 추가하고 test_semantic.py에 사례를 넣는다.
ADMINISTRATIVE_CLAUSES = (
    ("보안서약서 문구", re.compile(r"(?:본인[은이]|나는|당사는).{0,100}(?:누설|유출|파기|반납|보안|비밀|기밀|내부정보)"
                                 r"|보안\s*서약서")),
    ("지식재산권·산출물 귀속", re.compile(r"지식\s*재산권|산출물.{0,10}반출|공동\s*활용할\s*계획")),
    ("실적 인정 규칙", re.compile(r"입증책임|(?:합병|분할)\s*:|발주한\s*경우.{0,30}준용")),
    ("참가자격 법조항 (T1.5 참가자격에서 검증)", re.compile(r"「[^」]{1,30}법」\s*제\s*\d+\s*조.{0,80}(?:등록|신고)을?\s*필한")),
)


FORMAL_REQUIREMENT_ID = re.compile(r"^(?!TECH-)[A-Z]{3}-\d{3}$")  # 요구사항 정의표 ID(SFR-001 등)


def administrative_clause(requirement_id: str, text: str) -> str | None:
    """Return why a requirement is an administrative clause (skip company retrieval), or None.

    요구사항 정의표의 정식 ID는 제외하지 않는다 — 표 행이 합쳐진 상세 텍스트에 다른 조항 문구가 섞일 수 있다
    (QUR-002 "산출물 완성도 관리"가 "반출"로 오탐된 사례).
    """
    if FORMAL_REQUIREMENT_ID.match(requirement_id or ""):
        return None
    squashed = re.sub(r"\s+", " ", text)
    return next((label for label, pattern in ADMINISTRATIVE_CLAUSES if pattern.search(squashed)), None)


def clean_requirement_query(text: str) -> str:
    """Drop RFP table scaffolding ('요구사항 고유번호 |', '표 행:') so the embedding sees the requirement itself."""
    return re.sub(r"\s+", " ", re.sub(r"요구사항\s*(?:고유번호|명칭|분류|정의|세부내용)|표 행:|\|", " ", text)).strip()[:1500]


def company_semantic_scores(queries: list[str], source_path: Path | None = None, embed=None,
                            cache_dir: Path | None = None):
    """Cosine similarity matrix (queries × records) and record ids. Raises SemanticUnavailable on API failure.

    Record vectors are cached in outputs/rag/company_embeddings.npy and rebuilt when the company JSON changes.
    """
    import hashlib

    import numpy as np

    from .semantic import EMBEDDING_MODEL, SemanticUnavailable, _normalise, openai_embedder

    source_path = Path(source_path or PROJECT_ROOT / "data/company/company")
    cache_dir = Path(cache_dir or PROJECT_ROOT / "outputs/rag")
    records = json.loads(source_path.read_text(encoding="utf-8")).get("records", [])
    record_ids = [record["id"] for record in records]
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    matrix_path, meta_path = cache_dir / "company_embeddings.npy", cache_dir / "company_embeddings_meta.json"
    try:
        embed = embed or openai_embedder(EMBEDDING_MODEL)
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        if meta.get("source_sha256") == source_sha and meta.get("record_ids") == record_ids and matrix_path.is_file():
            matrix = np.load(matrix_path)
        else:
            matrix = _normalise(np.asarray(embed([_semantic_text(record) for record in records]), dtype=np.float32))
            cache_dir.mkdir(parents=True, exist_ok=True)
            np.save(matrix_path, matrix)
            meta_path.write_text(json.dumps({"model": EMBEDDING_MODEL, "source_sha256": source_sha,
                                             "record_ids": record_ids}, ensure_ascii=False, indent=2), encoding="utf-8")
        vectors = _normalise(np.asarray(embed(queries), dtype=np.float32)) if queries else np.zeros((0, matrix.shape[1]))
    except Exception as exc:  # never surface the key or response body
        raise SemanticUnavailable(f"회사 자료 임베딩 실패({type(exc).__name__}).") from None
    return vectors @ matrix.T, record_ids


def ensure_company_index(
    source_path: Path | None = None,
    database_path: Path | None = None,
) -> Path:
    """Build the index when absent or older than the company evidence JSON."""
    source_path = Path(source_path or PROJECT_ROOT / "data/company/company")
    database_path = Path(database_path or PROJECT_ROOT / "outputs/rag/company_retrieval.sqlite3")
    if not database_path.is_file() or source_path.stat().st_mtime_ns > database_path.stat().st_mtime_ns:
        build_company_index(source_path, database_path)
    return database_path


def search_company_evidence(
    query: str,
    *,
    limit: int = 5,
    record_types: list[str] | None = None,
    match_mode: str = "any",
    source_path: Path | None = None,
    database_path: Path | None = None,
) -> list[RetrievedCompanyEvidence]:
    """Search only ㈜아이티센엔텍 records and return source-linked evidence."""
    if limit < 1 or limit > 50:
        raise ValueError("limit must be between 1 and 50")
    expression = _fts_expression(query, match_mode)
    if not expression:
        return []
    database_path = ensure_company_index(source_path=source_path, database_path=database_path)
    conditions = ["company_evidence_fts MATCH ?", "company_entity = ?"]
    parameters: list[Any] = [expression, TARGET_COMPANY]
    if record_types:
        placeholders = ",".join("?" for _ in record_types)
        conditions.append(f"record_type IN ({placeholders})")
        parameters.extend(record_types)
    parameters.append(limit)

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT record_id, text, metadata, bm25(company_evidence_fts) AS rank "
            f"FROM company_evidence_fts WHERE {' AND '.join(conditions)} "
            "ORDER BY rank LIMIT ?",
            parameters,
        ).fetchall()
    finally:
        connection.close()

    return [
        RetrievedCompanyEvidence(
            record_id=row["record_id"],
            text=row["text"],
            metadata=json.loads(row["metadata"]),
            rank=float(row["rank"]),
        )
        for row in rows
    ]
