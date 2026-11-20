"""Runs the existing CLI as a subprocess and turns its stdout into UI events."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import AsyncIterator, Callable, Literal

from ..settings import PROJECT_ROOT
from . import runs

NODES = ("agent1", "agent2", "agent3", "agent4")
STATES = ("start", "done", "skip")
MIN_SECRET_LENGTH = 8


class JobBusy(Exception):
    """Another review is still running (one at a time)."""


@dataclass
class Job:
    id: str
    output_dir: Path
    events: list[dict] = field(default_factory=list)
    status: Literal["running", "done", "error"] = "running"
    report_run_id: str | None = None
    on_finish: Callable[[Path], None] | None = None  # called with the new report before the done event


def build_command(output_dir: Path, options: dict) -> list[str]:
    rfp = options.get("rfp_path") or str(output_dir / "rfp.pdf")
    if options.get("mode", "offline") == "offline":
        # 일원화: offline 웹 실행은 LangGraph(T1.5 검증 먼저). live(CrewAI LLM 브리핑)는 아직 run.py.
        command = [sys.executable, "-m", "bid_review.web.langgraph_job", "--rfp", rfp, "--output", str(output_dir)]
        return command + (["--notice-pdf", str(output_dir / "notice.pdf")] if options.get("notice") else [])
    command = [sys.executable, "run.py", "--mode", options.get("mode", "offline"), "--rfp", rfp]
    if options.get("notice"):
        command += ["--notice-pdf", str(output_dir / "notice.pdf")]
    if options.get("market_api"):
        command.append("--allow-market-api")
    return command + ["--output", str(output_dir), "--save-intermediate", "--verbose"]


def parse_line(line: str) -> dict:
    parts = line.split()
    if len(parts) == 3 and parts[0] == "[STAGE]" and parts[1] in NODES and parts[2] in STATES:
        return {"type": "stage", "node": parts[1], "state": parts[2]}
    return {"type": "log", "text": line}


def mask(line: str, secrets: list[str]) -> str:
    for secret in secrets:
        if len(secret) >= MIN_SECRET_LENGTH:
            line = line.replace(secret, "***")
    return line


class JobManager:
    def __init__(self, command_builder: Callable[[Path, dict], list[str]], secrets: list[str]):
        self.command_builder = command_builder
        self.secrets = secrets
        self._jobs: dict[str, Job] = {}
        self._current: Job | None = None
        self._lock = threading.Lock()

    def start(self, output_dir: Path, options: dict, on_finish: Callable[[Path], None] | None = None) -> Job:
        with self._lock:
            if self._current is not None:
                raise JobBusy()
            job = Job(id=output_dir.name, output_dir=output_dir, on_finish=on_finish)
            self._jobs[job.id] = job
            self._current = job
        process = subprocess.Popen(
            self.command_builder(output_dir, options), cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"},
        )
        threading.Thread(target=self._pump, args=(job, process), daemon=True).start()
        return job

    def current(self) -> Job | None:
        return self._current

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def _pump(self, job: Job, process: subprocess.Popen) -> None:
        stages: dict[str, str] = {}
        failed = False
        try:
            for raw in process.stdout:
                event = parse_line(mask(raw.rstrip("\n"), self.secrets))
                if event["type"] == "stage":
                    stages[event["node"]] = event["state"]
                elif event["text"].startswith("[실패]"):
                    failed = True
                job.events.append(event)
            code = process.wait()
            reports = sorted((p for p in job.output_dir.glob("*.json") if runs.REPORT_NAME.match(runs._nfc(p.name))),
                             key=lambda p: p.stat().st_mtime)
            if code == 0 and not failed and reports:
                job.report_run_id = runs.encode_run_id(reports[-1])
                if job.on_finish:
                    try:
                        job.on_finish(reports[-1])
                    except Exception as exc:  # the report exists; only the web bookkeeping failed
                        job.events.append({"type": "log", "text": f"[웹] 실행 후 기록 실패: {type(exc).__name__}"})
                job.events.append({"type": "done", "run_id": job.report_run_id})
                job.status = "done"
                return
            for node, state in stages.items():
                if state == "start":
                    job.events.append({"type": "stage", "node": node, "state": "fail"})
            logs = [e["text"] for e in job.events if e["type"] == "log" and e["text"].strip()]
            job.events.append({"type": "error", "message": "\n".join(logs[-3:]) or f"실행 종료 코드 {code}"})
            job.status = "error"
        except Exception as exc:  # the stream must always end, or the UI looks frozen
            process.kill()
            job.events.append({"type": "error", "message": f"웹 서버 내부 오류: {type(exc).__name__}"})
            job.status = "error"
        finally:
            with self._lock:
                self._current = None

    async def stream(self, job_id: str) -> AsyncIterator[dict]:
        job = self._jobs[job_id]
        sent = 0
        while True:
            while sent < len(job.events):
                event = job.events[sent]
                sent += 1
                yield event
                if event["type"] in ("done", "error"):
                    return
            await asyncio.sleep(0.2)  # ponytail: polling loop; a per-job asyncio.Queue if latency ever matters
