"""Count provider-reported embedding tokens during one benchmark run.

The benchmark runs top-level cases one at a time. Branch threads share this
collector so parallel T2/T3 calls can contribute to the same measurement.
"""
from __future__ import annotations

from contextlib import contextmanager
from threading import Lock

_lock = Lock()
_active: dict[str, int] | None = None


@contextmanager
def collect_embedding_usage():
    global _active
    with _lock:
        if _active is not None:
            raise RuntimeError("동시에 여러 최상위 벤치마크를 실행할 수 없습니다.")
        counts = {"input_tokens": 0, "total_tokens": 0,
                  "requests_with_usage": 0, "requests_missing_usage": 0}
        _active = counts
    try:
        yield counts
    finally:
        with _lock:
            _active = None


def record_embedding_usage(usage) -> None:
    with _lock:
        if _active is None:
            return
        if usage is None or getattr(usage, "total_tokens", None) is None:
            _active["requests_missing_usage"] += 1
            return
        _active["requests_with_usage"] += 1
        _active["input_tokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
        _active["total_tokens"] += int(usage.total_tokens)
