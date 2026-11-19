"""外部 API 없이 보고서·근거 파일 저장과 CLI 연결을 점검한다."""
from __future__ import annotations

from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bid_review.rag.report_export import export_review_artifacts
from bid_review.rag.report_nodes import generate_t4_report
from test_t4_report import sample_state


def generated():
    state = sample_state()
    state["rfp"]["project_summary"]["project_name"] = "파일 저장 연결 테스트"
    state["rfp"]["project_summary"]["budget_krw"] = 107_000_000
    state["rfp"]["basic_info"]["budget_krw"].update(
        value=107_000_000, source_page=2, source_excerpt="사업금액 107,000,000원 VAT 별도",
    )
    state.update(generate_t4_report(state))
    return state


class ExportTests(unittest.TestCase):
    def test_review_needed_report_saved_with_label(self):
        with TemporaryDirectory() as tmp:
            result = export_review_artifacts(generated(), tmp)
            self.assertEqual(result["validation_status"], "needs_review")
            text = Path(result["files"]["report_md"]).read_text(encoding="utf-8")
            self.assertIn("담당자 검토 필요", text)
            self.assertIn("107000000", text)
            self.assertIn("VAT 별도", text)
            self.assertIn("최종 결정: 미결정", text)

    def test_debug_references_exist(self):
        with TemporaryDirectory() as tmp:
            result = export_review_artifacts(generated(), tmp)
            folder = Path(result["directory"])
            for name in ("01_rfp_analysis.json", "02_capability_matching.json", "03_market_research.json"):
                self.assertTrue((folder / "debug" / name).is_file())
            for path in result["files"].values():
                self.assertTrue(Path(path).is_file())

    def test_invalid_report_diagnostic_only(self):
        state = generated()
        state["report"]["project_scale"]["amount_krw"] = 999
        with TemporaryDirectory() as tmp:
            result = export_review_artifacts(state, tmp)
            self.assertEqual(result["validation_status"], "failed")
            self.assertNotIn("report_md", result["files"])
            self.assertFalse((Path(result["directory"]) / "report.json").exists())
            self.assertTrue(Path(result["files"]["diagnostic_md"]).is_file())

    def test_stale_pass_cannot_bypass_revalidation(self):
        state = generated()
        state["report_validation"] = {"status": "passed"}
        state["report"]["final_decision"] = "Go"
        with TemporaryDirectory() as tmp:
            self.assertEqual(export_review_artifacts(state, tmp)["validation_status"], "failed")

    def test_t15_review_without_t4(self):
        state = {"decision": "human_review_required", "verification": {"status": "human_review"},
                 "routing_reasons": ["VAT・일정 원문 확인 필요"]}
        with TemporaryDirectory() as tmp:
            result = export_review_artifacts(state, tmp)
            self.assertEqual(result["validation_status"], "not_run")
            self.assertNotIn("report_md", result["files"])
            text = Path(result["files"]["diagnostic_md"]).read_text(encoding="utf-8")
            self.assertIn("VAT・일정 원문 확인 필요", text)

    def test_generation_blocked(self):
        state = sample_state()
        state["decision"] = "human_review_required"
        state.update(generate_t4_report(state))
        with TemporaryDirectory() as tmp:
            self.assertEqual(export_review_artifacts(state, tmp)["validation_status"], "not_run")

    def test_separate_directories_keep_old_result(self):
        with TemporaryDirectory() as tmp:
            first = export_review_artifacts(generated(), tmp)
            old = Path(first["files"]["report_md"]).read_bytes()
            second = export_review_artifacts(generated(), tmp)
            self.assertNotEqual(first["directory"], second["directory"])
            self.assertEqual(Path(first["files"]["report_md"]).read_bytes(), old)

    def test_input_unchanged_and_json_readable(self):
        state = generated()
        before = deepcopy(state)
        with TemporaryDirectory() as tmp:
            result = export_review_artifacts(state, tmp)
            self.assertEqual(state, before)
            for path in result["files"].values():
                if path.endswith(".json"):
                    json.loads(Path(path).read_text(encoding="utf-8"))

    def test_missing_details_not_exported_as_report(self):
        state = generated()
        state.pop("report_details")
        with TemporaryDirectory() as tmp:
            result = export_review_artifacts(state, tmp)
            self.assertEqual(result["validation_status"], "failed")
            self.assertNotIn("report_md", result["files"])

    def test_search_failure_warning_visible(self):
        state = sample_state()
        state["similar_projects_status"] = "unavailable: 테스트용 색인 없음"
        state.update(generate_t4_report(state))
        with TemporaryDirectory() as tmp:
            result = export_review_artifacts(state, tmp)
            text = Path(result["files"]["report_md"]).read_text(encoding="utf-8")
            self.assertIn("색인 없음", text)

    def test_cli_saves_summary_and_reports_without_real_api(self):
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "new" / "state_after.json"
            argv = ["run_langgraph_review.py", "--rfp", "test-only.pdf", "--output", str(output)]
            with patch("bid_review.rag.workflow.run_bid_review", return_value=generated()) as run:
                with patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
                    runpy.run_path(str(ROOT / "scripts/run_langgraph_review.py"), run_name="__main__")
                run.assert_called_once()
            state = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(state["report_validation"]["status"], "needs_review")
            self.assertEqual(len(list(output.parent.glob("state_after_reports/review_*/report.md"))), 1)

    def test_markdown_escapes_source_text(self):
        state = sample_state()
        state["rfp"]["project_summary"]["project_name"] = "<script>테스트</script>|항목"
        state.update(generate_t4_report(state))
        with TemporaryDirectory() as tmp:
            result = export_review_artifacts(state, tmp)
            text = Path(result["files"]["report_md"]).read_text(encoding="utf-8")
            self.assertNotIn("<script>", text)
            self.assertIn("&lt;script&gt;", text)

    def test_g2b_buyer_and_closing_fill_missing_rfp_values(self):
        """RFP에 발주기관·제출기한이 없을 때 나라장터 조회값을 출처와 함께 표에 보인다(상수도관 실제 실행에서 발견)."""
        from bid_review.rag.report_validation import validate_t4_report
        state = sample_state()
        state["rfp"]["project_summary"].update(buyer=None, submission_deadline=None)
        state["g2b_notice_lookup"] = {"status": "found", "notice": {
            "dminsttNm": "주식회사 셈즈", "bidClseDt": "2026-10-19 10:00:00",
            "bidNtceDtlUrl": "https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo=R26BK01762350&bidPbancOrd=000"}}
        state.update(generate_t4_report(state))
        state.update(validate_t4_report(state))
        checks = {c["check_id"]: c["status"] for c in state["report_validation"]["checks"]}
        self.assertEqual(checks["g2b_overview"], "passed")
        self.assertEqual(checks["project_summary"], "passed")  # RFP 요약은 그대로
        with TemporaryDirectory() as tmp:
            text = Path(export_review_artifacts(state, tmp)["files"]["report_md"]).read_text(encoding="utf-8")
        self.assertIn("| 발주기관 | 주식회사 셈즈 (나라장터) |", text)
        self.assertIn("| 입찰 마감 (나라장터) | 2026-10-19 10:00:00 |", text)
        self.assertIn("제안서 제출기한과 다를 수 있습니다", text)
        state["report_details"]["g2b_overview"]["buyer"] = "다른 기관"  # 보고서만 바뀐 경우
        checks = {c["check_id"]: c["status"] for c in validate_t4_report(state)["report_validation"]["checks"]}
        self.assertEqual(checks["g2b_overview"], "failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
