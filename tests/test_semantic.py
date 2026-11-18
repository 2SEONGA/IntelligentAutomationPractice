"""M4 의미 검색 (가짜 임베딩, 네트워크 없음): `python tests/test_semantic.py`."""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bid_review.rag.semantic import SemanticUnavailable, build_semantic_index, search_semantic  # noqa: E402

AXES = ("예산", "마감", "공동")  # 가짜 임베딩: 단어 등장 여부를 축으로 쓰는 3차원 벡터


def fake_embed(texts):
    return [[1.0 if word in text else 0.0 for word in AXES] for text in texts]


def corpus(folder: Path) -> Path:
    rows = [("a", "사업예산 1억원", "doc1", "N1", "rfp_pdf"), ("b", "제안서 마감 10월", "doc1", "N1", "rfp_pdf"),
            ("c", "공동수급 불허", "doc2", "N1", "notice_pdf"), ("d", "예산 산출 서식", "doc3", "N2", "rfp_pdf")]
    path = folder / "chunks.jsonl"
    path.write_text("\n".join(json.dumps({"chunk_id": cid, "text": text, "metadata": {
        "document_id": doc, "related_notice_no": no, "document_type": kind, "section": "", "page": 1}},
        ensure_ascii=False) for cid, text, doc, no, kind in rows) + "\n", encoding="utf-8")
    return path


def test_rank_filter_and_stale_index() -> None:
    folder = Path(tempfile.mkdtemp())
    try:
        search_semantic("예산", embed=fake_embed, rag_dir=folder)
    except SemanticUnavailable:
        pass
    else:
        raise AssertionError("색인이 없으면 SemanticUnavailable")
    chunks = corpus(folder)
    meta = build_semantic_index(chunks, folder, embed=fake_embed, model="fake")
    assert meta["count"] == 4 and meta["dimensions"] == 3

    hits = search_semantic("예산 얼마", embed=fake_embed, rag_dir=folder)
    assert [h.chunk_id for h in hits[:2]] == ["a", "d"] and hits[0].metadata["semantic_score"] == 1.0
    assert [h.chunk_id for h in search_semantic("예산", document_id="doc1", embed=fake_embed, rag_dir=folder)][0] == "a"
    assert [h.chunk_id for h in search_semantic("공동", notice_no="N1", document_type="notice_pdf",
                                                embed=fake_embed, rag_dir=folder)] == ["c"]
    assert hits[0].rank < hits[-1].rank, "rank는 bm25처럼 낮을수록 좋음"

    chunks.write_text(chunks.read_text(encoding="utf-8") + "\n", encoding="utf-8")  # 코퍼스 변경
    try:
        search_semantic("예산", embed=fake_embed, rag_dir=folder)
    except SemanticUnavailable as exc:
        assert "오래" in str(exc)
    else:
        raise AssertionError("코퍼스가 바뀌면 행-chunk 대응이 틀어지므로 거부")


def test_api_failure_is_reported_without_details() -> None:
    folder = Path(tempfile.mkdtemp())
    build_semantic_index(corpus(folder), folder, embed=fake_embed, model="fake")

    def broken(texts):
        raise RuntimeError("Incorrect API key provided: sk-secret")
    try:
        search_semantic("예산", embed=broken, rag_dir=folder)
    except SemanticUnavailable as exc:
        assert "RuntimeError" in str(exc) and "sk-secret" not in str(exc)
    else:
        raise AssertionError("API 실패는 SemanticUnavailable로 바꿔 키워드 검색만 쓰게 함")


def test_company_semantic_scores_cache_and_failure() -> None:
    """M21: 회사 자료 레코드 벡터 캐시·변경 시 재계산·API 실패 처리, 요구사항 질의 정리."""
    from bid_review.rag.company_retriever import TARGET_COMPANY, clean_requirement_query, company_semantic_scores
    folder = Path(tempfile.mkdtemp())
    company = folder / "company"
    records = [{"id": "R1", "title": "예산 관리 시스템", "keywords": [], "excerpt": ""},
               {"id": "R2", "title": "공동 플랫폼", "keywords": [], "excerpt": ""}]
    company.write_text(json.dumps({"company_name": TARGET_COMPANY, "records": records, "sources": []}, ensure_ascii=False))
    calls = []

    def counting(texts):
        calls.append(len(texts))
        return fake_embed(texts)
    scores, ids = company_semantic_scores(["예산", "공동"], company, embed=counting, cache_dir=folder)
    assert ids == ["R1", "R2"] and scores[0][0] == 1.0 and scores[1][1] == 1.0 and scores[0][1] == 0.0
    company_semantic_scores(["예산"], company, embed=counting, cache_dir=folder)
    assert calls == [2, 2, 1], "두 번째 호출은 레코드 벡터 캐시 재사용(질의만 임베딩)"
    records.append({"id": "R3", "title": "마감 관리", "keywords": [], "excerpt": ""})
    company.write_text(json.dumps({"company_name": TARGET_COMPANY, "records": records, "sources": []}, ensure_ascii=False))
    _, ids = company_semantic_scores(["마감"], company, embed=counting, cache_dir=folder)
    assert ids == ["R1", "R2", "R3"] and calls[-2] == 3, "회사 JSON이 바뀌면 레코드 벡터 재계산"

    def broken(texts):
        raise RuntimeError("key sk-secret")
    try:
        company_semantic_scores(["예산"], company, embed=broken, cache_dir=Path(tempfile.mkdtemp()))
    except SemanticUnavailable as exc:
        assert "sk-secret" not in str(exc)
    else:
        raise AssertionError("API 실패는 SemanticUnavailable → 키워드 후보만 사용")
    assert clean_requirement_query("요구사항 고유번호 | SFR-001 요구사항 명칭 | GIS 표 행: 관리") == "SFR-001 GIS 관리"


def test_administrative_clause_detection() -> None:
    """M22: 행정 조항은 회사 자료 검색에서 제외, 요구사항 정의표 ID와 기술 기능은 유지."""
    from bid_review.rag.company_retriever import administrative_clause as clause
    assert clause("TECH-014", "1. 본인은 사업수행 중 취득한 모든 자료를 반납 및 파기 하였으며") == "보안서약서 문구"
    assert clause("TECH-071", "3. 나는 업무상 알게 된 개인정보 및 일체의 내용에 관한 비밀을 유지하고") == "보안서약서 문구"
    assert clause("TECH-076", "3. 당사는 청운대학교의 사전 동의 없이 내부정보 및 기타") == "보안서약서 문구"
    assert clause("TECH-008", "라. 공급자는 지식재산권의 활용을 위하여 SW산출물의 반출을 요청") == "지식재산권·산출물 귀속"
    assert clause("TECH-012", "가. 합병 : 소멸 업체의 실적을 존속 또는 신설 업체의 실적에 합산") == "실적 인정 규칙"
    assert clause("TECH-003", "「소프트웨어진흥법」 제58조 및 같은법 시행령 제53조의 규정에 의거 소프트웨어사업자 등록을 필한 업체").startswith("참가자격")
    assert clause("QUR-002", "시스템 유지보수성, 확장성 및 산출물 완성도 관리 ... 산출물의 반출") is None, "정의표 ID는 제외하지 않음"
    assert clause("TECH-029", "- 검색된 자료에 대한 승인/반려 과정을 통한 반출 기능") is None, "기능 설명은 유지"
    assert clause("TECH-009", "2. 유사분야 인정 범위 인정 유사분야 상수관망 관련 시스템") is None, "유사 실적 범위는 회사 실적과 대조 대상"


def test_similar_projects_search_and_awards() -> None:
    """M23: 과거 사업 유사도 검색(임계값·현재 공고 제외·재공고 표시·색인 불일치 거부)과 낙찰 조회 기간 분할."""
    import hashlib
    from datetime import date
    import numpy as np
    import bid_review.rag.past_projects as pp
    from bid_review.rag.semantic import _normalise
    folder = Path(tempfile.mkdtemp())
    projects = [{"notice_id": "P1-000", "related_notice_ids": ["P1-000", "P1-001"], "title": "예산 마감", "buyer": "A", "award": None, "profile": "예산 마감"},
                {"notice_id": "P2-000", "related_notice_ids": ["P2-000"], "title": "예산", "buyer": "B", "award": {"bidwinnrNm": "X"}, "profile": "예산"},
                {"notice_id": "P3-000", "related_notice_ids": ["P3-000"], "title": "공동", "buyer": "C", "award": None, "profile": "공동"}]
    payload = json.dumps(projects, ensure_ascii=False)
    (folder / "past_projects.json").write_text(payload, encoding="utf-8")
    np.save(folder / "past_projects.npy", _normalise(np.asarray(fake_embed([p["profile"] for p in projects]), dtype=np.float32)))
    (folder / "past_projects_meta.json").write_text(json.dumps({"model": "fake", "profiles_sha256": hashlib.sha256(payload.encode()).hexdigest()}))
    hits = pp.search_similar_projects("예산 마감", embed=fake_embed, index_dir=folder)
    assert [h["notice_id"] for h in hits] == ["P1-000", "P2-000"], "공동(유사도 0)은 임계값 미만"
    assert hits[0]["same_project_suspected"] and not hits[1]["same_project_suspected"] and "profile" not in hits[0]
    assert [h["notice_id"] for h in pp.search_similar_projects("예산 마감", exclude_notice_no="P1", embed=fake_embed, index_dir=folder)] == ["P2-000"]
    by_title = pp.search_similar_projects("예산 마감", exclude_notice_no="P1", query_title="예산", embed=fake_embed, index_dir=folder)
    assert by_title[0]["same_project_suspected"], "유사도가 낮아도 사업명이 같으면 재공고 의심"
    (folder / "past_projects.json").write_text(payload + " ", encoding="utf-8")
    try:
        pp.search_similar_projects("예산", embed=fake_embed, index_dir=folder)
    except SemanticUnavailable:
        pass
    else:
        raise AssertionError("프로필 파일과 벡터가 어긋나면 거부")

    class AwardSession:
        def __init__(self):
            self.spans = []

        def get(self, url, params=None, timeout=None):
            from datetime import datetime
            span = (datetime.strptime(params["inqryEndDt"][:8], "%Y%m%d") - datetime.strptime(params["inqryBgnDt"][:8], "%Y%m%d")).days
            self.spans.append(span)
            class R:
                def json(inner):
                    if span > 6:
                        return {"nkoneps.com.response.ResponseError": {"header": {"resultCode": "07"}}}
                    if params["bidNtceNm"] == "오류":
                        return {"nkoneps.com.response.ResponseError": {"header": {"resultCode": "99"}}}
                    return {"response": {"header": {"resultCode": "00"}, "body": {"items": [
                        {"bidNtceNo": "P2", "bidNtceOrd": "0", "bidwinnrNm": "X", "sucsfbidAmt": "100"}]}}}
            return R()
    session = AwardSession()
    awards, failed = pp.fetch_awards(["정상", "오류"], date(2026, 10, 1), date(2026, 10, 20), session=session)
    assert max(session.spans) <= 6 and "P2-000" in awards and awards["P2-000"]["bidwinnrNm"] == "X"
    assert failed == 3, "오류 응답 구간은 '낙찰 없음'이 아니라 실패로 센다 (20일 → 7일 구간 3개)"

    class PagedAwardSession:
        """M28: 키워드 없는 낙찰 조회는 한 주가 999건을 넘는다 — totalCount까지 페이지를 따라간다."""
        def __init__(self):
            self.pages = []

        def get(self, url, params=None, timeout=None):
            page = params["pageNo"]
            self.pages.append(page)
            class R:
                def json(inner):
                    return {"response": {"header": {"resultCode": "00"}, "body": {"totalCount": 1500, "items": [
                        {"bidNtceNo": f"Q{page}", "bidNtceOrd": "000", "bidwinnrNm": "Y"}]}}}
            return R()
    paged = PagedAwardSession()
    awards, failed = pp.fetch_awards([""], date(2026, 10, 1), date(2026, 10, 7), session=paged)
    assert paged.pages == [1, 2] and {"Q1-000", "Q2-000"} <= set(awards) and failed == 0, "두 번째 페이지도 읽음"


def test_past_collection_quota_resume_and_spread() -> None:
    """M24: 할당량 오류는 'PDF 없음'과 구분해 멈추고, 확인한 공고는 다시 조회하지 않으며, 주 단위로 고르게 고른다."""
    from datetime import date
    import bid_review.rag.past_projects as pp
    from bid_review.web import notices
    assert pp._spread_by_week(["a1", "a2", "b1"], {"a1": "2026-11-10", "a2": "2026-11-11", "b1": "2026-10-01"}) == ["a2", "b1", "a1"]

    weeks = [f"2026-{m:02d}-{d:02d}" for m, d in ((11, 10), (11, 3), (10, 27), (10, 20))]
    items = [{"bidNtceNo": f"N{i}", "bidNtceOrd": "000", "bidNtceDt": weeks[i % 4], "pubPrcrmntLrgClsfcNm": "ICT 서비스"}
             for i in range(8)]
    state = {"quota_after": 3, "calls": 0}

    def fake_info(no, order, session=None):
        state["calls"] += 1
        if state["calls"] > state["quota_after"]:
            raise notices.NoticeError("나라장터 첨부정보 API 오류 22: 일일 서비스 요청제한 횟수 초과 에러")
        kind = "rfp" if int(no[1:]) % 2 == 0 else "other"
        return {"attachments": [{"name": f"{no}.pdf", "url": "https://x", "kind": kind}],
                "api_operation": "op", "retrieved_at": "t"}

    saved_fetch, saved_info, saved_dl, saved_dir, saved_awards = (
        notices._fetch, notices.fetch_notice_attachment_info, notices._download, pp.PAST_DIR, pp.fetch_awards)
    try:
        keywords_used = set()
        notices._fetch = lambda session, keyword, b, e: keywords_used.add(keyword) or items
        notices.fetch_notice_attachment_info = fake_info
        notices._download = lambda session, att, target, **k: target.write_bytes(b"%PDF-1.4") or True
        pp.PAST_DIR = Path(tempfile.mkdtemp())
        pp.fetch_awards = lambda *a, **k: ({}, 0)
        first = pp.collect_past_projects(days=30, max_projects=10, today=date(2026, 11, 17), log=lambda *_: None)
        assert first["stopped"] == "api_quota_exhausted" and first["api_error"] == 0, "할당량 오류는 api_error/no_pdf로 세지 않음"
        assert first["saved"] + first["no_rfp"] == 3
        state.update(quota_after=10**6, calls=0)
        second = pp.collect_past_projects(days=30, max_projects=10, today=date(2026, 11, 17), log=lambda *_: None)
        assert second["already_saved"] + second["already_checked"] == 3 and state["calls"] == 5, "이전 실행분은 재조회 없음"
        assert second["stopped"] is None and len(list(pp.PAST_DIR.glob("*/meta.json"))) == 4
        assert keywords_used == {""}, "M28: 공고 수집 키워드가 비면(현재 설정) 키워드 없이 전체 조회"
    finally:
        notices._fetch, notices.fetch_notice_attachment_info, notices._download, pp.PAST_DIR, pp.fetch_awards = (
            saved_fetch, saved_info, saved_dl, saved_dir, saved_awards)


def test_past_collection_order_and_contract_exclusion() -> None:
    """M29: 수의계약은 후보·색인에서 빠지고(첨부 조회 없음), 주 안에서는 사업금액 큰 순, 금액 모르면 맨 뒤."""
    from datetime import date
    import shutil
    import bid_review.rag.past_projects as pp
    from bid_review.settings import PROJECT_ROOT
    from bid_review.web import notices
    dates = {"s": "2026-11-10", "b": "2026-11-11", "u": "2026-11-12", "old": "2026-10-01"}
    assert pp._spread_by_week(list(dates), dates, {"s": 100, "b": 5_000, "u": None, "old": 1}) == ["b", "old", "s", "u"]
    assert pp._spread_by_week(["a1", "a2", "b1"], {"a1": "2026-11-10", "a2": "2026-11-11", "b1": "2026-10-01"}) == ["a2", "b1", "a1"], \
        "금액이 없으면 이전처럼 최신순"

    def notice(no, contract="일반경쟁", amount="300000000"):
        return {"bidNtceNo": no, "bidNtceOrd": "000", "bidNtceNm": f"{no} 사업", "dminsttNm": "기관", "bidNtceDt": "2026-11-10", "pubPrcrmntLrgClsfcNm": "ICT 서비스",
                "cntrctCnclsMthdNm": contract, "sucsfbidMthdNm": "협상에의한계약", "asignBdgtAmt": amount}
    looked: list[str] = []
    saved = (notices._fetch, notices.fetch_notice_attachment_info, notices._download, pp.PAST_DIR, pp.fetch_awards,
             pp.INDEX_DIR, pp.project_profile)
    work = Path(tempfile.mkdtemp(dir=PROJECT_ROOT / "outputs"))  # rfp_path는 PROJECT_ROOT 기준 상대경로
    try:
        notices._fetch = lambda session, keyword, b, e: [notice("SMALL"), notice("BIG", amount="9000000000"), notice("SOLE", "수의계약")]
        notices.fetch_notice_attachment_info = lambda no, order, session=None: looked.append(no) or {
            "attachments": [{"name": f"{no}.pdf", "url": "https://www.g2b.go.kr/x", "kind": "rfp"}], "api_operation": "op", "retrieved_at": "t"}
        notices._download = lambda session, att, target, **k: target.write_bytes(b"%PDF-1.4") or True
        pp.PAST_DIR, pp.INDEX_DIR = work / "past", work / "rag"
        pp.fetch_awards = lambda *a, **k: ({}, 0)
        result = pp.collect_past_projects(days=7, max_projects=10, today=date(2026, 11, 17), log=lambda *_: None)
        assert looked == ["BIG", "SMALL"] and result["skipped_contract"] == 1, (looked, result)
        sole = pp.PAST_DIR / "SOLE-000"  # 이미 받아 둔 수의계약 공고(M23 수집분)
        sole.mkdir()
        (sole / "rfp.pdf").write_bytes(b"%PDF-1.4")
        (sole / "meta.json").write_text(json.dumps({**notice("SOLE", "수의계약"), "notice_id": "SOLE-000", "bidNtceNm": "상용SW 3자단가"}), encoding="utf-8")
        pp.project_profile = lambda path, title="", buyer="", max_pages=12: f"사업 {path.parent.name}"
        built = pp.build_past_project_index(embed=fake_embed, log=lambda *_: None)
        assert built["excluded_contract"] == 1 and built["projects"] == 2, built
        assert json.loads((pp.INDEX_DIR / "past_projects_meta.json").read_text(encoding="utf-8"))["excluded_contract"] == 1
        assert (sole / "meta.json").is_file(), "폴더는 지우지 않음"
    finally:
        (notices._fetch, notices.fetch_notice_attachment_info, notices._download, pp.PAST_DIR, pp.fetch_awards,
         pp.INDEX_DIR, pp.project_profile) = saved
        shutil.rmtree(work, ignore_errors=True)


def test_threshold_evaluation() -> None:
    """M25: 담당자 표시로 임계값별 정밀도·재현율과 최적 F1 기준을 계산."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from evaluate_thresholds import evaluate, parse_labels
    labels = parse_labels("[M25 평가] E001:2 E002:0 E003:1 E004:0 E999:7")
    assert labels == {"E001": 2, "E002": 0, "E003": 1, "E004": 0}, "형식에 맞는 표시만 읽음"
    items = [{"id": "E001", "kind": "t15", "score": 0.50}, {"id": "E002", "kind": "t15", "score": 0.45},
             {"id": "E003", "kind": "t15", "score": 0.42}, {"id": "E004", "kind": "t15", "score": 0.36}]
    report = evaluate(items, labels)["t15"]
    assert report["labelled"] == 4 and report["relevant"] == 2
    assert report["current"] == {"threshold": 0.4, "kept": 3, "precision": 0.667, "recall": 1.0, "f1": 0.8}
    assert report["best_f1"]["threshold"] == 0.42 and report["best_f1"]["f1"] == 0.8 and report["best_f1"]["precision"] == 0.667


def test_profile_skips_table_of_contents_lines() -> None:
    """M25: 사업 프로필에서 목차 점선 줄은 버리고 본문 줄은 남긴다."""
    from bid_review.rag.past_projects import TOC_LEADER
    for toc in ("사업개요 ································ 1", "2. 추진배경 및 필요성 .......... 3", "Ⅱ. 제안 요청내용 ··· 5"):
        assert TOC_LEADER.search(toc), toc
    for body in ("다. 사업예산 : 300,000,000 원(부가가치세 포함)", "○ 사업기간 : 계약 후 ~ 2030년 12월 31일 (5년)", "- 모바일 학사정보시스템 구성"):
        assert not TOC_LEADER.search(body), body


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("semantic OK")
