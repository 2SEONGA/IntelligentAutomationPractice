"""YAML 설정 검사: config/*.yaml을 고친 뒤 `python tests/test_config.py`로 실행한다."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bid_review.policy import render_prompt  # noqa: E402
from bid_review.settings import load_config  # noqa: E402

TASK_NAMES = ["rfp_analysis", "capability_matching", "market_research", "report_writing"]
# CrewAI가 kickoff(inputs=...)로 채우는 값. YAML에서 빠지면 Agent가 입력을 못 받는다.
REQUIRED_INPUTS = {
    "rfp_analysis": ["{notice_summary}", "{rfp_summary}"],
    "capability_matching": ["{company_summary}"],
    "market_research": ["{market_summary}"],
    "report_writing": [],
}


def test_prompts_render() -> None:
    agents, tasks = load_config("agents"), load_config("tasks")
    assert list(agents) == TASK_NAMES and list(tasks) == TASK_NAMES
    for name in TASK_NAMES:
        rendered = [render_prompt(agents[name][f]) for f in ("role", "goal", "backstory")]
        rendered += [render_prompt(tasks[name][f]) for f in ("description", "expected_output")]
        assert all(text.strip() and "$" not in text for text in rendered), name
        for placeholder in REQUIRED_INPUTS[name]:
            assert placeholder in rendered[3], f"{name}: {placeholder} 누락"
        assert "공통 검토 원칙" in rendered[2] and "공통 검토 원칙" in rendered[3], name


def test_keywords() -> None:
    settings = load_config("settings")
    rfp = settings["rfp"]
    for key in ("category_keywords", "legal_terms", "staffing_terms", "submission_terms", "generic_titles", "generic_entity_headers"):
        assert rfp[key], key
    assert set(rfp["category_keywords"]) <= set(settings["scoring"]["category_weights"]), "가중치 없는 요구사항 범주"
    assert settings["capability"]["performance_terms"]
    assert settings["pdf_evidence"]["max_pages"] > 0 and settings["pdf_evidence"]["max_chars_per_page"] > 0


def test_checklist() -> None:
    checklist = load_config("checklist")
    ids = [item["id"] for item in checklist["knockout"] + checklist["scoring"]]
    assert len(ids) == len(set(ids)), "중복 id"
    max_score = sum(max(item["levels"]) for item in checklist["scoring"])
    assert 0 < checklist["decision"]["review"] < checklist["decision"]["go"] <= max_score


if __name__ == "__main__":
    test_prompts_render()
    test_keywords()
    test_checklist()
    print("config OK")
