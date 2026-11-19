"""Run paired sequential/parallel comparisons on five or more real input cases."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
from statistics import mean, median
from time import perf_counter

from bid_review.rag.usage_tracking import collect_embedding_usage

# T1.5 이후 분기 전 Node. 두 모드에서 똑같이 순차 실행되므로, 전체 시간에서 빼면 T2·T3 이후 구간 시간이 된다.
PRE_FORK_NODES = {"t1_extract", "g2b_notice_lookup", "t15_verify", "t15_route", "targeted_retrieval",
                  "t15_reverify", "t1_reanalyze", "human_review"}
RESULT_KEYS = ("capability", "similar_projects", "similar_projects_status",
               "agent23_evaluation", "market", "report", "report_details", "report_validation")


# 실행 시각은 비교에서 뺀다. 임베딩 점수는 OpenAI API가 같은 입력에도 소수 넷째 자리에서 흔들려
# (같은 모드끼리 반복해도 다름, 2026-11-20 실측) 이 허용치 안이면 같은 결과로 본다. 그 밖의 값은 정확히 같아야 한다.
VOLATILE_KEYS = {"checked_at"}
SCORE_TOLERANCE = 0.002


def _normalize(value, scores: list, key: str = ""):
    """비교용 사본: 실행 시각 제거, 점수는 따로 모으고 자리표시자로 바꾼다."""
    if isinstance(value, dict):
        return {k: _normalize(v, scores, k) for k, v in value.items() if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [_normalize(v, scores, key) for v in value]
    if isinstance(value, float) and ("score" in key or "similarity" in key):
        scores.append(value)
        return "<score>"
    return value


def result_difference(left: dict, right: dict) -> tuple[list[str], float]:
    """(다른 결과 키, 최대 점수 차). 점수 차가 허용치를 넘으면 그 키도 다른 것으로 본다."""
    different, worst = [], 0.0
    for key in RESULT_KEYS:
        ls, rs = [], []
        a, b = _normalize(left.get(key), ls), _normalize(right.get(key), rs)
        delta = max((abs(x - y) for x, y in zip(ls, rs)), default=0.0)
        worst = max(worst, delta)
        if a != b or len(ls) != len(rs) or delta > SCORE_TOLERANCE:
            different.append(key)
    return different, round(worst, 6)


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _fingerprint(path: str | None) -> dict | None:
    if not path:
        return None
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    return {"path": str(resolved), "sha256": sha256(resolved.read_bytes()).hexdigest()}


def _round(case: dict, mode: str, case_dir: Path, repeat: int, fingerprints: dict,
           approve_note: str | None = None) -> dict:
    from bid_review.rag.workflow import run_bid_review
    folder = case_dir / f"repeat_{repeat:02d}" / mode
    with collect_embedding_usage() as usage:
        started = perf_counter()
        try:
            state = run_bid_review(case["rfp"], case.get("company"), case.get("notice_no"),
                                   case.get("retrieval_limit", 2), notice_order=case.get("notice_order"),
                                   notice_pdf_path=case.get("notice_pdf"), workflow_mode=mode,
                                   # 담당자 검토에서 멈추는 실제 공고도 T2·T3까지 가도록 두 모드에 같은 고정 승인을 넣는다.
                                   **({"review_decision": "approved", "review_note": approve_note,
                                       "reviewer": "benchmark"} if approve_note else {}))
            error = None
        except Exception as exc:
            state = {}
            error = f"{type(exc).__name__}: {exc}"
        elapsed = perf_counter() - started
    changed = {key: _fingerprint(case.get(key)) != value for key, value in fingerprints.items()}
    if state:
        _write(folder / "state.json", state)
    events = state.get("execution_history") or []
    measurement = {"case_id": case["id"], "repeat": repeat, "mode": mode,
                   "elapsed_seconds": round(elapsed, 6), "embedding_usage": dict(usage),
                   "token_scope": "OpenAI 임베딩 API 응답에 포함된 사용량만 측정; 미측정 요청은 별도 표시",
                   "node_seconds": [{"node": event["node"], "duration_seconds": event.get("duration_seconds")}
                                    for event in events],
                   "post_t15_seconds": round(elapsed - sum(event.get("duration_seconds") or 0 for event in events
                                                           if event["node"] in PRE_FORK_NODES), 6),
                   "benchmark_approval": approve_note,
                   "t23_reached": any(event["node"] == "t23_evaluate" for event in events),
                   "t4_validated": any(event["node"] == "t4_validate" for event in events),
                   "validation_status": (state.get("report_validation") or {}).get("status"),
                   "decision": state.get("decision"), "input_changed": changed, "error": error}
    _write(folder / "measurement.json", measurement)
    return {"measurement": measurement, "state": state}


def compare(cases: list[dict], output: Path, repeats: int = 2, approve_note: str | None = None) -> dict:
    if len(cases) < 5 or len({case.get("id") for case in cases}) != len(cases):
        raise ValueError("서로 다른 id를 가진 사례가 최소 5건 필요합니다.")
    if repeats < 1:
        raise ValueError("repeats는 1 이상이어야 합니다.")
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"빈 출력 폴더를 지정하세요: {output}")
    pairs = []
    if approve_note is not None and not approve_note.strip():
        raise ValueError("고정 승인 사유가 비어 있습니다.")
    metadata = {"model": os.getenv("MODEL", "openai/gpt-4.1-mini"), "repeats": repeats, "cases": [],
                "benchmark_approval": approve_note}
    for index, case in enumerate(cases):
        if not isinstance(case.get("id"), str) or not case["id"].strip() or "rfp" not in case:
            raise ValueError("각 사례에 id와 rfp가 필요합니다.")
        fingerprints = {key: _fingerprint(case.get(key)) for key in ("rfp", "company", "notice_pdf")}
        metadata["cases"].append({"id": case["id"], "inputs": fingerprints,
                                   "notice_no": case.get("notice_no"), "notice_order": case.get("notice_order")})
        for repeat in range(1, repeats + 1):
            # Alternate order to reduce warm-cache and temporal order bias.
            order = ("sequential", "parallel") if (index + repeat) % 2 else ("parallel", "sequential")
            runs = {mode: _round(case, mode, output / f"case_{index + 1:02d}", repeat, fingerprints, approve_note)
                    for mode in order}
            left, right = runs["sequential"], runs["parallel"]
            lm, rm = left["measurement"], right["measurement"]
            valid = (not lm["error"] and not rm["error"] and lm["t23_reached"] and rm["t23_reached"]
                     and lm["t4_validated"] and rm["t4_validated"]
                     and lm["validation_status"] in {"passed", "needs_review"}
                     and rm["validation_status"] in {"passed", "needs_review"}
                     and not any(lm["input_changed"].values()) and not any(rm["input_changed"].values()))
            differences, score_delta = result_difference(left["state"], right["state"]) if valid else ([], 0.0)
            pairs.append({"case_id": case["id"], "repeat": repeat, "comparison_valid": valid,
                          "result_equivalent": valid and not differences and lm["decision"] == rm["decision"],
                          "different_fields": differences, "max_score_delta": score_delta,
                          "sequential": lm, "parallel": rm})
            _write(output / "pairs.json", pairs)
    valid_pairs = [pair for pair in pairs if pair["comparison_valid"]]
    averages = {mode: {"elapsed_seconds": mean(pair[mode]["elapsed_seconds"] for pair in valid_pairs),
                       "post_t15_median_seconds": median(pair[mode]["post_t15_seconds"] for pair in valid_pairs),
                        "embedding_total_tokens": (mean(pair[mode]["embedding_usage"]["total_tokens"]
                                                       for pair in valid_pairs)
                                                   if all(pair[mode]["embedding_usage"]["requests_with_usage"] > 0
                                                          and pair[mode]["embedding_usage"]["requests_missing_usage"] == 0
                                                          for pair in valid_pairs) else None)}
                for mode in ("sequential", "parallel")} if valid_pairs else {}
    summary = {"metadata": metadata, "pairs": pairs, "valid_pair_count": len(valid_pairs),
               "valid_case_count": len({pair["case_id"] for pair in valid_pairs}),
               "all_valid_results_equivalent": bool(valid_pairs) and all(pair["result_equivalent"] for pair in valid_pairs),
               "averages_for_valid_pairs": averages,
               "decision_ready": len({pair["case_id"] for pair in valid_pairs}) >= 5
                                 and all(pair["result_equivalent"] for pair in valid_pairs)}
    _write(output / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="순차·병렬 Workflow 동일 사례 비교")
    parser.add_argument("--cases", required=True, type=Path, help="최소 5개 사례를 담은 JSON 배열")
    parser.add_argument("--output", required=True, type=Path, help="비어 있는 새 결과 폴더")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--approve-note", help="담당자 검토에서 멈추는 사례를 이 사유로 고정 승인해 T2·T3까지 진행(벤치마크 전용, 결과에 기록)")
    args = parser.parse_args()
    summary = compare(json.loads(args.cases.read_text(encoding="utf-8")), args.output, args.repeats, args.approve_note)
    print(f"유효 비교: {summary['valid_case_count']}건 / {summary['valid_pair_count']}쌍")
    print(f"결과 동일: {summary['all_valid_results_equivalent']}, 선택 판단 준비: {summary['decision_ready']}")
    print(f"비교 결과: {args.output.resolve() / 'summary.json'}")


if __name__ == "__main__":
    main()
