"""검토 건·결재 상태 전이 검사 (임시 폴더): `python tests/test_workflow.py`."""
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from bid_review.web import runs  # noqa: E402
from bid_review.web.runs import Conflict  # noqa: E402
from test_runs import NEW, OLD, report_path, sandbox  # noqa: E402


def setup(core_review_done: bool = True):
    tmp = sandbox()
    path = report_path(tmp, NEW)
    if core_review_done:  # 결재 상신 전제: T1.5 검토 + T2 완료
        runs.core_review_path(path.parent).write_text(json.dumps(
            {"reviewed_report": path.name, "decision": "company_matching_complete"}), encoding="utf-8")
    return tmp, runs.encode_run_id(path)


def test_submit_needs_core_review() -> None:
    _, run_id = setup(core_review_done=False)
    expect(Conflict, runs.transition, run_id, "submit", "staff")


def expect(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as error:
        return error
    raise AssertionError(f"{exc.__name__}가 나야 함: {fn.__name__}{args}")


def test_happy_path() -> None:
    _, run_id = setup()
    assert runs.load_run(run_id)["workflow"]["status"] == "검토 완료"
    runs.transition(run_id, "submit", "staff")
    runs.transition(run_id, "reject", "director", "증빙 보완 필요", "수기 보완")
    runs.transition(run_id, "resubmit", "staff")
    runs.save_decision(run_id, "No-Go", "규모 미달")
    flow = runs.load_run(run_id)["workflow"]
    assert flow["status"] == "결정 완료"
    assert [h["action"] for h in flow["history"]] == ["submit", "reject", "resubmit", "decide"]
    reject = flow["history"][1]
    assert reject["role"] == "director" and reject["reason"] == "증빙 보완 필요" and reject["request"] == "수기 보완"
    assert reject["report"] == f"{NEW}.json" and reject["at"].endswith("+09:00")


def test_double_transition_conflicts() -> None:
    _, run_id = setup()
    runs.transition(run_id, "submit", "staff")
    error = expect(Conflict, runs.transition, run_id, "submit", "staff")
    assert error.status == "결재 대기"


def test_role_rules() -> None:
    _, run_id = setup()
    expect(PermissionError, runs.transition, run_id, "submit", "director")
    runs.transition(run_id, "submit", "staff")
    expect(PermissionError, runs.transition, run_id, "reject", "staff", "사유", "재실행")
    expect(ValueError, runs.transition, run_id, "approve", "director")


def test_reject_needs_reason_and_request() -> None:
    _, run_id = setup()
    runs.transition(run_id, "submit", "staff")
    expect(ValueError, runs.transition, run_id, "reject", "director", "", "재실행")
    expect(ValueError, runs.transition, run_id, "reject", "director", "사유", "기타")
    assert runs.load_run(run_id)["workflow"]["status"] == "결재 대기"


def test_old_report_cannot_transition() -> None:
    tmp, _ = setup()
    old = runs.encode_run_id(report_path(tmp, OLD))
    assert runs.load_run(old)["is_current"] is False
    expect(Conflict, runs.transition, old, "submit", "staff")
    expect(Conflict, runs.save_overrides, old, {"K2": "충족"})


def test_locks() -> None:
    _, run_id = setup()
    expect(Conflict, runs.save_decision, run_id, "Go", "검토 완료에서는 결정 불가")
    runs.transition(run_id, "submit", "staff")
    expect(Conflict, runs.save_overrides, run_id, {"K2": "충족"})
    runs.save_decision(run_id, "보류", "공고문 확인 후")
    runs.save_decision(run_id, "Go", "결정 완료 뒤 변경은 가능")
    assert runs.load_run(run_id)["decision"]["decision"] == "Go"


def test_list_cases_one_per_folder() -> None:
    tmp, run_id = setup()
    web = tmp / "outputs/web/20261027_120000_abcdef"
    web.mkdir(parents=True)
    shutil.copy(report_path(tmp, NEW), web / f"{NEW}.json")
    (web / "upload.json").write_text(json.dumps({"rfp_name": "웹.pdf", "mode": "offline", "notice_id": "R26-000"}), encoding="utf-8")
    runs.transition(run_id, "submit", "staff")
    runs.transition(run_id, "reject", "director", "보완", "재실행")
    cases = {c["run_id"]: c for c in runs.list_cases()}
    assert len(cases) == 2, "보고서 2개 든 폴더도 1건"
    cli = cases[run_id]
    assert cli["status"] == "보완 요청" and cli["rejections"] == 1 and cli["notice_id"] is None
    assert cli["ai_recommendation"] == "보류" and cli["checklist_recommendation"] and cli["submitted_at"]
    assert cli["amount_krw"] == 1_070_500_000
    linked = next(c for c in cases.values() if c["via_web"])
    assert linked["notice_id"] == "R26-000" and linked["status"] == "검토 완료"


def test_rerun_inputs() -> None:
    tmp, run_id = setup()
    cli = runs.rerun_inputs(run_id)
    assert cli and Path(cli["rfp_path"]).parent == runs.INPUT_DIR and cli["notice"] is False
    web = tmp / "outputs/web/20261027_130000_abcdef"
    web.mkdir(parents=True)
    shutil.copy(report_path(tmp, NEW), web / f"{NEW}.json")
    (web / "rfp.pdf").write_bytes(b"%PDF-1.4")
    (web / "notice.pdf").write_bytes(b"%PDF-1.4")
    web_inputs = runs.rerun_inputs(runs.encode_run_id(web / f"{NEW}.json"))
    assert web_inputs == {"rfp_path": str(web / "rfp.pdf"), "notice": True}
    for f in runs.INPUT_DIR.iterdir():
        f.unlink()
    assert runs.rerun_inputs(run_id) is None
    assert runs.load_run(run_id)["rerun_available"] is False


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("workflow OK")
