"""과거 사업 1건만 제안요청서를 다시 받는다(잘못 저장된 계약서 등 교체용).

    python scripts/recollect_past_project.py R26BK01589082-000

- 기존 폴더는 outputs/backups/past_rfps/<공고>_<시각>/ 에 먼저 복사한다(원본 보존).
- 수집기(collect_past_projects)와 같은 규칙으로 고른다: 계약서·서약서 제외, PDF 제안요청서 우선, 없으면 제안요청/과업 HWP·HWPX.
- 받은 파일은 형식 magic과 hwp_text 읽기로 확인한다. 실패하면 기존 폴더를 그대로 되돌린다.
- 전체 수집과 달리 다른 공고·awards.json(낙찰)은 건드리지 않는다. 나라장터 첨부정보 API 1회 + 파일 다운로드 1회.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import shutil
import sys

from bid_review.hwp_text import hwp_text
from bid_review.rag.past_projects import PAST_DIR, RFP_FILES, RFP_MAGIC, _pick_rfp
from bid_review.settings import PROJECT_ROOT
from bid_review.web import notices

BACKUP_DIR = PROJECT_ROOT / "outputs/backups/past_rfps"


def recollect(notice_id: str, session=None) -> dict:
    folder = PAST_DIR / notice_id
    meta_path = folder / "meta.json"
    if not meta_path.is_file():
        raise SystemExit(f"{folder}에 meta.json이 없습니다. 처음 수집은 build_past_projects.py를 쓰세요.")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    backup = BACKUP_DIR / f"{notice_id}_{datetime.now():%Y%m%d_%H%M%S}"
    shutil.copytree(folder, backup)

    number, order = notice_id.split("-")
    info = notices.fetch_notice_attachment_info(number, order, session=session)
    rfp, filename = _pick_rfp(info["attachments"])
    if not rfp:
        return {"status": "no_rfp", "backup": str(backup), "attachments": [a["name"] for a in info["attachments"]]}
    if rfp["name"] == (meta.get("rfp_attachment") or {}).get("name"):
        return {"status": "unchanged", "backup": str(backup), "picked": rfp["name"]}

    staging = folder.with_name(folder.name + ".new")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    target = staging / filename
    try:
        if not notices._download(session or notices.requests.Session(), rfp, target, magic=RFP_MAGIC[target.suffix]):
            raise ValueError("다운로드 실패 또는 파일 형식(magic) 불일치")
        if target.suffix != ".pdf":
            hwp_text(target)  # 암호·배포용·손상 HWP는 쓰지 않는다
    except Exception as exc:  # 기존 폴더는 손대지 않았다
        shutil.rmtree(staging, ignore_errors=True)
        return {"status": "failed", "error": str(exc), "backup": str(backup), "picked": rfp["name"]}

    data = target.read_bytes()
    meta.update({"rfp_attachment": {"name": rfp["name"], "url": rfp["url"], "sha256": hashlib.sha256(data).hexdigest()},
                 "api_operation": info["api_operation"], "retrieved_at": info["retrieved_at"],
                 "replaced": {"previous": backup.name, "reason": "잘못 저장된 첨부 교체(recollect_past_project.py)"}})
    (staging / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in RFP_FILES:  # 예전 rfp.* 는 백업에 있으므로 새 파일만 남긴다
        (folder / name).unlink(missing_ok=True)
    shutil.copy2(target, folder / filename)
    shutil.copy2(staging / "meta.json", meta_path)
    shutil.rmtree(staging)
    return {"status": "replaced", "backup": str(backup), "picked": rfp["name"], "file": filename, "bytes": len(data)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("notice_id", help="예: R26BK01589082-000")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    result = recollect(args.notice_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["status"] in ("replaced", "unchanged") else 1)


if __name__ == "__main__":
    main()
