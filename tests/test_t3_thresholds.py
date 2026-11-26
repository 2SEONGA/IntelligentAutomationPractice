"""T3 임계값 재평가(남은 업무 3번) 계산 검사: `python tests/test_t3_thresholds.py` (네트워크 없음)."""
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
MODULE = runpy.run_path(str(ROOT / "scripts/evaluate_thresholds.py"))
parse_t3_sheet, evaluate_t3 = MODULE["parse_t3_sheet"], MODULE["evaluate_t3"]

ITEMS = [{"id": f"T3-00{i}", "case": "A", "title": f"사업{i}", "similarity": score}
         for i, score in enumerate((0.95, 0.85, 0.75, 0.65), start=1)]
SHEET = """| ID | 시연 공고 | ... | 판정 |
|---|---|---|---|
| T3-001 | A | x | 관련 |
| T3-002 | A | x | 무관 |
| T3-003 | A | x | 부분 |
| T3-004 | A | x | 무관 |
"""


def test_parse_sheet() -> None:
    assert parse_t3_sheet(SHEET) == {"T3-001": "관련", "T3-002": "무관", "T3-003": "부분", "T3-004": "무관"}
    assert parse_t3_sheet(SHEET.replace("| 무관 |\n| T3-003", "|  |\n| T3-003"))["T3-002"] is None, "빈 칸은 미판정"


def test_threshold_metrics() -> None:
    report = evaluate_t3(ITEMS, parse_t3_sheet(SHEET))
    by = {row["threshold"]: row for row in report["overall"]}
    assert by[0.70]["kept"] == 3 and by[0.70]["precision"] == 0.667 and by[0.70]["recall"] == 1.0
    assert by[0.80]["kept"] == 2 and by[0.80]["precision"] == 0.5 and by[0.80]["recall"] == 0.5
    assert by[0.80]["missed_relevant"] == ["T3-003 사업3 0.750"] and by[0.80]["irrelevant_included"] == ["T3-002 사업2 0.850"]
    assert by[0.90]["kept"] == 1 and by[0.90]["precision"] == 1.0
    assert report["best_f1_any_threshold"]["threshold"] == 0.75, "세 값 밖의 더 나은 임계값도 찾는다"


def test_unlabelled_items_block_metrics() -> None:
    labels = parse_t3_sheet(SHEET)
    labels["T3-004"] = None
    report = evaluate_t3(ITEMS, labels)
    assert report["unlabelled"] == ["T3-004"] and "best_f1_any_threshold" not in report
    assert all(row["status"].startswith("평가 미완료") and "precision" not in row for row in report["overall"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("t3 thresholds OK")
