"""3년치 용역 낙찰 목록을 월 단위로 받아 아이티센 그룹 낙찰만 저장 (재실행 시 완료 월은 건너뜀).

실행: .venv/bin/python scripts/collect_g2b_awards.py [출력폴더]  — .env의 G2B_SERVICE_KEY 필요, 약 300회 호출·20분.
API는 낙찰자명 필터를 지원하지 않고 조회 기간이 1개월로 제한되어 전체를 받아 로컬에서 거른다.
"""
import json, sys, time, calendar, requests
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from dotenv import dotenv_values
out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/market/g2b_awards_raw"); out.mkdir(parents=True, exist_ok=True)
key = dotenv_values(".env")["G2B_SERVICE_KEY"]
base = "https://apis.data.go.kr/1230000/as/ScsbidInfoService/getScsbidListSttusServcPPSSrch?"
TERMS = ("아이티센", "쌍용정보통신", "콤텍", "클로잇")
today = date.today() - timedelta(days=1); y, m = today.year - 3, today.month
while (y, m) <= (today.year, today.month):
    f = out / f"{y}{m:02d}.json"
    if not f.exists():
        last = min(calendar.monthrange(y, m)[1], today.day if (y, m) == (today.year, today.month) else 31)
        hits, total, page = [], None, 1
        while True:
            for attempt in range(4):
                try:
                    r = requests.get(base + urlencode({"serviceKey": key, "type": "json", "numOfRows": 999, "pageNo": page, "inqryDiv": "1",
                        "inqryBgnDt": f"{y}{m:02d}010000", "inqryEndDt": f"{y}{m:02d}{last:02d}2359"}, safe="%"), timeout=90)
                    body = r.json()["response"]["body"]; break
                except Exception as e:
                    print(f"{y}{m:02d} p{page} retry {attempt}: {type(e).__name__}", flush=True); time.sleep(5 * (attempt + 1))
            else:
                sys.exit(f"{y}{m:02d} p{page} 실패 — 재실행하면 이어서 진행")
            items = body.get("items") or []
            items = items if isinstance(items, list) else items.get("item", [])
            total = int(body.get("totalCount") or 0)
            hits += [i for i in items if any(t in (i.get("bidwinnrNm") or "") for t in TERMS)]
            if page * 999 >= total: break
            page += 1
        f.write_text(json.dumps({"month": f"{y}{m:02d}", "total": total, "hits": hits}, ensure_ascii=False))
        print(f"{y}{m:02d} total={total} group_hits={len(hits)}", flush=True)
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
print("DONE")
