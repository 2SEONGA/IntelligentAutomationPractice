"""T3 business-profile quality and index quarantine tests (no network)."""
from pathlib import Path
import json
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bid_review.rag.project_profile import ProfileExtractionError, extract_profile_body
import bid_review.rag.past_projects as pp

BODY = ("도시 교통과 생활 안전 정보를 통합 관리하는 스마트도시 플랫폼을 구축한다. "
        "기존 센서와 관제 시스템의 데이터를 연계하고 시민에게 실시간 교통 및 환경 서비스를 제공한다. "
        "서비스 범위를 확대하고 운영 기능을 고도화하여 도시 문제에 대응하는 정보 관리 기반을 마련한다.")
SCOPE = ("통합 플랫폼과 데이터 수집 시스템을 연계하여 관제 서비스를 제공한다. "
         "도시 운영 현황을 분석하는 대시보드와 실시간 장애 감시 기능을 개발하고, "
         "기존 인프라를 활용하여 신규 서비스가 단계적으로 확장될 수 있도록 시스템을 구축한다.")


def rejects(pages):
    try:
        extract_profile_body(pages)
    except ProfileExtractionError:
        return
    raise AssertionError("본문 부족 자료를 벡터화해서는 안 됨")


def test_numberless_toc_and_real_heading_on_same_block():
    toc = "목 차\nⅠ 사업 안내\n1절 사업개요\n1. 사업명\n2. 사업의 배경 및 목적\n3. 사업기간\n4. 사업금액\n5. 사업 범위\nⅡ 제안안내\n1. 참가자격\n2. 제안서 평가"
    result = extract_profile_body([toc + "\nⅠ 사업 안내\n1절 사업개요\n1. 사업명: 스마트도시 구축\n2. 사업의 배경 및 목적\n" + BODY])
    assert BODY in result.text and "제안서 평가" not in result.text
    assert "3. 사업기간" not in result.text and result.source_blocks == (1,)


def test_sections_continue_across_physical_pages_or_hwp_blocks():
    result = extract_profile_body(["Ⅰ. 사업 개요", BODY, "Ⅱ. 계약조건\n제1조(목적) 계약상대자의 의무"])
    assert BODY in result.text and "계약상대자" not in result.text and result.source_blocks == (1, 2)


def test_procurement_methods_and_generic_requirement_legend_end_business_sections():
    result = extract_profile_body(["1. 사업 개요\n" + BODY + "\n가. 입찰방법\n경쟁입찰 및 협상절차 안내\n"
                                  "나. 과업 개요\n" + SCOPE + "\n다. 요구사항 분류\n"
                                  "기능 요구사항 SFR은 목표시스템이 수행하는 기능의 분류를 의미한다."])
    assert BODY in result.text and SCOPE in result.text
    assert "경쟁입찰" not in result.text and "SFR" not in result.text


def test_contract_prose_mentioning_business_is_not_a_description():
    rejects(["제1조(목적) 이 계약은 갑과 을의 의무를 정한다.\n"
             "제2조(과업) “을”은 사업 내용을 변경할 경우 “갑”의 승인을 받아야 한다.\n"
             "제12조(보고) “을”은 시스템 구축 및 운영 실적을 “갑”에게 보고하여야 한다."])


def test_heading_alone_and_toc_with_page_numbers_are_rejected():
    rejects(["목차\n1. 사업 개요 ····· 1\n2. 사업 목적\t3\n3. 사업 범위 .......... 5"])
    rejects(["사업 개요\n사업명\n사업기간\n사업예산"])


def test_plain_page_numbers_and_annex_titles_do_not_leak_into_profile():
    result = extract_profile_body(["목차\nⅠ 사업 개요 1\nⅡ 현황 5\n별첨 A 요구사항 목록 및 상세내역 별도 파일\n"
                                   "별첨 B 도입 하드웨어 목록 19\n서식 1 신청서 45\nⅠ 사업 개요\n1 추진배경\n" + BODY])
    assert BODY in result.text and "별첨" not in result.text and "사업 개요 1" not in result.text


def test_approval_forms_and_doubled_glyphs_are_not_business_descriptions():
    rejects(["과업내용 (확정) 종합 심의결과서\n" + BODY + "\n심의결과 원안 승인"])
    doubled = "".join(ch * 2 if "가" <= ch <= "힣" else ch for ch in BODY)
    rejects(["사업 개요\n" + doubled])


def test_inline_heading_and_multiple_business_sections():
    result = extract_profile_body(["사업목적: " + BODY + "\n2. 과업 범위\n" + SCOPE + "\nⅢ. 제안서 작성지침\n비교에 쓰지 않는 문구"])
    assert BODY in result.text and SCOPE in result.text and "쓰지 않는" not in result.text


def test_headingless_technical_description_fallback_and_privacy():
    result = extract_profile_body([BODY + "\n문의 이메일: person@example.com\n\n" + SCOPE])
    assert result.method == "technical_paragraphs" and "person@example.com" not in result.text
    rejects([BODY])  # A single paragraph must not be counted twice to pass the quality check.
    rejects([BODY, BODY])
    assert BODY in result.text and SCOPE in result.text


def test_profile_limit_and_original_source_preserved():
    raw = "사업 개요\n" + BODY + "\n" + SCOPE
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "rfp.hwpx"
        path.write_bytes(b"original bytes")
        with patch.object(pp, "_rfp_pages", return_value=[raw]):
            profile = pp.project_profile(path, "사업명", "기관")
            assert profile.startswith("사업명: 사업명\n수요기관: 기관\n") and BODY in profile
        assert path.read_bytes() == b"original bytes"
    result = extract_profile_body([raw], max_chars=100)
    assert len(result.text) <= 100


def test_contract_attachment_ignored_even_if_api_mislabels_it():
    contract = {"kind": "rfp", "name": "2. 계약서(계정계 구축).hwp", "document_kind": "제안요청서"}
    real = {"kind": "other", "name": "붙임 제안요청서.hwpx", "document_kind": "제안요청서"}
    assert pp._pick_rfp([contract]) == (None, "")
    assert pp._pick_rfp([contract, real]) == (real, "rfp.hwpx")
    assert not pp._non_rfp_attachment("제안요청서 및 계약서.hwpx")


def test_named_rfp_beats_pdf_labelled_task_order():
    """M41: 문서구분 '과업지시서'인 매뉴얼·심의결과서 PDF보다 이름이 '제안요청서'인 HWP를 고른다(실제 공고 2건)."""
    manual = {"kind": "rfp", "name": "6) 상생결제제도 이용 매뉴얼(거래업체용).pdf", "document_kind": "과업지시서"}
    named = {"kind": "other", "name": "2. 제안요청서_2027~2029년 정보시스템 통합유지관리 용역.hwpx", "document_kind": "과업지시서"}
    assert pp._pick_rfp([manual, named]) == (named, "rfp.hwpx")
    review = {"kind": "rfp", "name": "붙임 4 과업내용 (확정) 종합 심의결과서.pdf", "document_kind": "기타문서"}
    hwp = {"kind": "other", "name": "붙임 2 제안요청서(최종).hwp", "document_kind": "기타문서"}
    assert pp._pick_rfp([review, hwp]) == (hwp, "rfp.hwp")
    pdf = {"kind": "rfp", "name": "제안요청서.pdf", "document_kind": "제안요청서"}
    assert pp._pick_rfp([hwp, pdf]) == (pdf, "rfp.pdf"), "같은 이름이면 PDF 우선(기존 규칙)"
    assert pp._pick_rfp([manual]) == (manual, "rfp.pdf"), "이름 단서가 없으면 기존 규칙 그대로"


def build_fixture(root, *, good=True):
    for number, name in (("BAD", "2. 계약서(계정계 구축).hwp"), *(([("GOOD", "제안요청서.hwpx")]) if good else [])):
        folder = root / "past" / f"{number}-000"
        folder.mkdir(parents=True)
        (folder / "rfp.hwpx").write_bytes(b"fake input")
        (folder / "meta.json").write_text(json.dumps({"notice_id": f"{number}-000", "bidNtceNm": number,
             "dminsttNm": "기관", "rfp_attachment": {"name": name}}), encoding="utf-8")


def test_index_quarantines_bad_attachment_and_embeds_only_good_text():
    with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as directory:
        root = Path(directory)
        build_fixture(root)
        calls = []
        def embed(texts):
            calls.extend(texts)
            return [[1.0, 0.0] for _ in texts]
        with patch.object(pp, "PAST_DIR", root / "past"), patch.object(pp, "INDEX_DIR", root / "rag"), \
             patch.object(pp, "_rfp_pages", return_value=["사업 개요\n" + BODY]):
            result = pp.build_past_project_index(embed=embed, log=lambda *_: None)
        assert result["projects"] == 1 and result["profile_review_required"] == 1
        assert len(calls) == 1 and "GOOD" in calls[0] and BODY in calls[0]
        review = json.loads((root / "rag/past_projects_review.json").read_text(encoding="utf-8"))
        assert review["items"][0]["notice_id"] == "BAD-000" and "계약" in review["items"][0]["reason"]
        assert (root / "past/BAD-000/rfp.hwpx").is_file()


def test_no_good_profile_retains_previous_index_and_creates_review_list():
    with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as directory:
        root = Path(directory)
        build_fixture(root, good=False)
        (root / "rag").mkdir()
        (root / "rag/past_projects.json").write_text("previous index", encoding="utf-8")
        with patch.object(pp, "PAST_DIR", root / "past"), patch.object(pp, "INDEX_DIR", root / "rag"):
            try:
                pp.build_past_project_index(embed=lambda _: (_ for _ in ()).throw(AssertionError("no API")), log=lambda *_: None)
            except ValueError:
                pass
            else:
                raise AssertionError("모두 보류된 경우 성공으로 처리하지 않음")
        assert (root / "rag/past_projects.json").read_text(encoding="utf-8") == "previous index"
        assert json.loads((root / "rag/past_projects_review.json").read_text(encoding="utf-8"))["review_required"] == 1


def test_embedding_error_does_not_expose_secret_or_replace_index():
    with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as directory:
        root = Path(directory)
        build_fixture(root)
        def broken(_):
            raise RuntimeError("Incorrect API key: sk-private-key")
        with patch.object(pp, "PAST_DIR", root / "past"), patch.object(pp, "INDEX_DIR", root / "rag"), \
             patch.object(pp, "_rfp_pages", return_value=["사업 개요\n" + BODY]):
            try:
                pp.build_past_project_index(embed=broken, log=lambda *_: None)
            except Exception as exc:
                assert "RuntimeError" in str(exc) and "sk-private" not in str(exc)
            else:
                raise AssertionError("API failure")
        assert not (root / "rag/past_projects.json").exists()


if __name__ == "__main__":
    count = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            count += 1
            print("ok", name)
    print(f"project profile OK ({count} tests)")
