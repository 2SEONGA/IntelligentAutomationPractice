"""VS Code entry point: run from any current working directory."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("PYTHONUTF8", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

if __name__ == "__main__":
    try:
        from bid_review.cli import main
        main()
    except ModuleNotFoundError as exc:
        raise SystemExit(
            f"필수 패키지 '{exc.name}'를 찾을 수 없습니다. "
            "먼저 setup.ps1을 실행하고 VS Code에서 .venv Python을 선택하세요."
        ) from exc
