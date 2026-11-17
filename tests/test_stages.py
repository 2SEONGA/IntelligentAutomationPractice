"""`[STAGE]` 진행 표시 줄 검사: `python tests/test_stages.py` (offline 실제 실행 포함, 약 10초)."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bid_review.crew import BidReviewCrew  # noqa: E402
from bid_review.models import CapabilityReviewSummary, ExecutiveDecisionSummary, MarketReviewSummary, RFPReviewSummary  # noqa: E402


def stages(stdout: str) -> list[str]:
    return [line.removeprefix("[STAGE] ") for line in stdout.splitlines() if line.startswith("[STAGE] ")]


def run_cli(*args: str) -> str:
    with tempfile.TemporaryDirectory() as out:
        result = subprocess.run([sys.executable, "run.py", *args, "--output", out], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8")
        return result.stdout


def test_offline_emits_all_stages_in_order() -> None:
    out = run_cli("--mode", "offline", "--rfp", "data/input/통합정보시스템 고도화 구축 용역.pdf")
    assert stages(out) == ["agent1 start", "agent1 done", "agent2 start", "agent2 done",
                           "agent3 start", "agent3 done", "agent4 start", "agent4 done"], out


def test_live_callbacks_emit_stages_without_verbose() -> None:
    crew = BidReviewCrew(verbose=False)
    fake = SimpleNamespace(pydantic=None, raw="{}")
    callbacks = {
        1: crew._task_callback(1, "RFP Analysis Agent", RFPReviewSummary, "document_status"),
        2: crew._task_callback(2, "Capability Matching Agent", CapabilityReviewSummary, "overall_fit"),
        3: crew._task_callback(3, "Market Research Agent", MarketReviewSummary, "research_status"),
        4: crew._task_callback(4, "Report Writing Agent", ExecutiveDecisionSummary, "recommendation"),
    }
    captured = io.StringIO()
    with redirect_stdout(captured):
        for step in (1, 3, 2, 4):
            callbacks[step](fake)
    assert stages(captured.getvalue()) == ["agent1 done", "agent2 start", "agent3 start", "agent3 done",
                                           "agent2 done", "agent4 start", "agent4 done"], captured.getvalue()


def test_gate_hold_emits_skip() -> None:
    from pypdf import PdfWriter
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "blank.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with pdf.open("wb") as handle:
            writer.write(handle)
        out = run_cli("--mode", "offline", "--rfp", str(pdf))
    assert stages(out)[-3:] == ["agent2 skip", "agent3 skip", "agent4 skip"], out


if __name__ == "__main__":
    test_live_callbacks_emit_stages_without_verbose()
    test_offline_emits_all_stages_in_order()
    test_gate_hold_emits_skip()
    print("stages OK")
