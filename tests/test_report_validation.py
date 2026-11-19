"""외부 API 없이 구조화 보고서의 불일치·근거 부족과 그래프 연결을 검사한다."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from langgraph.graph import START, END, StateGraph
from bid_review.rag.report_nodes import generate_t4_report
from bid_review.rag.report_validation import validate_t4_report
from bid_review.rag.workflow import BidReviewState
from test_t4_report import sample_state


def generated():
    state = sample_state()
    state["rfp"]["project_summary"]["budget_krw"] = 107_000_000
    state["rfp"]["basic_info"]["budget_krw"].update(
        value=107_000_000, source_page=2, source_excerpt="사업금액 107,000,000원 VAT 별도",
    )
    state.update(generate_t4_report(state))
    return state


def check_status(result, check_id):
    return next(x["status"] for x in result["report_validation"]["checks"] if x["check_id"] == check_id)


class ValidationTests(unittest.TestCase):
    def test_matching_data_retains_review_needed(self):
        state = generated()
        before = deepcopy(state)
        result = validate_t4_report(state)
        self.assertEqual(result["report_validation"]["status"], "needs_review")
        self.assertEqual(check_status(result, "summary_budget"), "passed")
        self.assertEqual(check_status(result, "project_summary"), "passed")
        self.assertEqual(state, before)

    def test_summary_amount_changed(self):
        state = generated()
        state["report"]["project_scale"]["amount_krw"] = 999
        result = validate_t4_report(state)
        self.assertEqual(check_status(result, "summary_budget"), "failed")
        self.assertEqual(result["report_validation"]["status"], "failed")

    def test_details_amount_changed(self):
        state = generated()
        state["report_details"]["project_summary"]["budget_krw"] = 999
        self.assertEqual(check_status(validate_t4_report(state), "project_summary"), "failed")

    def test_eligibility_dropped(self):
        state = generated()
        state["report_details"].pop("eligibility_requirements")
        self.assertEqual(check_status(validate_t4_report(state), "eligibility_requirements"), "failed")

    def test_company_score_changed(self):
        state = generated()
        state["report_details"]["company_analysis"]["fit_score"]["value"] = 100
        self.assertEqual(check_status(validate_t4_report(state), "company_analysis"), "failed")

    def test_budget_source_changed(self):
        state = generated()
        state["report"]["project_scale"]["source_page"] = 999
        self.assertEqual(check_status(validate_t4_report(state), "budget_citation"), "failed")

    def test_unknown_items_dropped(self):
        state = generated()
        state["report_details"]["market_analysis"]["unknown_items"] = []
        self.assertEqual(check_status(validate_t4_report(state), "market_analysis"), "failed")

    def test_market_and_report_both_tampered(self):
        state = generated()
        state["market"]["unknown_items"] = []
        state["report_details"]["market_analysis"]["unknown_items"] = []
        self.assertEqual(check_status(validate_t4_report(state), "t3_candidates"), "failed")

    def test_final_decision_cannot_be_assigned(self):
        state = generated()
        state["report"]["final_decision"] = "Go"
        self.assertEqual(check_status(validate_t4_report(state), "schema"), "failed")

    def test_unknown_source_reference(self):
        state = generated()
        state["report"]["key_findings"][0]["evidence_references"] = ["missing-source.json"]
        self.assertEqual(check_status(validate_t4_report(state), "source_references"), "failed")

    def test_generation_blocked_validation_skipped(self):
        state = sample_state()
        state["decision"] = "human_review_required"
        state.update(generate_t4_report(state))
        result = validate_t4_report(state)
        self.assertEqual(result["report_validation"]["status"], "not_run")
        self.assertNotIn("t4_status", result)

    def test_missing_details(self):
        state = generated()
        state.pop("report_details")
        self.assertEqual(validate_t4_report(state)["report_validation"]["status"], "failed")

    def test_graph_preserves_details_and_validation(self):
        graph = StateGraph(BidReviewState)
        graph.add_node("t4_report", generate_t4_report)
        graph.add_node("t4_validate", validate_t4_report)
        graph.add_edge(START, "t4_report")
        graph.add_edge("t4_report", "t4_validate")
        graph.add_edge("t4_validate", END)
        result = graph.compile().invoke(sample_state())
        self.assertEqual(result["report_validation"]["status"], "needs_review")
        self.assertIn("report_details", result)
        self.assertEqual(result["t4_status"], "generated_review_required")


if __name__ == "__main__":
    unittest.main(verbosity=2)
