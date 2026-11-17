"""Download each RAG case's G2B 공고문 PDF (official 첨부파일정보 API) for indexing.

Writes data/notice_attachments/<공고번호>-<차수>/notice.pdf and attachment.json
(name, URL, SHA-256, retrieval time). The RFP attachment is only hashed, so the
corpus can say whether data/input's RFP is byte-identical to the G2B version.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

import requests
import yaml

from bid_review.settings import PROJECT_ROOT
from bid_review.web import notices, runs

OUT = PROJECT_ROOT / "data/notice_attachments"


def main() -> None:
    cases = (yaml.safe_load((PROJECT_ROOT / "config/rag_sources.yaml").read_text(encoding="utf-8")) or {}).get("cases", [])
    session = requests.Session()
    for case in cases:
        no, order = str(case["notice_no"]), str(case.get("notice_order") or "000")
        info = notices.fetch_notice_attachment_info(no, order, session=session)
        by_kind = {kind: next((a for a in info["attachments"] if a["kind"] == kind), None) for kind in ("notice", "rfp")}
        folder = OUT / f"{no}-{order}"
        folder.mkdir(parents=True, exist_ok=True)
        record = {"notice_no": no, "notice_order": order, "api_operation": info["api_operation"],
                  "api_source_url": info["api_source_url"], "retrieved_at": info["retrieved_at"]}
        notice = by_kind["notice"]
        (folder / "notice.pdf").unlink(missing_ok=True)  # never keep a stale/misclassified file
        if notice and notices._download(session, notice, folder / "notice.pdf"):
            data = (folder / "notice.pdf").read_bytes()
            record["notice"] = {"name": notice["name"], "url": notice["url"], "document_kind": notice["document_kind"],
                                "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        rfp = by_kind["rfp"]
        if rfp:
            body = session.get(rfp["url"], timeout=120).content
            record["rfp_attachment"] = {"name": rfp["name"], "url": rfp["url"],
                                        "sha256": hashlib.sha256(body).hexdigest() if body.startswith(b"%PDF-") else None}
        record["saved_at"] = datetime.now(runs.KST).isoformat(timespec="seconds")
        (folder / "attachment.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        print(no, order, "notice:", "saved" if "notice" in record else "missing",
              "| rfp sha:", (record.get("rfp_attachment") or {}).get("sha256", "-")[:12])


if __name__ == "__main__":
    main()
