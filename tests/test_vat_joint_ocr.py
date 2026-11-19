"""송하연 보완 검사(VAT 예외처리·공동도급 금지 우선·계약기간 불일치·OCR 보완): `python tests/test_vat_joint_ocr.py`."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bid_review import ocr, vat  # noqa: E402
from bid_review.agent01_rfp import JOINT_FORBID_RE, PageText, RFPExtractor, merge_document_results  # noqa: E402
from bid_review.models import RFPAnalysisResult, RFPFieldCandidate, RFPQualityGate  # noqa: E402
from bid_review.pdf_context import document_quality_context  # noqa: E402
from bid_review.rag import verification  # noqa: E402
from bid_review.settings import load_settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WATER_RFP = ROOT / "data/input/AI 영상 기반 상수도관 노후도 분석 및 GIS 연동 소프트웨어 개발 용역.pdf"
WATER_NOTICE = ROOT / "data/notice_attachments/R26BK01762350-000/notice.pdf"
UNIV_RFP = ROOT / "data/input/통합정보시스템 고도화 구축 용역.pdf"
UNIV_NOTICE = ROOT / "data/notice_attachments/R26BK01747679-000/notice.pdf"
EXTRACTOR = RFPExtractor(load_settings())
_CACHE: dict = {}


def analyze(path: Path, kind: str = "rfp_pdf") -> RFPAnalysisResult:
    if (path, kind) not in _CACHE:
        _CACHE[(path, kind)] = EXTRACTOR.analyze(path, document_kind=kind)
    return _CACHE[(path, kind)].model_copy(deep=True)


def test_vat_basis_and_compare() -> None:
    assert vat.vat_basis("금 107,000,000원 (부가가치세 별도)") == "excluded"
    assert vat.vat_basis("기초금액 117,700,000원 부가가치세 포함") == "included"
    assert vat.vat_basis("추정가격 107,000,000원 부가가치세 제외") == "excluded"
    assert vat.vat_basis("1,070,500,000원 (VAT 포함, 5년간)") == "included"
    assert vat.vat_basis("300,000,000원") == "unknown"
    assert vat.vat_basis("부가세 미포함") == "excluded"
    assert vat.compare(107_000_000, "excluded", 117_700_000, "included")[0] == "same_after_vat"
    assert vat.compare(117_700_000, "included", 107_000_000, "excluded")[0] == "same_after_vat"
    assert vat.compare(107_000_000, "unknown", 117_700_000, "unknown")[0] == "vat_unknown"
    assert vat.compare(107_000_000, "excluded", 130_000_000, "included")[0] == "different"
    assert vat.compare(107_000_000, "excluded", 107_000_000, "included")[0] == "basis_mismatch"
    assert vat.compare(107_000_000, "unknown", 107_000_000, "excluded")[0] == "same"
    assert vat.convert(107_000_000, "unknown", "included") is None, "VAT 기준을 모르면 환산하지 않음"


def test_joint_venture_forbid_pattern() -> None:
    for text in ("공동도급은 허용하지 않음", "공동수급 허용 여부 : 불허", "공동수급(공동도급) 불가",
                 "본 사업은 공동계약을 허용하지 않습니다.", "공동수급(도급 및 하도급), 분합 납품은 허용하지 않음"):
        assert JOINT_FORBID_RE.search(text), text
    for text in ("공동수급을 허용하며, 하도급은 불허", "공동수급체 구성원은 다른 공동수급체에 중복 참여할 수 없다",
                 "공동수급협정서는 제출할 수 없음", "공동이행방식 공동수급 허용"):
        assert not JOINT_FORBID_RE.search(text), text
    assert RFPExtractor._joint_venture_summary("공동수급 허용 여부 : 불허\n공동이행방식 서식 첨부") == "공동도급(공동수급) 불허 — 단독 입찰만 가능"
    assert RFPExtractor._joint_venture_summary("공동이행방식에 의한 공동수급 허용, 대표사를 포함하여 3개사 이하").startswith("공동이행방식 허용")


def test_basic_info_checks_joint_forbid_first() -> None:
    """금지 조항(4쪽)이 공동수급 서식 안내(28쪽)보다 우선한다. 이전에는 '공동이행방식 허용'으로 오판."""
    policy = analyze(WATER_RFP).basic_info.joint_venture_policy
    assert policy.value.startswith("공동도급(공동수급) 불허") and policy.source_page == 4
    assert "허용하지 않" in policy.source_excerpt
    pages = [PageText(1, "표지"), PageText(2, "공동수급협정서 서식: 공동이행방식\n공동도급은 허용하지 않음")]
    basic, _, _ = EXTRACTOR._basic_info(pages, "rfp_pdf")
    assert "불허" in basic.joint_venture_policy.value and "공동이행방식 허용" not in basic.joint_venture_policy.value


def test_choose_reconciles_vat_only_difference() -> None:
    def cand(value: int, excerpt: str) -> RFPFieldCandidate:
        return RFPFieldCandidate(value=value, source_page=1, source_excerpt=excerpt, score=90, page_type="body_page")
    chosen, reason = EXTRACTOR._choose("budget_krw", [cand(117_700_000, "기초금액 117,700,000원 부가가치세 포함"),
                                                       cand(107_000_000, "사업예산: 107,000,000원 부가가치세 별도")])
    assert chosen.value == 107_000_000 and "VAT 표기 차이" in reason
    chosen, reason = EXTRACTOR._choose("budget_krw", [cand(117_700_000, "사업비: 117,700,000원"),
                                                       cand(107_000_000, "사업예산: 107,000,000원")])
    assert chosen.value is None and "VAT 조건 원문 확인" in reason, "VAT 기준을 모르면 자동 확정하지 않음"


def test_merge_vat_difference_is_not_conflict() -> None:
    """RFP 107,000,000원(VAT 별도) ↔ 공고문 117,700,000원(VAT 포함)은 충돌이 아니다."""
    rfp, notice = analyze(WATER_RFP), analyze(WATER_NOTICE, "notice_pdf")
    assert notice.basic_info.budget_krw.value == 117_700_000
    merged = merge_document_results(rfp, notice)
    assert not [c for c in merged.conflicts if c.field in {"budget_krw", "estimated_price_krw"}]
    notes = [r.reason for r in merged.basic_info_selection_reasons if r.field == "budget_krw_vat"]
    assert notes and "VAT 표기 차이" in notes[0]
    # 같은 쌍에서 공고문 금액이 VAT 기준을 맞춰도 다르면 실제 충돌로 남긴다.
    notice.basic_info.budget_krw.value = 130_000_000
    notice.basic_info.budget_krw.source_excerpt = "기초금액 130,000,000원 부가가치세 포함"
    merged = merge_document_results(rfp, notice)
    assert [c for c in merged.conflicts if c.field == "budget_krw"]


def test_merge_contract_period_conflict() -> None:
    """통합정보시스템: RFP 2027년 2월 15일 ↔ 공고문 2026년 2월 15일."""
    merged = merge_document_results(analyze(UNIV_RFP), analyze(UNIV_NOTICE, "notice_pdf"))
    conflict = next(c for c in merged.conflicts if c.field == "contract_period")
    assert "2027년 2월 15일" in conflict.reason and "2026년 2월 15일" in conflict.reason
    assert not merged.quality_gate.passed


def test_merge_keeps_forbid_over_allow() -> None:
    rfp, notice = analyze(WATER_RFP), analyze(WATER_NOTICE, "notice_pdf")
    notice.quality_gate = RFPQualityGate(status="passed")
    notice.basic_info.joint_venture_policy = RFPFieldCandidate(value="공동이행방식 허용", source_page=1,
                                                              source_excerpt="공동이행방식 허용", source_document="notice_pdf")
    notice.eligibility_requirements = []
    merged = merge_document_results(rfp, notice)
    assert "불허" in merged.basic_info.joint_venture_policy.value
    assert any(c.field == "joint_venture_policy" for c in merged.conflicts)
    assert any(e.evidence.section == "공동도급 참가 제한" for e in merged.eligibility_requirements)


def test_t15_api_crosscheck_vat_exception() -> None:
    rfp = analyze(WATER_RFP)
    original = verification.search_keyword
    verification.search_keyword = lambda *args, **kwargs: []
    try:
        verifier = verification.T15Verifier.__new__(verification.T15Verifier)

        def budget_claim(amount: str):
            result = verifier._verify_amounts(rfp, "doc", "R26BK01762350", {"bidNtceNo": "R26BK01762350", "asignBdgtAmt": amount}, "found", None)
            return next(c for c in result.claims if c.field == "g2b_crosscheck_budget_krw")
        assert budget_claim("117700000").status == verification.VerificationStatus.CONFIRMED
        assert budget_claim("130000000").status == verification.VerificationStatus.CONFLICT
        assert budget_claim("107000000").status == verification.VerificationStatus.HUMAN_REVIEW
    finally:
        verification.search_keyword = original


def test_ocr_fallback_replaces_broken_page() -> None:
    """OCR 엔진이 있으면 OCR 필요 페이지(통합정보시스템 1·2쪽 등)를 OCR 텍스트로 대체한다."""
    original = (ocr.engine_available, ocr.ocr_page)
    ocr.engine_available = lambda: True
    ocr.ocr_page = lambda page, lang="kor+eng", resolution=300: ("사업명 : 통합정보시스템 고도화 구축 용역\n발주기관 : 테스트대학교 정보화본부\n"
                                                               "사업 개요와 추진 배경, 주요 과업 범위를 OCR로 읽은 본문 텍스트입니다" * 3)
    try:
        pages, _, analyses, _ = EXTRACTOR._extract_pages(UNIV_RFP)
        applied = [a.page_number for a in analyses if a.ocr_applied]
        assert applied and all("tesseract_ocr" in a.extraction_methods for a in analyses if a.ocr_applied)
        assert all(p.page_type != "body_page" or not p.ocr_applied or "통합정보시스템" in p.text for p in pages)
        off = RFPExtractor({**EXTRACTOR.settings, "rfp": {**EXTRACTOR.settings["rfp"], "ocr": {"mode": "off"}}})
        assert not any(a.ocr_applied for a in off._extract_pages(UNIV_RFP)[2])
    finally:
        ocr.engine_available, ocr.ocr_page = original
    assert not any(a.ocr_applied for a in analyze(UNIV_RFP).page_analysis), "엔진이 없으면 기존 추출 그대로"
    assert document_quality_context(analyze(UNIV_RFP))["ocr_applied_pages"] == []


def test_ocr_is_better() -> None:
    assert not ocr.is_better("정상적인 본문 텍스트가 충분히 길게 추출된 페이지입니다", "정상적인 본문")
    assert ocr.is_better("···· ---- ||||", "제안요청서 사업 개요 및 추진 배경에 대한 설명입니다")
    assert not ocr.is_better("abc", None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("vat/joint/ocr OK")
