"""Review runs = report files under outputs/ (CLI and web alike), plus their sidecar JSON.

Filenames may be NFC or NFD (folders copied from Windows vs created on macOS), so every
name comparison goes through NFC.
"""
from __future__ import annotations

import base64
import binascii
from datetime import datetime
import json
from pathlib import Path
import re
import threading
import unicodedata
from zoneinfo import ZoneInfo

from ..checklist import apply_overrides, evaluate, summarize
from ..settings import PROJECT_ROOT, load_config
from . import criteria

OUTPUTS = PROJECT_ROOT / "outputs"
INPUT_DIR = PROJECT_ROOT / "data/input"
AWARDS_FILE = PROJECT_ROOT / "data/market/itcen_group_awards_3y.json"
REPORT_NAME = re.compile(r"^입찰검토보고서_(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(\d{2})_[0-9a-f]{6}\.json$")
DECISIONS = ("Go", "보류", "No-Go")
KNOCKOUT_VALUES = ("충족", "확인 필요", "미충족")
SCORE_VALUES = (0, 1, 2, None)
KST = ZoneInfo("Asia/Seoul")
STATUSES = ("검토 완료", "결재 대기", "보완 요청", "결정 완료")
REJECT_REQUESTS = ("재실행", "수기 보완")
# action -> (role, from status, to status). Decisions go through save_decision.
TRANSITIONS = {
    "submit": ("staff", "검토 완료", "결재 대기"),
    "resubmit": ("staff", "보완 요청", "결재 대기"),
    "reject": ("director", "결재 대기", "보완 요청"),
    "rerun": ("staff", "보완 요청", "검토 완료"),
}
_workflow_lock = threading.Lock()  # ponytail: one lock for every case; per-case locks if this ever serves many users


class Conflict(Exception):
    """Transition not allowed in the case's current state (HTTP 409)."""

    def __init__(self, message: str, status: str):
        super().__init__(message)
        self.status = status


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _is_report(path: Path) -> bool:
    return bool(REPORT_NAME.match(_nfc(path.name)))


def encode_run_id(report_path: Path) -> str:
    rel = report_path.relative_to(OUTPUTS).as_posix()
    return base64.urlsafe_b64encode(rel.encode("utf-8")).decode("ascii").rstrip("=")


def decode_run_id(run_id: str) -> Path:
    try:
        rel = base64.urlsafe_b64decode(run_id + "=" * (-len(run_id) % 4)).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise ValueError("잘못된 실행 ID") from None
    parts = Path(rel).parts
    if not parts or Path(rel).is_absolute() or ".." in parts:
        raise ValueError("잘못된 실행 ID")
    path = OUTPUTS / rel
    if not path.resolve().is_relative_to(OUTPUTS.resolve()) or not _is_report(path) or not path.is_file():
        raise ValueError("실행을 찾을 수 없습니다.")
    return path


def _reports() -> list[Path]:
    return [p for p in OUTPUTS.rglob("*.json") if _is_report(p)] if OUTPUTS.exists() else []


def _created_at(path: Path) -> str:
    day, hh, mm, ss = REPORT_NAME.match(_nfc(path.name)).groups()
    return f"{day}T{hh}:{mm}:{ss}+09:00"


def _sidecar(path: Path, kind: str) -> Path:
    return path.with_name(f"{path.name.removesuffix('.json')}.{kind}.json")


def _read_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _via_web(path: Path) -> bool:
    return _nfc(path.relative_to(OUTPUTS).parts[0]) == "web"


def _mode(path: Path) -> str:
    """offline | live for every run: the per-report record wins (re-runs may change mode), then folder, upload.json."""
    recorded = (_read_json(_sidecar(path, "run")) or {}).get("mode")
    if recorded in ("offline", "live"):
        return recorded
    first = _nfc(path.relative_to(OUTPUTS).parts[0])
    if first in ("offline", "live"):
        return first
    upload = _read_json(path.parent / "upload.json") or {}
    if upload.get("mode") in ("offline", "live"):
        return upload["mode"]
    manifest_mode = (_read_json(path.parent / "crew_execution_manifest.json") or {}).get("mode", "")
    return "live" if manifest_mode == "live" else "offline" if manifest_mode else "unknown"


def _project_name(path: Path) -> str:
    upload = _read_json(path.parent / "upload.json")
    if upload and upload.get("project_name"):  # started from a G2B notice: the notice title
        return _nfc(upload["project_name"])
    if upload and upload.get("rfp_name"):
        return _nfc(upload["rfp_name"]).removesuffix(".pdf")
    return re.sub(r"^\d+_", "", _nfc(path.parent.name))


def _is_latest(path: Path) -> bool:
    siblings = [p for p in path.parent.glob("*.json") if _is_report(p)]
    return max(siblings, key=_created_at) == path


def _decision(path: Path) -> dict | None:
    return _read_json(_sidecar(path, "decision"))


def core_review_path(case: Path) -> Path:
    return case / "langgraph_review.json"


def load_core_review(case: Path, report: Path | None = None) -> dict | None:
    result = _read_json(core_review_path(case))
    if result is None:
        return None
    if report is not None and result.get("reviewed_report") != report.name:
        return None
    return result


def save_core_review(run_id: str, result: dict, *, action: str, reason: str, role: str) -> dict:
    """Persist the LangGraph state and an append-only human-review audit entry."""
    path = decode_run_id(run_id)
    case = path.parent
    with _workflow_lock:
        if path != current_report(case):
            raise Conflict("옛 보고서는 핵심정보 검토를 진행할 수 없어요.", workflow(case)["status"])
        audit_path = case / "core_review_audit.json"
        audit = _read_json(audit_path) or {"history": []}
        result = {**result, "reviewed_report": path.name}
        entry = {
            "action": action, "role": role, "reason": reason.strip(), "at": _now(),
            "report": path.name,
            "decision": result.get("decision"),
            "verification_status": (result.get("verification") or {}).get("status"),
        }
        audit["history"].append(entry)
        audit["latest"] = entry
        audit["status"] = result.get("decision", "unknown")
        core_review_path(case).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"result": result, "audit": audit}


def case_of(run_id: str) -> Path:
    return decode_run_id(run_id).parent


def current_report(case: Path) -> Path:
    return max((p for p in case.glob("*.json") if _is_report(p)), key=_created_at)


def workflow(case: Path) -> dict:
    return _read_json(case / "workflow.json") or {"status": "검토 완료", "history": []}


def _now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _record(path: Path, action: str, role: str, to: str, allowed_from: tuple[str, ...], **extra) -> dict:
    """Append one transition for the case holding `path`; caller holds _workflow_lock."""
    case = path.parent
    flow = workflow(case)
    if path != current_report(case):
        raise Conflict("옛 보고서는 결재할 수 없어요. 현재 보고서에서 진행하세요.", flow["status"])
    if flow["status"] not in allowed_from:
        raise Conflict(f"지금 상태({flow['status']})에서는 할 수 없어요.", flow["status"])
    flow["history"].append({"action": action, "from": flow["status"], "to": to, "role": role, "at": _now(),
                            "report": path.name, **{k: v for k, v in extra.items() if v}})
    flow["status"] = to
    (case / "workflow.json").write_text(json.dumps(flow, ensure_ascii=False, indent=2), encoding="utf-8")
    return flow


def transition(run_id: str, action: str, role: str, reason: str = "", request: str = "") -> dict:
    if action not in TRANSITIONS:
        raise ValueError(f"알 수 없는 결재 동작: {action}")
    needed, source, target = TRANSITIONS[action]
    if role != needed:
        raise PermissionError("사업본부장만 할 수 있어요." if needed == "director" else "사업기획 담당자만 할 수 있어요.")
    if action == "reject" and (not reason.strip() or request not in REJECT_REQUESTS):
        raise ValueError("반려 사유와 요청 방식(재실행/수기 보완)을 입력하세요")
    path = decode_run_id(run_id)
    with _workflow_lock:
        if action in ("submit", "resubmit"):
            core_review = load_core_review(path.parent, path) or {}
            if core_review.get("decision") not in (
                "company_matching_complete", "human_approved_company_matching_complete"
            ):
                raise Conflict("결재를 올리기 전에 T1.5 핵심정보 검토와 T2 회사 역량 분석을 완료하세요.", workflow(path.parent)["status"])
        return _record(path, action, role, target, (source,), reason=reason.strip(), request=request)


def _upload(case: Path) -> dict:
    return _read_json(case / "upload.json") or {}


def list_cases() -> list[dict]:
    cases = []
    for case in sorted({p.parent for p in _reports()}):
        path = current_report(case)
        report = _read_json(path) or {}
        flow = workflow(case)
        decision = _decision(path)
        submits = [h["at"] for h in flow["history"] if h["action"] in ("submit", "resubmit")]
        debug = case / "debug"
        checklist = _checklist(path, report, _read_json(debug / "01_rfp_analysis.json"), _read_json(debug / "02_capability_matching.json"),
                               load_core_review(case, path))
        cases.append({
            "case_id": case.relative_to(OUTPUTS).as_posix(), "run_id": encode_run_id(path), "project_name": _project_name(path),
            "created_at": _created_at(path), "mode": _mode(path), "via_web": _via_web(path),
            "notice_id": _upload(case).get("notice_id"), "status": flow["status"],
            "ai_recommendation": report.get("decision_status"), "checklist_recommendation": checklist["summary"]["recommendation"],
            "decision": decision["decision"] if decision else None, "decision_reason": decision["reason"] if decision else None,
            "decided_at": decision["decided_at"] if decision else None,
            "rejections": sum(h["action"] == "reject" for h in flow["history"]),
            "last_reject": next((h for h in reversed(flow["history"]) if h["action"] == "reject"), None),
            "submitted_at": submits[-1] if submits else None,
            "amount_krw": (report.get("project_scale") or {}).get("amount_krw"),
        })
    return sorted(cases, key=lambda c: c["created_at"], reverse=True)


def record_run(report: Path, mode: str) -> None:
    """Remember how this report was produced (web jobs)."""
    _sidecar(report, "run").write_text(json.dumps({"mode": mode}, ensure_ascii=False), encoding="utf-8")


def rerun_inputs(run_id: str) -> dict | None:
    path = decode_run_id(run_id)
    rfp = _find_pdf(path)
    return {"rfp_path": str(rfp), "notice": (path.parent / "notice.pdf").is_file()} if rfp else None


def list_runs() -> list[dict]:
    runs = []
    for path in _reports():
        report = _read_json(path) or {}
        decision = _decision(path)
        runs.append({"id": encode_run_id(path), "project_name": _project_name(path), "created_at": _created_at(path),
                     "mode": _mode(path), "via_web": _via_web(path), "ai_recommendation": report.get("decision_status"),
                     "decision": decision["decision"] if decision else None})
    return sorted(runs, key=lambda r: r["created_at"], reverse=True)


def _checklist(path: Path, report: dict, rfp: dict | None, capability: dict | None, core_review: dict | None = None) -> dict:
    name, config = criteria.checklist_for(path.parent)
    awards = (_read_json(AWARDS_FILE) or {}).get("awards", [])
    items = apply_overrides(evaluate(report, rfp, capability, config, awards, core_review),
                            _read_json(_sidecar(path, "checklist")) or {})
    return {"version": config["version"], "criteria_set": name, "items": items, "summary": summarize(items, config)}


def load_run(run_id: str) -> dict:
    path = decode_run_id(run_id)
    report = _read_json(path)
    details = _is_latest(path)
    debug = path.parent / "debug"
    rfp, capability, market = (
        (_read_json(debug / "01_rfp_analysis.json"), _read_json(debug / "02_capability_matching.json"),
         _read_json(debug / "03_market_research.json")) if details else (None, None, None))
    return {
        "id": run_id, "project_name": _project_name(path), "created_at": _created_at(path), "mode": _mode(path), "via_web": _via_web(path),
        "report": report, "rfp": rfp, "capability": capability, "market": market, "details_available": details,
        "checklist": _checklist(path, report, rfp, capability, load_core_review(path.parent, path)), "decision": _decision(path),
        "core_review": load_core_review(path.parent, path),
        "core_review_audit": _read_json(path.parent / "core_review_audit.json"),
        "workflow": workflow(path.parent), "is_current": details, "rerun_available": _find_pdf(path) is not None,
        "files": {"report_md": "report.md" if path.with_suffix(".md").is_file() else None, "report_json": "report.json",
                  "pdf": "rfp.pdf" if _find_pdf(path) else None,
                  "notice_pdf": "notice.pdf" if (path.parent / "notice.pdf").is_file() else None},
    }


def save_overrides(run_id: str, overrides: dict) -> dict:
    path = decode_run_id(run_id)
    config = criteria.checklist_for(path.parent)[1]
    kinds = {**{i["id"]: KNOCKOUT_VALUES for i in config["knockout"]}, **{i["id"]: SCORE_VALUES for i in config["scoring"]}}
    for key, value in overrides.items():
        if key not in kinds or value not in kinds[key] or isinstance(value, bool):
            raise ValueError(f"체크리스트 값이 올바르지 않습니다: {key}={value!r}")
    flow = workflow(path.parent)
    if not _is_latest(path) or flow["status"] not in ("검토 완료", "보완 요청"):
        raise Conflict("결재 중이거나 결정된 건, 옛 보고서의 체크리스트는 고칠 수 없어요.", flow["status"])
    _sidecar(path, "checklist").write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_run(run_id)["checklist"]


def save_decision(run_id: str, decision: str, reason: str) -> dict:
    path = decode_run_id(run_id)
    if decision not in DECISIONS:
        raise ValueError("결정은 Go, 보류, No-Go 중 하나여야 합니다.")
    if not reason or not reason.strip():
        raise ValueError("결정 사유를 입력하세요")
    with _workflow_lock:
        _record(path, "decide", "director", "결정 완료", ("결재 대기", "결정 완료"), reason=reason.strip(), decision=decision)
        previous = _decision(path)
        history = (previous or {}).pop("decision_history", []) + ([previous] if previous else [])
        record = {"decision": decision, "reason": reason.strip(), "decided_at": _now(),
                  "checklist_snapshot": load_run(run_id)["checklist"], "decision_history": history}
        _sidecar(path, "decision").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def _find_pdf(path: Path) -> Path | None:
    uploaded = path.parent / "rfp.pdf"
    if uploaded.is_file():
        return uploaded
    rfp = _read_json(path.parent / "debug/01_rfp_analysis.json") or {}
    evidence = (rfp.get("project_summary") or {}).get("evidence") or []
    name = _nfc(evidence[0].get("document", "")) if evidence else ""
    if not name.lower().endswith(".pdf") or not INPUT_DIR.is_dir():
        return None
    match = next((p for p in INPUT_DIR.iterdir() if _nfc(p.name) == name), None)
    return match if match and match.resolve().is_relative_to(INPUT_DIR.resolve()) else None


def resolve_file(run_id: str, name: str) -> Path | None:
    path = decode_run_id(run_id)
    candidates = {"report.json": path, "report.md": path.with_suffix(".md"), "rfp.pdf": None,
                  "notice.pdf": path.parent / "notice.pdf",
                  "decision.json": _sidecar(path, "decision"), "checklist.json": _sidecar(path, "checklist")}
    if name not in candidates:
        raise ValueError("허용되지 않은 파일")
    target = _find_pdf(path) if name == "rfp.pdf" else candidates[name]
    return target if target and target.is_file() else None
