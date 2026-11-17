"""Run one RFP through the LangGraph T1/T1.5/T2 review workflow."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bid_review.rag.workflow import run_bid_review


def main() -> None:
    parser = argparse.ArgumentParser(description="LangGraph 입찰 핵심정보 검증 및 회사 역량 분석")
    parser.add_argument("--rfp", required=True, help="검토할 RFP 원본 PDF 경로")
    parser.add_argument("--company", help="회사 역량 JSON 경로 (선택)")
    parser.add_argument("--notice-no", help="나라장터 공고번호 (자동 추출 실패 시 지정)")
    parser.add_argument("--notice-order", default="000", help="나라장터 공고 차수 (기본 000)")
    parser.add_argument("--retrieval-limit", type=int, default=2, choices=range(0, 4),
                        help="미확인 항목 추가 검색 횟수 상한 (0~3, 기본 2)")
    parser.add_argument("--output", default="outputs/langgraph_review.json", help="결과 JSON 경로")
    args = parser.parse_args()

    state = run_bid_review(
        args.rfp, args.company, args.notice_no, args.retrieval_limit,
        notice_order=args.notice_order,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    verification = state.get("verification", {})
    print(f"결과: {output}")
    print(f"최종 분기: {state.get('decision', 'unknown')}")
    print(f"T1.5 상태: {verification.get('status', 'unknown')}")
    for key, value in verification.get("categories", {}).items():
        print(f"- {key}: {value.get('status')} (근거 후보 {sum(len(c.get('citations', [])) for c in value.get('claims', []))}건)")
    for reason in state.get("routing_reasons", []):
        print(f"- 검토 사유: {reason}")


if __name__ == "__main__":
    main()
