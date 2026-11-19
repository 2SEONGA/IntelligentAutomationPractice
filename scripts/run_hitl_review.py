"""SQLite에 저장된 LangGraph 실행의 시작·상태 조회·사람 검토 후 재개 CLI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
from uuid import uuid4

from bid_review.rag.hitl_runtime import DEFAULT_DB, resume_review, review_status, start_review
from bid_review.rag.report_export import export_review_artifacts
from bid_review.settings import PROJECT_ROOT


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def save_result(result, output_root, db_path=DEFAULT_DB):
    folder = Path(output_root) / result["thread_id"]
    folder.mkdir(parents=True, exist_ok=True)
    _write(folder / "status.json", result)
    if "case_identity" in result["state"]:
        _write(folder / "case_identity.json", result["state"]["case_identity"])
    if "execution_history" in result["state"]:
        _write(folder / "execution_history.json", result["state"]["execution_history"])
    print(f"thread_id: {result['thread_id']}\n상태: {result['status']}\n상태 파일: {folder / 'status.json'}")
    print(f"처리 결과: {result['state'].get('decision', '검토 중')}")
    if result["status"] == "paused":
        pending = result["interrupts"][0]
        _write(folder / f"request_{pending['id']}.json", pending)
        response_path = folder / f"response_{pending['id']}.json"
        if not response_path.exists():
            _write(response_path, {"thread_id": result["thread_id"], "interrupt_id": pending["id"],
                                  "action": "", "reviewer": "", "note": "", "corrections": {}})
        print(f"검토 후 작성할 응답 파일: {response_path}")
        command = ["python", "scripts/run_hitl_review.py", "--db", str(db_path),
                   "--output-root", str(output_root), "resume", "--response", str(response_path)]
        print("재개 명령: " + shlex.join(command))
    elif result["status"] == "completed":
        manifest = export_review_artifacts(result["state"], folder / "reports")
        print(f"보고서 검증 상태: {manifest['validation_status']}\n결과 폴더: {manifest['directory']}")
    else:
        print("실행 오류·미완료 상태입니다. status.json의 task_errors를 확인하세요.")


def main():
    parser = argparse.ArgumentParser(description="입찰 검토 중단·재개 (로컬 단일 작업자용)")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite 체크포인트 경로. 재개할 때 같은 파일 사용")
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "outputs/hitl"))
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--rfp", required=True)
    start.add_argument("--company")
    start.add_argument("--notice-pdf")
    start.add_argument("--notice-no")
    start.add_argument("--notice-order", default="000")
    start.add_argument("--retrieval-limit", type=int, default=2, choices=range(4))
    start.add_argument("--thread-id")
    status = commands.add_parser("status")
    status.add_argument("--thread-id", required=True)
    resume = commands.add_parser("resume")
    resume.add_argument("--response", required=True)
    args = parser.parse_args()
    try:
        if args.command == "start":
            thread_id = args.thread_id or uuid4().hex
            print(f"새 실행 thread_id: {thread_id}", flush=True)
            result = start_review(args.rfp, company_path=args.company, notice_pdf_path=args.notice_pdf,
                                  notice_no=args.notice_no, notice_order=args.notice_order,
                                  retrieval_limit=args.retrieval_limit, thread_id=thread_id, db_path=args.db)
        elif args.command == "status":
            result = review_status(args.thread_id, db_path=args.db)
        else:
            response = json.loads(Path(args.response).read_text(encoding="utf-8"))
            if not isinstance(response, dict) or not response.get("thread_id") or not response.get("interrupt_id"):
                raise ValueError("응답 파일에 thread_id와 interrupt_id가 필요합니다.")
            response = dict(response)
            thread_id, interrupt_id = response.pop("thread_id"), response.pop("interrupt_id")
            result = resume_review(thread_id, interrupt_id, response, db_path=args.db)
        save_result(result, args.output_root, args.db)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"처리 중단: {exc}\n")


if __name__ == "__main__":
    main()
