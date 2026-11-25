"""M25: score the semantic-search thresholds against reviewer labels from M25_임계값_평가표.html.

.venv/bin/python scripts/evaluate_thresholds.py "[M25 평가] E001:2 E002:0 ..."   (or a file path)
Label 2 = 관련, 1 = 부분 관련, 0 = 무관. "relevant" means label >= 1.

T3 재평가(남은 업무 3번, 0.70·0.80·0.90 비교):
.venv/bin/python scripts/evaluate_thresholds.py --t3 docs/final_project/회의_개선점/261118/T3_임계값_재평가_판정표.md
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


T3_THRESHOLDS = (0.70, 0.80, 0.90)
T3_LABELS = {"관련": 2, "부분": 1, "무관": 0}


def parse_t3_sheet(text: str) -> dict[str, str | None]:
    """판정표 표의 'ID'와 마지막 '판정' 칸. 빈 칸·모르는 값은 None(미판정)."""
    labels: dict[str, str | None] = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and re.fullmatch(r"T3-\d{3}", cells[0]):
            labels[cells[0]] = cells[-1] if cells[-1] in T3_LABELS else None
    return labels


def evaluate_t3(items: list[dict], labels: dict[str, str | None], thresholds=T3_THRESHOLDS) -> dict:
    """남은 업무 3번: 임계값별 후보 수·정밀도·재현율·F1·놓친 관련 사업·포함된 무관 사업.

    관련 = '관련' 또는 '부분'(M25와 같이 부분 관련도 관련으로 센다). 재현율의 분모는 판정 풀(0.60 이상 상위 10건)
    안의 관련 후보 수다 — 풀 밖의 관련 사업은 알 수 없다. 미판정 항목이 하나라도 있으면 정밀도·재현율을 내지 않는다.
    """
    missing = [item["id"] for item in items if labels.get(item["id"]) is None]
    cases = list(dict.fromkeys(item["case"] for item in items))

    def at(rows: list[dict], threshold: float) -> dict:
        kept = [row for row in rows if row["similarity"] >= threshold]
        out = {"threshold": threshold, "kept": len(kept),
               "candidates": [f"{row['id']} {row['title'][:30]} {row['similarity']:.3f}" for row in kept]}
        if missing:
            return {**out, "status": "평가 미완료(미판정 항목 있음)"}
        relevant = [row for row in rows if T3_LABELS[labels[row["id"]]] >= 1]
        hits = [row for row in kept if T3_LABELS[labels[row["id"]]] >= 1]
        precision = len(hits) / len(kept) if kept else None
        recall = len(hits) / len(relevant) if relevant else None
        f1 = (2 * precision * recall / (precision + recall)) if precision and recall else 0.0
        return {**out, "status": "평가 완료", "relevant_in_pool": len(relevant),
                "precision": None if precision is None else round(precision, 3),
                "recall": None if recall is None else round(recall, 3), "f1": round(f1, 3),
                "missed_relevant": [f"{row['id']} {row['title'][:30]} {row['similarity']:.3f}" for row in relevant if row not in kept],
                "irrelevant_included": [f"{row['id']} {row['title'][:30]} {row['similarity']:.3f}"
                                        for row in kept if T3_LABELS[labels[row["id"]]] == 0]}

    report = {"labelled": len(items) - len(missing), "unlabelled": missing, "overall": [at(items, t) for t in thresholds],
              "by_case": {case: [at([i for i in items if i["case"] == case], t) for t in thresholds] for case in cases}}
    if not missing:  # 세 값 밖에서 더 나은 값이 있는지: 관측된 점수 전부를 임계값 후보로
        sweep = [at(items, score) for score in sorted({item["similarity"] for item in items})]
        report["best_f1_any_threshold"] = {k: v for k, v in max(sweep, key=lambda r: (r["f1"], r["precision"] or 0)).items()
                                           if k != "candidates"}
    return report


def main() -> None:
    if len(sys.argv) > 2 and sys.argv[1] == "--t3":
        sheet = Path(sys.argv[2]).read_text(encoding="utf-8")
        payload = json.loads((ROOT / "outputs/eval/t3_threshold_items.json").read_text(encoding="utf-8"))
        print(json.dumps(evaluate_t3(payload["items"], parse_t3_sheet(sheet)), ensure_ascii=False, indent=2))
        return
    arg = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    text = Path(arg).read_text(encoding="utf-8") if Path(arg).is_file() else arg
    items = json.loads((ROOT / "outputs/eval/eval_items.json").read_text(encoding="utf-8"))
    print(json.dumps(evaluate(items, parse_labels(text)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
