"""남은 업무 6번: 시연 3건의 T1 → T1.5 → (HITL 중단) → 승인 → 재개 → T2 → T3 → T4 흐름과 T1.5 품질 지표.

    python scripts/check_demo_flow.py [--json outputs/eval/demo_flow.json]

- 실제 운영과 같은 HITL 실행기(hitl_runtime, SQLite 체크포인트)를 임시 DB로 쓴다. 웹 결재 기록·운영 DB는 건드리지 않는다.
- 중단되면 '시연 점검용 승인'으로 재개한다(승인 사유가 결과에 남는다). 한 건은 반려 경로도 확인한다.
- 품질 지표(범주: 금액·참가자격·기간/일정): 근거 적중(인용 1개 이상), 필수 정보 누락(값 없음), 충돌, 사람 확인,
  자동 확정(confirmed) 중 근거가 없는 것(= 자동 통과해서는 안 되는 사례, 0이어야 함).
- 외부 호출: 나라장터 공고 조회(무료). OpenAI 키가 없으면 의미 검색·T3는 '사용 불가'로 기록되고 키워드 검색으로 진행한다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import time

import yaml

from bid_review.rag import hitl_runtime
from bid_review.settings import PROJECT_ROOT

CATEGORIES = {"amount": "금액", "eligibility": "참가자격", "schedule": "기간·일정"}


def demo_cases() -> list[dict]:
    cases = yaml.safe_load((PROJECT_ROOT / "config/rag_sources.yaml").read_text(encoding="utf-8"))["cases"]
    inputs = sorted((PROJECT_ROOT / "data/input").glob("*.pdf"))
    out = []
    for case in cases:
        alias = case["title_alias"].replace(" ", "")
        pdf = next(p for p in inputs if alias[:6] in p.stem.replace(" ", ""))
        notice = PROJECT_ROOT / f"data/notice_attachments/{case['notice_no']}-{case['notice_order']}/notice.pdf"
        out.append({"name": case["title_alias"], "pdf": pdf, "notice_no": case["notice_no"],
                    "notice_order": case["notice_order"], "notice_pdf": notice if notice.is_file() else None})
    return out


def quality(verification: dict) -> dict:
    rows = {}
    for key, label in CATEGORIES.items():
        claims = ((verification.get("categories") or {}).get(key) or {}).get("claims") or []
        status = [c.get("status") for c in claims]
        rows[label] = {
            "claims": len(claims),
            "category_status": ((verification.get("categories") or {}).get(key) or {}).get("status"),
            "with_citation": sum(1 for c in claims if c.get("citations")),
            "missing_value": sum(1 for c in claims if c.get("claim") in (None, "", [])),
            "confirmed": status.count("confirmed"), "conflict": status.count("conflict"),
            "human_review": status.count("human_review"), "retrieve_more": status.count("retrieve_more"),
            # 자동 통과해서는 안 되는 사례: 확정인데 근거 인용이 없거나 인용문이 비었다
            "confirmed_without_evidence": [c.get("field") for c in claims if c.get("status") == "confirmed"
                                           and not any((x.get("quote") or "").strip() for x in c.get("citations") or [])],
        }
    return rows


def summarize(snapshot: dict) -> dict:
    state = snapshot["state"]
    nodes = [event["node"] for event in state.get("execution_history") or []]
    return {"status": snapshot["status"], "decision": state.get("decision"), "nodes": nodes,
            "verification_status": (state.get("verification") or {}).get("status"),
            "g2b_lookup": (state.get("g2b_notice_lookup") or {}).get("status"),
            "notice_pdf_status": state.get("notice_pdf_status"),
            "semantic_retrieval_status": state.get("semantic_retrieval_status"),
            "similar_projects_status": state.get("similar_projects_status"),
            "t4_status": state.get("t4_status"), "report_validation": (state.get("report_validation") or {}).get("status"),
            "report_decision": (state.get("report") or {}).get("decision_status"),
            "routing_reasons": (state.get("routing_reasons") or [])[:3]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
    db = Path(tempfile.mkdtemp()) / "demo_checkpoints.sqlite3"
    company = PROJECT_ROOT / "data/company/company"
    report = []
    for index, case in enumerate(demo_cases()):
        started = time.perf_counter()
        first = hitl_runtime.start_review(str(case["pdf"]), company_path=str(company), notice_no=case["notice_no"],
                                          notice_order=case["notice_order"],
                                          notice_pdf_path=str(case["notice_pdf"]) if case["notice_pdf"] else None,
                                          thread_id=f"demo-{index}", db_path=db)
        row = {"case": case["name"], "notice_no": case["notice_no"], "notice_pdf": bool(case["notice_pdf"]),
               "start": summarize(first), "start_seconds": round(time.perf_counter() - started, 2),
               "quality": quality(first["state"].get("verification") or {})}
        if first["status"] == "paused":
            interrupt = first["interrupts"][0]["id"]
            if index == 0:  # 반려 경로: 같은 실행을 복사하지 않고 별도 thread로 한 번 더 시작해 반려
                other = hitl_runtime.start_review(str(case["pdf"]), company_path=str(company), notice_no=case["notice_no"],
                                                  notice_order=case["notice_order"],
                                                  notice_pdf_path=str(case["notice_pdf"]) if case["notice_pdf"] else None,
                                                  thread_id=f"demo-{index}-reject", db_path=db)
                rejected = hitl_runtime.resume_review(f"demo-{index}-reject", other["interrupts"][0]["id"],
                                                      {"action": "reject", "reviewer": "demo-check", "note": "반려 경로 점검",
                                                       "corrections": {}}, db_path=db)
                row["reject_path"] = summarize(rejected)
            started = time.perf_counter()
            resumed = hitl_runtime.resume_review(f"demo-{index}", interrupt,
                                                 {"action": "approve", "reviewer": "demo-check",
                                                  "note": "시연 점검용 승인(미확인 항목은 원문 확인 전제)", "corrections": {}},
                                                 db_path=db)
            row["after_approve"] = summarize(resumed)
            row["resume_seconds"] = round(time.perf_counter() - started, 2)
        report.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "quality"}, ensure_ascii=False)[:900], flush=True)
        for label, q in row["quality"].items():
            print(f"   {label}: {q}", flush=True)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
