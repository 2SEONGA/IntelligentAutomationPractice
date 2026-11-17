"""Build page-addressable, privacy-filtered chunks from original PDF sources."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from ..agent01_rfp import RFPExtractor
from ..settings import PROJECT_ROOT, load_settings


PHONE_OR_EMAIL = re.compile(
    r"(?:[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}|"
    r"(?:\+?82[- .]?)?0\d{1,2}[- .]?\d{3,4}[- .]?\d{4})",
    re.IGNORECASE,
)
CONTACT_LABEL = re.compile(r"성명|연락처|이메일|전자우편|전화번호|휴대전화|팩스번호")
SECTION_HEADING = re.compile(
    r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[.)．、]?\s*\S.*|"
    r"\d+(?:\.\d+){0,3}\s*[.)．、]?\s*\S.*|"
    r"제\s*\d+\s*조(?:\s*\([^)]*\))?.*|"
    r"[가-하]\s*[.)、]\s*\S.*)$"
)
MAX_CHUNK_CHARACTERS = 1800
G2B_SERVICE_URL = "https://www.data.go.kr/data/15129397/openapi.do"
NOTICE_FIELD_LABELS = {
    "bidNtceNo": "나라장터 공고번호",
    "bidNtceOrd": "공고 차수",
    "bidNtceNm": "공고명",
    "ntceInsttNm": "공고기관",
    "dminsttNm": "수요기관",
    "asignBdgtAmt": "배정예산",
    "presmptPrce": "추정가격",
    "bidClseDt": "입찰 마감 일시",
    "bidNtceDt": "공고 일시",
    "cntrctCnclsMthdNm": "계약 체결 방법",
    "sucsfbidMthdNm": "낙찰 방법",
    "pubPrcrmntLrgClsfcNm": "조달 대분류",
}


@dataclass(frozen=True)
class PdfPage:
    number: int
    text: str
    table_rows: list[str]
    page_type: str
    extraction_issue: str | None


def _normal(value: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", value).casefold() if ch.isalnum())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _privacy_filter(text: str) -> tuple[str, int]:
    """Drop contact rows and redact any remaining phone/email tokens."""
    kept: list[str] = []
    removed_rows = 0
    skip_next_value_row = False
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            skip_next_value_row = False
            continue
        has_contact = bool(PHONE_OR_EMAIL.search(line))
        has_contact_label = bool(CONTACT_LABEL.search(line))
        if has_contact or has_contact_label:
            removed_rows += 1
            skip_next_value_row = has_contact_label and not has_contact
            continue
        if skip_next_value_row:
            removed_rows += 1
            skip_next_value_row = False
            continue
        kept.append(line)
    return "\n".join(kept), removed_rows


def _split_text(text: str, max_chars: int = MAX_CHUNK_CHARACTERS) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            lines = paragraph.splitlines()
            line_chunk = ""
            for line in lines:
                if len(line) > max_chars:
                    if line_chunk:
                        chunks.append(line_chunk)
                        line_chunk = ""
                    chunks.extend(line[i:i + max_chars] for i in range(0, len(line), max_chars))
                elif line_chunk and len(line_chunk) + len(line) + 1 > max_chars:
                    chunks.append(line_chunk)
                    line_chunk = line
                else:
                    line_chunk = f"{line_chunk}\n{line}".strip()
            if line_chunk:
                chunks.append(line_chunk)
            continue
        if current and len(current) + len(paragraph) + 2 > max_chars:
            chunks.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}".strip()
    if current:
        chunks.append(current)
    return chunks


def _page_sections(text: str, table_rows: list[str]) -> list[tuple[str, str, str]]:
    """Return (section, body, content_kind) records, all tied to one PDF page."""
    sections: list[tuple[str, list[str], str]] = []
    section_name = "페이지 본문"
    body: list[str] = []
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            if body and body[-1] != "":
                body.append("")
            continue
        if SECTION_HEADING.match(cleaned) and len(cleaned) <= 140:
            if body:
                sections.append((section_name, body, "text"))
            section_name = cleaned[:140]
            body = [cleaned]
        else:
            body.append(cleaned)
    if body:
        sections.append((section_name, body, "text"))

    result = [(name, "\n".join(lines).strip(), kind) for name, lines, kind in sections if "\n".join(lines).strip()]
    if table_rows:
        table_text = "\n".join(f"표 행: {row}" for row in table_rows)
        result.append(("표 추출", table_text, "table"))
    return result or [("페이지 본문", "", "text")]


def _load_notice_records(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    notices = data.get("notices", {}) if isinstance(data, dict) else {}
    return {str(key): value for key, value in notices.items() if isinstance(value, dict)}


def _load_attachment_records(folder: Path) -> list[dict[str, Any]]:
    records = []
    for sidecar in sorted(folder.glob("*/attachment.json")):
        try:
            record = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        records.append({**record, "folder": sidecar.parent})
    return records


def _related_case(path: Path, cases: list[dict[str, Any]], notices: dict[str, dict[str, Any]]) -> dict[str, Any]:
    haystack = _normal(path.stem)
    for case in cases:
        alias = _normal(str(case.get("title_alias", "")))
        if not alias or alias not in haystack:
            continue
        notice_no = str(case.get("notice_no", ""))
        notice = next(
            (item for item in notices.values() if str(item.get("bidNtceNo", "")) == notice_no),
            {},
        )
        return {
            "related_notice_no": notice_no or None,
            "related_notice_order": str(case.get("notice_order", "")) or None,
            "related_notice_title": notice.get("bidNtceNm"),
            "notice_url": notice.get("bidNtceDtlUrl"),
            "notice_attachments": notice.get("attachments", []),
            "source_version_note": "provided_original; G2B attachment version not byte-compared",
        }
    return {
        "related_notice_no": None,
        "related_notice_order": None,
        "related_notice_title": None,
        "notice_url": None,
        "notice_attachments": [],
        "source_version_note": "no_related_notice_record",
    }


def _chunk_id(document_id: str, page: int, section: str, ordinal: int, text: str) -> str:
    content = f"{document_id}\0{page}\0{section}\0{ordinal}\0{text}".encode("utf-8")
    return hashlib.sha256(content).hexdigest()[:24]


def _index_notice_metadata(
    case: dict[str, Any],
    notice: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    notice_no = str(case.get("notice_no", ""))
    notice_order = str(case.get("notice_order", ""))
    notice_title = str(notice.get("bidNtceNm", ""))
    api_document_id = f"g2b-{notice_no}-{notice_order}"
    detail_url = notice.get("bidNtceDtlUrl")
    chunks: list[dict[str, Any]] = []
    for field_name, label in NOTICE_FIELD_LABELS.items():
        value = notice.get(field_name)
        if value is None or not str(value).strip():
            continue
        value_text = str(value).strip()
        section = label
        chunk_text = f"{label}: {value_text}"
        chunks.append({
            "chunk_id": _chunk_id(api_document_id, 0, section, 1, chunk_text),
            "text": chunk_text,
            "metadata": {
                "document_id": api_document_id,
                "document_sha256": None,
                "document_title": notice_title,
                "document_type": "g2b_notice_metadata",
                "source_path": "outputs/notices/notices.json",
                "page": None,
                "section": section,
                "content_kind": "structured_notice_field",
                "field_name": field_name,
                "field_label": label,
                "field_value": value_text,
                "related_notice_no": notice_no,
                "related_notice_order": notice_order,
                "related_notice_title": notice_title,
                "notice_url": detail_url,
                "source_url": G2B_SERVICE_URL,
                "source_version_note": "나라장터 입찰공고 API 수집 레코드; 첨부 원문과 별도 출처로 취급",
            },
        })
    source = {
        "document_id": api_document_id,
        "document_type": "g2b_notice_metadata",
        "title": notice_title,
        "notice_no": notice_no,
        "notice_order": notice_order,
        "field_chunk_count": len(chunks),
        "detail_url": detail_url,
        "source_url": G2B_SERVICE_URL,
        "attachments": notice.get("attachments", []),
        "source_version_note": "API fields are structured notice metadata, not page-cited attachment text",
    }
    return chunks, source


def index_pdf(
    path: Path,
    source_case: dict[str, Any],
    settings: dict[str, Any] | None = None,
    document_type: str = "rfp_pdf",
    title: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Extract and chunk a PDF while preserving citation and version metadata."""
    settings = settings or load_settings()
    extractor = RFPExtractor(settings)
    pages, issues, analyses, _ = extractor._extract_pages(path)
    document_sha = _sha256(path)
    document_id = document_sha[:16]
    page_quality = {item.page_number: item for item in analyses}
    chunks: list[dict[str, Any]] = []
    redacted_rows = 0

    for page in pages:
        safe_text, removed = _privacy_filter(page.text)
        redacted_rows += removed
        safe_tables: list[str] = []
        for row in page.table_rows:
            safe_row, removed = _privacy_filter(row)
            redacted_rows += removed
            if safe_row.strip():
                safe_tables.append(safe_row.strip())

        for section, body, content_kind in _page_sections(safe_text, safe_tables):
            for ordinal, chunk_text in enumerate(_split_text(body), start=1):
                if not chunk_text:
                    continue
                quality = page_quality.get(page.number)
                chunks.append({
                    "chunk_id": _chunk_id(document_id, page.number, section, ordinal, chunk_text),
                    "text": chunk_text,
                    "metadata": {
                        "document_id": document_id,
                        "document_sha256": document_sha,
                        "document_title": title or path.stem,
                        "document_type": document_type,
                        "source_path": str(path.relative_to(PROJECT_ROOT)),
                        "page": page.number,
                        "section": section,
                        "content_kind": content_kind,
                        "page_type": page.page_type,
                        "ocr_required": bool(quality.ocr_required) if quality else False,
                        "extraction_issue": quality.ocr_reason if quality else page.extraction_issue,
                        "related_notice_no": source_case.get("related_notice_no"),
                        "related_notice_order": source_case.get("related_notice_order"),
                        "related_notice_title": source_case.get("related_notice_title"),
                        "notice_url": source_case.get("notice_url"),
                        "source_version_note": source_case.get("source_version_note"),
                    },
                })

    source_record = {
        "document_id": document_id,
        "path": str(path.relative_to(PROJECT_ROOT)),
        "title": title or path.stem,
        "document_type": document_type,
        "sha256": document_sha,
        "page_count": len(pages),
        "chunk_count": len(chunks),
        "privacy_rows_removed": redacted_rows,
        "extraction_issues": [issue.model_dump() for issue in issues],
        "related_notice_no": source_case.get("related_notice_no"),
        "related_notice_order": source_case.get("related_notice_order"),
        "related_notice_title": source_case.get("related_notice_title"),
        "notice_url": source_case.get("notice_url"),
        "notice_attachments": source_case.get("notice_attachments", []),
        "source_version_note": source_case.get("source_version_note"),
    }
    return chunks, source_record


def build_local_corpus(
    input_dir: Path | None = None,
    notice_index_path: Path | None = None,
    source_manifest_path: Path | None = None,
    attachment_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    input_dir = input_dir or PROJECT_ROOT / "data/input"
    notice_index_path = notice_index_path or PROJECT_ROOT / "outputs/notices/notices.json"
    source_manifest_path = source_manifest_path or PROJECT_ROOT / "config/rag_sources.yaml"
    source_manifest = yaml.safe_load(source_manifest_path.read_text(encoding="utf-8")) or {}
    cases = source_manifest.get("cases", [])
    notices = _load_notice_records(notice_index_path)
    all_chunks: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    notice_sources: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    attachments = _load_attachment_records(attachment_dir or PROJECT_ROOT / "data/notice_attachments")
    g2b_rfp_hashes = {(record.get("rfp_attachment") or {}).get("sha256"): record for record in attachments}
    for pdf_path in sorted(input_dir.glob("*.pdf"), key=lambda item: item.name.casefold()):
        try:
            related = _related_case(pdf_path, cases, notices)
            match = g2b_rfp_hashes.get(_sha256(pdf_path))
            if match:
                related["source_version_note"] = (
                    f"provided_original; byte-identical to G2B attachment {match['notice_no']}-{match['notice_order']} "
                    f"({match['rfp_attachment']['name']}, checked {match['saved_at']})")
            chunks, source = index_pdf(pdf_path, related)
            all_chunks.extend(chunks)
            sources.append(source)
        except Exception as exc:
            errors.append({"path": str(pdf_path.relative_to(PROJECT_ROOT)), "error": type(exc).__name__, "message": str(exc)[:300]})

    for record in attachments:
        notice_pdf = record["folder"] / "notice.pdf"
        if not record.get("notice") or not notice_pdf.is_file():
            continue
        related = {
            "related_notice_no": record["notice_no"], "related_notice_order": record["notice_order"],
            "related_notice_title": record["notice"]["name"], "notice_url": record["notice"]["url"],
            "notice_attachments": [], "source_version_note": (
                f"G2B {record['api_operation']} attachment, sha256 {record['notice']['sha256']}, saved {record['saved_at']}"),
        }
        try:
            chunks, source = index_pdf(notice_pdf, related, document_type="notice_pdf",
                                       title=f"나라장터 공고문 — {record['notice']['name']}")
            all_chunks.extend(chunks)
            sources.append(source)
        except Exception as exc:
            errors.append({"path": str(notice_pdf.relative_to(PROJECT_ROOT)), "error": type(exc).__name__, "message": str(exc)[:300]})

    for case in cases:
        notice_no = str(case.get("notice_no", ""))
        notice_order = str(case.get("notice_order", ""))
        notice = next((
            item for item in notices.values()
            if str(item.get("bidNtceNo", "")) == notice_no
            and str(item.get("bidNtceOrd", "")) == notice_order
        ), None)
        if notice is None:
            continue
        chunks, source = _index_notice_metadata(case, notice)
        all_chunks.extend(chunks)
        notice_sources.append(source)

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds"),
        "input_directory": str(input_dir.relative_to(PROJECT_ROOT)),
        "notice_index": str(notice_index_path.relative_to(PROJECT_ROOT)) if notice_index_path.exists() else None,
        "source_count": len(sources) + len(notice_sources),
        "pdf_source_count": len(sources),
        "notice_pdf_source_count": sum(1 for item in sources if item.get("document_type") == "notice_pdf"),
        "notice_metadata_source_count": len(notice_sources),
        "chunk_count": len(all_chunks),
        "privacy_rows_removed": sum(item["privacy_rows_removed"] for item in sources),
        "sources": sources,
        "notice_metadata_sources": notice_sources,
        "errors": errors,
        "notice_attachment_policy": ("공고문 PDFs from data/notice_attachments (scripts/fetch_notice_attachments.py) are indexed "
                                     "as document_type=notice_pdf with their own document_id; RFP checks stay scoped to the RFP."),
    }
    return all_chunks, manifest


def write_local_corpus(
    output_dir: Path | None = None,
    input_dir: Path | None = None,
) -> dict[str, Any]:
    output_dir = output_dir or PROJECT_ROOT / "outputs/rag"
    output_dir.mkdir(parents=True, exist_ok=True)
    chunks, manifest = build_local_corpus(input_dir=input_dir)
    chunks_path = output_dir / "chunks.jsonl"
    with chunks_path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False, sort_keys=True) + "\n")
    manifest["chunks_file"] = str(chunks_path.relative_to(PROJECT_ROOT))
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
