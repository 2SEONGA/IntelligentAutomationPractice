"""나라장터 클라이언트 회귀 검사 (네트워크 없음): `python tests/test_g2b.py`."""
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bid_review.agent03_g2b import G2BApiError, G2BAwardClient  # noqa: E402

SETTINGS = {"allow_live_lookup": True, "base_url": "https://example.test/svc", "service_operation": "op",
            "lookback_days": 95, "max_results": 20, "timeout_seconds": 1}


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload
        self.content = json.dumps(payload).encode()

    def json(self):
        return self.payload


class FakeSession:
    """실제 API처럼 조회 기간이 31일을 넘으면 07 오류 봉투를 돌려준다."""

    def __init__(self, items):
        self.items, self.calls = items, []

    def get(self, url, timeout):
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        self.calls.append(query)
        span = datetime.strptime(query["inqryEndDt"], "%Y%m%d%H%M") - datetime.strptime(query["inqryBgnDt"], "%Y%m%d%H%M")
        if span.days > 31:
            return FakeResponse({"nkoneps.com.response.ResponseError": {"header": {"resultCode": "07", "resultMsg": "입력범위값 초과 에러"}}})
        return FakeResponse({"response": {"header": {"resultCode": "00"}, "body": {"totalCount": len(self.items), "items": self.items}}})


def test_list_items_are_returned_and_windows_fit_api_limit() -> None:
    session = FakeSession([{"bidNtceNm": "나이스 유지관리", "bidNtceNo": "1"}])
    records, _ = G2BAwardClient(SETTINGS, "key", session).search_service_awards("나이스", None)
    assert records and records[0]["bidNtceNm"] == "나이스 유지관리", "JSON items 리스트가 버려짐"
    assert len(session.calls) == 4, "95일은 31일 이하 구간 4개로 나눠 조회해야 함"


def test_error_envelope_raises() -> None:
    session = FakeSession([])
    session.get = lambda url, timeout: FakeResponse({"nkoneps.com.response.ResponseError": {"header": {"resultCode": "07", "resultMsg": "입력범위값 초과 에러"}}})
    try:
        G2BAwardClient(SETTINGS, "key", session).search_service_awards("나이스", None)
    except G2BApiError as exc:
        assert "07" in str(exc)
    else:
        raise AssertionError("오류 응답이 0건 성공으로 처리됨")


def test_company_awards_filter_by_winner_name() -> None:
    dataset = {"meta": {"source_url": "https://www.data.go.kr/data/15129397/openapi.do"}, "awards": [
        {"bidNtceNm": "A", "bidwinnrNm": "주식회사 아이티센엔텍"},
        {"bidNtceNm": "B", "bidwinnrNm": "쌍용정보통신(주)"},
        {"bidNtceNm": "C", "bidwinnrNm": "다른회사"},
    ]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump(dataset, handle, ensure_ascii=False)
    client = G2BAwardClient(dict(SETTINGS, company_awards_file=handle.name), "key", FakeSession([]))
    records, source = client.search_company_awards("아이티센엔텍")
    assert [r["bidNtceNm"] for r in records] == ["A"], records
    assert source.startswith("https://")


def test_opening_first_bidder() -> None:
    from bid_review.agent03_market import _opening_first_bidder
    assert _opening_first_bidder({"opengCorpInfo": "(주)나라원시스템^1478602432^심재춘"}) == "(주)나라원시스템"
    assert _opening_first_bidder({"opengCorpInfo": ""}) is None  # 유찰


if __name__ == "__main__":
    test_opening_first_bidder()
    test_list_items_are_returned_and_windows_fit_api_limit()
    test_error_envelope_raises()
    test_company_awards_filter_by_winner_name()
    print("g2b OK")
