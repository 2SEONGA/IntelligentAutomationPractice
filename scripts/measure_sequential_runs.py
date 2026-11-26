"""남은 업무 4번: 같은 사례를 순차 모드로 반복 실행해 단계별 시간과 결과 재현성을 잰다(변경 전후 비교용).

    python scripts/measure_sequential_runs.py --cases cases.json --repeats 3 --output out.json [--approve-note "벤치마크 고정 승인"]

- 실행마다 새 프로세스: 웹 작업처럼 매번 새로 시작한 상태를 잰다(프로세스 안 캐시가 다음 회차를 빠르게 만드는 효과 제외).
- 기록: 전체 시간, Node별 시간(T1 = t1_extract), T3 후보 공고번호·유사도, T1 핵심값(사업명·예산·기간·마감), 판정.
- 사례 JSON: [{"id": "...", "rfp": "경로", "notice_no": "...", "notice_order": "000", "notice_pdf": "경로(선택)"}, ...]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time


def run_once(case: dict, approve_note: str | None) -> dict:
    from bid_review.rag.workflow import run_bid_review
    started = time.perf_counter()
    state = run_bid_review(case["rfp"], case.get("company"), case.get("notice_no"), notice_order=case.get("notice_order"),
                           notice_pdf_path=case.get("notice_pdf"), workflow_mode="sequential",
                           **({"review_decision": "approved", "review_note": approve_note, "reviewer": "benchmark"}
                              if approve_note else {}))
    elapsed = time.perf_counter() - started
    basic = (state.get("rfp") or {}).get("basic_info") or {}
    nodes: dict[str, float] = {}
    for event in state.get("execution_history") or []:
        nodes[event["node"]] = round(nodes.get(event["node"], 0.0) + (event.get("duration_seconds") or 0.0), 4)
    return {"elapsed_seconds": round(elapsed, 4), "node_seconds": nodes,
            "t1_values": {k: (basic.get(k) or {}).get("value") for k in
                          ("project_name", "budget_krw", "contract_period", "proposal_deadline", "procuring_entity")},
            "similar_projects_status": state.get("similar_projects_status"),
            "similar_projects": [(p.get("notice_id"), p.get("similarity")) for p in state.get("similar_projects") or []],
            "decision": state.get("decision"), "verification_status": (state.get("verification") or {}).get("status"),
            "report_validation": (state.get("report_validation") or {}).get("status")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--approve-note")
    parser.add_argument("--worker", help=argparse.SUPPRESS)  # 내부용: 사례 하나를 한 번 실행하고 JSON 출력
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if args.worker:
        case = next(c for c in cases if c["id"] == args.worker)
        print("@@RESULT@@" + json.dumps(run_once(case, args.approve_note), ensure_ascii=False, default=str))
        return
    results = []
    for case in cases:
        for repeat in range(1, args.repeats + 1):
            command = [sys.executable, __file__, "--cases", str(args.cases), "--worker", case["id"]]
            if args.approve_note:
                command += ["--approve-note", args.approve_note]
            done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
            line = next((l for l in done.stdout.splitlines() if l.startswith("@@RESULT@@")), None)
            row = json.loads(line[len("@@RESULT@@"):]) if line else {"error": (done.stderr or done.stdout)[-500:]}
            results.append({"case_id": case["id"], "repeat": repeat, **row})
            print(case["id"], repeat, row.get("elapsed_seconds"), (row.get("node_seconds") or {}).get("t1_extract"),
                  row.get("similar_projects_status") or row.get("error", "")[:120], flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
