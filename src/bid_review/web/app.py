"""FastAPI app: notices → CLI subprocess → SSE progress → results, checklist, approvals and decisions."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import re
import shutil
import threading
import time
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import dotenv_values
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..settings import PROJECT_ROOT, load_config
from . import criteria, notices, runs
from .dashboard import compute_dashboard
from .jobs import JobBusy, JobManager, build_command

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
LOCAL_HOSTS = ("127.0.0.1", "localhost")
WEB_DIST = PROJECT_ROOT / "web/dist"
WEB_FOLDER = re.compile(r"^\d{8}_\d{6}_[0-9a-f]{6}$")
ROLES = {"staff": "사업기획 담당자", "director": "사업본부장"}
ROLE_NOTE = "시작 화면의 역할 선택은 아직 보안 인증이 아닙니다. 같은 PC의 누구든 역할을 고를 수 있고, 실제 운영 때는 사내 계정 로그인으로 바꿉니다."
BUSY = "이미 실행 중인 검토가 있습니다. 끝난 뒤 다시 시작하세요."


class ChecklistBody(BaseModel):
    overrides: dict[str, str | int | None]


class DecisionBody(BaseModel):
    decision: str
    reason: str


class CollectBody(BaseModel):
    days: int | None = None
    keywords: list[str] | None = None


class ReviewBody(BaseModel):
    include: bool
    reason: str


class RunOptions(BaseModel):
    mode: str = "offline"
    market_api: bool = False


class CriteriaSetBody(BaseModel):
    values: dict


class ActiveBody(BaseModel):
    name: str


class WorkflowBody(BaseModel):
    action: str
    reason: str = ""
    request: str = ""


class CoreReviewBody(BaseModel):
    action: Literal["analyze", "approve_and_run_t2", "reject"]
    reason: str = ""


def _env() -> dict[str, str]:
    return {k: v for k, v in dotenv_values(PROJECT_ROOT / ".env").items() if v}


def _availability() -> tuple[bool, bool, bool]:
    env = _env()
    return (bool(env.get("OPENAI_API_KEY")), bool(env.get("G2B_SERVICE_KEY")),
            bool(env.get("G2B_BID_NOTICE_KEY") or env.get("G2B_SERVICE_KEY")))


def role_of(x_role: str | None = Header(None)) -> str | None:
    return x_role if x_role in ROLES else None


def require(*allowed: str):
    def check(role: str | None = Depends(role_of)) -> str:
        if role not in allowed:
            raise HTTPException(403, f"{' 또는 '.join(ROLES[r] for r in allowed)}만 할 수 있어요.")
        return role
    return check


def _check_options(mode: str, market_api: bool, has_notice: bool) -> None:
    live, market, _ = _availability()
    if mode not in ("offline", "live"):
        raise HTTPException(400, "실행 방식은 offline 또는 live여야 합니다.")
    if mode == "live" and not live:
        raise HTTPException(400, ".env에 OPENAI_API_KEY가 없어 live로 실행할 수 없습니다.")
    if market_api and not (mode == "live" and has_notice and market):
        raise HTTPException(400, "나라장터 시장조사는 live 모드에서 공고문 PDF와 G2B_SERVICE_KEY가 있을 때만 켤 수 있습니다.")


async def _read_pdf(upload: UploadFile, label: str) -> bytes:
    data = await upload.read()
    if not (upload.filename or "").lower().endswith(".pdf") or not data.startswith(b"%PDF-"):
        raise HTTPException(400, f"{label}는 PDF 파일이어야 합니다.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"{label}는 50MB 이하여야 합니다.")
    return data


def _new_folder():
    folder = runs.OUTPUTS / "web" / f"{datetime.now(runs.KST):%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
    folder.mkdir(parents=True)
    return folder


def _write_upload(folder, **fields) -> None:
    path = folder / "upload.json"
    current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    path.write_text(json.dumps({**current, **fields}, ensure_ascii=False, indent=2), encoding="utf-8")


def create_app(job_manager: JobManager | None = None, allowed_hosts: tuple[str, ...] = LOCAL_HOSTS) -> FastAPI:
    manager = job_manager or JobManager(build_command, list(_env().values()))
    collect_lock = threading.Lock()
    app = FastAPI(title="공공 SI 입찰 검토")

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Bound to 127.0.0.1, but any page the user opens could still POST here (CSRF) or reach it
        # through a rebound DNS name; a paid live run must only start from this app's own pages.
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in allowed_hosts:
            return JSONResponse({"detail": "허용되지 않은 호스트입니다."}, status_code=400)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD") and origin and urlsplit(origin).hostname not in allowed_hosts:
            return JSONResponse({"detail": "다른 사이트에서 보낸 요청은 받지 않습니다."}, status_code=403)
        response = await call_next(request)
        if response.headers.get("content-type", "").startswith("text/html"):
            # index.html names the hashed bundle; revalidate it so a rebuild shows up without a hard refresh
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(runs.Conflict)
    async def conflict(_: Request, exc: runs.Conflict):
        return JSONResponse({"detail": str(exc), "status": exc.status}, status_code=409)

    @app.exception_handler(PermissionError)
    async def forbidden(_: Request, exc: PermissionError):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    def start_job(folder, options: dict, after=None) -> dict:
        def finish(report):
            runs.record_run(report, options["mode"])
            if after:
                after(report)
        try:
            job = manager.start(folder, options, on_finish=finish)
        except JobBusy:
            raise HTTPException(409, BUSY) from None
        # after start, so a busy refusal keeps the case's old snapshot; the checklist is only read once the run is done
        criteria.snapshot(folder)
        return {"job_id": job.id}

    # --- status / read-only views -------------------------------------------------
    @app.get("/api/status")
    def status() -> dict:
        live, market, notice = _availability()
        current = manager.current()
        return {"live_available": live, "market_available": market, "notice_available": notice,
                "checklist_version": load_config("checklist")["version"], "running_job_id": current.id if current else None,
                "role_note": ROLE_NOTE}

    @app.get("/api/dashboard")
    def dashboard() -> dict:
        return compute_dashboard(notices.load_notices(), runs.list_cases(), datetime.now(runs.KST).replace(tzinfo=None))

    @app.get("/api/approvals")
    def approvals() -> dict:
        cases = runs.list_cases()
        return {"pending": [c for c in cases if c["status"] == "결재 대기"],
                "supplement": [c for c in cases if c["status"] == "보완 요청"], "all": cases}

    @app.get("/api/decisions")
    def decisions() -> dict:
        decided = sorted((c for c in runs.list_cases() if c["decision"]), key=lambda c: c["decided_at"], reverse=True)
        return {"counts": {d: sum(c["decision"] == d for c in decided) for d in runs.DECISIONS}, "items": decided}

    @app.get("/api/criteria")
    def get_criteria(set: str | None = None) -> dict:
        awards = runs._read_json(runs.AWARDS_FILE) or {}
        try:
            selected = criteria.resolve(set)
        except KeyError:
            raise HTTPException(404, "판단 기준을 찾을 수 없습니다.") from None
        return {"notice_filter": selected["notice_filter"], "checklist": selected["checklist"],
                "sets": criteria.list_sets(), "active": criteria.active_name(), "selected": selected["name"],
                "values": criteria.values_of(selected["name"]),
                "awards_meta": {**awards.get("meta", {}), "count": len(awards.get("awards", []))}, "role_note": ROLE_NOTE}

    @app.put("/api/criteria/sets/{name}")
    def put_criteria_set(name: str, body: CriteriaSetBody, _: str = Depends(require("staff", "director"))) -> dict:
        try:
            return {"name": criteria.save_set(name, body.values)["name"]}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.delete("/api/criteria/sets/{name}")
    def delete_criteria_set(name: str, _: str = Depends(require("staff", "director"))) -> dict:
        try:
            criteria.delete_set(name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except KeyError:
            raise HTTPException(404, "판단 기준을 찾을 수 없습니다.") from None
        return {"ok": True}

    @app.put("/api/criteria/active")
    def put_active_criteria(body: ActiveBody, _: str = Depends(require("director"))) -> dict:
        try:
            criteria.set_active(body.name)
        except KeyError:
            raise HTTPException(404, "판단 기준을 찾을 수 없습니다.") from None
        return {"active": criteria.active_name()}

    # --- ① 수집 · ② 필터 · ②-1 HITL-2 · ③ 문서 확보 -----------------------------------
    @app.get("/api/notices")
    def list_notices(verdict: str | None = None) -> dict:
        store = notices.load_notices()
        current = {}
        for folder in {n["case_folder"] for n in store["notices"].values() if n.get("case_folder")}:
            case = runs.OUTPUTS / "web" / folder
            if case.is_dir() and any(runs._is_report(p) for p in case.glob("*.json")):
                current[folder] = runs.encode_run_id(runs.current_report(case))
        items = [{"id": nid, **n, "run_id": current.get(n.get("case_folder"))} for nid, n in store["notices"].items()]
        counts = {v: sum(i["verdict"] == v for i in items) for v in ("검토 대상", "확인 필요", "제외")}
        counts["started"] = sum(bool(i["case_folder"]) for i in items)
        if verdict:
            items = [i for i in items if i["verdict"] == verdict]
        items.sort(key=lambda i: (i["judged"].get("deadline") or "9999", -(i["judged"].get("amount_krw") or 0)))
        return {"notices": items, "counts": counts, "runs": store["runs"], "notice_available": _availability()[2]}

    @app.post("/api/notices/collect")
    def collect(body: CollectBody, _: str = Depends(require("staff"))) -> dict:
        rules = criteria.resolve()["notice_filter"]
        if not collect_lock.acquire(blocking=False):
            raise HTTPException(409, "이미 공고를 수집하는 중입니다.")
        try:
            return notices.collect(body.days or rules["default_days"], body.keywords or rules["collect_keywords"])
        except notices.NoticeError as exc:
            raise HTTPException(502, str(exc)) from None
        finally:
            collect_lock.release()

    @app.put("/api/notices/{nid}/review")
    def review_notice(nid: str, body: ReviewBody, role: str = Depends(require("staff"))) -> dict:
        try:
            return notices.set_review(nid, body.include, body.reason, role)
        except KeyError:
            raise HTTPException(404, "공고를 찾을 수 없습니다.") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/notices/{nid}/start")
    def start_notice(nid: str, body: RunOptions, _: str = Depends(require("staff"))) -> dict:
        entry = notices.load_notices()["notices"].get(nid)
        if not entry:
            raise HTTPException(404, "공고를 찾을 수 없습니다.")
        _check_options(body.mode, False, True)
        if manager.current() or batch["running"]:
            raise HTTPException(409, BUSY)
        return prepare_and_start(nid, entry, body)

    def prepare_and_start(nid: str, entry: dict, body: RunOptions) -> dict:
        folder = _new_folder()
        fetched = notices.fetch_attachments(nid, folder)
        market_api = body.market_api and fetched["notice"]
        _check_options(body.mode, market_api, fetched["notice"])
        rfp_name = next((a["name"] for a in entry["attachments"] if a["kind"] in ("rfp", "pdf")), None) if fetched["rfp"] else None
        notice_attachment = next((a for a in entry["attachments"] if a["kind"] == "notice"), None) if fetched["notice"] else None
        notice_name = notice_attachment["name"] if notice_attachment else None
        if notice_attachment and (folder / "notice.pdf").is_file():
            notice_bytes = (folder / "notice.pdf").read_bytes()
            (folder / "notice_attachment.json").write_text(json.dumps({
                "notice_id": nid,
                "notice_no": entry.get("bidNtceNo"),
                "notice_order": entry.get("bidNtceOrd"),
                "attachment_name": notice_name,
                "source_url": notice_attachment.get("url"),
                "downloaded_at": datetime.now(runs.KST).isoformat(timespec="seconds"),
                "local_file": "notice.pdf",
                "sha256": hashlib.sha256(notice_bytes).hexdigest(),
                "source_note": "기존 공고 수집 저장소의 첨부 URL에서 내려받음. 이번 실행 중 API 재조회 사실을 뜻하지 않음.",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_upload(folder, project_name=entry["bidNtceNm"], rfp_name=rfp_name or entry["bidNtceNm"], notice_name=notice_name, mode=body.mode,
                      market_api=market_api, notice_id=nid)
        notices.attach_case(nid, folder.name)
        info = {"notice_pdf": fetched["notice"], "skipped": fetched["skipped"], "rfp_guessed": fetched["rfp_guessed"],
                "market_api": market_api, "market_requested": body.market_api}
        if not fetched["rfp"]:
            return {"needs_upload": True, "folder_id": folder.name, **info}
        return {**start_job(folder, {"mode": body.mode, "notice": fetched["notice"], "market_api": market_api}), **info}

    # --- 전체 검토 시작: 검토 대상 공고를 한 건씩 차례로 (JobManager는 한 번에 하나) ---------------
    batch: dict = {"running": False, "stopped": False, "total": 0, "current": None, "done": [], "waiting": [], "failed": [], "mode": None}

    def run_batch(targets: list[tuple[str, dict]], body: RunOptions) -> None:
        for nid, entry in targets:
            if batch["stopped"]:
                break
            item = {"id": nid, "name": entry["bidNtceNm"]}
            batch["current"] = item
            try:
                started = prepare_and_start(nid, entry, body)
            except HTTPException as exc:
                batch["failed"].append({**item, "reason": exc.detail})
                continue
            except Exception as exc:  # network/attachment errors must not kill the rest of the batch
                batch["failed"].append({**item, "reason": str(exc)})
                continue
            if started.get("needs_upload"):
                batch["waiting"].append(item)
                continue
            # ponytail: poll the single job slot; an event from on_finish would miss failed runs
            while manager.current() is not None:
                time.sleep(0.2)
            job = manager.get(started["job_id"])
            ok = job is not None and job.status == "done"
            (batch["done"] if ok else batch["failed"]).append(item if ok else {**item, "reason": "실행 실패 — 진행 로그 확인"})
        batch.update(running=False, current=None)

    @app.post("/api/notices/start-all")
    def start_all(body: RunOptions, _: str = Depends(require("staff"))) -> dict:
        _check_options(body.mode, False, True)
        if manager.current() or batch["running"]:
            raise HTTPException(409, BUSY)
        store = notices.load_notices()["notices"]
        targets = sorted(((nid, n) for nid, n in store.items() if n["verdict"] == "검토 대상" and not n.get("case_folder")),
                         key=lambda t: t[1]["judged"].get("deadline") or "9999")
        if not targets:
            raise HTTPException(409, "새로 시작할 검토 대상 공고가 없어요.")
        options = RunOptions(mode=body.mode, market_api=False)  # 시장조사는 공고별로 켜서 다시 실행
        batch.update(running=True, stopped=False, total=len(targets), current=None, done=[], waiting=[], failed=[], mode=body.mode)
        threading.Thread(target=run_batch, args=(targets, options), daemon=True).start()
        return {"total": len(targets), "mode": body.mode}

    @app.get("/api/notices/batch")
    def batch_state() -> dict:
        return batch

    @app.post("/api/notices/batch/stop")
    def stop_batch(_: str = Depends(require("staff"))) -> dict:
        batch["stopped"] = True  # the run in progress finishes; the rest are not started
        return batch

    # --- 검토 실행 -------------------------------------------------------------------
    @app.post("/api/runs", status_code=201)
    async def start_run(rfp: UploadFile = File(...), notice: UploadFile | None = File(None),
                        mode: str = Form("offline"), market_api: bool = Form(False), folder_id: str | None = Form(None),
                        _: str = Depends(require("staff"))) -> dict:
        existing = None
        if folder_id is not None:
            existing = runs.OUTPUTS / "web" / folder_id
            if not WEB_FOLDER.match(folder_id) or not existing.is_dir():
                raise HTTPException(400, "잘못된 검토 폴더입니다.")
            if any(runs._is_report(p) for p in existing.glob("*.json")):
                # a report exists: changing it now would bypass the approval flow; use 다시 실행 instead
                raise runs.Conflict("이미 보고서가 있는 검토 건이에요. 결재 흐름의 '다시 실행'을 쓰세요.", runs.workflow(existing)["status"])
        has_notice = bool(notice) or bool(existing and (existing / "notice.pdf").is_file())
        _check_options(mode, market_api, has_notice)
        if manager.current():
            raise HTTPException(409, BUSY)
        rfp_bytes = await _read_pdf(rfp, "제안요청서")
        notice_bytes = await _read_pdf(notice, "입찰공고문") if notice else None
        folder = existing or _new_folder()
        (folder / "rfp.pdf").write_bytes(rfp_bytes)
        if notice_bytes:
            (folder / "notice.pdf").write_bytes(notice_bytes)
        _write_upload(folder, rfp_name=rfp.filename, mode=mode, market_api=market_api,
                      **({"notice_name": notice.filename} if notice else {}))
        try:
            return start_job(folder, {"mode": mode, "notice": has_notice, "market_api": market_api})
        except HTTPException:
            if not existing:
                shutil.rmtree(folder)
            raise

    @app.get("/api/jobs/{job_id}/events")
    def job_events(job_id: str) -> StreamingResponse:
        if not manager.get(job_id):
            raise HTTPException(404, "실행을 찾을 수 없습니다.")

        async def sse():
            async for event in manager.stream(job_id):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/runs")
    def list_runs() -> list[dict]:
        return runs.list_runs()

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        try:
            return runs.load_run(run_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.post("/api/runs/{run_id}/core-review")
    def core_review(run_id: str, body: CoreReviewBody, role: str = Depends(require("staff"))) -> dict:
        try:
            path = runs.decode_run_id(run_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
        if not runs.load_run(run_id)["is_current"]:
            raise runs.Conflict("옛 실행 결과에서는 핵심정보 검토를 진행할 수 없어요.", runs.workflow(path.parent)["status"])
        status = runs.workflow(path.parent)["status"]
        if status not in ("검토 완료", "보완 요청"):
            raise runs.Conflict(f"결재 상태가 {status}인 실행은 핵심정보 검토를 변경할 수 없어요.", status)
        pdf_path = runs._find_pdf(path)
        if pdf_path is None:
            raise runs.Conflict("검토할 RFP 원본 PDF가 없습니다.", status)

        prior = runs.load_core_review(path.parent, path)
        if body.action == "approve_and_run_t2":
            if not body.reason.strip():
                raise HTTPException(400, "미확인/충돌 항목을 확인했다는 검토 사유를 입력하세요.")
            if not prior or prior.get("decision") != "human_review_required":
                raise runs.Conflict("먼저 T1.5 검토를 실행하고 사람 검토 결과를 확인해야 합니다.", status)
            expected_id = (prior.get("verification") or {}).get("document_id")
            if expected_id:
                current_id = hashlib.sha256(pdf_path.read_bytes()).hexdigest()[:16]
                if current_id != expected_id:
                    raise runs.Conflict("T1.5 실행 후 RFP 원본이 바뀌었습니다. 먼저 검증을 다시 실행하세요.", status)
        elif body.action == "reject":
            if not body.reason.strip():
                raise HTTPException(400, "검토 반려 사유를 입력하세요.")
            if not prior or prior.get("decision") != "human_review_required":
                raise runs.Conflict("먼저 T1.5 검토를 실행해야 반려할 수 있습니다.", status)
            result = {**prior, "decision": "human_rejected", "review_decision": "rejected",
                      "review_note": body.reason.strip(), "reviewer": role}
            return runs.save_core_review(run_id, result, action=body.action, reason=body.reason, role=role)
        else:
            if prior and prior.get("decision") == "human_approved_company_matching_complete":
                raise runs.Conflict("T2 분석까지 끝난 실행입니다. 새 검토 실행을 만들어 주세요.", status)

        try:
            from ..rag.workflow import run_bid_review
            company_file = PROJECT_ROOT / "data/company/company"
            result = run_bid_review(
                pdf_path,
                company_path=company_file if company_file.is_file() else None,
                notice_no=(prior or {}).get("notice_no"),
                retrieval_limit=1,
                notice_pdf_path=path.parent / "notice.pdf",
                review_decision="approved" if body.action == "approve_and_run_t2" else None,
                review_note=body.reason,
                reviewer=role,
            )
        except ImportError as exc:
            raise HTTPException(503, "LangGraph 의존성을 사용할 수 없습니다. 프로젝트 가상환경 설치를 확인하세요.") from exc
        except Exception as exc:
            raise HTTPException(500, f"핵심정보 검토 실행 실패: {type(exc).__name__}: {exc}") from exc
        return runs.save_core_review(run_id, result, action=body.action, reason=body.reason, role=role)

    @app.get("/api/runs/{run_id}/files/{name}")
    def get_file(run_id: str, name: str) -> Response:
        if name == "checklist.json":  # the full evaluation (AI values, overrides, summary), not just the overrides file
            try:
                checklist = runs.load_run(run_id)["checklist"]
            except ValueError:
                raise HTTPException(404, "파일을 찾을 수 없습니다.") from None
            return JSONResponse(checklist, headers={"Content-Disposition": 'attachment; filename="checklist.json"'})
        try:
            path = runs.resolve_file(run_id, name)
        except ValueError:
            path = None
        if path is None:
            raise HTTPException(404, "파일을 찾을 수 없습니다.")
        inline = name == "rfp.pdf"
        return FileResponse(path, filename=runs._nfc(path.name), content_disposition_type="inline" if inline else "attachment")

    # --- 체크리스트 · 결재 · 결정 · 다시 실행 ---------------------------------------------
    @app.put("/api/runs/{run_id}/checklist")
    def put_checklist(run_id: str, body: ChecklistBody, _: str = Depends(require("staff"))) -> dict:
        try:
            return runs.save_overrides(run_id, body.overrides)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.put("/api/runs/{run_id}/decision")
    def put_decision(run_id: str, body: DecisionBody, _: str = Depends(require("director"))) -> dict:
        try:
            return runs.save_decision(run_id, body.decision, body.reason)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.put("/api/runs/{run_id}/workflow")
    def put_workflow(run_id: str, body: WorkflowBody, role: str = Depends(require("staff", "director"))) -> dict:
        if body.action == "rerun":
            raise HTTPException(400, "다시 실행은 /rerun으로 요청하세요.")
        try:
            runs.decode_run_id(run_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
        try:
            return runs.transition(run_id, body.action, role, body.reason, body.request)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/runs/{run_id}/rerun")
    def rerun(run_id: str, body: RunOptions, role: str = Depends(require("staff"))) -> dict:
        try:
            inputs = runs.rerun_inputs(run_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
        status = runs.workflow(runs.case_of(run_id))["status"]
        if inputs is None:
            raise runs.Conflict("원본 RFP를 찾을 수 없어 다시 실행할 수 없어요.", status)
        _check_options(body.mode, body.market_api, inputs["notice"])
        if status != "보완 요청" or not runs.load_run(run_id)["is_current"]:
            raise runs.Conflict(f"지금 상태({status})에서는 다시 실행할 수 없어요.", status)
        if manager.current():
            raise HTTPException(409, BUSY)
        # The case moves to 검토 완료 only when the re-run produced a new report; a failed run keeps 보완 요청.
        return start_job(runs.case_of(run_id), {"mode": body.mode, "notice": inputs["notice"], "market_api": body.market_api,
                                                "rfp_path": inputs["rfp_path"]},
                         after=lambda report: runs.transition(runs.encode_run_id(report), "rerun", role))

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app
