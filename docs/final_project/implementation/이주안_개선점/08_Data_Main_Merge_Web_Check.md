# data 브랜치 main 병합과 웹 점검

- 날짜: 2026-11-27
- 작업: 이주안
- 대상: `data` 브랜치(M40~M49 작업) → `main`
- 병합: [PR #1 Merge data branch into main](https://github.com/2SEONGA/IntelligentAutomationPractice/pull/1)

---

## 1. 변경사항 확인

`data`는 `main`보다 커밋 13개 앞서 있었고, 그 사이 `main`에 새 커밋은 없었다. 그래서 충돌 없이 병합할 수 있었다(GitHub "Able to merge"). 바뀐 파일은 68개(+3,022 / −320)다.

| 영역 | 바뀐 내용 |
|---|---|
| 문서 읽기 | 겹쳐 찍힌 굵은 글자 정리, 별첨·별표·양식 쪽 판별 보완, 공고문 HWP·HWPX 지원 |
| 과거 사업 데이터 | 잘못 저장된 첨부 6건을 실제 제안요청서로 교체, 색인 102 → 110건 |
| T3 유사 사업 | 임계값 0.70 유지, AI 재정렬로 "관련 / 부분 / 분야 다름" 표시 |
| 속도·재현성 | T1 → T3 텍스트 재사용, 추출·임베딩 캐시 (5건 65.0초 → 52.7초) |
| 웹 실행 경로 | "나라장터 시장조사" 옵션 제거, 모든 웹 실행을 LangGraph로 통일(B안) |
| Windows | HITL 파일 잠금을 `msvcrt`로 처리, `fastapi` 의존성 추가, UTF-8 명시 |
| 새 공고 대응 | 새 공고를 T1.5 색인에 자동 추가, 재검색 때 의미 검색 상시 사용, T3 과거 사업 확장(웹 실행만) |

자세한 내용은 [`M40-M48_남은_업무_수행_기록.md`](../M40-M48_남은_업무_수행_기록.md)에 있다.

## 2. 병합

GitHub 웹에서 `base: main ← compare: data`로 PR #1을 만들고 Merge했다. `data` 브랜치는 지우지 않고 남겨 두었다.

## 3. 웹 점검

`data` 작업 기록의 "알려진 한계 1번"(작업 PC에 Node.js가 없어 웹 화면 빌드를 확인하지 못함)을 병합 후 확인했다.

환경: Windows 10, Node.js 26.13.1, 병합 직후 `main`(`c050936`)

```powershell
cd web
npm install
npm run build   # tsc -b && vite build
npm test        # node --test src/*.test.ts
```

| 항목 | 결과 |
|---|---|
| 타입 검사·빌드 | 성공 (874 모듈, `dist/` 생성) |
| 화면 테스트 | 28개 중 28개 통과 |
| 남은 `market_api`·`marketApi`·`market_available` 참조 | `web/src`, `src/bid_review/web`에 없음 |

웹 화면 코드는 고칠 곳이 없었다.

### 웹에서 달라진 점 (병합 결과)
- 새 검토 화면: "나라장터 시장조사" 스위치가 없어지고 "유사 사업·낙찰 정보" 안내 문구로 바뀌었다(실시간 조회 아님, 과거 사업 색인의 낙찰정보 사용).
- 역량·유사 사업 탭: 유사 사업 옆에 "AI 재정렬: 관련/부분/분야 다름"이 표시된다.
- API: `market_api` 옵션과 `/api/status`의 `market_available`이 없어졌다.
- 공고 검토 시작: 공고문을 PDF가 없으면 HWPX·HWP로 받는다.

## 4. `WEB_README.md`와 다른 점

`WEB_README.md`는 고치지 않았다. 병합 후 기준으로 아래 내용이 예전 설명으로 남아 있다.

| 위치 | 지금 적힌 내용 | 실제 (병합 후) |
|---|---|---|
| 첫 소개 | CrewAI 파이프라인(`run.py`)을 화면에서 실행 | LangGraph(T1 → T1.5 → 담당자 확인 → T2 → T3 → T4). live는 T4 뒤 CrewAI 브리핑 |
| 1. 실행 `.env` 표 | `G2B_SERVICE_KEY`: 시장조사용, 없으면 시장조사 꺼짐 | 웹 실행에서는 쓰지 않음(과거 사업 수집 스크립트 전용) |
| 2. 기술 스택 | `run.py`를 하위 프로세스로 실행 | `python -m bid_review.web.langgraph_job` 실행 |
| 4. 공고함 | 공고문 PDF를 받음 / 전체 검토는 시장조사를 끈 채 실행 | 공고문 PDF·HWPX·HWP / 시장조사 옵션 없음 |
| 5. 업무 흐름 3번 | RFP 분석 → 역량 매칭 ∥ 시장 조사 → 보고서 | T1 → T1.5 → 담당자 확인 → T2 → T3 → T4 → 체크리스트 |
| 10. 코드 구조 | `jobs.py`: run.py 하위 프로세스 실행 | `jobs.py`: langgraph_job 실행, `langgraph_job.py` 추가 |

## 5. 처음 실행할 때

`outputs/`는 Git에 없으므로 색인을 한 번 만들어야 한다. 없으면 T3가 "사용 불가"로 끝난다.

```powershell
pip install -r requirements-windows-py312.txt
# .env: OPENAI_API_KEY, OPENAI_MODEL, G2B_SERVICE_KEY, G2B_BID_NOTICE_KEY
$env:PYTHONPATH="src"
python scripts/build_rag_corpus.py
python scripts/build_rag_search_index.py
python scripts/build_semantic_index.py
python scripts/build_past_projects.py --index-only
cd web; npm install; npm run build; cd ..
python run_web.py
```
