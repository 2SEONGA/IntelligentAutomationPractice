"""보완 5: 공고/문서/실행 분리와 Node 실행 기록."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bid_review.rag.state_history import case_identity, validate_api_identity, source_matches, citation_conflicts, record_stage
from bid_review.rag.conditional_routing import plan_verification


class StateHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "rfp.pdf"
        self.path.write_bytes(b"case-one")

    def identity(self, notice="R26BK00000100", run="run-1"):
        return case_identity({"pdf_path": str(self.path), "notice_no": notice,
                              "notice_order": "001", "run_id": run}, notice)

    def test_same_notice_separate_runs_and_document_version(self):
        one, two = self.identity(run="run-1"), self.identity(run="run-2")
        self.assertEqual(one["case_id"], two["case_id"])
        self.assertNotEqual(one["run_id"], two["run_id"])
        self.path.write_bytes(b"case-two")
        changed = self.identity()
        self.assertNotEqual(one["rfp"]["sha256"], changed["rfp"]["sha256"])
        self.assertNotEqual(one["rfp"]["document_id"], changed["rfp"]["document_id"])

    def test_conflicting_document_and_api_notice_block_routing(self):
        identity = case_identity({"pdf_path": str(self.path), "notice_no": "R26BK00000100"}, "R26BK00000200")
        self.assertEqual(identity["status"], "conflict")
        state = {"case_identity": identity, "verification": {"status": "confirmed"},
                 "review_decision": "approved"}
        self.assertEqual(plan_verification(state)["action"], "human_review")
        identity = validate_api_identity(self.identity(), {"notice": {"bidNtceNo": "R26BK00000200", "bidNtceOrd": "002"}})
        self.assertEqual(identity["status"], "conflict")
        self.assertEqual(len(identity["conflicts"]), 2)

    def test_notice_pdf_mismatch_and_api_order_normalization(self):
        identity = case_identity({"pdf_path": str(self.path), "notice_no": "R26BK00000100"}, "R26BK00000100", "R26BK00000200")
        self.assertEqual(identity["status"], "conflict")
        same = validate_api_identity(self.identity(), {"notice": {"bidNtceNo": "R26BK00000100", "bidNtceOrd": "1"}})
        self.assertEqual(same["status"], "identified")

    def test_agency_notice_number_is_not_compared_with_g2b_number(self):
        """공고문의 기관 자체 번호(제2026-013호)는 나라장터 번호와 비교하지 않는다(통합정보시스템 오탐)."""
        identity = case_identity({"pdf_path": str(self.path), "notice_no": "R26BK01747679"}, None, "2026-013")
        self.assertEqual(identity["status"], "identified")
        self.assertEqual(identity["agency_notice_numbers"], ["2026-013"])
        self.assertEqual(identity["notice_no"], "R26BK01747679")
        # 입력 공고번호가 없으면 문서에서 읽은 나라장터 번호를 조회에 쓴다(직접 업로드 건)
        found = case_identity({"pdf_path": str(self.path)}, None, "공고번호 R26BK01762350-000")
        self.assertEqual(found["notice_no"], "R26BK01762350")
        # 나라장터 형식끼리 다르면 여전히 충돌
        clash = case_identity({"pdf_path": str(self.path), "notice_no": "R26BK01747679"}, None, "R26BK01762350")
        self.assertEqual(clash["status"], "conflict")

    def test_rag_evidence_scoped_by_notice_and_document_version(self):
        identity = self.identity()
        meta = {"document_type": "rfp_pdf", "document_id": identity["rfp"]["document_id"],
                "document_sha256": identity["rfp"]["sha256"], "related_notice_no": "R26BK00000100",
                "related_notice_order": "001"}
        self.assertTrue(source_matches(identity, meta, document_type="rfp_pdf"))
        self.assertFalse(source_matches(identity, {**meta, "related_notice_no": "R26BK00000200"}, document_type="rfp_pdf"))
        self.assertFalse(source_matches(identity, {**meta, "document_sha256": "wrong"}, document_type="rfp_pdf"))
        self.assertFalse(source_matches(identity, {**meta, "related_notice_order": "002"}, document_type="rfp_pdf"))
        notice = {"document_type": "notice_pdf", "document_id": "other",
                  "related_notice_no": "R26BK00000100", "related_notice_order": "1"}
        self.assertTrue(source_matches(identity, notice, document_type="notice_pdf"))
        self.assertFalse(source_matches(identity, {**notice, "related_notice_no": "R26BK00000200"}, document_type="notice_pdf"))

    def test_verification_citation_from_other_notice_is_detected(self):
        verification = {"categories": {"amount": {"claims": [{"citations": [
            {"chunk_id": "wrong:1", "notice_no": "R26BK00000200", "notice_order": "002"}]}]}}}
        errors = citation_conflicts(self.identity(), verification)
        self.assertEqual(len(errors), 2)
        self.assertIn("R26BK00000200", errors[0])

    def test_append_only_run_history_evaluation_retry_and_correction(self):
        identity = self.identity()
        original = {"case_identity": identity, "execution_history": []}
        first = record_stage(original, "t15_route", {"routing_plan": {
            "action": "retrieve_more", "reason": "근거 부족", "targets": [{"category": "amount", "field": "budget"}]}})
        self.assertEqual(original["execution_history"], [])
        self.assertEqual(first["execution_history"][0]["run_id"], "run-1")
        second = record_stage({**original, **first}, "targeted_retrieval", {
            "retrieval_round": 1, "supplemental_evidence": [{"chunk_id": "source:1"}]})
        third = record_stage({**original, **second}, "t23_evaluate", {"agent23_evaluation": {
            "status": "needs_review", "checks": [{"check_id": "t2_source", "status": "needs_review"}]}})
        last = record_stage({**original, **third}, "human_review", {"review_history": [{
            "action": "edit", "reviewer": "담당자", "note": "쪽수 확인", "changes": {"budget_krw": {}}}]})
        self.assertEqual([e["sequence"] for e in last["execution_history"]], [1, 2, 3, 4])
        self.assertEqual(last["execution_history"][1]["evidence_ids"], ["source:1"])
        self.assertEqual(last["execution_history"][2]["failure_reasons"], ["t2_source"])
        self.assertEqual(last["execution_history"][3]["correction_fields"], ["budget_krw"])

    def test_graph_state_and_rejection_guard(self):
        if importlib.util.find_spec("langgraph") is None:
            self.skipTest("프로젝트 .venv에서 LangGraph 통합 테스트 실행")
        from bid_review.rag.workflow import BidReviewState, create_bid_review_graph
        from bid_review.rag.hitl_review import validate_review_response
        self.assertIn("execution_history", BidReviewState.__annotations__)
        graph = create_bid_review_graph()
        self.assertIn("t15_route", graph.get_graph().nodes)
        with self.assertRaisesRegex(ValueError, "불일치"):
            validate_review_response({"action": "approve", "reviewer": "담당자", "note": "확인"},
                                     {"case_identity": {"conflicts": ["다른 공고"]}})

    def test_production_graph_history_survives_pause_and_resume(self):
        if importlib.util.find_spec("langgraph") is None:
            self.skipTest("프로젝트 .venv에서 LangGraph 통합 테스트 실행")
        import bid_review.rag.workflow as workflow
        from bid_review.models import CapabilityMatchingResult, RFPAnalysisResult
        from bid_review.rag.hitl_runtime import start_review, resume_review
        verification = workflow.CoreInfoVerification(document_id="fixture", status=workflow.VerificationStatus.HUMAN_REVIEW,
                                                     categories={}, routing_reasons=["담당자 확인"])
        with patch.object(workflow, "RFPExtractor") as extractor, patch.object(workflow, "T15Verifier") as verifier, \
             patch.object(workflow, "CapabilityMatcher") as matcher, \
             patch.object(workflow.BidReviewCrew, "_load_profile"), \
             patch.object(workflow, "project_profile", return_value="fixture"), \
             patch.object(workflow, "search_similar_projects", return_value=[]):
            extractor.return_value.analyze.return_value = RFPAnalysisResult()
            verifier.return_value.verify.return_value = verification
            matcher.return_value.analyze.return_value = CapabilityMatchingResult(fit_score={"value": 0, "formula": "test"})
            db = Path(self.tmp.name) / "checkpoints.sqlite3"
            paused = start_review(self.path, thread_id="history-1", db_path=db,
                                  graph_factory=workflow.create_bid_review_graph)
            self.assertEqual(paused["status"], "paused")
            cli = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/run_hitl_review.py"))
            cli["save_result"](paused, Path(self.tmp.name) / "outputs", db)
            saved = Path(self.tmp.name) / "outputs/history-1"
            self.assertEqual(json.loads((saved / "case_identity.json").read_text())["run_id"], "history-1")
            self.assertTrue((saved / "execution_history.json").is_file())
            before = paused["state"]["execution_history"]
            self.assertEqual(before[0]["node"], "t1_extract")
            self.assertEqual(before[-1]["node"], "t15_route")
            completed = resume_review("history-1", paused["interrupts"][0]["id"],
                {"action": "approve", "reviewer": "테스트 담당자", "note": "진행 승인", "corrections": {}},
                db_path=db, graph_factory=workflow.create_bid_review_graph)
            self.assertEqual(completed["status"], "completed")
            history = completed["state"]["execution_history"]
            self.assertEqual([event["sequence"] for event in history], list(range(1, len(history) + 1)))
            self.assertEqual(sum(event["node"] == "t1_extract" for event in history), 1)
            self.assertEqual(sum(event["node"] == "human_review" for event in history), 1)
            self.assertEqual({event["run_id"] for event in history}, {"history-1"})
            self.assertIn("t23_evaluate", [event["node"] for event in history])


if __name__ == "__main__":
    unittest.main(verbosity=2)
