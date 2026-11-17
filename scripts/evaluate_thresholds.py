"""M25: score the semantic-search thresholds against reviewer labels from M25_임계값_평가표.html.

.venv/bin/python scripts/evaluate_thresholds.py "[M25 평가] E001:2 E002:0 ..."   (or a file path)
Label 2 = 관련, 1 = 부분 관련, 0 = 무관. "relevant" means label >= 1.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CURRENT = {"t15": 0.40, "company": 0.36, "project": 0.70}  # semantic.MIN_SCORE, workflow.COMPANY_MIN_SCORE, past_projects.MIN_PROJECT_SCORE


def parse_labels(text: str) -> dict[str, int]:
    return {item_id: int(value) for item_id, value in re.findall(r"(E\d{3}):([012])", text)}


def evaluate(items: list[dict], labels: dict[str, int]) -> dict[str, dict]:
    """Per kind: precision/recall at the current threshold and the best-F1 threshold over observed scores."""
    report = {}
    for kind, current in CURRENT.items():
        rows = [(item["score"], labels[item["id"]]) for item in items if item["kind"] == kind and item["id"] in labels]
        relevant_total = sum(1 for _, label in rows if label >= 1)

        def at(threshold: float) -> dict:
            kept = [label for score, label in rows if score >= threshold]
            hits = sum(1 for label in kept if label >= 1)
            precision = hits / len(kept) if kept else 0.0
            recall = hits / relevant_total if relevant_total else 0.0
            f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
            return {"threshold": round(threshold, 4), "kept": len(kept), "precision": round(precision, 3),
                    "recall": round(recall, 3), "f1": round(f1, 3)}
        candidates = [at(score) for score in sorted({score for score, _ in rows})]
        best = max(candidates, key=lambda row: (row["f1"], row["precision"]), default=None)
        report[kind] = {"labelled": len(rows), "relevant": relevant_total, "current": at(current), "best_f1": best}
    return report


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    text = Path(arg).read_text(encoding="utf-8") if Path(arg).is_file() else arg
    items = json.loads((ROOT / "outputs/eval/eval_items.json").read_text(encoding="utf-8"))
    print(json.dumps(evaluate(items, parse_labels(text)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
