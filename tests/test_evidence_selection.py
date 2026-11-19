"""T1.5 candidate selection regression tests; no network, embeddings or LLM calls."""
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bid_review.models import RFPAnalysisResult
from bid_review.rag.evidence_selection import select_evidence_candidates
from bid_review.rag.retriever import RetrievedEvidence
from bid_review.rag.verification import (
    CategoryVerification, CoreInfoVerification, VerifiedClaim, VerificationStatus as S,
)


def claim(category="amount", field="budget_krw", value=None, queries=None):
    return VerifiedClaim(category=category, field=field, claim=value, status=S.RETRIEVE_MORE,
                         retrieval_queries=queries or ["사업예산"])


def evidence(cid, text, *, score=None, source="rfp_pdf", page=1):
    metadata = {"document_title": "테스트 원문", "document_type": source,
                "page": page, "section": "사업 개요", "source_path": "/original.pdf"}
    if score is not None:
        metadata["semantic_score"] = score
    return {"chunk_id": cid, "text": text, "metadata": metadata,
            "rank": -9 if score is None else -score, "retrieval_queries": ["사업예산"]}


def ids(target, rows):
    return [item.evidence["chunk_id"] for item in select_evidence_candidates(target, rows)]


def test_later_amount_beats_first_heading_and_template():
    rows = [evidence("toc", "사업예산 ····· 3"),
            evidence("form", "[서식] 작성요령: 사업예산 1원으로 작성하십시오."),
            evidence("body", "사업예산 총액 300,000,000원 (부가가치세 포함)", page=3)]
    selected = select_evidence_candidates(claim(), rows)
    assert selected[0].evidence["chunk_id"] == "body"
    assert all(item.evidence["chunk_id"] != "toc" for item in selected)
    assert "대상 금액" in selected[0].reason


def test_keep_multiple_values_and_provenance_without_changing_t1():
    target = claim(value=300_000_000)
    rows = [evidence("rfp", "사업예산 300,000,000원", page=3),
            evidence("notice", "사업예산 330,000,000원", source="notice_pdf", page=2)]
    before = target.model_dump()
    assert ids(target, rows) == ["rfp", "notice"], "수치가 다른 후보도 사람에게 보여야 함"
    assert target.model_dump() == before, "후보 선택은 T1 값이나 검증 상태를 수정하지 않음"
    assert select_evidence_candidates(target, rows)[1].evidence["metadata"]["document_type"] == "notice_pdf"


def test_amounts_must_belong_to_target_label():
    wrong = evidence("other", "사업예산은 입찰공고문 참조. 추정가격: 300,000,000원")
    right = evidence("budget", "사업예산: 330,000,000원. 추정가격: 300,000,000원")
    assert ids(claim(value=300_000_000), [wrong, right]) == ["budget"]
    assert not ids(claim(), [evidence("year", "사업예산 검토는 2027년 3월에 실시한다.")])
    assert ids(claim(), [evidence("unit", "사업 예산: 1억 700만 원")]) == ["unit"]


def test_schedule_type_date_and_relative_period():
    target = claim("schedule", "proposal_deadline", queries=["제안서 제출 마감"])
    rows = [evidence("wrong", "제안서 제출 마감은 공고문 참조. 계약기간: 2027년 2월 15일"),
            evidence("form", "제안서 제출 마감: 20XX년 XX월 XX일"),
            evidence("date", "제안서 제출 마감: 2026년 11월 30일 18:00", score=.42)]
    assert ids(target, rows) == ["date"]
    assert ids(claim("schedule", "contract_period", queries=["계약기간"]),
               [evidence("relative", "계약기간: 착수일로부터 12개월")]) == ["relative"]
    assert not ids(target, [evidence("invalid", "제안서 제출 마감: 2026년 2월 30일")])


def test_eligibility_substance_not_heading_and_preserve_exceptions():
    target = claim("eligibility", "eligibility_section_coverage", queries=["공동수급"])
    rows = [evidence("heading", "공동수급"),
            evidence("clause", "공동수급은 허용하지 않음. 다만 법령에 따른 예외 적용은 별도 확인하여야 한다.")]
    assert ids(target, rows) == ["clause"]
    assert "허용하지 않음" in select_evidence_candidates(target, rows)[0].evidence["text"]


def test_deduplicate_chunks_and_text_and_limit_to_three():
    first = evidence("a", "사업예산 1억원")
    rows = [first, first, evidence("copy", "사업 예산 1억 원")]
    rows.extend(evidence(str(i), f"사업예산 {i}억원") for i in range(2, 7))
    assert ids(claim(), rows) == ["a", "2", "3"]
    assert select_evidence_candidates(claim(), rows, limit=0) == []
    assert len(select_evidence_candidates(claim(), rows, limit=99)) == 3


def run_graph(rows, *, semantic=False):
    import bid_review.rag.workflow as workflow
    target = claim()
    verification = CoreInfoVerification(document_id="doc", status=S.RETRIEVE_MORE, categories={
        "amount": CategoryVerification(category="amount", status=S.RETRIEVE_MORE,
                                       claims=[target], coverage_complete=False),
    })
    hits = [RetrievedEvidence(row["chunk_id"], row["text"], row["metadata"], row["rank"]) for row in rows]
    with patch.object(workflow.RFPExtractor, "analyze", return_value=RFPAnalysisResult()), \
         patch.object(workflow.T15Verifier, "verify", side_effect=lambda *a, **kw: verification.model_copy(deep=True)), \
         patch.object(workflow, "search_keyword", return_value=[] if semantic else hits), \
         patch.object(workflow, "search_semantic", return_value=hits if semantic else []), \
         patch.object(workflow.CapabilityMatcher, "analyze", side_effect=AssertionError("T2 must not run")), \
         patch.object(workflow, "search_similar_projects", side_effect=AssertionError("T3 must not run")):
        return workflow.create_bid_review_graph().invoke({"pdf_path": "fixture.pdf", "retrieval_limit": 1})


def test_graph_exposes_later_candidates_and_still_requires_human_review():
    rows = [evidence("heading", "사업예산"),
            evidence("budget", "사업예산 300,000,000원", page=3),
            evidence("other_budget", "사업예산 330,000,000원", source="notice_pdf", page=2)]
    state = run_graph(rows)
    target = state["verification"]["categories"]["amount"]["claims"][0]
    assert state["decision"] == "human_review_required"
    assert target["status"] == "human_review" and target["claim"] is None
    assert [c["chunk_id"] for c in target["citations"]] == ["budget", "other_budget"]
    assert target["citations"][1]["page"] == 2
    assert "2건" in target["checks"][-1]["detail"]


def test_semantic_hits_and_irrelevant_hits_cannot_automatically_pass():
    state = run_graph([evidence("vector", "사업예산 300,000,000원", score=.41)], semantic=True)
    target = state["verification"]["categories"]["amount"]["claims"][0]
    assert target["status"] == "human_review" and "0.41" in target["checks"][-1]["detail"]
    state = run_graph([evidence("empty", "사업예산 ····· 3")])
    target = state["verification"]["categories"]["amount"]["claims"][0]
    assert target["status"] == "retrieve_more" and not target["citations"]
    assert state["decision"] == "human_review_required"


def test_relative_period_needs_a_number_not_just_a_start_event():
    target = claim("schedule", "contract_period", queries=["계약기간"])
    assert ids(target, [evidence("generic", "계약기간: 계약일로부터 사업종료일까지 월간 보고한다.")]) == []
    assert ids(target, [evidence("duration", "계약기간: 계약일로부터 12개월")]) == ["duration"]
    assert ids(target, [evidence("days", "계약기간: 총 180일")]) == ["days"]


def test_graph_retrieves_schedule_alias_and_keeps_human_review():
    import bid_review.rag.workflow as workflow
    target = claim("schedule", "contract_period", queries=["계약기간"])
    verification = CoreInfoVerification(document_id="doc", status=S.RETRIEVE_MORE, categories={
        "schedule": CategoryVerification(category="schedule", status=S.RETRIEVE_MORE,
                                          claims=[target], coverage_complete=False)})
    row = evidence("real_period", "과업기간: 계약일로부터 2027년 12월 30일까지", page=3)
    hit = RetrievedEvidence(row["chunk_id"], row["text"], row["metadata"], row["rank"])
    seen = []
    def keyword(query, **kwargs):
        seen.append(query)
        return [hit] if query == "과업기간" else []
    with patch.object(workflow.RFPExtractor, "analyze", return_value=RFPAnalysisResult()), \
         patch.object(workflow.T15Verifier, "verify", side_effect=lambda *a, **k: verification.model_copy(deep=True)), \
         patch.object(workflow, "search_keyword", side_effect=keyword), \
         patch.object(workflow, "search_semantic", return_value=[]), \
         patch.object(workflow.CapabilityMatcher, "analyze", side_effect=AssertionError("T2 must not run")):
        state = workflow.create_bid_review_graph().invoke({"pdf_path": "fixture.pdf", "retrieval_limit": 1})
    result = state["verification"]["categories"]["schedule"]["claims"][0]
    assert "과업기간" in seen and result["citations"][0]["chunk_id"] == "real_period"
    assert result["status"] == "human_review" and state["decision"] == "human_review_required"


if __name__ == "__main__":
    count = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            count += 1
            print("ok", name)
    print(f"evidence selection OK ({count} tests)")
