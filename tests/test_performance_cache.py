"""성능·재현성(남은 업무 4번): 질의 임베딩 캐시, T1 쪽 추출 캐시. `python tests/test_performance_cache.py` (네트워크 없음)."""
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bid_review import agent01_rfp  # noqa: E402
from bid_review.agent01_rfp import RFPExtractor  # noqa: E402
from bid_review.rag.semantic import cached_embedder, embedding_cache_key  # noqa: E402
from bid_review.settings import load_settings  # noqa: E402

WATER = ROOT / "data/input/AI 영상 기반 상수도관 노후도 분석 및 GIS 연동 소프트웨어 개발 용역.pdf"


def test_embedding_cache_reuses_exact_vector() -> None:
    calls: list[list[str]] = []
    counter = {"n": 0}

    def flaky(texts):  # 호출할 때마다 조금씩 다른 값(실제 API의 소수점 흔들림 흉내)
        calls.append(list(texts))
        counter["n"] += 1
        return [[0.1 + counter["n"] * 1e-5, 0.2, 0.3] for _ in texts]

    db = Path(tempfile.mkdtemp()) / "cache.sqlite3"
    embed = cached_embedder(flaky, "model-a", db)
    first = embed(["상수도관 노후도", "학사 시스템"])
    again = embed(["학사 시스템", "상수도관 노후도", "상수도관 노후도"])
    assert again[1] == first[0] and again[0] == first[1] and again[2] == first[0], "같은 텍스트 → 완전히 같은 벡터"
    assert calls == [["상수도관 노후도", "학사 시스템"]], "두 번째는 API를 부르지 않음"
    embed(["상수도관 노후도 "])  # 공백 하나라도 다르면 다른 입력
    assert calls[-1] == ["상수도관 노후도 "]
    other_model = cached_embedder(flaky, "model-b", db)
    assert other_model(["학사 시스템"]) != first[1] and calls[-1] == ["학사 시스템"], "모델이 바뀌면 캐시를 쓰지 않음"
    assert embedding_cache_key("m", "x") != embedding_cache_key("m", "x ") != embedding_cache_key("n", "x")


def test_embedding_cache_survives_unwritable_path() -> None:
    folder = Path(tempfile.mkdtemp())
    blocker = folder / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    embed = cached_embedder(lambda texts: [[1.0, 0.0] for _ in texts], "m", blocker / "cache.sqlite3")
    try:
        assert embed(["a"]) == [[1.0, 0.0]], "캐시 파일을 못 만들어도 임베딩은 동작"
    except OSError:
        raise AssertionError("캐시 실패가 검색을 막으면 안 됨")


def test_page_cache_returns_identical_independent_copies() -> None:
    agent01_rfp._PAGE_CACHE.clear()
    extractor = RFPExtractor(load_settings())
    first = extractor._extract_pages(WATER)
    calls = {"n": 0}
    original = RFPExtractor._extract_pages_uncached

    def counting(self, path):
        calls["n"] += 1
        return original(self, path)
    RFPExtractor._extract_pages_uncached = counting
    try:
        second = RFPExtractor(load_settings())._extract_pages(WATER)
    finally:
        RFPExtractor._extract_pages_uncached = original
    assert calls["n"] == 0, "같은 파일·설정이면 다시 읽지 않음(T1.5 재해석 노드)"
    assert [p.text for p in first[0]] == [p.text for p in second[0]]
    second[0][0].text = "바뀐 값"
    assert RFPExtractor(load_settings())._extract_pages(WATER)[0][0].text == first[0][0].text, "돌려준 사본을 고쳐도 캐시는 그대로"
    copy = Path(tempfile.mkdtemp()) / "renamed.pdf"
    copy.write_bytes(WATER.read_bytes())
    renamed = extractor._extract_pages(copy)
    assert all(e.document == "renamed.pdf" for issue in renamed[1] for e in issue.evidence), "이름이 다르면 근거 문서명도 다름"
    changed = RFPExtractor({**extractor.settings, "rfp": {**extractor.settings["rfp"], "low_text_page_characters": 10_000}})
    assert changed._extract_pages(WATER)[3] is True, "설정이 바뀌면 캐시를 쓰지 않음(짧은 쪽 기준 → OCR 필요)"


def test_t3_reuses_t1_page_text_exactly() -> None:
    """T3 질의 프로필(_rfp_pages)은 T1이 읽은 같은 파일의 쪽 텍스트를 다시 쓰고, 결과는 직접 읽은 것과 같다."""
    from bid_review import pdf_chars
    from bid_review.rag import past_projects as pp
    pdf_chars._PLAIN_TEXTS.clear()
    direct = pp._rfp_pages(WATER, 24)
    RFPExtractor(load_settings())._extract_pages_uncached(WATER)
    opened = {"n": 0}
    import pdfplumber
    original = pdfplumber.open

    def counting(*args, **kwargs):
        opened["n"] += 1
        return original(*args, **kwargs)
    pdfplumber.open = counting
    try:
        reused = pp._rfp_pages(WATER, 24)
    finally:
        pdfplumber.open = original
    assert reused == direct and opened["n"] == 0, "같은 텍스트, PDF를 다시 열지 않음"
    other = Path(tempfile.mkdtemp()) / "changed.pdf"
    other.write_bytes(WATER.read_bytes() + b"\n%changed")
    assert pdf_chars.recalled_plain_texts(pdf_chars.file_sha256(other)) is None, "내용이 다르면 재사용하지 않음"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("performance cache OK")
