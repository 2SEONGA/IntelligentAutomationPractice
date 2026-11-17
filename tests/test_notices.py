"""공고 수집·저장·첨부 검사 (가짜 응답, 네트워크 없음): `python tests/test_notices.py`."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bid_review.web import notices, runs  # noqa: E402
from bid_review.web.notices import NoticeError  # noqa: E402

NOW = datetime(2026, 10, 27, 12, 0)
PDF = b"%PDF-1.4 test"
# 실시간 now를 쓰는 앱 테스트도 있으므로 마감일은 항상 30일 뒤 (고정 날짜는 마감 임박/지남으로 바뀜)
FUTURE_CLOSE = (max(datetime.now(), NOW) + timedelta(days=30)).strftime("%Y-%m-%d 10:00:00")


class Response:
    def __init__(self, payload=None, content=b"", status=200, headers=None):
        self.payload, self.content, self.status_code, self.headers = payload, content, status, headers or {}
        self.read = 0  # bytes handed out by iter_content (a streamed download must stop early)

    def iter_content(self, size):
        for start in range(0, len(self.content), size):
            self.read += size
            yield self.content[start:start + size]

    def close(self):
        pass

    def json(self):
        if self.payload is None:
            raise ValueError("not json")
        return self.payload


def item(no: str, name: str, amount: int = 5_000_000_000, **extra) -> dict:
    return {"bidNtceNo": no, "bidNtceOrd": "000", "bidNtceNm": name, "ntceInsttNm": "기관", "dminsttNm": "기관",
            "asignBdgtAmt": str(amount), "presmptPrce": "", "bidClseDt": FUTURE_CLOSE,
            "pubPrcrmntLrgClsfcNm": "ICT 서비스", "cntrctCnclsMthdNm": "일반경쟁", "sucsfbidMthdNm": "협상에의한계약",
            "bidNtceDtlUrl": f"https://g2b.test/{no}", **extra}


class FakeSession:
    """키워드별로 준비된 공고를 돌려주고, MAX_WINDOW_DAYS 넘는 구간은 실제 API처럼 07 오류."""

    def __init__(self, by_keyword: dict[str, list[dict]], files: dict[str, bytes] | None = None, fail: bool = False):
        self.by_keyword, self.files, self.fail, self.calls = by_keyword, files or {}, fail, []

    def get(self, url, timeout=None, **kwargs):
        if url in self.files:
            return Response(content=self.files[url])
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.calls.append(query)
        if self.fail:
            return Response({"nkoneps.com.response.ResponseError": {"header": {"resultCode": "30", "resultMsg": "등록되지 않은 서비스키"}}})
        span = datetime.strptime(query["inqryEndDt"], "%Y%m%d%H%M") - datetime.strptime(query["inqryBgnDt"], "%Y%m%d%H%M")
        if span.days >= notices.MAX_WINDOW_DAYS:
            return Response({"nkoneps.com.response.ResponseError": {"header": {"resultCode": "07", "resultMsg": "입력범위값 초과 에러"}}})
        found = self.by_keyword.get(query.get("bidNtceNm", ""), [])  # parse_qs drops blank → "" means full search
        return Response({"response": {"header": {"resultCode": "00"}, "body": {"totalCount": len(found), "items": found}}})


def sandbox() -> None:
    runs.OUTPUTS = Path(tempfile.mkdtemp())
    notices.KEY_OVERRIDE = "test-key-123456"


def windows(days: int) -> int:
    return -(-(days + 1) // notices.MAX_WINDOW_DAYS)  # 시작·끝 날짜 포함


def test_collect_merges_and_judges() -> None:
    sandbox()
    shared = item("A1", "차세대 정보시스템 구축")
    session = FakeSession({"차세대": [shared, item("B2", "차세대 소형", amount=100_000_000)], "정보시스템": [shared]})
    summary = notices.collect(30, ["차세대", "정보시스템"], session=session, now=NOW)
    assert summary["collected"] == 2 and summary["total"] == 2
    assert summary["by_verdict"] == {"검토 대상": 1, "확인 필요": 0, "제외": 1}
    stored = notices.load_notices()
    assert stored["notices"]["A1-000"]["verdict"] == "검토 대상" and stored["runs"][-1]["count"] == 2
    assert "test-key" not in notices.STORE().read_text(encoding="utf-8"), "키는 저장하지 않음"
    assert len(session.calls) == 2 * windows(30)


def test_empty_keywords_collect_everything_and_skip_non_ict() -> None:
    """M27: 빈 키워드 = 전체 조회 1회/구간. ICT 분야가 아닌 공고는 건수만 세고 저장하지 않는다."""
    sandbox()
    building = item("G7", "청사 시설관리 용역", pubPrcrmntLrgClsfcNm="기술용역")
    small_ict = item("H8", "홈페이지 유지관리", amount=100_000_000)
    session = FakeSession({"": [item("A1", "2027년 공통기반 유지관리"), small_ict, building]})
    summary = notices.collect(30, [], session=session, now=NOW)
    assert len(session.calls) == windows(30) and all("bidNtceNm" not in c for c in session.calls)
    assert summary["collected"] == 3 and summary["skipped_non_ict"] == 1 and summary["total"] == 2
    assert summary["by_verdict"] == {"검토 대상": 1, "확인 필요": 0, "제외": 2}, "미저장 공고도 제외 건수에 포함"
    stored = notices.load_notices()
    assert "G7-000" not in stored["notices"] and stored["notices"]["H8-000"]["verdict"] == "제외", "ICT 제외는 HITL-2용으로 저장"
    assert stored["runs"][-1]["keywords"] == [] and stored["runs"][-1]["skipped_non_ict"] == 1


def test_non_ict_with_human_review_is_kept() -> None:
    sandbox()
    building = item("J9", "정보시스템 실 이전 공사 감리", pubPrcrmntLrgClsfcNm="기술용역")
    session = FakeSession({"정보시스템": [building]})
    store = notices.load_notices()
    store["notices"]["J9-000"] = {"review": {"include": True, "reason": "확인", "role": "staff", "at": "x"}, "case_folder": None}
    notices._save(store)
    notices.collect(30, ["정보시스템"], session=session, now=NOW)
    kept = notices.load_notices()["notices"]["J9-000"]
    assert kept["judged"]["verdict"] == "제외" and kept["review"]["include"] is True, "담당자가 손댄 공고는 계속 갱신"


def test_long_period_is_split() -> None:
    sandbox()
    session = FakeSession({"AI": [item("C3", "AI 플랫폼")]})
    notices.collect(60, ["AI"], session=session, now=NOW)
    assert len(session.calls) == windows(60) > 1, "긴 기간은 MAX_WINDOW_DAYS 이하 구간으로 분할"


def test_recollect_preserves_review_and_case() -> None:
    sandbox()
    held = item("D4", "제한경쟁 정보시스템", cntrctCnclsMthdNm="제한경쟁")
    session = FakeSession({"정보시스템": [held]})
    notices.collect(30, ["정보시스템"], session=session, now=NOW)
    assert notices.load_notices()["notices"]["D4-000"]["verdict"] == "확인 필요"
    notices.set_review("D4-000", True, "공고문 확인 결과 참여 가능", "staff")
    notices.attach_case("D4-000", "20261027_120000_abcdef")
    notices.collect(30, ["정보시스템"], session=session, now=NOW)
    stored = notices.load_notices()["notices"]["D4-000"]
    assert stored["review"]["include"] is True and stored["review"]["role"] == "staff"
    assert stored["case_folder"] == "20261027_120000_abcdef" and stored["verdict"] == "검토 대상"
    assert stored["judged"]["verdict"] == "확인 필요", "규칙 판정 원본은 따로 보존"


def test_collect_error_keeps_store() -> None:
    sandbox()
    notices.collect(30, ["AI"], session=FakeSession({"AI": [item("E5", "AI")]}), now=NOW)
    before = notices.STORE().read_text(encoding="utf-8")
    try:
        notices.collect(30, ["AI"], session=FakeSession({}, fail=True), now=NOW)
    except NoticeError as exc:
        assert "등록되지 않은 서비스키" in str(exc)
    else:
        raise AssertionError("오류 응답이 성공으로 처리됨")
    assert notices.STORE().read_text(encoding="utf-8") == before


def test_review_requires_reason() -> None:
    sandbox()
    notices.collect(30, ["AI"], session=FakeSession({"AI": [item("F6", "AI")]}), now=NOW)
    for args in (("F6-000", True, "  ", "staff"), ("없는공고", True, "사유", "staff")):
        try:
            notices.set_review(*args)
        except (ValueError, KeyError):
            continue
        raise AssertionError(f"거부되지 않음: {args}")


def test_classify() -> None:
    kinds = notices.classify(["(공고문)x.pdf", "제안요청서.pdf", "과업내용서.hwpx", "붙임1.pdf", "입찰공고.PDF"])
    assert kinds == {"(공고문)x.pdf": "notice", "제안요청서.pdf": "rfp", "과업내용서.hwpx": "other",
                     "붙임1.pdf": "pdf", "입찰공고.PDF": "notice"}
    # 실제 나라장터 첨부 이름(2026-10-27 수집): '공고서'·'재공고서'도 공고문이다
    real = notices.classify(["공고서_국가_일반_구축_공동_설명회_수요기관평가.pdf", "재공고서(국가SW,80억미만,오프,차등)AI 플랫폼.pdf",
                             "(공고서)2027년 관세정보시스템 유지관리 사업_261015.pdf"])
    assert set(real.values()) == {"notice"}, real


def test_download_skips_non_pdf() -> None:
    sandbox()
    files = {"https://www.g2b.go.kr/f/1": PDF, "https://www.g2b.go.kr/f/2": b"<html>error</html>", "https://www.g2b.go.kr/f/3": b"PK..."}
    attached = item("G7", "정보시스템", ntceSpecFileNm1="(공고문)G7.pdf", ntceSpecDocUrl1="https://www.g2b.go.kr/f/1",
                    ntceSpecFileNm2="제안요청서.pdf", ntceSpecDocUrl2="https://www.g2b.go.kr/f/2",
                    ntceSpecFileNm3="과업.hwpx", ntceSpecDocUrl3="https://www.g2b.go.kr/f/3")
    session = FakeSession({"정보시스템": [attached]}, files=files)
    notices.collect(30, ["정보시스템"], session=session, now=NOW)
    stored = notices.load_notices()["notices"]["G7-000"]
    assert [a["kind"] for a in stored["attachments"]] == ["notice", "rfp", "other"]
    folder = Path(tempfile.mkdtemp())
    result = notices.fetch_attachments("G7-000", folder, session=session)
    assert result == {"rfp": False, "notice": True, "skipped": ["제안요청서.pdf"], "rfp_guessed": False}
    assert (folder / "notice.pdf").read_bytes() == PDF and not (folder / "rfp.pdf").exists()


def test_first_plain_pdf_becomes_rfp() -> None:
    sandbox()
    attached = item("H8", "정보시스템", ntceSpecFileNm1="붙임1 사업설명.pdf", ntceSpecDocUrl1="https://www.g2b.go.kr/f/9")
    session = FakeSession({"정보시스템": [attached]}, files={"https://www.g2b.go.kr/f/9": PDF})
    notices.collect(30, ["정보시스템"], session=session, now=NOW)
    folder = Path(tempfile.mkdtemp())
    fetched = notices.fetch_attachments("H8-000", folder, session=session)
    assert fetched["rfp"] is True and fetched["rfp_guessed"] is True, "이름 단서 없는 PDF를 RFP로 쓴 건 알려야 함"


def test_missing_key() -> None:
    sandbox()
    notices.KEY_OVERRIDE = ""
    saved = notices.ENV_FILE
    notices.ENV_FILE = Path(tempfile.mkdtemp()) / "none.env"
    try:
        notices.collect(30, ["AI"], session=FakeSession({}), now=NOW)
    except NoticeError as exc:
        assert "키" in str(exc)
    else:
        raise AssertionError("키 없이 수집됨")
    finally:
        notices.ENV_FILE = saved


def test_key_error_xml_shows_reason() -> None:
    sandbox()
    import requests

    class XmlSession:
        def get(self, url, timeout=None, **kwargs):
            r = Response()
            r.status_code, r.text = 403, "<OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>"
            def bad_json():
                raise requests.exceptions.JSONDecodeError("Expecting value", r.text, 0)
            r.json = bad_json
            return r
    try:
        notices.collect(30, ["AI"], session=XmlSession(), now=NOW)
    except NoticeError as exc:
        assert "SERVICE_KEY_IS_NOT_REGISTERED_ERROR" in str(exc) and "연결" not in str(exc), str(exc)
    else:
        raise AssertionError("키 오류가 성공으로 처리됨")


def test_attachment_info_and_byte_comparison() -> None:
    """M15: 첨부파일정보 오퍼레이션 해석 + 저장 PDF/API 첨부 SHA-256 비교 분기."""
    sandbox()
    rows = [
        {"bidNtceNo": "R1", "bidNtceOrd": "000", "atchDocDivNm": "공고서", "fileNm": "입찰공고문.pdf", "fileUrl": "https://www.g2b.go.kr/f/n"},
        {"bidNtceNo": "R1", "bidNtceOrd": "000", "atchDocDivNm": "제안요청서", "fileNm": "과업.pdf", "fileUrl": "https://www.g2b.go.kr/f/r"},
        {"bidNtceNo": "R1", "bidNtceOrd": "001", "atchDocDivNm": "공고서", "fileNm": "정정.pdf", "fileUrl": "https://www.g2b.go.kr/f/x"},
        {"bidNtceNo": "R1", "bidNtceOrd": "000", "atchDocDivNm": "공고서(변환본)", "fileNm": "제안요청서 수정.pdf", "fileUrl": "https://www.g2b.go.kr/f/c"},
        {"bidNtceNo": "R1", "bidNtceOrd": "000", "atchDocDivNm": "공고서", "fileNm": "붙임1.pdf", "fileUrl": "https://www.g2b.go.kr/f/a"},
    ]

    class AttachSession:
        def __init__(self, files):
            self.files, self.params = files, None

        def get(self, url, params=None, timeout=None, **kwargs):
            if params is not None:
                self.params = params
                return Response({"response": {"header": {"resultCode": "00"}, "body": {"items": rows}}})
            if url not in self.files:
                raise notices.requests.ConnectionError()
            return Response(content=self.files[url])

    session = AttachSession({"https://www.g2b.go.kr/f/n": PDF})
    info = notices.fetch_notice_attachment_info("R1", "0", session=session)
    assert session.params["inqryDiv"] == "2" and session.params["bidNtceOrd"] == "000" and session.params["bsnsDivCd"] == "5"
    assert info["status"] == "found" and [a["kind"] for a in info["attachments"]] == ["notice", "rfp", "rfp", "notice"], \
        "다른 차수 제외 · 파일명이 문서구분보다 우선 · 이름이 모호할 때만 문서구분 사용"
    assert "test-key" not in json.dumps(info), "키는 결과에 남기지 않음"

    local = Path(tempfile.mkdtemp()) / "notice.pdf"
    compare = lambda files, path=local: notices.compare_notice_pdf_bytes(info["attachments"], path, session=AttachSession(files))  # noqa: E731
    assert compare({}, None)["status"] == "local_pdf_missing"
    local.write_bytes(PDF)
    assert compare({"https://www.g2b.go.kr/f/n": PDF})["status"] == "byte_match"
    changed = compare({"https://www.g2b.go.kr/f/n": PDF + b" changed"})
    assert changed["status"] == "byte_mismatch" and changed["local_sha256"] != changed["g2b_sha256"]
    assert compare({"https://www.g2b.go.kr/f/n": b"<html>"})["status"] == "download_not_pdf"
    assert compare({})["status"] == "download_unavailable"
    assert notices.compare_notice_pdf_bytes([], local)["status"] == "g2b_notice_attachment_missing"


def test_attachment_download_is_g2b_only_and_capped() -> None:
    """M29 보안 보완: https://*.g2b.go.kr만, 리다이렉트도 매번 확인, 50MB를 넘으면 받는 도중 중단."""
    class Hops:
        def __init__(self, routes):
            self.routes, self.urls = routes, []

        def get(self, url, timeout=None, stream=False, allow_redirects=True, **kwargs):
            assert stream and not allow_redirects, "스트리밍·리다이렉트 직접 확인"
            self.urls.append(url)
            return self.routes[url]

    ok = "https://www.g2b.go.kr/f/ok"
    assert notices.fetch_attachment_bytes(Hops({ok: Response(content=PDF)}), ok) == PDF
    for bad in ("http://www.g2b.go.kr/f/x", "https://evil.example/x", "https://g2b.go.kr.evil.example/x", "https://169.254.169.254/x"):
        try:
            notices.fetch_attachment_bytes(Hops({}), bad)
        except notices.DownloadRejected as exc:
            assert exc.args[0] == "invalid_attachment_url", bad
        else:
            raise AssertionError(f"허용하면 안 됨: {bad}")
    redirect = Hops({ok: Response(status=302, headers={"Location": "https://127.0.0.1/admin"})})
    try:
        notices.fetch_attachment_bytes(redirect, ok)
    except notices.DownloadRejected:
        assert redirect.urls == [ok], "다른 호스트로 가는 리다이렉트는 따라가지 않음"
    else:
        raise AssertionError("리다이렉트 대상도 확인해야 함")
    moved = "https://www.g2b.go.kr/f/moved"
    assert notices.fetch_attachment_bytes(Hops({ok: Response(status=302, headers={"Location": "/f/moved"}),
                                                moved: Response(content=PDF)}), ok) == PDF, "같은 사이트 리다이렉트는 허용"
    saved = notices.MAX_ATTACHMENT_BYTES
    try:
        notices.MAX_ATTACHMENT_BYTES = 1000
        declared = Response(content=PDF, headers={"Content-Length": "5000"})
        big = Response(content=b"%PDF-" + b"x" * 10_000_000)
        for response in (declared, big):
            try:
                notices.fetch_attachment_bytes(Hops({ok: response}), ok)
            except notices.DownloadRejected as exc:
                assert exc.args[0] == "download_too_large"
            else:
                raise AssertionError("크기 제한")
        assert declared.read == 0 and big.read < 200_000, "제한을 넘으면 더 받지 않음"
        target = Path(tempfile.mkdtemp()) / "rfp.pdf"
        assert not notices._download(Hops({ok: Response(content=b"%PDF-" + b"x" * 5000)}), {"url": ok}, target)
        assert not target.exists() and not list(target.parent.iterdir()), "실패하면 파일·임시 파일을 남기지 않음"
    finally:
        notices.MAX_ATTACHMENT_BYTES = saved
    local = Path(tempfile.mkdtemp()) / "notice.pdf"
    local.write_bytes(PDF)
    evil = notices.compare_notice_pdf_bytes([{"kind": "notice", "url": "https://evil.example/n", "name": "n.pdf"}], local, session=Hops({}))
    assert evil["status"] == "invalid_attachment_url"


def test_collect_prunes_expired_untouched_notices() -> None:
    """M29: 마감이 30일 넘게 지났고 담당자가 손대지 않은 공고만 수집 때 정리. 마감일을 모르면 남긴다."""
    sandbox()
    close = lambda days: (NOW - timedelta(days=days)).strftime("%Y-%m-%d 10:00:00")  # noqa: E731
    store = notices.load_notices()
    def old(no, days, **extra):
        return {"bidNtceNo": no, "judged": {"deadline": close(days) if days is not None else None}, "review": None,
                "case_folder": None, **extra}
    store["notices"] = {"OLD-000": old("OLD", 31), "RECENT-000": old("RECENT", 29), "NODATE-000": old("NODATE", None),
                        "REVIEWED-000": old("REVIEWED", 90, review={"include": False, "reason": "r", "role": "staff", "at": "x"}),
                        "CASE-000": old("CASE", 90, case_folder="20261001_000000_x")}
    notices._save(store)
    summary = notices.collect(30, ["AI"], session=FakeSession({"AI": [item("NEW", "AI 플랫폼"), item("GONE", "AI", bidClseDt=close(40))]}), now=NOW)
    kept = set(notices.load_notices()["notices"])
    assert kept == {"RECENT-000", "NODATE-000", "REVIEWED-000", "CASE-000", "NEW-000"}, kept
    assert summary["pruned"] == 2 and notices.load_notices()["runs"][-1]["pruned"] == 2, "OLD + 방금 받은 마감 40일 지난 GONE"


def test_change_history_and_routing_reason() -> None:
    """M17: 변경이력 해석(다른 공고 행 제외, 시간순) + 정정이 있으면 사람 검토 사유 생성."""
    from bid_review.rag.verification import change_history_reason
    sandbox()
    rows = [
        {"bidNtceNo": "R1", "bidNtceOrd": "001", "chgDt": "2026-10-12 09:00:00", "chgItemNm": "입찰마감일시",
         "bfchgVal": "2026/10/19 10:00", "afchgVal": "202610211000", "chgDataDivNm": "공고"},
        {"bidNtceNo": "R1", "bidNtceOrd": "001", "chgDt": "2026-10-11 09:00:00", "chgItemNm": "공고서", "chgDataDivNm": "공고"},
        {"bidNtceNo": "R9", "bidNtceOrd": "000", "chgDt": "2026-10-10 09:00:00", "chgItemNm": "개찰일시"},
    ]

    class HistorySession:
        def get(self, url, params=None, timeout=None):
            assert params["inqryDiv"] == "2" and params["bidNtceNo"] == "R1"
            return Response({"response": {"header": {"resultCode": "00"}, "body": {"items": rows}}})

    history = notices.fetch_notice_change_history("R1", session=HistorySession())
    assert history["status"] == "changes_found" and [c["chgItemNm"] for c in history["changes"]] == ["공고서", "입찰마감일시"]
    assert "test-key" not in json.dumps(history)
    reason = change_history_reason(history)
    assert reason and "2건" in reason and "입찰마감일시" in reason
    assert change_history_reason({"status": "no_changes", "changes": []}) is None
    assert change_history_reason({"status": "unavailable", "changes": []}) is None, "조회 실패는 기존 API 상태로 별도 표시"


def test_eligibility_api_crosscheck() -> None:
    """M19: 업종(면허) 제한·공동수급 방식을 RFP 참가자격 claim과 교차 확인."""
    from bid_review.rag.verification import VerificationStatus as S, VerifiedClaim, api_eligibility_claims
    sandbox()

    class LicenseSession:
        def get(self, url, params=None, timeout=None):
            rows = [{"bidNtceNo": "R1", "bidNtceOrd": "000", "lcnsLmtNm": "소프트웨어사업자(컴퓨터관련서비스사업)/1468", "lmtGrpNo": "1"},
                    {"bidNtceNo": "R1", "bidNtceOrd": "001", "lcnsLmtNm": "정보통신공사업/0036"}]
            return Response({"response": {"header": {"resultCode": "00"}, "body": {"items": rows}}})

    limits = notices.fetch_notice_license_limits("R1", "000", session=LicenseSession())
    assert [item["name"] for item in limits["limits"]] == ["소프트웨어사업자(컴퓨터관련서비스사업)/1468"], "다른 차수 제외"

    def rfp(*texts):
        return [VerifiedClaim(category="eligibility", field=f"condition_{i}", claim=t, status=S.CONFIRMED)
                for i, t in enumerate(texts, start=1)]

    record = {"bidNtceNo": "R1", "bidNtceOrd": "000", "indstrytyLmtYn": "Y", "license_limits": limits,
              "cmmnSpldmdMethdNm": "(없음)공동수급불허"}
    matched = {c.field: c for c in api_eligibility_claims(record, rfp(
        "소프트웨어사업자 (컴퓨터관련서비스사업) 등록을 필한 업체", "공동도급은 허용하지 않음"))}
    assert matched["g2b_license_limit"].status == S.CONFIRMED and "condition_1" in matched["g2b_license_limit"].checks[0].detail
    assert matched["g2b_joint_contract"].status == S.CONFIRMED

    missing = {c.field: c for c in api_eligibility_claims(record, rfp("나라장터 입찰참가자격 등록"))}
    assert missing["g2b_license_limit"].status == S.HUMAN_REVIEW and missing["g2b_joint_contract"].status == S.HUMAN_REVIEW

    conflict = {c.field: c for c in api_eligibility_claims(record, rfp("공동수급 허용 (구성원 3인 이내)"))}
    assert conflict["g2b_joint_contract"].status == S.CONFLICT

    failed = api_eligibility_claims({**record, "license_limits": {"status": "unavailable", "error": "연결 실패", "limits": []}}, rfp())
    assert failed[0].field == "g2b_license_limit" and failed[0].status == S.HUMAN_REVIEW
    assert api_eligibility_claims(None, rfp("x")) == []
    from_notice = {c.field: c for c in api_eligibility_claims(record, rfp("입찰참가자격 등록"), {
        "공고문 p.1": "아. 소프트웨어사업자(컴퓨터관련서비스사업)로 신고를 필한 업체\n사. 공동수급(도급 및 하도급), 분합 납품은 허용하지 않음"})}
    assert from_notice["g2b_license_limit"].status == S.CONFIRMED and "공고문 p.1" in from_notice["g2b_license_limit"].checks[0].detail
    assert from_notice["g2b_joint_contract"].status == S.CONFIRMED, "RFP에 없어도 공고문 조항으로 확인"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("notices OK")
