"""체크리스트 자동 판정 검사: `python tests/test_checklist.py`."""
import glob
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bid_review.checklist import apply_overrides, evaluate, summarize  # noqa: E402
from bid_review.settings import load_config  # noqa: E402

CHECKLIST = load_config("checklist")
EOK = 100_000_000


def items_by_id(items):
    return {item["id"]: item for item in items}


def run(amount=None, buyer=None, name="", scope=None, eligibility=None, staffing=None, awards=None):
    report = {"project_scale": {"amount_krw": amount, "source_page": 4} if amount is not None else None}
    rfp = {"basic_info": {"procuring_entity": {"value": buyer}},
           "project_summary": {"project_name": name, "buyer": None, "scope": scope}}
    capability = {"eligibility_matches": [{"status": s} for s in (eligibility or [])],
                  "staffing_matches": [{"status": s} for s in (staffing or [])]}
    return items_by_id(evaluate(report, rfp, capability, CHECKLIST, awards or []))


def test_amount_boundaries() -> None:
    assert run(int(39.9 * EOK))["S1"]["ai"] == 0
    assert run(40 * EOK)["S1"]["ai"] == 1
    assert run(int(99.9 * EOK))["S1"]["ai"] == 1
    assert run(100 * EOK)["S1"]["ai"] == 2
    assert run(None)["S1"]["ai"] is None
    assert run(40 * EOK)["S1"]["source_page"] == 4


def test_knockout_aggregation() -> None:
    assert run(eligibility=["충족", "충족"])["K1"]["ai"] == "충족"
    assert run(eligibility=["충족", "부분 충족"])["K1"]["ai"] == "확인 필요"
    assert run(eligibility=["충족", "미충족"])["K1"]["ai"] == "미충족"
    assert run(eligibility=[])["K1"]["ai"] == "확인 필요"
    assert run(staffing=["미충족"])["K4"]["ai"] == "미충족"
    items = run(eligibility=["충족"], staffing=["충족"])
    assert items["K2"]["ai"] == "확인 필요" and items["K3"]["ai"] == "확인 필요"


def test_business_type_keywords() -> None:
    assert run(name="2026년 자치단체 공통기반 유지관리")["S2"]["ai"] == 2
    assert run(name="학사 시스템 고도화")["S2"]["ai"] == 1
    assert run(name="홍보 영상 제작")["S2"]["ai"] is None


def test_agency_history() -> None:
    awards = [
        {"bidNtceNm": "2025년 자치단체 공통기반 및 재해복구시스템 유지관리", "dminsttNm": "한국지역정보개발원",
         "prtcptCnum": "1", "fnlSucsfDate": "2024-12-27"},
        {"bidNtceNm": "2024년도 인터넷침해대응센터 운영", "dminsttNm": "한국인터넷진흥원",
         "prtcptCnum": "2", "fnlSucsfDate": "2023-12-06"},
        {"bidNtceNm": "2026년도 인터넷침해대응센터 전산시스템 운영 및 관리강화", "dminsttNm": "한국인터넷진흥원",
         "prtcptCnum": "3", "fnlSucsfDate": "2025-12-04"},
    ]
    assert run(buyer="행정안전부 국가정보자원관리원", awards=awards)["S3"]["ai"] == 2
    assert run(buyer="한국인터넷진흥원", name="보안관제 신규 구축", awards=awards)["S3"]["ai"] == 1
    assert run(buyer="한국인터넷진흥원", name="보안관제 신규 구축", awards=awards)["S4"]["ai"] == 0
    assert run(buyer="대한체육회", awards=awards)["S3"]["ai"] == 0
    renewal = run(buyer="한국지역정보개발원", name="2027년 자치단체 공통기반 및 재해복구시스템 유지관리", awards=awards)
    assert renewal["S4"]["ai"] == 2 and renewal["S5"]["ai"] == 2  # 직전 차수 단독(1개사)
    latest = run(buyer="한국인터넷진흥원", name="2027년도 인터넷침해대응센터 전산시스템 운영 및 관리강화", awards=awards)
    assert latest["S4"]["ai"] == 2 and latest["S5"]["ai"] == 0  # 가장 최근 직전 차수 3개사
    nameless = run(buyer="한국인터넷진흥원", name="", awards=awards)
    assert nameless["S4"]["ai"] is None and nameless["S5"]["ai"] is None, "사업명이 없으면 비교 근거 없음"
    unknown = run(buyer=None, awards=awards)
    assert unknown["S3"]["ai"] is None and unknown["S4"]["ai"] is None and unknown["S5"]["ai"] is None


def make(knockout, points):
    items = [{"id": f"K{i}", "kind": "knockout", "ai": v, "value": v} for i, v in enumerate(knockout, 1)]
    items += [{"id": f"S{i}", "kind": "scoring", "ai": v, "value": v} for i, v in enumerate(points, 1)]
    return items


def test_summarize_priority() -> None:
    ok = ["충족"] * 4
    assert summarize(make(["충족", "미충족", "확인 필요", "충족"], [2] * 5), CHECKLIST)["recommendation"] == "No-Go 권고"
    held = summarize(make(["충족", "확인 필요", "충족", "충족"], [2] * 5), CHECKLIST)
    assert held["knockout_result"] == "보류" and held["recommendation"] == "보류" and held["knockout_passed"] == 3
    assert summarize(make(ok, [2, 2, 2, 0, 0]), CHECKLIST)["recommendation"] == "Go 권고"
    assert summarize(make(ok, [2, 2, 1, 0, 0]), CHECKLIST)["recommendation"] == "사업본부장 검토"
    assert summarize(make(ok, [2, 1, 0, 0, 0]), CHECKLIST)["recommendation"] == "No-Go 권고"
    partial = summarize(make(ok, [2, None, None, 1, 0]), CHECKLIST)
    assert partial["score"] == 3 and partial["undetermined"] == 2


def test_undetermined_items_hold_the_recommendation() -> None:
    """미정 항목으로 권고 구간이 바뀔 수 있으면 확정 권고 대신 보류 (원장 Task 9 시나리오)."""
    ok = ["충족"] * 4
    task9 = summarize(make(ok, [1, None, None, None, 0]), CHECKLIST)
    assert task9["recommendation"] == "보류" and task9["score"] == 1 and task9["score_max"] == 7
    assert summarize(make(ok, [2, 2, 2, None, 0]), CHECKLIST)["recommendation"] == "Go 권고", "미정이 있어도 이미 Go 구간"
    assert summarize(make(ok, [0, 0, 1, None, 0]), CHECKLIST)["recommendation"] == "No-Go 권고", "최대 3점이면 No-Go 확정"
    assert summarize(make(ok, [2, 2, 2, 0, 0]), CHECKLIST)["score_max"] == 6


def test_overrides() -> None:
    items = evaluate({"project_scale": None}, None, None, CHECKLIST, [])
    changed = items_by_id(apply_overrides(items, {"K2": "충족", "S1": 2}))
    assert changed["K2"]["value"] == "충족" and changed["K2"]["ai"] == "확인 필요" and changed["K2"]["overridden"]
    assert changed["S1"]["value"] == 2 and changed["S1"]["ai"] is None
    assert not changed["K1"]["overridden"]
    assert items_by_id(items)["K2"]["value"] == "확인 필요", "원본 목록은 바뀌지 않아야 함"


def test_real_cdw_output() -> None:
    folder = Path(sorted(glob.glob(str(ROOT / "outputs/offline/002_*")))[0])
    report = json.loads(max(folder.glob("입찰검토보고서_*.json")).read_text(encoding="utf-8"))
    rfp = json.loads((folder / "debug/01_rfp_analysis.json").read_text(encoding="utf-8"))
    capability = json.loads((folder / "debug/02_capability_matching.json").read_text(encoding="utf-8"))
    awards = json.loads((ROOT / "data/market/itcen_group_awards_3y.json").read_text(encoding="utf-8"))["awards"]
    items = evaluate(report, rfp, capability, CHECKLIST, awards)
    by_id = items_by_id(items)
    assert by_id["S1"]["ai"] == 0 and by_id["K2"]["ai"] == "확인 필요"
    assert summarize(items, CHECKLIST)["recommendation"] == "보류"


def core(budget=117_700_000, crosscheck="confirmed", joint="(없음)공동수급불허", joint_status="confirmed",
         clause=None, **notice_extra):
    """LangGraph T1.5 결과(langgraph_review.json)의 K2·K3 관련 부분만 흉내 낸다."""
    eligibility = [{"category": "eligibility", "field": "g2b_joint_contract", "claim": joint, "status": joint_status}]
    if clause:
        eligibility.append({"category": "eligibility", "field": "condition_6", "claim": clause, "status": "human_review"})
    return {"verification": {"categories": {
                "amount": {"claims": [{"category": "amount", "field": "g2b_crosscheck_budget_krw", "status": crosscheck}]},
                "eligibility": {"claims": eligibility}}},
            "g2b_notice_lookup": {"notice": {"asignBdgtAmt": str(budget), "presmptPrce": "", **notice_extra}}}


def k(core_review):
    return items_by_id(evaluate({"project_scale": None}, None, None, CHECKLIST, [], core_review))


def test_k2_k3_from_t15() -> None:
    # 상수도관: 배정예산 1.177억(VAT 포함, RFP와 일치) < 40억 → 미충족, 공동수급 불허 일치 + 지역 제한 없음 → 충족
    got = k(core(clause="대기업 참여 제한 사업임"))
    assert got["K2"]["ai"] == "미충족" and "1.18억" in got["K2"]["evidence"] and "대기업 참여 제한" in got["K2"]["evidence"]
    assert got["K3"]["ai"] == "충족" and "공동수급불허" in got["K3"]["evidence"]
    assert summarize(list(got.values()), CHECKLIST)["knockout_result"] == "No-Go"
    assert k(core(budget=40 * EOK))["K2"]["ai"] == "충족"
    assert k(core(budget=50 * EOK, clause="대기업 참여 제한"))["K2"]["ai"] == "확인 필요", "하한 이상 + 제한 조항은 사람이 확인"
    # RFP와 나라장터 금액이 일치하지 않으면(confirmed 아님) 자동 판정하지 않는다
    assert k(core(crosscheck="conflict"))["K2"]["ai"] == "확인 필요"
    assert k(core(joint_status="human_review"))["K3"]["ai"] == "확인 필요"
    assert k(core(rgnLmtBidLocplcJdgmBssNm="법인등기부상 본점소재지"))["K3"]["ai"] == "확인 필요"
    assert k(core(joint="공동이행 의무"))["K3"]["ai"] == "확인 필요"
    # T1.5 결과가 없으면 기존처럼 확인 필요, 담당자 수정이 여전히 우선
    assert k(None)["K2"]["ai"] == "확인 필요" and k(None)["K3"]["ai"] == "확인 필요"
    overridden = items_by_id(apply_overrides(list(k(core()).values()), {"K2": "충족"}))
    assert overridden["K2"]["value"] == "충족" and overridden["K2"]["ai"] == "미충족" and overridden["K2"]["overridden"]


def test_k2_k3_real_sangsudo_state() -> None:
    """오늘 실제 실행(상수도관 T1→T4) 결과로 K2 미충족·K3 충족이 나오는지. 결과 폴더가 없으면 건너뛴다."""
    status = ROOT / "outputs/hitl/real-sangsudo-261119b/status.json"
    if not status.is_file():
        return
    got = k(json.loads(status.read_text(encoding="utf-8"))["state"])
    assert got["K2"]["ai"] == "미충족" and got["K3"]["ai"] == "충족", (got["K2"], got["K3"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("checklist OK")
