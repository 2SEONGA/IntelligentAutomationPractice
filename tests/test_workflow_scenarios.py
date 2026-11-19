"""보완 6: 운영 그래프의 Node 순서·State·분기·최종 상태를 사례별로 확인."""
from __future__ import annotations

from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@unittest.skipUnless(importlib.util.find_spec("langgraph") is not None, "프로젝트 .venv에서 통합 테스트 실행")
class WorkflowScenarios(unittest.TestCase):
    def setUp(self):
        from bid_review.models import CapabilityMatchingResult, RFPAnalysisResult
        import bid_review.rag.workflow as workflow
        self.w = workflow
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pdf = Path(self.temp.name) / "fixture.pdf"
        self.pdf.write_bytes(b"fixed-workflow-input")
        self.rfp = RFPAnalysisResult()
        self.capability = CapabilityMatchingResult(fit_score={"value": 0, "formula": "연결 테스트"})

    def verified(self, status="confirmed", *, check_id=None, field="budget_krw", queries=None):
        w = self.w
        from bid_review.rag.verification import CategoryVerification, VerifiedClaim
        if check_id is None:
            return w.CoreInfoVerification(document_id="fixture", status=w.VerificationStatus(status),
                                          categories={}, routing_reasons=[] if status == "confirmed" else ["담당자 확인"])
        claim = VerifiedClaim(category="amount", field=field, status=w.VerificationStatus(status),
                                retrieval_queries=queries or [], checks=[w.VerificationCheck(
                                    check_id=check_id, label="테스트 검사", status=w.VerificationStatus(status),
                                    detail="고정 입력 검증")])
        category = CategoryVerification(category="amount", status=w.VerificationStatus(status),
                                          coverage_complete=True, claims=[claim])
        return w.CoreInfoVerification(document_id="fixture", status=w.VerificationStatus(status),
                                      categories={"amount": category}, routing_reasons=["amount: " + status])

    LOG_DIR = ROOT / "outputs" / "workflow_scenarios"

    def execute(self, statuses, **kwargs):
        """실행 뒤 Node 순서·State 변화·분기·최종 상태를 사례별 JSON으로 남긴다(보완 6 로그 요구)."""
        state, paused, calls, count = self._execute(statuses, **kwargs)
        log = {"scenario": self._testMethodName,
               "execution_history": state.get("execution_history", []),
               "paused_at": paused["execution_history"][-1]["node"] if paused else None,
               "routing_plan": state.get("routing_plan"), "retrieval_round": state.get("retrieval_round"),
               "search_calls": calls, "verify_calls": count, "decision": state.get("decision"),
               "verification_status": (state.get("verification") or {}).get("status"),
               "report_validation_status": (state.get("report_validation") or {}).get("status"),
               "review_history": state.get("review_history", [])}
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        (self.LOG_DIR / f"{self._testMethodName}.json").write_text(
            json.dumps(log, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return state, paused, calls, count

    def _execute(self, statuses, *, retrieval_limit=2, checkpointer=None):
        """실제 StateGraph를 사용하고 PDF 추출·외부 API·검색 결과만 고정한다."""
        w = self.w
        with ExitStack() as stack:
            extractor = stack.enter_context(patch.object(w, "RFPExtractor"))
            verifier = stack.enter_context(patch.object(w, "T15Verifier"))
            matcher = stack.enter_context(patch.object(w, "CapabilityMatcher"))
            stack.enter_context(patch.object(w.BidReviewCrew, "_load_profile"))
            search = stack.enter_context(patch.object(w, "search_keyword", return_value=[]))
            stack.enter_context(patch.object(w, "search_semantic", return_value=[]))
            stack.enter_context(patch.object(w, "project_profile", return_value="fixture"))
            stack.enter_context(patch.object(w, "search_similar_projects", return_value=[]))
            extractor.return_value.analyze.return_value = self.rfp
            verifier.return_value.verify.side_effect = statuses
            matcher.return_value.analyze.return_value = self.capability
            graph = w.create_bid_review_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "scenario"}, "recursion_limit": 50} if checkpointer else None
            initial = {"pdf_path": str(self.pdf), "company_path": None,
                       "retrieval_limit": retrieval_limit, "run_id": "scenario"}
            if checkpointer:
                graph.invoke(initial, config)
                snapshot = graph.get_state(config)
                if snapshot.tasks and snapshot.tasks[0].interrupts:
                    from langgraph.types import Command
                    paused_state = dict(snapshot.values)
                    result = graph.invoke(Command(resume={"action": "approve", "reviewer": "담당자",
                                                          "note": "테스트 진행 승인", "corrections": {}}), config)
                    return result, paused_state, search.call_count, verifier.return_value.verify.call_count
                return dict(snapshot.values), None, search.call_count, verifier.return_value.verify.call_count
            return graph.invoke(initial), None, search.call_count, verifier.return_value.verify.call_count

    @staticmethod
    def nodes(state):
        events = state["execution_history"]
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        return [event["node"] for event in events]

    def test_normal_path_reaches_report(self):
        state, _, calls, _ = self.execute([self.verified()])
        nodes = self.nodes(state)
        self.assertEqual(nodes, ["t1_extract", "g2b_notice_lookup", "t15_verify", "t15_route",
                                 "t2_company_rag_retrieval", "t2_company_match", "t3_similar_projects",
                                 "t23_evaluate", "t4_report", "t4_validate"])
        self.assertEqual(calls, 0)
        self.assertEqual(state["routing_plan"]["action"], "company_match")
        self.assertIn("report_validation", state)
        self.assertIn(state["report_validation"]["status"], {"passed", "needs_review"})
        self.assertEqual(state["case_identity"]["run_id"], "scenario")

    def test_missing_rag_evidence_retries_then_human_review(self):
        missing = self.verified("retrieve_more", check_id="candidate_present", queries=["사업 금액"])
        state, _, calls, count = self.execute([missing, missing], retrieval_limit=1)
        nodes = self.nodes(state)
        self.assertEqual(nodes.count("targeted_retrieval"), 1)
        self.assertLess(nodes.index("targeted_retrieval"), nodes.index("t15_reverify"))
        self.assertEqual(nodes[-1], "human_review")
        self.assertGreater(calls, 0)
        self.assertEqual(count, 2)
        self.assertEqual(state["retrieval_round"], 1)
        self.assertEqual(state["decision"], "human_review_required")
        self.assertNotIn("capability", state)

    def test_conflicting_source_goes_directly_to_person(self):
        conflict = self.verified("conflict", check_id="notice_crosscheck")
        state, _, calls, _ = self.execute([conflict])
        self.assertEqual(self.nodes(state)[-1], "human_review")
        self.assertEqual(state["routing_plan"]["action"], "human_review")
        self.assertEqual(state["retrieval_round"], 0)
        self.assertEqual(calls, 0)
        self.assertNotIn("capability", state)

    def test_retrieval_limit_zero_skips_search(self):
        missing = self.verified("retrieve_more", check_id="candidate_present", queries=["사업 금액"])
        state, _, calls, _ = self.execute([missing], retrieval_limit=0)
        self.assertNotIn("targeted_retrieval", self.nodes(state))
        self.assertEqual(calls, 0)
        self.assertEqual(state["routing_plan"]["action"], "human_review")

    def test_human_approval_resumes_saved_graph_once(self):
        from langgraph.checkpoint.memory import InMemorySaver
        state, paused, calls, count = self.execute([self.verified("human_review")], checkpointer=InMemorySaver())
        self.assertEqual(self.nodes(paused)[-1], "t15_route")
        nodes = self.nodes(state)
        self.assertEqual(nodes.count("t1_extract"), 1)
        self.assertEqual(nodes.count("human_review"), 1)
        self.assertLess(nodes.index("human_review"), nodes.index("t2_company_match"))
        self.assertIn("t4_validate", nodes)
        self.assertIn(state["report_validation"]["status"], {"passed", "needs_review"})
        self.assertEqual(state["review_history"][0]["action"], "approve")
        self.assertEqual(state["human_override"]["reviewer"], "담당자")
        self.assertEqual(count, 1)
        self.assertEqual(calls, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
