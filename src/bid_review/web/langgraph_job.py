"""웹 첫 실행(offline): LangGraph로 T1 → 나라장터 조회 → T1.5 검증을 먼저 하고, 웹 화면이 읽는 보고서 파일을 쓴다.

- T1.5가 confirmed면 같은 실행에서 T2 → T3 → T2·T3 평가 → T4까지 가고 T4 보고서를 쓴다.
- 담당자 확인이 필요하면 중단 지점을 저장(SQLite)하고 "검토 대기" 보류 보고서를 쓴다. 승인하면
  `POST /api/runs/{id}/core-review`가 같은 실행을 이어 가고 T4 보고서를 새 보고서로 쓴다.
- live 모드: T4까지 끝난 뒤 CrewAI Agent 4개가 검증된 결과의 compact 요약만 받아 브리핑을 붙인다(판정은 규칙 기반 유지).
- 시장·낙찰 정보(M46, B안): T3 색인에 연결된 낙찰정보만 쓴다. 웹의 모든 실행이 이 경로다.

    python -m bid_review.web.langgraph_job --rfp <pdf> --output <case 폴더> [--notice-pdf <pdf>] [--mode live]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4

from ..agent04_render import capability_markdown, market_markdown, rfp_markdown
from ..agent04_report import briefing_lines, report_markdown
from ..crew import BidReviewCrew, _report_artifact_stem
from ..models import (BidReviewReport, CapabilityMatchingResult, DecisionStatus, MarketResearchResult,
                      ReportFinding, RFPAnalysisResult)
from ..settings import PROJECT_ROOT
from . import runs

COMPANY_FILE = PROJECT_ROOT / "data/company/company"
# 웹 진행 표시는 Agent 1~4 단계 이름을 그대로 쓴다. LangGraph Node를 단계에 대응시킨다.
STAGE_NODES = {"agent1": {"t1_extract", "g2b_notice_lookup", "t15_verify"},
               "agent2": {"t2_company_rag_retrieval", "t2_company_match"},
               "agent3": {"t3_similar_projects"},
               "agent4": {"t4_report", "t4_validate"}}


def notice_key(case: Path, prior: dict | None = None) -> dict:
    """나라장터 조회용 공고번호·차수. 공고함에서 시작한 건은 notice_attachment.json, 직접 업로드는 upload.json(입력값)."""
    for name in ("notice_attachment.json", "upload.json"):
        saved = runs._read_json(case / name) or {}
        if saved.get("notice_no"):
            return {"notice_no": saved["notice_no"], "notice_order": saved.get("notice_order") or "000"}
    if (prior or {}).get("notice_no"):
        return {"notice_no": prior["notice_no"], "notice_order": prior.get("notice_order") or "000"}
    return {}


def core_result(snapshot: dict) -> dict:
    """중단·재개 실행기 결과 → langgraph_review.json에 저장할 State(+중단 지점)."""
    result = dict(snapshot["state"])
    result["hitl"] = {"thread_id": snapshot["thread_id"], "status": snapshot["status"],
                      "interrupt_id": snapshot["interrupts"][0]["id"] if snapshot["interrupts"] else None}
    if snapshot["status"] == "paused":
        result["decision"] = "human_review_required"  # 중단 시점 State에는 아직 판정이 없다.
    return result


def _hold_report(state: dict) -> BidReviewReport:
    verification = state.get("verification") or {}
    reasons = state.get("routing_reasons") or verification.get("routing_reasons") or []
    return BidReviewReport(
        decision_status=DecisionStatus.HOLD,
        executive_summary=("T1.5 핵심정보 검증에서 담당자 확인이 필요한 항목이 있어 회사 역량(T2)·유사 사업(T3)·"
                           "종합 보고서(T4)를 아직 만들지 않았습니다. '핵심정보 검증' 탭에서 확인 후 승인하면 "
                           "같은 실행을 이어서 보고서를 만듭니다."),
        key_findings=[ReportFinding(topic="T1.5 핵심정보 검증", assessment=f"상태: {verification.get('status', '미확인')}",
                                    evidence_references=["debug/01_rfp_analysis.json"],
                                    action="핵심정보 검증 탭에서 미확인·충돌 항목 확인 후 승인 또는 반려")],
        bid_conditions=[str(reason) for reason in reasons] or ["T1.5 미확인 항목 확인 필요"],
        next_actions=["핵심정보 검증 탭에서 원문·나라장터 근거 확인", "승인하면 T2·T3·T4가 이어서 실행됨"],
        source_artifacts=["debug/01_rfp_analysis.json"], final_decision="미결정",
    )


def write_artifacts(case: Path, snapshot: dict, *, action: str, reason: str, role: str, live: bool = False) -> Path:
    """웹 화면이 읽는 파일(보고서 JSON·MD, debug/01~03)과 langgraph_review.json을 쓴다. 새 보고서 경로를 돌려준다."""
    state = snapshot["state"]
    rfp = RFPAnalysisResult.model_validate(state["rfp"])
    debug = case / "debug"
    debug.mkdir(parents=True, exist_ok=True)
    BidReviewCrew._write(debug / "01_rfp_analysis", rfp, rfp_markdown(rfp))
    report_path = case / f"{_report_artifact_stem()}.json"
    if snapshot["status"] == "completed" and state.get("report") and state.get("report_validation"):
        from ..rag.report_export import _report_markdown  # noqa: PLC0415 - T4 보고서와 같은 표기
        capability = CapabilityMatchingResult.model_validate(state["capability"])
        market = MarketResearchResult.model_validate(state["market"])
        BidReviewCrew._write(debug / "02_capability_matching", capability, capability_markdown(capability))
        BidReviewCrew._write(debug / "03_market_research", market, market_markdown(market))
        report = BidReviewReport.model_validate(state["report"])
        markdown = _report_markdown(state)
        if live:  # T4 검증이 끝난 결과만 CrewAI 브리핑에 넘긴다
            executive, summaries, _, files = BidReviewCrew().brief(rfp, capability, market, case)
            report = report.model_copy(update={"agent_executive_summary": executive})
            markdown += "\n" + "\n".join(briefing_lines(executive)) + "\n"
            (case / "crew_execution_manifest.json").write_text(json.dumps({
                "crew_framework": "CrewAI", "mode": "live", "pipeline": "LangGraph T1→T1.5→(승인)→T2→T3→T4 → CrewAI 브리핑",
                "context_policy": "compact_summary_only", "llm_tasks_executed": True,
                "briefing_scope": "T4 보고서 검증 이후 참고 브리핑. 판정 상태는 규칙 기반 결과를 따름",
                "agent_summaries": summaries, "artifacts": [f"debug/{name}" for name in files]},
                ensure_ascii=False, indent=2), encoding="utf-8")
    else:  # 담당자 검토 대기(또는 반려 등 T4 미도달): 분석 없는 보류 보고서
        report = _hold_report(state)
        markdown = report_markdown(report, rfp=rfp)
    report_path.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.with_suffix(".md").write_text(markdown, encoding="utf-8")
    runs.store_core_review(report_path, core_result(snapshot), action=action, reason=reason, role=role)
    return report_path


def _stages(snapshot: dict) -> None:
    ran = {event["node"] for event in snapshot["state"].get("execution_history") or []}
    for stage, nodes in STAGE_NODES.items():
        if stage != "agent1":
            print(f"[STAGE] {stage} {'done' if ran & nodes else 'skip'}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="웹 첫 실행: LangGraph 검토")
    parser.add_argument("--rfp", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--notice-pdf")
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    args = parser.parse_args()
    from ..rag import hitl_runtime  # noqa: PLC0415 - LangGraph 의존성은 실행 시에만
    case = Path(args.output).resolve()
    print("[STAGE] agent1 start", flush=True)
    snapshot = hitl_runtime.start_review(
        args.rfp, company_path=COMPANY_FILE if COMPANY_FILE.is_file() else None, notice_pdf_path=args.notice_pdf,
        retrieval_limit=1, thread_id=f"web-{uuid4().hex}", **notice_key(case))
    if snapshot["status"] == "failed":
        print("[실패] LangGraph 실행 오류: " + "; ".join(snapshot["task_errors"]), flush=True)
        sys.exit(1)
    print("[STAGE] agent1 done", flush=True)
    if args.mode == "live" and snapshot["status"] == "completed":
        print("[CrewAI] T4 검증 결과로 브리핑 생성", flush=True)
    path = write_artifacts(case, snapshot, action="analyze", reason="웹 첫 실행(LangGraph)", role="system",
                           live=args.mode == "live")
    _stages(snapshot)
    print(f"[LangGraph] {snapshot['status']} · {snapshot['state'].get('decision') or '담당자 검토 대기'} · {path.name}",
          flush=True)


if __name__ == "__main__":
    main()
