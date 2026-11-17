"""실행 목록·저장소 검사 (임시 폴더 사용): `python tests/test_runs.py`."""
import base64
import glob
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bid_review.web import runs  # noqa: E402

CDW = Path(sorted(glob.glob(str(ROOT / "outputs/offline/002_*")))[0])
OLD, NEW = "입찰검토보고서_2026-10-20_003022_4676a9", "입찰검토보고서_2026-10-27_201230_a0cb81"


def sandbox() -> Path:
    """임시 outputs/data/input을 만들고 runs 모듈이 그곳을 보게 한다."""
    tmp = Path(tempfile.mkdtemp())
    folder = tmp / "outputs/offline/002_임상연구정보검색시스템(CDW)"
    shutil.copytree(CDW / "debug", folder / "debug")
    shutil.copy(CDW / "crew_execution_manifest.json", folder)
    newest = max(CDW.glob("입찰검토보고서_*.json"))
    for stem in (OLD, NEW):  # 같은 폴더에 날짜가 다른 보고서 2개
        for suffix in (".json", ".md"):
            shutil.copy(newest.with_suffix(suffix), folder / f"{stem}{suffix}")
    pdf_name = json.loads((folder / "debug/01_rfp_analysis.json").read_text(encoding="utf-8"))["project_summary"]["evidence"][0]["document"]
    (tmp / "data/input").mkdir(parents=True)
    (tmp / "data/input" / unicodedata.normalize("NFD", pdf_name)).write_bytes(b"%PDF-1.4 test")  # macOS NFD 이름
    runs.OUTPUTS, runs.INPUT_DIR = tmp / "outputs", tmp / "data/input"
    return tmp


def report_path(tmp: Path, stem: str) -> Path:
    return tmp / "outputs/offline/002_임상연구정보검색시스템(CDW)" / f"{stem}.json"


def complete_core_review(run_id: str) -> None:
    """결재 상신 전제(T1.5 핵심정보 검토 + T2 완료)를 기록한다."""
    path = runs.decode_run_id(run_id)
    runs.core_review_path(path.parent).write_text(json.dumps(
        {"reviewed_report": path.name, "decision": "company_matching_complete"}), encoding="utf-8")


def test_run_id_roundtrip_and_traversal() -> None:
    tmp = sandbox()
    path = report_path(tmp, NEW)
    assert runs.decode_run_id(runs.encode_run_id(path)) == path
    for bad in ("../../etc/x.json", "/etc/passwd", "offline/002_임상연구정보검색시스템(CDW)/crew_execution_manifest.json"):
        token = base64.urlsafe_b64encode(bad.encode()).decode().rstrip("=")
        try:
            runs.decode_run_id(token)
        except ValueError:
            continue
        raise AssertionError(f"거부되지 않음: {bad}")
    try:
        runs.decode_run_id("%%%not-base64")
    except ValueError:
        pass
    else:
        raise AssertionError("잘못된 run_id가 거부되지 않음")


def test_list_runs_latest_only_has_details() -> None:
    tmp = sandbox()
    nfd = report_path(tmp, NEW).parent / unicodedata.normalize("NFD", "입찰검토보고서_2026-10-21_090000_abcdef.json")
    shutil.copy(report_path(tmp, NEW), nfd)  # NFD 파일명도 목록에 나와야 함
    listed = runs.list_runs()
    assert [r["created_at"][:10] for r in listed] == ["2026-10-27", "2026-10-21", "2026-10-20"], listed
    assert listed[0]["mode"] == "offline" and listed[0]["ai_recommendation"] == "보류" and listed[0]["decision"] is None
    assert listed[0]["project_name"]
    latest, older = runs.load_run(listed[0]["id"]), runs.load_run(listed[2]["id"])
    assert latest["details_available"] and latest["rfp"] and latest["capability"] and latest["market"]
    assert not older["details_available"] and older["rfp"] is None
    assert latest["checklist"]["summary"]["recommendation"] == "보류"


def test_decision_save_and_history() -> None:
    tmp = sandbox()
    run_id = runs.encode_run_id(report_path(tmp, NEW))
    complete_core_review(run_id)
    runs.transition(run_id, "submit", "staff")  # v2: 결정은 결재 대기에서만
    runs.save_decision(run_id, "보류", "공고문 확인 후 재검토")
    saved = runs.save_decision(run_id, "No-Go", "규모 미달")
    assert saved["decision"] == "No-Go" and len(saved["decision_history"]) == 1
    assert saved["decided_at"].endswith("+09:00") and "summary" in saved["checklist_snapshot"]
    stored = json.loads(report_path(tmp, NEW).with_name(f"{NEW}.decision.json").read_text(encoding="utf-8"))
    assert stored["decision"] == "No-Go"
    assert runs.list_runs()[0]["decision"] == "No-Go"
    for decision, reason in (("보류", "  "), ("Maybe", "사유")):
        try:
            runs.save_decision(run_id, decision, reason)
        except ValueError:
            continue
        raise AssertionError(f"거부되지 않음: {decision!r} {reason!r}")
    report = json.loads(report_path(tmp, NEW).read_text(encoding="utf-8"))
    assert report["final_decision"] == "미결정", "보고서 JSON은 수정하지 않음"


def test_overrides_persist() -> None:
    tmp = sandbox()
    run_id = runs.encode_run_id(report_path(tmp, NEW))
    checklist = runs.save_overrides(run_id, {"K2": "충족"})
    k2 = next(i for i in checklist["items"] if i["id"] == "K2")
    assert k2["value"] == "충족" and k2["ai"] == "확인 필요" and k2["overridden"]
    reloaded = next(i for i in runs.load_run(run_id)["checklist"]["items"] if i["id"] == "K2")
    assert reloaded["value"] == "충족"
    for bad in ({"K2": "maybe"}, {"S1": 5}, {"X9": 1}):
        try:
            runs.save_overrides(run_id, bad)
        except ValueError:
            continue
        raise AssertionError(f"거부되지 않음: {bad}")


def test_pdf_resolution() -> None:
    tmp = sandbox()
    run_id = runs.encode_run_id(report_path(tmp, NEW))
    pdf = runs.resolve_file(run_id, "rfp.pdf")
    assert pdf and pdf.parent == runs.INPUT_DIR  # CLI 실행: data/input에서 NFD 이름까지 찾음
    web = tmp / "outputs/web/20261027_120000_abcdef"
    web.mkdir(parents=True)
    shutil.copy(report_path(tmp, NEW), web / f"{NEW}.json")
    (web / "rfp.pdf").write_bytes(b"%PDF-1.4 web")
    (web / "upload.json").write_text(json.dumps({"rfp_name": "웹 공고.pdf", "mode": "live"}), encoding="utf-8")
    assert runs.resolve_file(runs.encode_run_id(web / f"{NEW}.json"), "rfp.pdf") == web / "rfp.pdf"
    first = runs.list_runs()[0]
    assert first["mode"] == "live" and first["via_web"] is True, "웹 실행도 offline/live 구분이 보여야 함"
    assert runs.list_runs()[1]["via_web"] is False
    for name in (unicodedata.normalize("NFD", ""), "../.env", "report.exe"):
        try:
            runs.resolve_file(run_id, name)
        except ValueError:
            continue
        raise AssertionError(f"거부되지 않음: {name!r}")
    for f in runs.INPUT_DIR.iterdir():
        f.unlink()
    assert runs.resolve_file(run_id, "rfp.pdf") is None
    assert runs.load_run(run_id)["files"]["pdf"] is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("runs OK")
