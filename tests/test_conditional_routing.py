"""보완 4: 검증 실패 원인별 분기 및 그래프 연결."""
from __future__ import annotations

from pathlib import Path
import importlib.util
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bid_review.rag.conditional_routing import plan_verification


def state(*claims, limit=2, round_no=0, attempts=None):
    return {"verification": {"status": "human_review", "categories": {
        "amount": {"claims": list(claims)}}}, "retrieval_limit": limit,
        "retrieval_round": round_no, "reanalysis_attempts": attempts or {}}


def claim(field, status, check, *, queries=()):
    return {"field": field, "status": status, "checks": [{"check_id": check, "status": status}],
            "retrieval_queries": list(queries)}


class ConditionalRoutingTests(unittest.TestCase):
    def test_missing_source_retrieves_only_failed_item(self):
        broken = claim("budget_krw", "retrieve_more", "primary_source_quote", queries=["사업 금액"])
        good = claim("estimated_price_krw", "confirmed", "primary_source_quote", queries=["추정가격"])
        result = plan_verification(state(broken, good))
        self.assertEqual(result["action"], "retrieve_more")
        self.assertEqual([x["field"] for x in result["targets"]], ["budget_krw"])

    def test_interpretation_reanalyzes_one_item_then_review(self):
        bad = claim("budget_krw", "conflict", "amount_normalization")
        self.assertEqual(plan_verification(state(bad))["action"], "reanalyze")
        result = plan_verification(state(bad, attempts={"amount:budget_krw": 1}))
        self.assertEqual(result["action"], "human_review")

    def test_other_document_conflict_requires_person(self):
        bad = claim("budget_krw", "conflict", "notice_crosscheck")
        self.assertEqual(plan_verification(state(bad))["action"], "human_review")

    def test_mixed_conflict_never_reruns_an_agent(self):
        bad = {"field": "budget_krw", "status": "conflict", "checks": [
            {"check_id": "amount_normalization", "status": "conflict"},
            {"check_id": "notice_crosscheck", "status": "conflict"}]}
        self.assertEqual(plan_verification(state(bad))["action"], "human_review")

    def test_limit_or_no_query_requires_review(self):
        bad = claim("budget_krw", "retrieve_more", "primary_source_quote", queries=["금액"])
        self.assertEqual(plan_verification(state(bad, limit=2, round_no=2))["action"], "human_review")
        self.assertEqual(plan_verification(state(bad, limit=0))["action"], "human_review")
        self.assertEqual(plan_verification(state(claim("x", "retrieve_more", "candidate_present")))["action"], "human_review")

    def test_preexisting_approval_and_reanalysis_failure(self):
        bad = claim("budget_krw", "conflict", "amount_normalization")
        self.assertEqual(plan_verification({**state(bad), "review_decision": "approved"})["action"], "company_match")
        self.assertEqual(plan_verification({**state(bad), "reanalysis_error": "OCR 실패"})["action"], "human_review")

    def test_graph_has_all_routes(self):
        if importlib.util.find_spec("langgraph") is None:
            self.skipTest("이 실행 환경에 langgraph가 없습니다. 프로젝트 .venv에서 그래프 연결을 확인하세요.")
        from bid_review.rag.workflow import create_bid_review_graph
        edges = {(e.source, e.target) for e in create_bid_review_graph().get_graph().edges}
        for source, target in (("t15_verify", "t15_route"), ("t15_route", "targeted_retrieval"),
                               ("t15_route", "t1_reanalyze"), ("t15_route", "human_review"),
                               ("t1_reanalyze", "t15_verify"), ("t15_reverify", "t15_route")):
            self.assertIn((source, target), edges)


if __name__ == "__main__":
    unittest.main(verbosity=2)
