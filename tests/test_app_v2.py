"""v2 API 검사: 역할 권한·공고 흐름·결재·다시 실행·대시보드 (가짜 실행·가짜 나라장터): `python tests/test_app_v2.py`."""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

from bid_review.web import notices, runs  # noqa: E402
from bid_review.web.app import create_app  # noqa: E402
from bid_review.web.jobs import JobManager  # noqa: E402
from test_app import fake_command, pdf_bytes  # noqa: E402
from test_notices import FakeSession, item  # noqa: E402
from test_runs import complete_core_review  # noqa: E402

STAFF, DIRECTOR = {"X-Role": "staff"}, {"X-Role": "director"}
FILES = {"https://www.g2b.go.kr/f/notice": pdf_bytes(), "https://www.g2b.go.kr/f/rfp": pdf_bytes(), "https://www.g2b.go.kr/f/hwp": b"PK.."}
FULL = item("N1", "차세대 정보시스템 구축", ntceSpecFileNm1="(공고문)N1.pdf", ntceSpecDocUrl1="https://www.g2b.go.kr/f/notice",
            ntceSpecFileNm2="제안요청서.pdf", ntceSpecDocUrl2="https://www.g2b.go.kr/f/rfp")
HWP_ONLY = item("N2", "정보시스템 운영", ntceSpecFileNm1="공고서_N2.pdf", ntceSpecDocUrl1="https://www.g2b.go.kr/f/notice",
                ntceSpecFileNm2="제안요청서.hwpx", ntceSpecDocUrl2="https://www.g2b.go.kr/f/hwp")
HELD = item("N3", "정보시스템 제한", cntrctCnclsMthdNm="제한경쟁")


class Commands:
    """가짜 실행 명령 + 받은 옵션 기록. fail_next면 한 번 실패한다."""

    def __init__(self):
        self.options, self.fail_next, self.slow_next = [], False, False

    def __call__(self, output_dir, options):
        self.options.append(options)
        if self.fail_next:
            self.fail_next = False
            return [sys.executable, "-c", "import sys; print('[실패] boom'); sys.exit(1)"]
        if self.slow_next:  # keeps the single job slot busy
            self.slow_next = False
            return [sys.executable, "-c", "import time; time.sleep(3)"]
        return fake_command(output_dir, options)


def client() -> tuple[TestClient, Commands]:
    runs.OUTPUTS = Path(tempfile.mkdtemp())
    notices.KEY_OVERRIDE = "test-key-123456"
    session = FakeSession({"정보시스템": [FULL, HWP_ONLY, HELD]}, files=FILES)
    notices.requests.Session = lambda: session
    commands = Commands()
    return TestClient(create_app(JobManager(commands, []), allowed_hosts=("testserver",))), commands


def wait_done(c: TestClient, job_id: str) -> str:
    with c.stream("GET", f"/api/jobs/{job_id}/events") as response:
        events = [json.loads(line[5:]) for line in response.iter_lines() if line.startswith("data:")]
    assert events[-1]["type"] == "done", events
    return events[-1]["run_id"]


def collect(c: TestClient) -> None:
    response = c.post("/api/notices/collect", json={"days": 30, "keywords": ["정보시스템"]}, headers=STAFF)
    assert response.status_code == 200, response.text


def test_mutations_require_role() -> None:
    c, _ = client()
    mutations = [("post", "/api/notices/collect", {"json": {}}), ("put", "/api/notices/N1-000/review", {"json": {"include": True, "reason": "x"}}),
                 ("post", "/api/notices/N1-000/start", {"json": {}}), ("post", "/api/runs", {"files": {"rfp": ("a.pdf", pdf_bytes())}}),
                 ("put", "/api/runs/x/checklist", {"json": {"overrides": {}}}), ("put", "/api/runs/x/decision", {"json": {"decision": "Go", "reason": "x"}}),
                 ("put", "/api/runs/x/workflow", {"json": {"action": "submit"}}), ("post", "/api/runs/x/rerun", {"json": {}})]
    for headers in ({}, {"X-Role": "admin"}):
        for method, url, kwargs in mutations:
            assert getattr(c, method)(url, headers=headers, **kwargs).status_code == 403, (url, headers)
    assert c.post("/api/notices/collect", json={}, headers=DIRECTOR).status_code == 403
    assert c.put("/api/runs/x/decision", json={"decision": "Go", "reason": "x"}, headers=STAFF).status_code == 403
    for url in ("/api/status", "/api/notices", "/api/dashboard", "/api/approvals", "/api/decisions", "/api/criteria", "/api/runs"):
        assert c.get(url).status_code == 200, url


def test_collect_and_review() -> None:
    c, _ = client()
    collect(c)
    listed = c.get("/api/notices").json()
    assert listed["counts"] == {"검토 대상": 2, "확인 필요": 1, "제외": 0, "started": 0}
    assert c.put("/api/notices/N3-000/review", json={"include": True, "reason": " "}, headers=STAFF).status_code == 400
    assert c.put("/api/notices/없음/review", json={"include": True, "reason": "x"}, headers=STAFF).status_code == 404
    assert c.put("/api/notices/N3-000/review", json={"include": True, "reason": "원문 확인"}, headers=STAFF).status_code == 200
    assert c.get("/api/notices").json()["counts"]["검토 대상"] == 3


def test_notice_flow_to_job() -> None:
    c, commands = client()
    collect(c)
    started = c.post("/api/notices/N1-000/start", json={"mode": "offline"}, headers=STAFF)
    assert started.status_code == 200 and "job_id" in started.json(), started.text
    run_id = wait_done(c, started.json()["job_id"])
    assert commands.options[-1]["notice"] is True
    case = next(x for x in c.get("/api/approvals").json()["all"] if x["run_id"] == run_id)
    assert case["notice_id"] == "N1-000"
    notice = next(n for n in c.get("/api/notices").json()["notices"] if n["id"] == "N1-000")
    assert notice["case_folder"] and notice["run_id"] == run_id


def test_notice_start_needs_upload_then_runs_with_folder() -> None:
    c, commands = client()
    collect(c)
    started = c.post("/api/notices/N2-000/start", json={"mode": "offline"}, headers=STAFF).json()
    assert started["needs_upload"] is True and started["notice_pdf"] is True
    folder = started["folder_id"]
    bad = c.post("/api/runs", files={"rfp": ("rfp.pdf", pdf_bytes())}, data={"mode": "offline", "folder_id": "../x"}, headers=STAFF)
    assert bad.status_code == 400
    response = c.post("/api/runs", files={"rfp": ("rfp.pdf", pdf_bytes())}, data={"mode": "offline", "folder_id": folder}, headers=STAFF)
    assert response.status_code == 201, response.text
    run_id = wait_done(c, response.json()["job_id"])
    assert runs.case_of(run_id).name == folder and commands.options[-1]["notice"] is True
    upload = json.loads((runs.OUTPUTS / "web" / folder / "upload.json").read_text(encoding="utf-8"))
    assert upload["notice_id"] == "N2-000" and upload["rfp_name"] == "rfp.pdf"
    assert c.get(f"/api/runs/{run_id}").json()["project_name"] == "정보시스템 운영", "공고에서 시작한 건은 공고명이 사업명"


def test_workflow_endpoints() -> None:
    c, _ = client()
    run_id = wait_done(c, c.post("/api/runs", files={"rfp": ("a.pdf", pdf_bytes())}, data={"mode": "offline"}, headers=STAFF).json()["job_id"])
    assert c.put(f"/api/runs/{run_id}/decision", json={"decision": "Go", "reason": "x"}, headers=DIRECTOR).status_code == 409
    complete_core_review(run_id)
    assert c.put(f"/api/runs/{run_id}/workflow", json={"action": "submit"}, headers=STAFF).status_code == 200
    again = c.put(f"/api/runs/{run_id}/workflow", json={"action": "submit"}, headers=STAFF)
    assert again.status_code == 409 and again.json()["status"] == "결재 대기"
    assert c.put(f"/api/runs/{run_id}/workflow", json={"action": "reject", "reason": "보완", "request": "수기 보완"}, headers=STAFF).status_code == 403
    assert c.put(f"/api/runs/{run_id}/workflow", json={"action": "reject", "reason": "보완", "request": "수기 보완"}, headers=DIRECTOR).status_code == 200
    supplement = c.get("/api/approvals").json()["supplement"]
    assert supplement and supplement[0]["last_reject"]["reason"] == "보완"
    assert c.put(f"/api/runs/{run_id}/workflow", json={"action": "resubmit"}, headers=STAFF).status_code == 200
    assert c.put(f"/api/runs/{run_id}/decision", json={"decision": "No-Go", "reason": "규모"}, headers=DIRECTOR).status_code == 200
    decisions = c.get("/api/decisions").json()
    assert decisions["counts"]["No-Go"] == 1 and decisions["items"][0]["rejections"] == 1


def test_rerun_requires_supplement_state_and_inputs() -> None:
    c, commands = client()
    run_id = wait_done(c, c.post("/api/runs", files={"rfp": ("a.pdf", pdf_bytes())}, data={"mode": "offline"}, headers=STAFF).json()["job_id"])
    assert c.post(f"/api/runs/{run_id}/rerun", json={"mode": "offline"}, headers=STAFF).status_code == 409
    complete_core_review(run_id)
    c.put(f"/api/runs/{run_id}/workflow", json={"action": "submit"}, headers=STAFF)
    c.put(f"/api/runs/{run_id}/workflow", json={"action": "reject", "reason": "다시", "request": "재실행"}, headers=DIRECTOR)
    rerun = c.post(f"/api/runs/{run_id}/rerun", json={"mode": "offline"}, headers=STAFF)
    assert rerun.status_code == 200, rerun.text
    wait_done(c, rerun.json()["job_id"])
    assert commands.options[-1]["rfp_path"].endswith("rfp.pdf") and commands.options[-1]["mode"] == "offline"
    assert runs.workflow(runs.case_of(run_id))["status"] == "검토 완료"


def test_dashboard_endpoint_numbers() -> None:
    c, _ = client()
    assert c.get("/api/dashboard").json()["cards"]["collected_total"] == 0
    collect(c)
    board = c.get("/api/dashboard").json()
    assert board["cards"]["collected_total"] == 3 and board["cards"]["passed"] == 2 and board["cards"]["confirm"] == 1


def test_criteria_and_status() -> None:
    c, _ = client()
    criteria = c.get("/api/criteria").json()
    assert criteria["notice_filter"]["min_amount_krw"] == 4_000_000_000 and "knockout" in criteria["checklist"]
    assert "보안" in criteria["role_note"]
    status = c.get("/api/status").json()
    assert status["notice_available"] is True and "test-key" not in json.dumps(status)


def test_index_html_is_not_cached() -> None:
    """다시 빌드한 뒤에도 브라우저가 예전 화면 파일을 쓰지 않도록 index.html은 매번 확인."""
    c, _ = client()
    response = c.get("/")
    assert response.status_code == 200 and "no-cache" in response.headers.get("cache-control", ""), response.headers


def test_folder_upload_refused_once_report_exists() -> None:
    c, _ = client()
    collect(c)
    folder = c.post("/api/notices/N2-000/start", json={"mode": "offline"}, headers=STAFF).json()["folder_id"]
    first = c.post("/api/runs", files={"rfp": ("rfp.pdf", pdf_bytes())}, data={"mode": "offline", "folder_id": folder}, headers=STAFF)
    wait_done(c, first.json()["job_id"])
    again = c.post("/api/runs", files={"rfp": ("rfp.pdf", pdf_bytes())}, data={"mode": "offline", "folder_id": folder}, headers=STAFF)
    assert again.status_code == 409, "보고서가 이미 있는 건에 업로드하면 결재 흐름을 우회함"


def test_rerun_failure_keeps_supplement_and_success_records_mode() -> None:
    c, commands = client()
    run_id = wait_done(c, c.post("/api/runs", files={"rfp": ("a.pdf", pdf_bytes())}, data={"mode": "offline"}, headers=STAFF).json()["job_id"])
    complete_core_review(run_id)
    c.put(f"/api/runs/{run_id}/workflow", json={"action": "submit"}, headers=STAFF)
    c.put(f"/api/runs/{run_id}/workflow", json={"action": "reject", "reason": "다시", "request": "재실행"}, headers=DIRECTOR)
    commands.fail_next = True
    failed = c.post(f"/api/runs/{run_id}/rerun", json={"mode": "offline"}, headers=STAFF)
    with c.stream("GET", f"/api/jobs/{failed.json()['job_id']}/events") as response:
        last = [json.loads(line[5:]) for line in response.iter_lines() if line.startswith("data:")][-1]
    assert last["type"] == "error"
    assert runs.workflow(runs.case_of(run_id))["status"] == "보완 요청", "실패한 다시 실행이 반려 요청을 지우면 안 됨"
    done = c.post(f"/api/runs/{run_id}/rerun", json={"mode": "live"}, headers=STAFF)
    new_run = wait_done(c, done.json()["job_id"])
    flow = runs.workflow(runs.case_of(run_id))
    assert flow["status"] == "검토 완료" and flow["history"][-1]["action"] == "rerun"
    assert c.get(f"/api/runs/{new_run}").json()["mode"] == "live", "다시 실행한 방식이 보고서에 기록돼야 함"


def test_start_reports_guess() -> None:
    c, _ = client()
    guessed = item("N4", "정보시스템 운영", ntceSpecFileNm1="붙임 사업설명.pdf", ntceSpecDocUrl1="https://www.g2b.go.kr/f/rfp")
    notices.requests.Session = lambda: FakeSession({"정보시스템": [guessed]}, files=FILES)
    collect(c)
    started = c.post("/api/notices/N4-000/start", json={"mode": "offline"}, headers=STAFF).json()
    assert started["rfp_guessed"] is True and started["notice_pdf"] is False and "market_api" not in started


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("app_v2 OK")
