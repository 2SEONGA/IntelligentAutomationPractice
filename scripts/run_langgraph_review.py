"""RFP 검토 후 보고서·검증·진단 파일을 실행별 폴더에 저장한다."""
from __future__ import annotations

import argparse
from pathlib import Path

from bid_review.rag.report_export import export_review_artifacts
from bid_review.rag.workflow import run_bid_review


def main() -> None:
    parser = argparse.ArgumentParser(description="LangGraph 입찰 검토 및 T4 보고서 저장")
    parser.add_argument("--rfp", required=True, help="RFP 원본 PDF 경로")
    parser.add_argument("--company", help="회사 역량 JSON 경로")
    parser.add_argument("--notice-pdf", help="같은 공고의 공고문 PDF 경로")
    parser.add_argument("--notice-no", help="나라장터 공고번호")
    parser.add_argument("--notice-order", default="000", help="공고 차수")
    parser.add_argument("--retrieval-limit", type=int, default=2, choices=range(0, 4), help="추가 검색 상한 0~3")
    parser.add_argument("--workflow-mode", choices=("sequential", "parallel"), default="sequential",
                        help="T2/T3 순차 또는 병렬 실행")
    parser.add_argument("--output", default="outputs/langgraph_review.json", help="전체 State JSON 경로")
    args = parser.parse_args()
    state = run_bid_review(
        args.rfp, args.company, args.notice_no, args.retrieval_limit,
        notice_order=args.notice_order, notice_pdf_path=args.notice_pdf,
        workflow_mode=args.workflow_mode,
    )
    output = Path(args.output)
    manifest = export_review_artifacts(state, output.parent / (output.stem + "_reports"))
    # --output에도 저장 직전 검사가 반영된 State를 복사한다. 실행별 원본은 별도 보존된다.
    output.write_text(Path(manifest["files"]["state_json"]).read_text(encoding="utf-8"), encoding="utf-8")
    print(f"전체 결과: {output}")
    print(f"실행 처리 상태: {state.get('decision', 'unknown')}")
    print(f"T1.5 상태: {(state.get('verification') or {}).get('status', 'unknown')}")
    print(f"T4 생성 상태: {manifest['t4_status']}")
    print(f"보고서 검증 상태: {manifest['validation_status']}")
    print(f"저장 폴더: {manifest['directory']}")
    report_md = manifest["files"].get("report_md")
    print(f"보고서: {report_md}" if report_md else "보고서 미생성: diagnostic.md의 사유를 확인하세요.")
    print(f"실행 진단: {manifest['files']['diagnostic_md']}")
    for reason in state.get("routing_reasons") or []:
        print(f"- 검토 사유: {reason}")


if __name__ == "__main__":
    main()
