"""공고 규칙 필터 검사: `python tests/test_notice_filter.py`."""
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bid_review.notice_filter import amount_of, judge  # noqa: E402
from bid_review.settings import load_config  # noqa: E402

RULES = load_config("notice_filter")
NOW = datetime(2026, 10, 27, 12, 0)
EOK = 100_000_000


def notice(**fields) -> dict:
    """실제 응답 필드 이름을 쓴 최소 공고. 기본값은 모든 규칙을 통과한다."""
    base = {"bidNtceNm": "차세대 정보시스템 구축", "asignBdgtAmt": str(50 * EOK), "presmptPrce": "",
            "bidClseDt": "2026-11-20 10:00:00", "pubPrcrmntLrgClsfcNm": "ICT 서비스",
            "cntrctCnclsMthdNm": "일반경쟁", "sucsfbidMthdNm": "협상에의한계약", "indstrytyLmtYn": "Y",
            "rgnLmtBidLocplcJdgmBssNm": "", "prdctClsfcLmtYn": "N", "bidPrtcptLmtYn": "N"}
    return {**base, **fields}


def rule(item: dict, name: str) -> str:
    return judge(item, RULES, NOW)["rules"][name]["verdict"]


def test_amount_boundaries() -> None:
    assert rule(notice(asignBdgtAmt=str(int(39.9 * EOK))), "amount") == "제외"
    assert rule(notice(asignBdgtAmt=str(40 * EOK)), "amount") == "검토 대상"
    assert rule(notice(asignBdgtAmt="", presmptPrce=str(50 * EOK)), "amount") == "검토 대상"
    assert rule(notice(asignBdgtAmt="", presmptPrce=""), "amount") == "확인 필요"
    assert rule(notice(asignBdgtAmt="0", presmptPrce="0"), "amount") == "확인 필요"
    assert amount_of(notice(asignBdgtAmt="", presmptPrce="123")) == 123


def test_deadline() -> None:
    assert rule(notice(bidClseDt="2026-10-26 10:00:00"), "deadline") == "제외"
    assert rule(notice(bidClseDt="2026-10-29 10:00:00"), "deadline") == "확인 필요"
    assert rule(notice(bidClseDt="2026-11-06 10:00:00"), "deadline") == "검토 대상"
    assert rule(notice(bidClseDt=""), "deadline") == "확인 필요"


def test_field() -> None:
    assert rule(notice(), "field") == "검토 대상"
    assert rule(notice(pubPrcrmntLrgClsfcNm="연구조사서비스"), "field") == "제외", "이름에 키워드가 있어도 ICT가 아니면 제외"
    assert rule(notice(pubPrcrmntLrgClsfcNm=""), "field") == "검토 대상", "분류가 비면 공고명 키워드로 대신 판정"
    assert rule(notice(pubPrcrmntLrgClsfcNm="", bidNtceNm="청사 청소 용역"), "field") == "제외"


def test_contract_and_conditions() -> None:
    assert rule(notice(cntrctCnclsMthdNm="수의계약"), "contract") == "제외"
    assert rule(notice(cntrctCnclsMthdNm="제한경쟁"), "conditions") == "확인 필요"
    assert rule(notice(rgnLmtBidLocplcJdgmBssNm="본사또는참여지사소재지"), "conditions") == "확인 필요"
    assert rule(notice(prdctClsfcLmtYn="Y"), "conditions") == "확인 필요"
    assert rule(notice(indstrytyLmtYn="Y"), "conditions") == "검토 대상", "업종제한 Y는 SW사업자 등록 요건이라 표시하지 않음"


def test_priority_and_reasons() -> None:
    result = judge(notice(asignBdgtAmt=str(int(0.4 * EOK)), cntrctCnclsMthdNm="제한경쟁"), RULES, NOW)
    assert result["verdict"] == "제외"
    assert any("40억 미만(0.4억)" in r for r in result["reasons"]) and any("제한경쟁" in r for r in result["reasons"])
    assert judge(notice(cntrctCnclsMthdNm="제한경쟁"), RULES, NOW)["verdict"] == "확인 필요"
    passed = judge(notice(), RULES, NOW)
    assert passed["verdict"] == "검토 대상" and passed["amount_krw"] == 50 * EOK and passed["deadline"] == "2026-11-20 10:00:00"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("notice_filter OK")
