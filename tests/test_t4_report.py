"""T4 단위·부분 그래프 테스트. 외부 API를 호출하지 않는다."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langgraph.graph import END, START, StateGraph
from bid_review.models import RFPAnalysisResult, CapabilityMatchingResult, BidReviewReport
from bid_review.rag.report_nodes import generate_t4_report
from bid_review.rag.workflow import BidReviewState, create_bid_review_graph


def sample_state():
    # 실제 입력 판정 정확도를 평가하는 자료가 아닌, 연결 검증용 고정 입력이다.
    return {
        "rfp": RFPAnalysisResult().model_dump(mode="json"),
        "capability": CapabilityMatchingResult(
            fit_score={"value": 0, "formula": "연결 테스트용"},
        ).model_dump(mode="json"),
        "verification": {"status": "confirmed"},
        "decision": "company_matching_complete",
        "similar_projects": [],
        "similar_projects_status": "none_above_threshold",
        "errors": [],
    }


class T4Tests(unittest.TestCase):
    def test_report_model_and_input_unchanged(self):
        state = sample_state()
        before = deepcopy(state)
        result = generate_t4_report(state)
        self.assertEqual(result["t4_status"], "generated_pending_validation")
        report = BidReviewReport.model_validate(result["report"])
        self.assertEqual(report.final_decision, "미결정")
        self.assertTrue(result["market"]["unknown_items"])
        self.assertEqual(state, before)

    def test_unverified_input_blocked(self):
        state = sample_state()
        state["verification"]["status"] = "human_review"
        self.assertEqual(generate_t4_report(state)["t4_status"], "blocked")

    def test_t2_not_completed_blocked(self):
        state = sample_state()
        state["decision"] = "human_review_required"
        self.assertIsNone(generate_t4_report(state)["report"])

    def test_missing_capability_failed(self):
        state = sample_state()
        del state["capability"]
        result = generate_t4_report(state)
        self.assertEqual(result["t4_status"], "failed")
        self.assertTrue(result["errors"])

    def test_missing_rfp_failed(self):
        state = sample_state()
        del state["rfp"]
        self.assertEqual(generate_t4_report(state)["t4_status"], "failed")

    def test_candidates_remain_unconfirmed(self):
        state = sample_state()
        state["similar_projects_status"] = "found 1"
        state["similar_projects"] = [{
            "title": "샘플 사업", "buyer": "샘플 기관",
            "notice_url": "https://example.org/notice/1",
            "similarity": 0.82, "profile_excerpt": "정보시스템 구축",
        }]
        result = generate_t4_report(state)
        project = result["market"]["similar_projects"][0]
        self.assertIsNone(project["awardee"])
        market_finding = next(f for f in result["report"]["key_findings"]
                              if f["topic"] == "시장·경쟁 환경")
        self.assertIn("검색 후보", market_finding["assessment"])

    def test_search_failure_kept(self):
        state = sample_state()
        state["similar_projects_status"] = "unavailable: 테스트용 검색 실패"
        result = generate_t4_report(state)
        self.assertEqual(result["t4_status"], "generated_pending_validation")
        self.assertIn("실패", result["market"]["unknown_items"][0])

    def test_human_approval_caveat(self):
        state = sample_state()
        state.update(verification={"status": "human_review"},
                     review_decision="approved", review_note="연결 테스트용 승인")
        result = generate_t4_report(state)
        self.assertTrue(any("자동 확정" in x for x in result["report"]["bid_conditions"]))

    def test_state_survives_graph_execution(self):
        graph = StateGraph(BidReviewState)
        graph.add_node("t4_report", generate_t4_report)
        graph.add_edge(START, "t4_report")
        graph.add_edge("t4_report", END)
        result = graph.compile().invoke(sample_state())
        self.assertEqual(result["t4_status"], "generated_pending_validation")
        self.assertIn("report", result)
        self.assertIn("market", result)

    def test_main_graph_connections(self):
        edges = {(e.source, e.target) for e in create_bid_review_graph().get_graph().edges}
        self.assertIn(("t3_similar_projects", "t23_evaluate"), edges)
        self.assertIn(("t23_evaluate", "t4_report"), edges)
        self.assertIn(("t4_report", "t4_validate"), edges)
        self.assertIn(("t4_validate", END), edges)
        self.assertNotIn(("t3_similar_projects", END), edges)
        self.assertIn(("human_review", END), edges)


if __name__ == "__main__":
    unittest.main(verbosity=2)
