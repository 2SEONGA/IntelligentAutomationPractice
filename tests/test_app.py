"""웹 API 검사 (가짜 실행 명령, 임시 outputs): `python tests/test_app.py`."""
import io
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from dotenv import dotenv_values  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pypdf import PdfWriter  # noqa: E402

from bid_review.web import app as app_module, runs  # noqa: E402
from bid_review.web.app import create_app  # noqa: E402
from bid_review.web.jobs import JobManager  # noqa: E402
from test_runs import complete_core_review  # noqa: E402

REPORT = "입찰검토보고서_2026-10-27_120000_abcdef.json"
FAKE_REPORT = {"decision_status": "보류", "final_decision": "미결정", "project_scale": None}


def fake_command(output_dir: Path, options: dict) -> list[str]:
    script = (f"import json, pathlib, time\nprint('[STAGE] agent1 start', flush=True)\ntime.sleep(0.3)\n"
              f"pathlib.Path({str(output_dir)!r}, {REPORT!r}).write_text({json.dumps(FAKE_REPORT, ensure_ascii=False)!r}, encoding='utf-8')\n"
              "print('[STAGE] agent1 done', flush=True)\n")
    return [sys.executable, "-c", script]


def pdf_bytes() -> bytes:
    writer, buffer = PdfWriter(), io.BytesIO()
    writer.add_blank_page(width=595, height=842)
    writer.write(buffer)
    return buffer.getvalue()


def client() -> TestClient:
    runs.OUTPUTS = Path(tempfile.mkdtemp())
    return TestClient(create_app(JobManager(fake_command, []), allowed_hosts=("testserver",)))


def upload(c: TestClient, rfp=("rfp.pdf", None), notice=None, headers=None, **form):
    files = {"rfp": (rfp[0], rfp[1] if rfp[1] is not None else pdf_bytes(), "application/pdf")}
    if notice:
        files["notice"] = ("notice.pdf", pdf_bytes(), "application/pdf")
    return c.post("/api/runs", files=files, data={"mode": "offline", "market_api": "false", **form},
                  headers={"X-Role": "staff", **(headers or {})})  # v2: 상태 변경은 역할 헤더 필요


def events(c: TestClient, job_id: str) -> list[dict]:
    with c.stream("GET", f"/api/jobs/{job_id}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        return [json.loads(line[5:]) for line in response.iter_lines() if line.startswith("data:")]


def test_status_has_no_secret_values() -> None:
    body = client().get("/api/status")
    assert body.status_code == 200
    data = body.json()
    assert set(data) == {"live_available", "market_available", "notice_available", "checklist_version", "running_job_id", "role_note"}
    for secret in dotenv_values(ROOT / ".env").values():
        if secret and len(secret) >= 8:
            assert secret not in body.text


def test_upload_validation() -> None:
    c = client()
    assert upload(c, rfp=("rfp.txt", b"%PDF-1.4")).status_code == 400
    assert upload(c, rfp=("rfp.pdf", b"hello, not a pdf")).status_code == 400
    assert upload(c, market_api="true").status_code == 400, "offline에서 나라장터 조회 불가"
    assert upload(c, mode="live", market_api="true").status_code == 400, "공고문 없이 나라장터 조회 불가"
    assert upload(c, mode="cloud").status_code == 400
    app_module.MAX_UPLOAD_BYTES, saved = 100, app_module.MAX_UPLOAD_BYTES
    try:
        response = upload(c)
        assert response.status_code == 400 and "50MB" in response.json()["detail"]
    finally:
        app_module.MAX_UPLOAD_BYTES = saved
    assert not list((runs.OUTPUTS / "web").glob("*")) if (runs.OUTPUTS / "web").exists() else True, "거부된 업로드는 폴더를 남기지 않음"


def test_run_flow() -> None:
    c = client()
    first = upload(c, rfp=("임상연구 CDW.pdf", None))
    assert first.status_code == 201
    job_id = first.json()["job_id"]
    assert c.get("/api/status").json()["running_job_id"] == job_id
    assert upload(c).status_code == 409
    stream = events(c, job_id)
    assert stream[0] == {"type": "stage", "node": "agent1", "state": "start"} and stream[-1]["type"] == "done"
    run_id = stream[-1]["run_id"]
    detail = c.get(f"/api/runs/{run_id}")
    assert detail.status_code == 200 and detail.json()["project_name"] == "임상연구 CDW"
    assert c.get("/api/runs").json()[0]["id"] == run_id
    pdf = c.get(f"/api/runs/{run_id}/files/rfp.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-") and "inline" in pdf.headers["content-disposition"]
    assert "attachment" in c.get(f"/api/runs/{run_id}/files/report.json").headers["content-disposition"]
    assert c.get("/api/runs/bm90LWEtcnVu").status_code == 404
    assert c.get("/api/jobs/nope/events").status_code == 404


def test_decision_and_checklist_endpoints() -> None:
    c = client()
    job_id = upload(c).json()["job_id"]
    run_id = events(c, job_id)[-1]["run_id"]
    staff, director = {"X-Role": "staff"}, {"X-Role": "director"}
    assert c.put(f"/api/runs/{run_id}/checklist", json={"overrides": {"K2": "없음"}}, headers=staff).status_code == 400
    checklist = c.put(f"/api/runs/{run_id}/checklist", json={"overrides": {"K2": "충족"}}, headers=staff)
    assert checklist.status_code == 200 and "summary" in checklist.json()
    complete_core_review(run_id)
    assert c.put(f"/api/runs/{run_id}/workflow", json={"action": "submit"}, headers=staff).status_code == 200  # v2: 결재 대기에서 결정
    assert c.put(f"/api/runs/{run_id}/decision", json={"decision": "보류", "reason": " "}, headers=director).status_code == 400
    saved = c.put(f"/api/runs/{run_id}/decision", json={"decision": "No-Go", "reason": "규모 미달"}, headers=director)
    assert saved.status_code == 200 and c.get(f"/api/runs/{run_id}").json()["decision"]["decision"] == "No-Go"


def test_files_endpoint_rejects_unknown_names() -> None:
    c = client()
    run_id = events(c, upload(c).json()["job_id"])[-1]["run_id"]
    for name in ("..%2F.env", ".env", "report.exe", "decision.json"):  # decision.json은 아직 없음
        assert c.get(f"/api/runs/{run_id}/files/{name}").status_code == 404, name


def test_checklist_result_download() -> None:
    c = client()
    run_id = events(c, upload(c).json()["job_id"])[-1]["run_id"]
    response = c.get(f"/api/runs/{run_id}/files/checklist.json")
    assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
    body = response.json()
    assert {"items", "summary", "version"} <= set(body), "수정 전에도 평가 결과 전체를 내려받을 수 있어야 함"


def test_cross_site_requests_rejected() -> None:
    c = client()
    assert upload(c, headers={"Origin": "https://evil.example"}).status_code == 403
    run_id = events(c, upload(c).json()["job_id"])[-1]["run_id"]
    assert c.put(f"/api/runs/{run_id}/decision", json={"decision": "Go", "reason": "x"},
                 headers={"Origin": "https://evil.example", "X-Role": "director"}).status_code == 403
    assert c.get("/api/status", headers={"Host": "evil.example"}).status_code == 400, "DNS rebinding 방지"
    assert upload(c, headers={"Origin": "http://testserver"}).status_code in (201, 409)


def test_core_review_pauses_and_resumes_saved_run() -> None:
    """웹 핵심정보 검토: 검증 실행은 중단 지점을 저장하고, 승인은 그 지점에서 이어 간다(T1 재실행 없음)."""
    from unittest.mock import patch
    from bid_review.rag import hitl_runtime
    c = client()
    run_id = events(c, upload(c).json()["job_id"])[-1]["run_id"]
    staff = {"X-Role": "staff"}
    paused = {"thread_id": "web-x", "status": "paused", "interrupts": [{"id": "int-1", "value": {}}], "task_errors": [],
              "state": {"verification": {"status": "human_review"}, "notice_no": "R26BK00000001"}}
    done = {"thread_id": "web-x", "status": "completed", "interrupts": [], "task_errors": [],
            "state": {"verification": {"status": "human_review"}, "decision": "human_approved_company_matching_complete",
                      "human_override": {"reviewer": "staff", "note": "금액 확인"}}}
    with patch.object(hitl_runtime, "start_review", return_value=paused) as start, \
         patch.object(hitl_runtime, "resume_review", return_value=done) as resume:
        # 공고함에서 시작한 건처럼 공고번호를 저장해 두면 첫 검증부터 나라장터 조회에 쓴다(예전에는 늘 생략됨)
        (runs.decode_run_id(run_id).parent / "notice_attachment.json").write_text(
            json.dumps({"notice_no": "R26BK01762350", "notice_order": "000"}), encoding="utf-8")
        first = c.post(f"/api/runs/{run_id}/core-review", json={"action": "analyze"}, headers=staff)
        assert first.status_code == 200, first.text
        saved = first.json()["result"]
        assert saved["decision"] == "human_review_required" and saved["hitl"] == {
            "thread_id": "web-x", "status": "paused", "interrupt_id": "int-1"}
        assert start.call_args.kwargs["thread_id"].startswith("web-")
        assert start.call_args.kwargs["notice_no"] == "R26BK01762350" and start.call_args.kwargs["notice_order"] == "000"
        assert c.post(f"/api/runs/{run_id}/core-review", json={"action": "approve_and_run_t2", "reason": " "},
                      headers=staff).status_code == 400
        approved = c.post(f"/api/runs/{run_id}/core-review", json={"action": "approve_and_run_t2", "reason": "금액 확인"},
                          headers=staff)
        assert approved.status_code == 200, approved.text
        assert resume.call_args.args[:2] == ("web-x", "int-1")
        assert resume.call_args.args[2] == {"action": "approve", "reviewer": "staff", "note": "금액 확인", "corrections": {}}
        assert approved.json()["result"]["decision"] == "human_approved_company_matching_complete"
        assert start.call_count == 1, "승인은 새 실행을 시작하지 않는다"
        # 이미 T2까지 끝난 실행은 다시 승인할 수 없다
        assert c.post(f"/api/runs/{run_id}/core-review", json={"action": "approve_and_run_t2", "reason": "다시"},
                      headers=staff).status_code == 409
    with patch.object(hitl_runtime, "start_review", return_value=paused), \
         patch.object(hitl_runtime, "resume_review", side_effect=ValueError("입력 파일이 변경됐습니다: pdf_path.")):
        c2 = client()
        run2 = events(c2, upload(c2).json()["job_id"])[-1]["run_id"]
        c2.post(f"/api/runs/{run2}/core-review", json={"action": "analyze"}, headers=staff)
        stale = c2.post(f"/api/runs/{run2}/core-review", json={"action": "reject", "reason": "반려"}, headers=staff)
        assert stale.status_code == 409 and "변경" in stale.text


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("app OK")
