"""실제 SQLite·interrupt·재개 테스트. PDF 추출·API는 고정 Node로 대체한다."""
from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Any, TypedDict
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from langgraph.graph import START, END, StateGraph
from bid_review.models import RFPAnalysisResult, CapabilityMatchingResult
from bid_review.rag.hitl_review import pause_for_human_review, route_after_human_review
from bid_review.rag.hitl_runtime import start_review, review_status, resume_review
from bid_review.rag.report_nodes import generate_t4_report
from bid_review.rag.report_validation import validate_t4_report


class StubState(TypedDict, total=False):
    pdf_path: str
    notice_pdf_path: str | None
    company_path: str | None
    notice_no: str | None
    notice_order: str
    retrieval_limit: int
    run_id: str
    source_fingerprints: dict
    review_history: list
    rfp: dict
    verification: dict
    review_decision: str | None
    reviewer: str
    review_note: str
    decision: str
    routing_reasons: list
    human_override: dict | None
    capability: dict | None
    company_evidence_candidates: dict
    similar_projects: list
    similar_projects_status: str
    market: dict | None
    report: dict | None
    report_details: dict | None
    report_validation: dict
    t4_status: str
    errors: list


def _event(state, node):
    with Path(state["pdf_path"]).with_suffix(".events").open("a", encoding="utf-8") as handle:
        handle.write(node + "\n")


def build_test_graph(checkpointer):
    def t1(state):
        _event(state, "t1")
        return {"rfp": RFPAnalysisResult().model_dump(mode="json"), "errors": []}

    def verify(state):
        _event(state, "t15")
        status = "confirmed" if state["rfp"]["basic_info"]["budget_krw"]["value"] == 123 else "human_review"
        return {"verification": {"status": status, "categories": {}}, "routing_reasons": ["테스트용 검토"]}

    def t2(state):
        _event(state, "t2")
        return {"capability": CapabilityMatchingResult(fit_score={"value": 0, "formula": "테스트용"}).model_dump(mode="json"),
                "decision": "company_matching_complete"}

    def t3(state):
        _event(state, "t3")
        return {"similar_projects": [], "similar_projects_status": "none_above_threshold"}

    graph = StateGraph(StubState)
    for name, node in (("t1", t1), ("t15", verify), ("human_review", pause_for_human_review),
                       ("t2", t2), ("t3", t3), ("t4", generate_t4_report), ("validate", validate_t4_report)):
        graph.add_node(name, node)
    graph.add_edge(START, "t1")
    graph.add_edge("t1", "t15")
    graph.add_conditional_edges("t15", lambda state: "t2" if state["verification"]["status"] == "confirmed" else "human_review")
    graph.add_conditional_edges("human_review", route_after_human_review,
                                {"company_match": "t2", "reverify": "t15", "end": END})
    graph.add_edge("t2", "t3")
    graph.add_edge("t3", "t4")
    graph.add_edge("t4", "validate")
    graph.add_edge("validate", END)
    return graph.compile(checkpointer=checkpointer)


class HITLTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.pdf = self.folder / "fixture.pdf"
        self.pdf.write_bytes(b"fixed-input-for-stub-nodes")
        self.db = self.folder / "checkpoints.sqlite3"

    def start(self, thread="case1"):
        return start_review(self.pdf, thread_id=thread, db_path=self.db, graph_factory=build_test_graph)

    def resume(self, result, action="approve", **kwargs):
        response = {"action": action, "reviewer": "테스트 담당자", "note": "고정 입력 연결 검토", "corrections": {}}
        response.update(kwargs)
        return resume_review(result["thread_id"], result["interrupts"][0]["id"], response,
                             db_path=self.db, graph_factory=build_test_graph)

    def edit(self, result, value=123):
        return self.resume(result, "edit", corrections={"budget_krw": {
            "value": value, "source_page": 1, "source_excerpt": "테스트 사업금액", "source_document": "rfp_pdf"}})

    def events(self):
        return self.pdf.with_suffix(".events").read_text().splitlines()

    def test_pause_saved_and_reopen_status(self):
        initial = self.start()
        reopened = review_status("case1", db_path=self.db, graph_factory=build_test_graph)
        self.assertEqual(initial["status"], "paused")
        self.assertEqual(initial["interrupts"], reopened["interrupts"])
        self.assertEqual(reopened["state"]["run_id"], "case1")

    def test_approve_resumes_without_repeating_t1(self):
        result = self.resume(self.start())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.events(), ["t1", "t15", "t2", "t3"])
        self.assertIn("report", result["state"])
        self.assertEqual(result["state"]["review_history"][0]["action"], "approve")

    def test_reject_does_not_run_t2_or_t4(self):
        result = self.resume(self.start(), "reject")
        self.assertEqual(result["state"]["decision"], "human_rejected")
        self.assertEqual(self.events(), ["t1", "t15"])
        self.assertFalse(result["state"].get("report"))

    def test_edit_reverifies_and_keeps_audit(self):
        result = self.edit(self.start())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.events(), ["t1", "t15", "t15", "t2", "t3"])
        state = result["state"]
        self.assertEqual(state["rfp"]["project_summary"]["budget_krw"], 123)
        self.assertIsNone(state["review_decision"])
        self.assertEqual(state["review_history"][0]["changes"]["budget_krw"]["after"]["value"], 123)

    def test_edit_unresolved_pauses_again(self):
        first = self.start()
        second = self.edit(first, 111)
        self.assertEqual(second["status"], "paused")
        self.assertNotEqual(first["interrupts"][0]["id"], second["interrupts"][0]["id"])
        self.assertEqual(self.events(), ["t1", "t15", "t15"])

    def test_old_interrupt_response_rejected(self):
        first = self.start()
        self.edit(first, 111)
        with self.assertRaises(ValueError):
            self.resume(first)

    def test_completed_run_cannot_resume_again(self):
        first = self.start()
        self.resume(first)
        with self.assertRaises(ValueError):
            self.resume(first)

    def test_bad_response_keeps_pause(self):
        first = self.start()
        with self.assertRaises(ValueError):
            self.resume(first, note=" ")
        self.assertEqual(review_status("case1", db_path=self.db, graph_factory=build_test_graph)["status"], "paused")
        self.assertEqual(self.resume(first)["status"], "completed")

    def test_no_corrections_hidden_in_approval(self):
        first = self.start()
        with self.assertRaises(ValueError):
            self.resume(first, corrections={"budget_krw": {"value": 123}})

    def test_identity_cannot_be_edited(self):
        first = self.start()
        with self.assertRaises(ValueError):
            self.resume(first, "edit", corrections={"notice_number": {"value": "other"}})

    def test_source_change_blocks_resume(self):
        first = self.start()
        self.pdf.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.resume(first)

    def test_thread_reuse_rejected(self):
        self.start()
        with self.assertRaises(ValueError):
            self.start()

    def test_separate_threads_do_not_mix(self):
        one = self.start("one")
        self.start("two")
        self.resume(one, "reject")
        two = review_status("two", db_path=self.db, graph_factory=build_test_graph)
        self.assertEqual(two["status"], "paused")
        self.assertEqual(two["state"]["review_history"], [])

    def test_missing_source_page_rejected(self):
        first = self.start()
        with self.assertRaises(ValueError):
            self.resume(first, "edit", corrections={"budget_krw": {
                "value": 123, "source_page": None, "source_excerpt": "근거", "source_document": "rfp_pdf"}})

    def test_other_document_not_in_run_rejected(self):
        first = self.start()
        with self.assertRaises(ValueError):
            self.resume(first, "edit", corrections={"budget_krw": {
                "value": 123, "source_page": 1, "source_excerpt": "근거", "source_document": "notice_pdf"}})

    def test_audit_not_duplicated_on_resume(self):
        first = self.start()
        second = self.edit(first, 111)
        result = self.resume(second, "reject")
        self.assertEqual([x["action"] for x in result["state"]["review_history"]], ["edit", "reject"])

    def test_real_process_restart(self):
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker-start", str(self.pdf), str(self.db)],
                               check=True, capture_output=True, text=True)
        first = json.loads(child.stdout)
        result = self.resume(first)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.events().count("t1"), 1)

    def test_cli_response_template_preserves_edits_and_db_path(self):
        result = self.start()
        module = runpy.run_path(str(ROOT / "scripts/run_hitl_review.py"))
        output = io.StringIO()
        with redirect_stdout(output):
            module["save_result"](result, self.folder / "ui", self.db)
        self.assertIn(str(self.db), output.getvalue())
        response_path = next((self.folder / "ui/case1").glob("response_*.json"))
        reply = json.loads(response_path.read_text(encoding="utf-8"))
        reply["note"] = "아직 검토 중인 사용자 입력"
        response_path.write_text(json.dumps(reply, ensure_ascii=False), encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            module["save_result"](result, self.folder / "ui", self.db)
        self.assertEqual(json.loads(response_path.read_text(encoding="utf-8"))["note"], reply["note"])

    def test_production_graph_wiring(self):
        from bid_review.rag.workflow import create_bid_review_graph
        from langgraph.checkpoint.memory import InMemorySaver
        graph = create_bid_review_graph(checkpointer=InMemorySaver())
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        self.assertIn(("human_review", "t15_verify"), edges)
        self.assertIn(("human_review", "t2_company_rag_retrieval"), edges)
        self.assertIn(("human_review", END), edges)
        self.assertIn(("t4_report", "t4_validate"), edges)

    def test_production_graph_approve_reaches_real_t4(self):
        # 실제 Workflow·T4·검증을 실행하되 추출·검증 응답·회사 매칭·검색은 고정 대역을 쓴다.
        import bid_review.rag.workflow as w
        rfp = RFPAnalysisResult()
        verification = w.CoreInfoVerification(document_id="fixture", status=w.VerificationStatus.HUMAN_REVIEW,
                                               categories={}, routing_reasons=["테스트용 검토"])
        capability = CapabilityMatchingResult(fit_score={"value": 0, "formula": "테스트용"})
        with patch.object(w, "RFPExtractor") as extractor, patch.object(w, "T15Verifier") as verifier, \
             patch.object(w, "CapabilityMatcher") as matcher, patch.object(w.BidReviewCrew, "_load_profile"), \
             patch.object(w, "project_profile", return_value="fixture"), \
             patch.object(w, "search_similar_projects", return_value=[]):
            extractor.return_value.analyze.return_value = rfp
            verifier.return_value.verify.return_value = verification
            matcher.return_value.analyze.return_value = capability
            first = start_review(self.pdf, thread_id="real-graph", db_path=self.db,
                                 graph_factory=w.create_bid_review_graph)
            self.assertEqual(first["status"], "paused")
            result = resume_review("real-graph", first["interrupts"][0]["id"],
                {"action": "approve", "reviewer": "테스트 담당자", "note": "연결 검증용 승인", "corrections": {}},
                db_path=self.db, graph_factory=w.create_bid_review_graph)
            self.assertEqual(result["status"], "completed")
            self.assertIn("report", result["state"])
            self.assertEqual(result["state"]["report_validation"]["status"], "needs_review")
            self.assertEqual(extractor.return_value.analyze.call_count, 1)
            self.assertEqual(verifier.return_value.verify.call_count, 1)
            self.assertEqual(matcher.return_value.analyze.call_count, 1)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--worker-start":
        print(json.dumps(start_review(sys.argv[2], thread_id="case1", db_path=sys.argv[3],
                                     graph_factory=build_test_graph), ensure_ascii=False))
    else:
        unittest.main(verbosity=2)
