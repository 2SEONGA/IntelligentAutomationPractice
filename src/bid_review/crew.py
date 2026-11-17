
from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo
from uuid import uuid4

from dotenv import load_dotenv

# CrewAI를 가져오기 전에 설정해야 합니다. 일부 환경에서는 기본 사용자 저장소에 쓸 수 없습니다.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("CREWAI_STORAGE_DIR", str(PROJECT_ROOT / ".crewai_storage"))
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TELEMETRY_ENABLED", "false")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("CREWAI_DISABLE_VERSION_CHECK", "true")
from crewai import Agent, Crew, Process, Task  # noqa: E402
from crewai.tools import tool  # noqa: E402

from .handoff import build_agent_input_summaries
from .pdf_context import artifact_payload, debug_page_analysis_payload, json_size
from .agent01_rfp import RFPExtractor, merge_document_results
from .agent03_g2b import G2BAwardClient, compact_awards_json
from .agent02_match import CapabilityMatcher
from .models import (
    BidReviewReport,
    CapabilityMatchingResult,
    CapabilityReviewSummary,
    CompanyProfile,
    ExecutiveDecisionSummary,
    MarketResearchResult,
    MarketReviewSummary,
    RFPAnalysisResult,
    RFPReviewSummary,
)
from .agent04_render import capability_markdown, market_markdown, rfp_markdown
from .agent03_market import MarketResearcher
from .agent04_report import ReportWriter, report_markdown
from .settings import load_config, load_settings
from .policy import render_prompt


_PDF_EVIDENCE_LIMITS = load_settings()["pdf_evidence"]
MAX_PDF_EVIDENCE_PAGES = int(_PDF_EVIDENCE_LIMITS["max_pages"])
MAX_PDF_EVIDENCE_CHARS_PER_PAGE = int(_PDF_EVIDENCE_LIMITS["max_chars_per_page"])
_PDF_EVIDENCE_PAGE_ALLOWLIST: dict[str, set[int]] = {}
TASK_NAMES = ["rfp_analysis", "capability_matching", "market_research", "report_writing"]


def _stage(node: str, state: str) -> None:
    """Machine-readable progress line for the web UI; printed regardless of --verbose."""
    print(f"[STAGE] {node} {state}", flush=True)


def _stage_gate_hold() -> None:
    _stage("agent1", "done")
    for node in ("agent2", "agent3", "agent4"):
        _stage(node, "skip")


def _report_artifact_stem() -> str:
    """Return the user-facing final-report name in Korea Standard Time."""
    report_timestamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d_%H%M%S")
    return f"입찰검토보고서_{report_timestamp}_{uuid4().hex[:6]}"


def _pdf_evidence_key(path: str | Path) -> str:
    return str(Path(path).resolve())


def _set_pdf_evidence_allowlist(path: str | Path, pages: list[int]) -> None:
    """Register the only pages a live RFP Agent may read from this PDF."""
    _PDF_EVIDENCE_PAGE_ALLOWLIST[_pdf_evidence_key(path)] = set(pages)


def _parse_pdf_page_spec(page_spec: str, total_pages: int) -> list[int]:
    """Parse a narrow 1-based page specification such as ``4,5,31-33``."""
    if not page_spec or not page_spec.strip():
        raise ValueError("pages must name specific pages, for example '4,5,31-33'.")

    selected: set[int] = set()
    for token in page_spec.replace(" ", "").split(","):
        if not token:
            raise ValueError("pages must be a comma-separated page list or ranges.")
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            if not start_text.isdigit() or not end_text.isdigit():
                raise ValueError("page ranges must use numbers, for example '31-33'.")
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError("page ranges must be in ascending order.")
            selected.update(range(start, end + 1))
        elif token.isdigit():
            selected.add(int(token))
        else:
            raise ValueError("pages must use numbers, for example '4,5,31-33'.")

    pages = sorted(selected)
    if any(page < 1 or page > total_pages for page in pages):
        raise ValueError(f"pages must be between 1 and {total_pages}.")
    if len(pages) > MAX_PDF_EVIDENCE_PAGES:
        raise ValueError(f"read at most {MAX_PDF_EVIDENCE_PAGES} pages per call; narrow the evidence request.")
    return pages


@tool("PDF evidence reader")
def pdf_evidence_reader(path: str, pages: str) -> str:
    """Read only named PDF pages (e.g. pages='4,5,31-33'); full-document reads are forbidden."""
    from pypdf import PdfReader

    reader = PdfReader(path)
    selected = _parse_pdf_page_spec(pages, len(reader.pages))
    allowed = _PDF_EVIDENCE_PAGE_ALLOWLIST.get(_pdf_evidence_key(path))
    if allowed is not None:
        disallowed = sorted(set(selected) - allowed)
        if disallowed:
            raise ValueError(f"pages {disallowed} are not approved for runtime evidence reading.")
    chunks: list[str] = []
    for number in selected:
        text = reader.pages[number - 1].extract_text() or "[NO EXTRACTED TEXT]"
        if len(text) > MAX_PDF_EVIDENCE_CHARS_PER_PAGE:
            text = f"{text[:MAX_PDF_EVIDENCE_CHARS_PER_PAGE]}\n[PAGE TEXT TRUNCATED: use a narrower source question]"
        chunks.append(f"[PDF page {number}]\n{text}")
    return "\n\n".join(chunks)


@tool("Company evidence reader")
def company_evidence_reader(path: str) -> str:
    """Read company capability evidence JSON by path."""
    return Path(path).read_text(encoding="utf-8")


@tool("Auditable market evidence reader")
def market_evidence_reader(path: str) -> str:
    """Read only source-linked market evidence supplied by a researcher."""
    if not path:
        return "No reviewed market-evidence file was supplied. Report unknown items; do not invent web results."
    return Path(path).read_text(encoding="utf-8")


@tool("나라장터 낙찰정보 조회")
def g2b_award_search(project_name: str, buyer: str = "") -> str:
    """Search source-linked G2B service bid-opening records using G2B_SERVICE_KEY."""
    return compact_awards_json(project_name, buyer or None, load_settings("config/settings.yaml")["market"])


@tool("Validated analysis artifact reader")
def analysis_artifact_reader(path: str) -> str:
    """Read a validated RFP, capability, or market JSON artifact for report writing."""
    return Path(path).read_text(encoding="utf-8")


class BidReviewCrew:
    """Four-agent CrewAI topology plus an evidence-first deterministic v1 executor.

    ``mode=live`` runs the defined CrewAI tasks using the configured LLM. ``offline``
    is intentionally used by CI/demo: it invokes the same task topology's domain
    services and validates every intermediate Pydantic artifact without an API key.
    """

    def _trace_box(self, title: str, rows: list[str]) -> None:
        """Show observable Crew hand-offs without exposing prompts or hidden reasoning."""
        if not self.trace:
            return
        width = 74
        print(f"\n╭{'─' * width}")
        print(f"│ {title}")
        for row in rows:
            print(f"│   {row}")
        print(f"╰{'─' * width}")

    def _trace_start(self, step: int, label: str) -> None:
        handoffs = {
            1: "입력: RFP compact summary → 출력: RFPReviewSummary",
            2: "입력: Agent 1 요약 + 역량 compact summary → 출력: CapabilityReviewSummary",
            3: "입력: Agent 1 요약 + 시장 compact summary → 출력: MarketReviewSummary",
            4: "입력: Agent 1·2·3 요약 + 실행요약 → 출력: ExecutiveDecisionSummary",
        }
        self._trace_box(f"[{step}/4] {label} 시작", [handoffs[step], "LLM 도구 호출: 없음 (검증된 compact 정보만 사용)"])

    def _trace_completion(self, step: int, label: str, value) -> None:
        if isinstance(value, RFPReviewSummary):
            rows = [
                f"결과: document_status={value.document_status}, proceed={value.proceed_to_next_stage}",
                f"전달 항목: 필수조건 {len(value.top_mandatory_conditions)} / 충돌 {len(value.top_conflicts)} / 확인 {len(value.critical_missing_information)}",
                "전달 대상: Capability Matching Agent, Market Research Agent",
            ]
        elif isinstance(value, CapabilityReviewSummary):
            rows = [
                f"결과: overall_fit={value.overall_fit}, blocking_risk={value.blocking_risk}",
                f"전달 항목: 원본확인 {len(value.confirmed_company_evidence)} / 공개강점 {len(value.public_reference_strengths)} / 입찰증빙확인 {len(value.missing_bid_evidence)}",
                "전달 대상: Report Writing Agent",
            ]
        elif isinstance(value, MarketReviewSummary):
            rows = [
                f"결과: research_status={value.research_status}, comparable_cases={value.comparable_cases_count}",
                f"전달 항목: 시장관찰 {len(value.market_observations)} / 한계 {len(value.search_limitations)}",
                "전달 대상: Report Writing Agent",
            ]
        else:
            rows = [
                f"결과: recommendation={value.recommendation}, 최종결정자={value.final_decision_owner}",
                f"전달 항목: 긍정 {len(value.top_positive_factors)} / 리스크 {len(value.top_blockers_or_risks)} / 조치 {len(value.required_next_actions)}",
                "후속 처리: 규칙 기반 렌더러가 최종 Markdown 보고서 생성",
            ]
        self._trace_box(f"[{step}/4] {label} 완료", rows)

    def _task_callback(self, step: int, label: str, schema, status_field: str, next_label: str | None = None):
        """Print small validated task outcomes; CrewAI's own verbose dump stays off."""
        def callback(output) -> None:
            if step == 1:
                _stage("agent1", "done")
                _stage("agent2", "start")
                _stage("agent3", "start")
            elif step in (2, 3):
                _stage(f"agent{step}", "done")
                self._parallel_done.add(step)
                if self._parallel_done == {2, 3}:
                    _stage("agent4", "start")
            else:
                _stage("agent4", "done")
            if not self._live_log_enabled:
                return
            try:
                value = schema.model_validate(output.pydantic) if output.pydantic is not None else schema.model_validate_json(output.raw)
                if self.trace:
                    self._trace_completion(step, label, value)
                else:
                    print(f"[{step}/4] {label} 완료: {status_field}={getattr(value, status_field)}")
                    print(f"[{step}/4] {label} 도구 호출: 없음 (검증된 compact summary만 사용)")
            except Exception as exc:  # output validation error is also retained by CrewAI
                print(f"[{step}/4] {label} 완료 후 결과 검증 실패: {type(exc).__name__}")
            if step == 1:
                if self.trace:
                    self._trace_start(2, "Capability Matching Agent (Agent 3과 병렬)")
                    self._trace_start(3, "Market Research Agent (Agent 2와 병렬)")
                else:
                    print("[2·3/4] 역량 매칭·시장 검토 병렬 시작; 두 결과 완료 후 Agent 4 실행")
        return callback

    def __init__(
        self,
        settings_path: str | Path = PROJECT_ROOT / "config/settings.yaml",
        model: str | None = None,
        verbose: bool = False,
        trace: bool = False,
        crew_verbose: bool = False,
    ):
        load_dotenv(PROJECT_ROOT / ".env")
        # .env의 빈 `OPENAI_API_KEY=`는 오프라인 CrewAI 구성 시 미설정 값처럼 처리합니다.
        # live 모드에서는 아래에서 키 존재 여부를 별도로 검증합니다.
        if not os.getenv("OPENAI_API_KEY"):
            os.environ.pop("OPENAI_API_KEY", None)
        self.settings = load_settings(settings_path)
        self.model = model or os.getenv("MODEL", "openai/gpt-4.1-mini")
        self.verbose = verbose
        self.trace = trace
        self.crew_verbose = crew_verbose
        self._live_log_enabled = False
        self._parallel_done: set[int] = set()
        self._crew = None

    @property
    def crew(self):
        """Only live execution/topology inspection constructs LLM clients."""
        if self._crew is None:
            self._initialize_agents()
        return self._crew

    @staticmethod
    def _agent_prompts(config: dict) -> dict[str, str]:
        return {field: render_prompt(config[field]) for field in ("role", "goal", "backstory")}

    def _initialize_agents(self):
        crew_verbose = self.crew_verbose
        agents, tasks = load_config("agents"), load_config("tasks")
        self.rfp_agent = Agent(
            **self._agent_prompts(agents["rfp_analysis"]),
            tools=[], allow_delegation=False, verbose=crew_verbose, llm=self.model,
        )
        self.capability_agent = Agent(
            **self._agent_prompts(agents["capability_matching"]),
            tools=[], allow_delegation=False, verbose=crew_verbose, llm=self.model,
        )
        self.market_agent = Agent(
            **self._agent_prompts(agents["market_research"]),
            tools=[], allow_delegation=False, verbose=crew_verbose, llm=self.model,
        )
        self.report_agent = Agent(
            **self._agent_prompts(agents["report_writing"]),
            tools=[], allow_delegation=False, verbose=crew_verbose, llm=self.model,
        )
        self.rfp_task = Task(
            name="rfp_analysis", agent=self.rfp_agent,
            description=render_prompt(tasks["rfp_analysis"]["description"]),
            expected_output=render_prompt(tasks["rfp_analysis"]["expected_output"]), output_pydantic=RFPReviewSummary,
            callback=self._task_callback(1, "RFP Analysis Agent", RFPReviewSummary, "document_status", "Capability Matching Agent"),
        )
        # Task context로 RFP → 역량 매칭, RFP → 시장 조사 의존성을 정의합니다.
        self.capability_task = Task(
            name="capability_matching", agent=self.capability_agent, context=[self.rfp_task], async_execution=True,
            description=render_prompt(tasks["capability_matching"]["description"]),
            expected_output=render_prompt(tasks["capability_matching"]["expected_output"]), output_pydantic=CapabilityReviewSummary,
            callback=self._task_callback(2, "Capability Matching Agent", CapabilityReviewSummary, "overall_fit", "Market Research Agent"),
        )
        self.market_task = Task(
            name="market_research", agent=self.market_agent, context=[self.rfp_task], async_execution=True,
            description=render_prompt(tasks["market_research"]["description"]),
            expected_output=render_prompt(tasks["market_research"]["expected_output"]), output_pydantic=MarketReviewSummary,
            callback=self._task_callback(3, "Market Research Agent", MarketReviewSummary, "research_status", "Report Writing Agent"),
        )
        self.report_task = Task(
            name="report_writing", agent=self.report_agent,
            description=render_prompt(tasks["report_writing"]["description"]),
            expected_output=render_prompt(tasks["report_writing"]["expected_output"]), output_pydantic=ExecutiveDecisionSummary,
            context=[self.rfp_task, self.capability_task, self.market_task],
            callback=self._task_callback(4, "Report Writing Agent", ExecutiveDecisionSummary, "recommendation"),
        )
        self._crew = Crew(
            name="public_si_bid_review", agents=[self.rfp_agent, self.capability_agent, self.market_agent, self.report_agent],
            tasks=[self.rfp_task, self.capability_task, self.market_task, self.report_task], process=Process.sequential, verbose=crew_verbose,
        )

    @staticmethod
    def _load_profile(path: str | Path | None) -> CompanyProfile:
        # 회사 역량 JSON이 제공되지 않은 경우, 증빙을 꾸며내지 않고 미확인으로 표시합니다.
        if path is None:
            return CompanyProfile(
                evidence_scope="회사 역량 자료 미제공",
                usage_rule="회사 역량 및 입찰 자격은 원본 증빙 확인 필요",
                missing_evidence=[
                    "회사 수행실적 증빙 미제공",
                    "기술 및 인력 증빙 미제공",
                    "입찰 참가자격 증빙 미제공",
                ],
                records=[],
            )

        with Path(path).open(encoding="utf-8") as handle:
            return CompanyProfile.model_validate(json.load(handle))

    @staticmethod
    def _write(path: Path, model, markdown: str) -> None:
        payload = artifact_payload(model) if isinstance(model, RFPAnalysisResult) else model.model_dump()
        path.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        path.with_suffix(".md").write_text(markdown, encoding="utf-8")

    @staticmethod
    def _write_debug_page_analysis(output: Path, rfp: RFPAnalysisResult) -> None:
        debug_dir = output / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / "page_analysis.json").write_text(
            json.dumps(debug_page_analysis_payload(rfp), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _agent_context_sizes(inputs: dict[str, dict], agent_summaries: dict | None = None) -> dict[str, int]:
        """Manifest-only proof that all live task inputs are bounded."""
        summaries = agent_summaries or {}
        sizes = {
            "rfp_analysis": json_size({"notice_summary": inputs.get("notice_summary", {}), "rfp_summary": inputs.get("rfp_summary", {})}),
            "capability_matching": json_size({"RFPReviewSummary": summaries.get("rfp_analysis", {}), "company_summary": inputs.get("company_summary", {})}),
            "market_research": json_size({"RFPReviewSummary": summaries.get("rfp_analysis", {}), "market_summary": inputs.get("market_summary", {})}),
        }
        sizes["report_writing"] = json_size(summaries)
        return sizes

    @staticmethod
    def _write_agent_summary(output: Path, name: str, summary) -> str:
        filename = f"agent_{name}_summary.json"
        (output / filename).write_text(
            json.dumps(summary.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return filename

    @staticmethod
    def _input_summary_contracts() -> dict[str, list[str]]:
        """Manifest-safe field names only; values stay out of default output."""
        return {
            "notice_summary": ["notice_number", "procuring_entity", "demanding_entity", "budget_krw", "estimated_price_krw", "proposal_submission_start", "bid_or_proposal_deadline", "joint_venture_deadline", "legal_eligibility_conditions<=5", "joint_venture_policy", "quality_status", "items_to_verify<=5"],
            "rfp_summary": ["project_name", "purpose", "contract_period", "key_technical_requirements<=5", "key_staffing_requirements<=5", "key_submission_documents<=5", "key_deliverables<=5", "conflicts_or_source_checks<=5"],
            "company_summary": ["matched_evidence<=3", "representative_public_records<=3", "public_technology_and_certifications<=5", "financial_reference", "evidence_limitations<=8"],
            "market_summary": ["g2b_api_status", "research_status", "similar_project_count", "representative_cases<=3", "limitations<=5"],
        }

    @staticmethod
    def _validated_task_output(task: Task, schema):
        """Convert CrewAI Task output to the declared Pydantic contract."""
        if task.output is None:
            raise RuntimeError(f"CrewAI task '{task.name}' returned no output.")
        if task.output.pydantic is not None:
            return schema.model_validate(task.output.pydantic)
        return schema.model_validate_json(task.output.raw)

    def run(self, pdf_path: str | Path, company_path: str | Path | None, output_dir: str | Path, market_path: str | Path | None = None, notice: dict | None = None, mode: Literal["offline", "live"] = "offline", notice_pdf: str | Path | None = None, save_intermediate: bool = False, allow_market_api: bool = False) -> dict:
        if mode not in {"offline", "live"}:
            raise ValueError("mode는 offline 또는 live여야 합니다.")
        if mode == "live" and not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("CrewAI live 실행에는 .env의 OPENAI_API_KEY가 필요합니다. 키 없이 검사하려면 --mode offline을 사용하세요.")
        market_settings = deepcopy(self.settings)
        market_settings.setdefault("market", {})["allow_live_lookup"] = mode == "live" and allow_market_api
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        report_stem = _report_artifact_stem()
        report_artifacts = [f"{report_stem}.md", f"{report_stem}.json"]
        def save_detail(name: str, model, markdown: str) -> None:
            """Always retain the detailed evidence cited by the final report."""
            (output / "debug").mkdir(parents=True, exist_ok=True)
            self._write(output / "debug" / name, model, markdown)
        # RFP and notice are independently validated before they are merged.  A
        # missing notice is a limited pre-review, not an RFP extraction failure.
        _stage("agent1", "start")
        extractor = RFPExtractor(self.settings)
        rfp_only = extractor.analyze(pdf_path, notice, document_kind="rfp_pdf")
        notice_result = extractor.analyze(notice_pdf, document_kind="notice_pdf") if notice_pdf else None
        if notice_result is not None:
            save_detail("00_notice_analysis", notice_result, rfp_markdown(notice_result))
        rfp = merge_document_results(rfp_only, notice_result)
        save_detail("01_rfp_analysis", rfp, rfp_markdown(rfp))
        if save_intermediate:
            self._write_debug_page_analysis(output, rfp)
        debug_artifact = ["debug/page_analysis.json"] if save_intermediate else []
        if not rfp.rfp_quality_gate or not rfp.rfp_quality_gate.passed:
            if self.trace:
                self._trace_box("문서 입력 품질 게이트 실패", [
                    f"{issue.field}: {issue.reason}" + (f" (p.{issue.source_page})" if issue.source_page else "")
                    for issue in rfp.quality_gate.issues
                ])
            report = ReportWriter(self.settings).write_quality_gate_hold(rfp)
            self._write(output / report_stem, report, report_markdown(report, rfp=rfp))
            manifest = {
                "crew_framework": "CrewAI", "mode": "blocked_by_rfp_quality_gate",
                "quality_gate": rfp.quality_gate.model_dump(),
                "skipped": ["RFP Analysis Agent", "Capability Matching Agent", "Market Research Agent", "Report Writing Agent", "G2B API"],
                "artifacts": [*report_artifacts, "crew_execution_manifest.json", *debug_artifact],
                "agent_context_bytes": {},
            }
            (output / "crew_execution_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            _stage_gate_hold()
            return {"rfp": rfp, "capability": None, "market": None, "report": report, "report_artifacts": report_artifacts, "manifest": manifest}
        # RFP-only reviews can run capability matching and live summary Tasks.
        # G2B stays disabled without a valid notice; the Market Agent reports skipped.
        if not rfp.market_research_allowed and rfp.analysis_scope != "rfp_only":
            if self.verbose:
                print("[문서 입력 확인] 공고문 기본정보 미확인 — Agent 2~4·나라장터 API·최종 입찰 판단을 실행하지 않음")
            if self.trace:
                self._trace_box("공고문 통합 판단 제한", [
                    f"{issue.field}: {issue.reason}" + (f" (p.{issue.source_page})" if issue.source_page else "")
                    for issue in (rfp.integration_quality_gate or rfp.quality_gate).issues
                ])
            report = ReportWriter(self.settings).write_notice_gate_hold(rfp)
            self._write(output / report_stem, report, report_markdown(report, rfp=rfp))
            manifest = {
                "crew_framework": "CrewAI", "mode": "rfp_pre_review",
                "rfp_quality_gate": rfp.rfp_quality_gate.model_dump(),
                "notice_quality_gate": rfp.notice_quality_gate.model_dump() if rfp.notice_quality_gate else None,
                "integration_quality_gate": rfp.integration_quality_gate.model_dump() if rfp.integration_quality_gate else None,
                "skipped": ["Capability Matching Agent", "Market Research Agent", "Report Writing Agent", "G2B API", "final Go/No-Go recommendation"],
                "artifacts": [*report_artifacts, "crew_execution_manifest.json", *debug_artifact],
                "agent_context_bytes": {},
            }
            (output / "crew_execution_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            _stage_gate_hold()
            return {"rfp": rfp, "capability": None, "market": None, "report": report, "report_artifacts": report_artifacts, "manifest": manifest}
        if mode == "live":
            if not os.getenv("OPENAI_API_KEY"):
                raise RuntimeError("--mode live requires a non-empty OPENAI_API_KEY in the project .env file or terminal environment.")
            # Canonical artifacts are produced once by deterministic services.
            # Only their deliberately bounded summary crosses the LLM boundary.
            try:
                company = self._load_profile(company_path)
                capability = CapabilityMatcher(self.settings).analyze(rfp, company)
                market = MarketResearcher(market_path, market_settings).analyze(rfp)
                save_detail("02_capability_matching", capability, capability_markdown(capability))
                save_detail("03_market_research", market, market_markdown(market))
                market_tool_result = {
                    "executor": "rule_based_market_research",
                    "status": "observations_recorded" if market.market_observations else "no_source_linked_observations",
                    "source_linked_observations": len(market.market_observations),
                    "similar_projects": len(market.similar_projects),
                }
                input_summaries = build_agent_input_summaries(rfp, capability, market, self.settings["minimum_project_amount_krw"])
                if self.verbose or self.trace:
                    print(f"[규칙 기반] 상세 산출물은 메모리 검증 / Agent 입력 최대 {max(self._agent_context_sizes(input_summaries).values()):,} bytes")
                    print(
                        "[규칙 기반] 나라장터 API: "
                        f"{market_tool_result['status']} / 관찰 {market_tool_result['source_linked_observations']}건"
                    )
                    if not self.trace:
                        print("[1/4] RFP Analysis Agent 시작")
                self._live_log_enabled = self.verbose or self.trace
                self._parallel_done = set()
                if self.trace:
                    self._trace_start(1, "RFP Analysis Agent")
                self.crew.kickoff(inputs={
                    key: json.dumps(value, ensure_ascii=False) for key, value in input_summaries.items()
                })
            except Exception as exc:
                # Keep a concise diagnostic artifact without serializing request
                # URLs, environment variables, or API keys.
                message = f"{type(exc).__name__}: {str(exc).replace(os.getenv('OPENAI_API_KEY', ''), '[REDACTED]') if os.getenv('OPENAI_API_KEY') else str(exc)}"
                (output / "crew_live_error.txt").write_text(message, encoding="utf-8")
                raise RuntimeError("CrewAI live 실행이 중단되었습니다. outputs의 crew_live_error.txt를 확인하세요.") from exc
            finally:
                self._live_log_enabled = False
            try:
                rfp_review = self._validated_task_output(self.rfp_task, RFPReviewSummary)
                capability_review = self._validated_task_output(self.capability_task, CapabilityReviewSummary)
                market_review = self._validated_task_output(self.market_task, MarketReviewSummary)
                executive_review = self._validated_task_output(self.report_task, ExecutiveDecisionSummary)
            except Exception as exc:
                raise RuntimeError("CrewAI live output did not pass the compact Pydantic schema validation.") from exc
            summary_files = []
            summary_files = [
                self._write_agent_summary(output / "debug", "01_rfp_review", rfp_review),
                self._write_agent_summary(output / "debug", "02_capability_review", capability_review),
                self._write_agent_summary(output / "debug", "03_market_review", market_review),
                self._write_agent_summary(output / "debug", "04_executive_decision", executive_review),
            ]
            # A live Agent recommendation is an attributed briefing.  Deterministic
            # legal/quality safeguards remain authoritative for the report status.
            report = ReportWriter(self.settings).write(rfp, capability, market, executive_summary=executive_review)
            self._write(output / report_stem, report, report_markdown(report, rfp, capability, market))
            summaries = {
                "rfp_analysis": rfp_review.model_dump(),
                "capability_matching": capability_review.model_dump(),
                "market_research": market_review.model_dump(),
                "report_writing": executive_review.model_dump(),
            }
            manifest = {
                "crew_framework": "CrewAI", "mode": "live", "tasks": [task.name for task in self.crew.tasks],
                "context_policy": "compact_summary_only", "tool_policy": "LLM tool calls disabled; deterministic services created canonical artifacts first",
                "execution_graph": "1 -> (2 || 3) -> 4; Task 4 context=[1,2,3]",
                "llm_tasks_executed": True,
                "market_api_enabled": mode == "live" and allow_market_api,
                "deterministic_tool_calls": {"market_research": market_tool_result},
                "market_research_status": input_summaries["market_summary"]["g2b_api_status"],
                "input_summary_contracts": self._input_summary_contracts(),
                "agent_context_bytes": self._agent_context_sizes(input_summaries, summaries),
                "agent_handoffs": [
                    {"agent": "Agent 1", "input": ["notice_summary", "rfp_summary"], "output": "RFPReviewSummary"},
                    {"agent": "Agent 2", "input": ["RFPReviewSummary", "company_summary"], "output": "CapabilityReviewSummary"},
                    {"agent": "Agent 3", "input": ["RFPReviewSummary", "market_summary"], "output": "MarketReviewSummary"},
                    {"agent": "Agent 4", "input": ["RFPReviewSummary", "CapabilityReviewSummary", "MarketReviewSummary"], "output": "ExecutiveDecisionSummary"},
                ],
                "artifacts": [*report_artifacts, "crew_execution_manifest.json", *[f"debug/{name}" for name in summary_files], *debug_artifact],
            }
            (output / "crew_execution_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"rfp": rfp, "capability": capability, "market": market, "report": report, "report_artifacts": report_artifacts, "agent_summaries": summaries, "manifest": manifest}
        if self.verbose:
            print("[RFP Preflight] PDF 구조화 및 품질 게이트 통과")
            print("[규칙 기반] 구조화 결과 및 Pydantic 검증 (LLM Task 미실행)")
        _stage("agent1", "done")
        _stage("agent2", "start")
        if self.verbose:
            print("[규칙 기반] 회사 공개 근거와 원본 증빙 확인 항목 분리")
        company = self._load_profile(company_path)
        capability = CapabilityMatcher(self.settings).analyze(rfp, company)
        _stage("agent2", "done")
        _stage("agent3", "start")
        if self.verbose:
            print("[규칙 기반] 제공된 시장 근거 확인 (외부 API 미호출)")
        market = MarketResearcher(market_path, market_settings).analyze(rfp)
        _stage("agent3", "done")
        _stage("agent4", "start")
        if self.verbose:
            print("[규칙 기반] 검증된 세 산출물 종합")
        report = ReportWriter(self.settings).write(rfp, capability, market)
        input_summaries = build_agent_input_summaries(rfp, capability, market, self.settings["minimum_project_amount_krw"])
        save_detail("01_rfp_analysis", rfp, rfp_markdown(rfp))
        save_detail("02_capability_matching", capability, capability_markdown(capability))
        save_detail("03_market_research", market, market_markdown(market))
        self._write(output / report_stem, report, report_markdown(report, rfp, capability, market))
        manifest = {
            "crew_framework": "CrewAI", "mode": "offline_evidence_executor",
            "topology": "RFP Analysis Task → Capability Matching Task; RFP Analysis Task → Market Research Task; (RFP, Capability, Market) → Report Writing Task",
            "tasks": TASK_NAMES, "artifacts": [*report_artifacts, "crew_execution_manifest.json", *debug_artifact],
            "llm_tasks_executed": False, "market_api_enabled": False,
            "market_research_status": input_summaries["market_summary"]["g2b_api_status"],
            "input_summary_contracts": self._input_summary_contracts(),
            "agent_context_bytes": self._agent_context_sizes(input_summaries),
        }
        (output / "crew_execution_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        _stage("agent4", "done")
        return {"rfp": rfp, "capability": capability, "market": market, "report": report, "report_artifacts": report_artifacts, "manifest": manifest}
