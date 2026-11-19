"""보완 7: 두 실행 모드, 분기 합류, 실행 기록과 비교 결과 검사."""
from __future__ import annotations

from contextlib import ExitStack
import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from bid_review.rag.state_history import merge_execution_history, record_stage
from bid_review.rag.usage_tracking import collect_embedding_usage, record_embedding_usage


class HistoryMergeTests(unittest.TestCase):
    def test_parallel_history_keeps_both_branches_once(self):
        base = {"execution_history": []}
        first = record_stage(base, "t15_route", {})["execution_history"]
        a = record_stage({"execution_history": first}, "t2_company_rag_retrieval", {})["execution_history"]
        b = record_stage({"execution_history": first}, "t3_similar_projects", {})["execution_history"]
        merged = merge_execution_history(a, b)
        self.assertEqual(len(merged), 3)
        self.assertEqual([x["sequence"] for x in merged], [1, 2, 3])
        self.assertEqual({x["node"] for x in merged},
                         {"t15_route", "t2_company_rag_retrieval", "t3_similar_projects"})
        self.assertEqual(merge_execution_history(merged, b), merged)

    def test_embedding_usage_counts_actual_provider_response(self):
        class Usage:
            prompt_tokens = 17
            total_tokens = 17
        with collect_embedding_usage() as counts:
            record_embedding_usage(Usage())
            record_embedding_usage(None)
        self.assertEqual(counts, {"input_tokens": 17, "total_tokens": 17,
                                  "requests_with_usage": 1, "requests_missing_usage": 1})

    def test_comparison_requires_five_cases_and_reports_unmeasured_tokens(self):
        script = ROOT / "scripts" / "compare_workflow_modes.py"
        spec = importlib.util.spec_from_file_location("workflow_comparison_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with TemporaryDirectory() as temp:
            pdf = Path(temp) / "rfp.pdf"
            pdf.write_bytes(b"test")
            cases = [{"id": f"notice-{i}", "rfp": str(pdf)} for i in range(5)]
            with self.assertRaisesRegex(ValueError, "최소 5건"):
                module.compare(cases[:4], Path(temp) / "invalid")

            def fake_run(*args, **kwargs):
                return {"decision": "company_matching_complete",
                        "execution_history": [{"node": "t23_evaluate", "duration_seconds": 0.1},
                                              {"node": "t4_validate", "duration_seconds": 0.1}],
                        "report_validation": {"status": "passed"}, "report": {"summary": "fixed"}}

            with patch.dict(sys.modules, {"bid_review.rag.workflow": SimpleNamespace(run_bid_review=fake_run)}):
                summary = module.compare(cases, Path(temp) / "comparison", repeats=2)
            self.assertEqual(summary["valid_case_count"], 5)
            self.assertEqual(summary["valid_pair_count"], 10)
            self.assertTrue(summary["decision_ready"])
            self.assertIsNone(summary["averages_for_valid_pairs"]["parallel"]["embedding_total_tokens"])
            self.assertTrue((Path(temp) / "comparison" / "summary.json").is_file())
            # 고정 승인: 두 모드 모두 같은 승인 인자를 받고, 결과에 벤치마크 승인임을 남긴다
            calls = []

            def recording_run(*args, **kwargs):
                calls.append(kwargs)
                return fake_run()
            with patch.dict(sys.modules, {"bid_review.rag.workflow": SimpleNamespace(run_bid_review=recording_run)}):
                approved = module.compare(cases, Path(temp) / "approved", repeats=1, approve_note="벤치마크용 승인")
            self.assertTrue(all(c["review_decision"] == "approved" and c["review_note"] == "벤치마크용 승인"
                                and c["reviewer"] == "benchmark" for c in calls))
            self.assertEqual({c["workflow_mode"] for c in calls}, {"sequential", "parallel"})
            self.assertEqual(approved["metadata"]["benchmark_approval"], "벤치마크용 승인")
            self.assertIn("post_t15_median_seconds", approved["averages_for_valid_pairs"]["parallel"])
            # 실행 시각·미세한 임베딩 점수 차는 같은 결과, 판정이나 큰 점수 차는 다른 결과
            base = {"capability": {"m": [{"semantic_score": 0.4391, "id": "r1"}]},
                    "market": {"similar_projects": [{"checked_at": "t1", "similarity": 0.78}]}}
            near = {"capability": {"m": [{"semantic_score": 0.4390, "id": "r1"}]},
                    "market": {"similar_projects": [{"checked_at": "t2", "similarity": 0.7801}]}}
            self.assertEqual(module.result_difference(base, near)[0], [])
            far = {**near, "capability": {"m": [{"semantic_score": 0.45, "id": "r1"}]}}
            self.assertEqual(module.result_difference(base, far)[0], ["capability"])
            other = {**near, "capability": {"m": [{"semantic_score": 0.4391, "id": "r2"}]}}
            self.assertEqual(module.result_difference(base, other)[0], ["capability"])
            with self.assertRaisesRegex(ValueError, "비어"):
                module.compare(cases, Path(temp) / "blank", approve_note=" ")


@unittest.skipUnless(importlib.util.find_spec("langgraph") is not None, "프로젝트 .venv에서 그래프 테스트 실행")
class GraphModesTests(unittest.TestCase):
    def setUp(self):
        import bid_review.rag.workflow as w
        from bid_review.models import RFPAnalysisResult, CapabilityMatchingResult
        self.w = w
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pdf = Path(self.temp.name) / "test.pdf"
        self.pdf.write_bytes(b"fixed-case")
        self.rfp = RFPAnalysisResult()
        self.capability = CapabilityMatchingResult(fit_score={"value": 0, "formula": "fixed"})

    def run_mode(self, mode):
        w = self.w
        with ExitStack() as stack:
            extractor = stack.enter_context(patch.object(w, "RFPExtractor"))
            verifier = stack.enter_context(patch.object(w, "T15Verifier"))
            matcher = stack.enter_context(patch.object(w, "CapabilityMatcher"))
            stack.enter_context(patch.object(w.BidReviewCrew, "_load_profile"))
            stack.enter_context(patch.object(w, "project_profile", return_value="fixture"))
            stack.enter_context(patch.object(w, "search_similar_projects", return_value=[]))
            extractor.return_value.analyze.return_value = self.rfp
            verifier.return_value.verify.return_value = w.CoreInfoVerification(
                document_id="fixture", status=w.VerificationStatus.CONFIRMED,
                categories={}, routing_reasons=[])
            matcher.return_value.analyze.return_value = self.capability
            state = w.create_bid_review_graph(workflow_mode=mode).invoke(
                {"pdf_path": str(self.pdf), "company_path": None, "run_id": mode})
        return state

    def test_both_modes_join_and_keep_results(self):
        sequential = self.run_mode("sequential")
        parallel = self.run_mode("parallel")
        for state in (sequential, parallel):
            nodes = [event["node"] for event in state["execution_history"]]
            self.assertEqual(len(nodes), len(set(nodes)))
            self.assertEqual([event["sequence"] for event in state["execution_history"]],
                             list(range(1, len(nodes) + 1)))
            self.assertLess(nodes.index("t2_company_match"), nodes.index("t23_evaluate"))
            self.assertLess(nodes.index("t3_similar_projects"), nodes.index("t23_evaluate"))
            self.assertIn("report_validation", state)
            self.assertIn("duration_seconds", state["execution_history"][-1])
        self.assertIn("analysis_fork", [event["node"] for event in parallel["execution_history"]])
        self.assertEqual(sequential["similar_projects_status"], parallel["similar_projects_status"])
        self.assertEqual(sequential["report_validation"]["status"], parallel["report_validation"]["status"])

    def test_parallel_edges_and_invalid_mode(self):
        graph = self.w.create_bid_review_graph(workflow_mode="parallel")
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        self.assertIn(("analysis_fork", "t2_company_rag_retrieval"), edges)
        self.assertIn(("analysis_fork", "t3_similar_projects"), edges)
        self.assertIn(("t23_evaluate", "t4_report"), edges)
        with self.assertRaises(ValueError):
            self.w.create_bid_review_graph(workflow_mode="typo")


if __name__ == "__main__":
    unittest.main(verbosity=2)
