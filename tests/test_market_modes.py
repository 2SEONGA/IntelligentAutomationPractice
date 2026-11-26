"""남은 업무 5번: A안(T3 뒤 낙찰 실시간 재조회) · B안(T3 색인 낙찰만). `python tests/test_market_modes.py` (네트워크 없음)."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bid_review.rag.live_awards import AwardLookupError, refresh_awards  # noqa: E402

URL = "https://www.g2b.go.kr/link/PNPE027_01/single/?bidPbancNo={}&bidPbancOrd=000"


def candidate(no: str, title: str, award: dict | None = None) -> dict:
    return {"notice_id": f"{no}-000", "title": title, "notice_url": URL.format(no), "award": award,
            "award_notice_id": f"{no}-000" if award else None, "award_source_url": URL.format(no) if award else None}


def test_refresh_links_only_same_notice_and_title() -> None:
    live = {
        "R1": [{"bidNtceNo": "R1", "bidNtceOrd": "001", "bidNtceNm": "통합 정보시스템 구축", "bidwinnrNm": "가회사", "sucsfbidAmt": "100"}],
        "R2": [{"bidNtceNo": "R2", "bidNtceOrd": "000", "bidNtceNm": "학사 시스템", "bidwinnrNm": "나회사", "sucsfbidAmt": "200"}],
        "R3": [{"bidNtceNo": "R3", "bidNtceOrd": "000", "bidNtceNm": "학사 시스템", "bidwinnrNm": "새회사", "sucsfbidAmt": "350"}],
        "R4": [{"bidNtceNo": "R4", "bidNtceOrd": "000", "bidNtceNm": "전혀 다른 사업", "bidwinnrNm": "다회사", "sucsfbidAmt": "1"}],
        "R5": [{"bidNtceNo": "R5X", "bidNtceNm": "다른 공고번호"}],
    }
    calls = []

    def fake(number):
        calls.append(number)
        return live[number]
    projects = [candidate("R1", "통합정보시스템 구축"), candidate("R2", "학사 시스템", {"bidwinnrNm": "나회사", "sucsfbidAmt": "200"}),
                candidate("R3", "학사 시스템", {"bidwinnrNm": "옛회사", "sucsfbidAmt": "300"}), candidate("R4", "학사 시스템"),
                candidate("R5", "상수도 관망")]
    result = refresh_awards(projects, fetch=fake, now="2026-11-25T00:00:00+09:00")
    status = {row["notice_id"]: row["status"] for row in result["live_award_lookup"]["results"]}
    assert status == {"R1-000": "new_award", "R2-000": "same_as_index", "R3-000": "updated",
                      "R4-000": "name_mismatch", "R5-000": "no_award_yet"}, status
    r1 = result["similar_projects"][0]
    assert r1["award"]["bidwinnrNm"] == "가회사" and r1["award_notice_id"] == "R1-001" and r1["award_source"] == "live_api"
    assert r1["award_source_url"].startswith("https://"), "기존 T2·T3 검증(공고번호·URL 일치)을 통과하는 형태"
    assert result["similar_projects"][3]["award"] is None, "공고명이 다르면 붙이지 않음"
    assert result["live_award_lookup"]["api_calls"] == 5 and calls == ["R1", "R2", "R3", "R4", "R5"]
    assert projects[0]["award"] is None, "입력 후보는 바꾸지 않음"


def test_failure_keeps_index_award_and_stops_calling() -> None:
    calls = []

    def broken(number):
        calls.append(number)
        raise AwardLookupError("낙찰정보 API 오류 30: 등록되지 않은 서비스키")
    projects = [candidate("R2", "학사", {"bidwinnrNm": "나회사"}), candidate("R3", "학사")]
    result = refresh_awards(projects, fetch=broken)
    assert calls == ["R2"], "키 미등록·장애면 첫 실패 뒤 더 부르지 않음"
    assert result["similar_projects"][0]["award"] == {"bidwinnrNm": "나회사"}, "색인 낙찰정보는 그대로"
    assert [row["status"] for row in result["live_award_lookup"]["results"]] == ["unavailable", "unavailable"]
    assert result["live_award_lookup"]["status"] == "unavailable" and "serviceKey" not in str(result)


def test_lookup_cap() -> None:
    projects = [candidate(f"R{i}", "x") for i in range(4)]
    result = refresh_awards(projects, fetch=lambda number: [], max_lookups=2)
    assert result["live_award_lookup"]["api_calls"] == 2
    assert [row["status"] for row in result["live_award_lookup"]["results"]][2:] == ["not_checked", "not_checked"]


def test_graph_wiring_default_unchanged() -> None:
    from bid_review.rag.workflow import create_bid_review_graph
    default = create_bid_review_graph()
    assert "t3_live_awards" not in default.get_graph().nodes, "B안(기본): 기존 그래프 그대로"
    for mode in ("sequential", "parallel"):
        graph = create_bid_review_graph(workflow_mode=mode, market_mode="live_awards").get_graph()
        targets = {(edge.source, edge.target) for edge in graph.edges}
        assert ("t3_similar_projects", "t3_live_awards") in targets and ("t3_live_awards", "t23_evaluate") in targets, mode
    try:
        create_bid_review_graph(market_mode="realtime")
    except ValueError:
        pass
    else:
        raise AssertionError("모르는 market_mode는 거부")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("market modes OK")
