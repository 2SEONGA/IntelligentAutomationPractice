"""검증 상태를 유지한 채 LangGraph 보고서·근거·진단을 실행별 폴더에 저장한다."""
from __future__ import annotations

from copy import deepcopy
import html
import json
from pathlib import Path
import tempfile
from typing import Any

from .report_validation import validate_t4_report


LABELS = {
    "passed": "구조화 일치 검사 통과 · 최종 판단은 담당자 확인 필요",
    "needs_review": "담당자 검토 필요 · 미확인 사항 포함",
    "failed": "보고서 검증 실패 · 수정 필요",
    "not_run": "T4 보고서 미생성 · 후속 검증 미실행",
}


def _text(value: Any) -> str:
    """PDF 발췌가 Markdown 표나 HTML 태그로 해석되지 않게 표시용으로 정리한다."""
    if value is None or value == "":
        return "확인 필요"
    text = " ".join(str(value).split())
    escaped = html.escape(text, quote=False)
    for token in ("\\", "`", "*", "_", "[", "]", "|", "#"):
        escaped = escaped.replace(token, "\\" + token)
    return escaped


def _dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _diagnostic_markdown(state: dict[str, Any]) -> str:
    verification = state.get("verification") or {}
    validation = state["report_validation"]
    lines = ["# LangGraph 실행 진단", "", f"상태: {LABELS[validation['status']]}", "",
             f"- 실행 ID: {_text(state.get('run_id'))}",
             f"- 공고 ID: {_text((state.get('case_identity') or {}).get('case_id'))}",
             f"- RFP 문서 ID: {_text(((state.get('case_identity') or {}).get('rfp') or {}).get('document_id'))}",
             f"- 실행 처리 상태: {_text(state.get('decision'))}",
             f"- T1.5 상태: {_text(verification.get('status'))}",
             f"- T4 생성 상태: {_text(state.get('t4_status', 'not_run'))}",
             "", "## 담당자 확인 사유", ""]
    reasons = state.get("routing_reasons") or []
    lines.extend(f"- {_text(reason)}" for reason in reasons)
    if not reasons:
        lines.append("- 별도 분기 사유 없음. 아래 검증 항목을 확인하세요.")
    lines += ["", "## 보고서 검증", "", f"검사 범위: {_text(validation.get('scope'))}", ""]
    for check in validation.get("checks", []):
        lines.append(f"- {_text(check['check_id'])} / {_text(check['status'])}: {_text(check['detail'])}")
    if state.get("errors"):
        lines += ["", "## 실행 오류", ""]
        lines.extend(f"- {_text(error)}" for error in state["errors"])
    lines += ["", "- 전체 결과: [state.json](state.json)",
              "- 항목별 검사: [report_validation.json](report_validation.json)", ""]
    return "\n".join(lines)


def _report_markdown(state: dict[str, Any]) -> str:
    report = state["report"]
    details = state["report_details"]
    summary = details["project_summary"]
    basic = details["basic_info"]
    company = details["company_analysis"]
    market = details["market_analysis"]
    validation = state["report_validation"]
    lines = [f"# {_text(summary.get('project_name') or basic['project_name']['value'])}", "",
             f"> **{LABELS[validation['status']]}**", "",
             f"- AI 사전 권고: {_text(report['decision_status'])}",
             f"- 최종 결정: {_text(report['final_decision'])}",
             "- 본 문서는 사전 검토 결과이며 입찰 참여 확정 또는 자격 인증이 아닙니다.",
             "", "## 1. 사업 개요", "", "| 항목 | 내용 |", "|---|---|"]
    g2b = details.get("g2b_overview") or {}
    buyer = summary.get("buyer") or (f"{g2b['buyer']} (나라장터)" if g2b.get("buyer") else None)
    rows = [("사업명", summary.get("project_name")), ("발주기관", buyer),
            ("사업금액(원)", summary.get("budget_krw")),
            ("추정가격(원)", summary.get("estimated_price_krw")),
            ("기간", summary.get("period")), ("제출기한", summary.get("submission_deadline"))]
    if g2b.get("bid_closing"):
        rows.append(("입찰 마감 (나라장터)", g2b["bid_closing"]))
    lines.extend(f"| {label} | {_text(value)} |" for label, value in rows)
    if g2b:
        lines.append(f"\n- 나라장터 값 출처: 공고 조회 API({', '.join(g2b['api_fields'].values())}) {_text(g2b.get('source_url'))}"
                     " · 입찰 마감은 제안서 제출기한과 다를 수 있습니다.")
    field = basic["budget_krw"]
    lines += ["", f"- 금액 근거: {_text(field.get('source_document'))} / PDF {_text(field.get('source_page'))}쪽",
              f"- 원문 발췌: {_text(field.get('source_excerpt'))}",
              "- VAT 포함 여부·총액·연차별 금액은 원문 조건을 함께 확인해야 합니다.",
              "", "## 2. 참가자격과 원문 근거", ""]
    eligibility = details["eligibility_requirements"]
    if not eligibility:
        lines.append("- 참가자격 항목 미확인. 조건이 없다는 뜻은 아닙니다.")
    for item in eligibility:
        evidence = item["evidence"]
        lines += [f"### {_text(item['name'])}", "", f"- 조건: {_text(item['details'])}",
                  f"- 추출 검토 상태: {_text(item['review_status'])}",
                  f"- 근거: {_text(evidence.get('document'))} / PDF {_text(evidence.get('pdf_page'))}쪽 / {_text(evidence.get('section'))}",
                  f"- 발췌: {_text(evidence.get('excerpt'))}", ""]
    lines += ["", "## 3. 회사 역량 분석", "", f"- 대상 법인: {_text(company['company_name'])}",
              f"- 기술 적합도 점수: {_text(company['fit_score']['value'])}",
              "- 점수는 법적 참가자격 또는 인력 가용성 확정을 뜻하지 않습니다.", ""]
    matches = company["eligibility_matches"] + company["requirement_matches"] + company["staffing_matches"]
    if not matches:
        lines.append("- 요건별 회사 분석 결과가 비어 있습니다. 증빙 확인이 필요합니다.")
    for match in matches:
        lines += [f"### {_text(match['requirement_id'])}", "",
                  f"- 판단: {_text(match['status'])}", f"- 보완사항: {_text(match['gap_or_check_item'])}"]
        for evidence in match["company_evidence"]:
            lines += [f"- 회사 근거: {_text(evidence['document'])} / {_text(evidence['section'])}",
                      f"- 발췌: {_text(evidence['excerpt'])}"]
        lines.append("")
    for note in company.get("human_review_items", []) + company.get("evidence_limitations", []):
        lines.append(f"- 확인 필요: {_text(note)}")
    evaluation = details.get("agent23_evaluation")
    if evaluation:
        lines += ["", "### T2·T3 근거 평가", "", f"- 상태: {_text(evaluation.get('status'))}"]
        lines.extend(f"- {_text(check.get('check_id'))} / {_text(check.get('status'))}: {_text(check.get('detail'))}"
                     for check in evaluation.get("checks", []) if check.get("status") != "passed")
    lines += ["", "## 4. 유사사업 검색 후보", "",
              "- 검색 후보이며, 이번 입찰 경쟁사·회사 수행실적·자격 충족으로 확정하지 않습니다.", ""]
    if not state["rfp"].get("market_research_allowed", True):
        lines.append("- 시장분석 허용 조건 미충족: 아래 후보는 참고자료이며 참여 판단 근거로 확정하지 않습니다.")
    if not market["similar_projects"]:
        lines.append("- 보고서에 포함할 후보 없음. 검색 실패 여부는 미확인 사항에서 구분합니다.")
    for project in market["similar_projects"]:
        lines += [f"### {_text(project['project_name'])}", "", f"- 수요기관: {_text(project.get('buyer'))}",
                  f"- 공고 출처: {_text(project['source_url'])}",
                  f"- 검색 근거: {_text(project['similarity_rationale'])}"]
        lines.extend(f"- 해석 한계: {_text(note)}" for note in project["comparison_limitations"])
        lines.append("")
    lines += ["", "## 5. 판단 요약과 담당자 확인사항", "", _text(report["executive_summary"]), ""]
    lines.extend(f"- {_text(note)}" for note in report["bid_conditions"] + report["next_actions"])
    lines.extend(f"- 시장 근거 확인: {_text(note)}" for note in market["unknown_items"])
    lines += ["", "## 6. 검증 결과와 상세 자료", "", f"검증 상태: {LABELS[validation['status']]}",
              "", f"검사 범위: {_text(validation['scope'])}", ""]
    lines.extend(f"- {_text(check['check_id'])} / {_text(check['status'])}: {_text(check['detail'])}"
                 for check in validation["checks"] if check["status"] != "passed")
    lines += ["", "- [구조화 보고서](report.json)", "- [보고서 상세](report_details.json)",
              "- [검증 결과](report_validation.json)", "- [T1 분석·원문 근거](debug/01_rfp_analysis.json)",
              "- [T2 회사 분석·증빙](debug/02_capability_matching.json)",
              "- [T3 변환 결과·한계](debug/03_market_research.json)",
              "- [전체 State·T1.5 근거·T3 원본 후보](state.json)", ""]
    if evaluation:
        lines.insert(-1, "- [T2·T3 근거 평가](agent23_evaluation.json)")
    return "\n".join(lines)


def export_review_artifacts(state: dict[str, Any], output_dir: str | Path) -> dict[str, Any]:
    """매번 새 폴더에 저장한다. failed/not_run이면 보고서 파일을 만들지 않는다."""
    snapshot = deepcopy(state)
    if not snapshot.get("report") and not snapshot.get("t4_status"):
        snapshot["report_validation"] = {
            "status": "not_run", "checks": [{"check_id": "generation", "status": "not_run",
                "detail": "T4에 도달하지 않아 보고서를 생성하지 않았습니다."}],
            "scope": "후속 분석·보고서 검증 미실행. T1.5 검토 사유는 State에 보존됩니다.",
        }
    else:
        # 저장 직전 재검사하여 오래된 passed 표지만으로 변조된 결과가 저장되지 않게 한다.
        snapshot.update(validate_t4_report(snapshot))
    status = snapshot["report_validation"]["status"]
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="review_", dir=root))
    paths: dict[str, str] = {}

    def save_json(name: str, payload: Any, key: str) -> None:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _dump(path, payload)
        paths[key] = str(path)

    save_json("state.json", snapshot, "state_json")
    save_json("report_validation.json", snapshot["report_validation"], "validation_json")
    if "case_identity" in snapshot:
        save_json("case_identity.json", snapshot["case_identity"], "case_identity_json")
    if "execution_history" in snapshot:
        save_json("execution_history.json", snapshot["execution_history"], "execution_history_json")
    if snapshot.get("agent23_evaluation") is not None:
        save_json("agent23_evaluation.json", snapshot["agent23_evaluation"], "agent23_evaluation_json")
    diagnostic = folder / "diagnostic.md"
    diagnostic.write_text(_diagnostic_markdown(snapshot), encoding="utf-8")
    paths["diagnostic_md"] = str(diagnostic)
    if status in {"passed", "needs_review"}:
        save_json("report.json", snapshot["report"], "report_json")
        save_json("report_details.json", snapshot["report_details"], "details_json")
        for key, filename in (("rfp", "01_rfp_analysis.json"), ("capability", "02_capability_matching.json"),
                              ("market", "03_market_research.json")):
            save_json("debug/" + filename, snapshot[key], key + "_json")
        markdown = folder / "report.md"
        markdown.write_text(_report_markdown(snapshot), encoding="utf-8")
        paths["report_md"] = str(markdown)
    manifest = {"validation_status": status, "t4_status": snapshot.get("t4_status", "not_run"),
                "directory": str(folder), "files": paths}
    _dump(folder / "manifest.json", manifest)
    return manifest
