"""웹페이지 실행: `.venv/bin/python run_web.py` → http://127.0.0.1:8000"""
from pathlib import Path
import os
import sys
import threading
import webbrowser

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("PYTHONUTF8", "1")
URL = "http://127.0.0.1:8000"

if __name__ == "__main__":
    if not (ROOT / "web/dist/index.html").is_file():
        raise SystemExit("웹 화면이 빌드되지 않았습니다. 먼저 실행하세요: cd web && npm install && npm run build")
    try:
        import uvicorn
        from bid_review.web.app import create_app
    except ModuleNotFoundError as exc:
        raise SystemExit(f"필수 패키지 '{exc.name}'가 없습니다. .venv/bin/pip install -e . 를 실행하세요.") from exc
    threading.Timer(1.5, webbrowser.open, args=(URL,)).start()
    print(f"공공 SI 입찰 검토 웹페이지: {URL}  (종료: Ctrl+C)")
    uvicorn.run(create_app(), host="127.0.0.1", port=8000, log_level="warning")
