"""T2·T3 검증의 출처 누락·법인 불일치·낙찰 연결을 확인한다."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bid_review.models import BidReviewReport, CapabilityMatchingResult, RFPAnalysisResult
from bid_review.policy import SOURCE_ARTIFACTS, TARGET_LEGAL_ENTITY, VISIBLE_COMPANY_NAME
from bid_review.rag.agent23_evaluation import evaluate_agent23
from bid_review.rag.report_nodes import generate_t4_report
from bid_review.rag.report_validation import validate_t4_report


def status(result, name):
    return next(x["status"] for x in result["agent23_evaluation"]["checks"] if x["check_id"] == name)


class Agent23EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "company.json"
        self.profile.write_text(json.dumps({
            "company_name": TARGET_LEGAL_ENTITY,
            "records": [{"id": "proof1", "type": "수행실적", "title": "SI 구축", "document": "실적증명서",
                         "section": "제1절", "excerpt": "시스템 구축", "buyer": "발주기관",
                         "period": "2025", "contract_amount_krw": 100000000, "scope": "시스템 구축",
                         "performing_legal_entity": TARGET_LEGAL_ENTITY}],
        }, ensure_ascii=False), encoding="utf-8")
        self.state = {
            "decision": "company_matching_complete", "company_path": str(self.profile),
            "capability": CapabilityMatchingResult(company_name=TARGET_LEGAL_ENTITY,
                fit_score={"value": 80, "formula": "test"},
                requirement_matches=[{"requirement_id": "REQ-1", "status": "충족",
                    "company_evidence": [{"document": "실적증명서", "section": "제1절", "excerpt": "시스템 구축",
                                          "record_id": "proof1"}],
                    "gap_or_check_item": "", "score": 1, "score_rationale": "test"}]).model_dump(mode="json"),
            "similar_projects": [], "similar_projects_status": "none_above_threshold",
        }

    def test_company_evidence_source_link(self):
        result = evaluate_agent23(self.state)
        self.assertEqual(status(result, "t2_legal_entity"), "passed")
        self.assertEqual(status(result, "t2_match:REQ-1"), "passed")
        self.assertEqual(status(result, "t3_search"), "info")  # 임계값 이상 후보 없음은 보류 사유가 아님
        self.assertEqual(result["agent23_evaluation"]["status"], "passed")

    def test_short_company_name_matches(self):
        # 실제 회사 자료는 짧은 상호(㈜아이티센엔텍)를 쓴다. 긴 기준명 안에서만 찾던 버그 재발 방지.
        data = json.loads(self.profile.read_text(encoding="utf-8"))
        data["company_name"] = VISIBLE_COMPANY_NAME
        self.profile.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.state["capability"]["company_name"] = VISIBLE_COMPANY_NAME
        self.assertEqual(status(evaluate_agent23(self.state), "t2_legal_entity"), "passed")

    def test_wrong_legal_entity_kept_for_review(self):
        self.state["capability"]["company_name"] = "다른 법인"
        self.assertEqual(status(evaluate_agent23(self.state), "t2_legal_entity"), "needs_review")

    def test_missing_performance_proof(self):
        self.state["capability"]["requirement_matches"][0]["company_evidence"][0]["record_id"] = "other"
        self.assertEqual(status(evaluate_agent23(self.state), "t2_match:REQ-1"), "needs_review")

    def test_missing_record_level_legal_owner(self):
        data = json.loads(self.profile.read_text(encoding="utf-8"))
        data["records"][0].pop("performing_legal_entity")
        self.profile.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(status(evaluate_agent23(self.state), "t2_match:REQ-1"), "needs_review")

    def test_source_excerpt_mismatch(self):
        self.state["capability"]["requirement_matches"][0]["company_evidence"][0]["excerpt"] = "꾸며낸 실적"
        self.assertEqual(status(evaluate_agent23(self.state), "t2_match:REQ-1"), "needs_review")

    def test_t3_source_and_award_linkage(self):
        source = self.root / "rfp.pdf"
        source.write_bytes(b"%PDF- fixture")
        self.state.update(similar_projects_status="found 1", similar_projects=[{
            "notice_id": "R26-000", "title": "유사 사업", "buyer": "기관", "notice_url": "https://example.org/notice",
            "rfp_path": "rfp.pdf", "profile_excerpt": "사업명: 유사 사업", "similarity": 0.81,
            "award": {"bidwinnrNm": "낙찰사"},
        }])
        with patch("bid_review.rag.agent23_evaluation.PROJECT_ROOT", self.root), \
             patch("bid_review.rag.agent23_evaluation._source_profile", return_value="사업명: 유사 사업\n사업범위"):
            result = evaluate_agent23(self.state)
        self.assertEqual(status(result, "t3_candidate:1:source"), "passed")
        self.assertEqual(status(result, "t3_candidate:1:award"), "needs_review")
        self.assertEqual(status(result, "t3_candidate:1:similarity"), "info")

    def test_t3_award_linked_to_same_notice_number_passes(self):
        source = self.root / "rfp.pdf"
        source.write_bytes(b"%PDF- fixture")
        candidate = {"notice_id": "R26-001", "title": "유사 사업", "buyer": "기관",
                     "notice_url": "https://example.org/notice", "rfp_path": "rfp.pdf",
                     "profile_excerpt": "사업명: 유사 사업", "similarity": 0.81, "award": {"bidwinnrNm": "낙찰사"},
                     "award_notice_id": "R26-000", "award_source_url": "https://example.org/notice"}
        self.state.update(similar_projects_status="found 1", similar_projects=[candidate])
        with patch("bid_review.rag.agent23_evaluation.PROJECT_ROOT", self.root), \
             patch("bid_review.rag.agent23_evaluation._source_profile", return_value="사업명: 유사 사업\n사업범위"):
            result = evaluate_agent23(self.state)
            self.assertEqual(status(result, "t3_candidate:1:award"), "passed")  # 같은 공고번호의 다른 차수
            self.assertEqual(result["agent23_evaluation"]["status"], "passed")  # 유사도 info만으로 보류되지 않음
            candidate["award_notice_id"] = "R27-000"  # 재공고 그룹의 다른 공고번호 낙찰
            self.assertEqual(status(evaluate_agent23(self.state), "t3_candidate:1:award"), "needs_review")

    def test_source_profile_cache_keyed_by_file_bytes(self):
        """평가 캐시: 같은 원문이면 재계산하지 않고, 원문이 바뀌면 다시 계산한다. 변조된 발췌는 여전히 걸린다."""
        import bid_review.rag.agent23_evaluation as module
        source = self.root / "rfp.pdf"
        source.write_bytes(b"%PDF- one")
        calls = []

        def fake_profile(path, title, buyer):
            calls.append(Path(path).read_bytes())
            return f"사업명: {title}\n본문 {len(calls)}"
        with patch.object(module, "PROFILE_CACHE", self.root / "cache.json"), \
             patch("bid_review.rag.past_projects.project_profile", side_effect=fake_profile):
            first = module._source_profile(source, "유사 사업", "기관")
            self.assertEqual(module._source_profile(source, "유사 사업", "기관"), first)
            self.assertEqual(len(calls), 1, "같은 파일 바이트면 다시 읽지 않는다")
            source.write_bytes(b"%PDF- two")
            self.assertNotEqual(module._source_profile(source, "유사 사업", "기관"), first)
            self.assertEqual(len(calls), 2, "원문이 바뀌면 다시 계산한다")
            self.state.update(similar_projects_status="found 1", similar_projects=[{
                "notice_id": "R26-000", "title": "유사 사업", "buyer": "기관", "notice_url": "https://example.org/n",
                "rfp_path": "rfp.pdf", "profile_excerpt": "사업명: 유사 사업\n꾸며낸 본문", "similarity": 0.8}])
            with patch("bid_review.rag.agent23_evaluation.PROJECT_ROOT", self.root):
                self.assertEqual(status(evaluate_agent23(self.state), "t3_candidate:1:source"), "needs_review")

    def test_t3_missing_original_excerpt(self):
        self.state.update(similar_projects_status="found 1", similar_projects=[{
            "notice_id": "R26-000", "title": "유사 사업", "notice_url": "https://example.org/notice",
            "rfp_path": "missing.pdf", "profile_excerpt": "내용", "similarity": 0.82,
        }])
        self.assertEqual(status(evaluate_agent23(self.state), "t3_candidate:1:source"), "needs_review")

    def test_t2_not_run(self):
        self.state["decision"] = "human_review_required"
        self.assertEqual(evaluate_agent23(self.state)["agent23_evaluation"]["status"], "not_run")

    def test_evaluation_reaches_report_and_marks_hold(self):
        self.state["capability"]["company_name"] = "다른 법인"  # 실제 보류 사유(법인 불일치)
        self.state.update(rfp=RFPAnalysisResult().model_dump(mode="json"),
                          verification={"status": "confirmed"})
        self.state.update(evaluate_agent23(self.state))
        draft = BidReviewReport(decision_status="Go", executive_summary="분석 초안",
                                source_artifacts=list(SOURCE_ARTIFACTS))
        with patch("bid_review.rag.report_nodes.load_settings", return_value={}), \
             patch("bid_review.rag.report_nodes.ReportWriter") as writer:
            writer.return_value.write.return_value = draft
            generated = generate_t4_report(self.state)
        self.assertEqual(generated["report"]["decision_status"], "보류")
        self.assertEqual(generated["report_details"]["agent23_evaluation"], self.state["agent23_evaluation"])
        self.state.update(generated)
        result = validate_t4_report(self.state)
        checks = result["report_validation"]["checks"]
        self.assertTrue(any(c["check_id"] == "agent23_transfer" and c["status"] == "passed" for c in checks))
        self.assertTrue(any(c["check_id"] == "agent23_evidence" and c["status"] == "needs_review" for c in checks))

    def test_tampered_evaluation_transfer_fails(self):
        self.state["capability"]["company_name"] = "다른 법인"  # 실제 보류 사유(법인 불일치)
        self.state.update(rfp=RFPAnalysisResult().model_dump(mode="json"),
                          verification={"status": "confirmed"})
        self.state.update(evaluate_agent23(self.state))
        draft = BidReviewReport(decision_status="보류", executive_summary="분석 초안",
                                source_artifacts=list(SOURCE_ARTIFACTS))
        with patch("bid_review.rag.report_nodes.load_settings", return_value={}), \
             patch("bid_review.rag.report_nodes.ReportWriter") as writer:
            writer.return_value.write.return_value = draft
            self.state.update(generate_t4_report(self.state))
        self.state["report_details"]["agent23_evaluation"]["status"] = "passed"
        checks = validate_t4_report(self.state)["report_validation"]["checks"]
        self.assertTrue(any(c["check_id"] == "agent23_transfer" and c["status"] == "failed" for c in checks))


if __name__ == "__main__":
    unittest.main(verbosity=2)
