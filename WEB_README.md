# 공공 SI 입찰 검토 웹

나라장터 공공 SI 입찰 공고를 **수집 → 규칙 필터 → Agent 검토 → 결재 → 결정**까지 브라우저 한 곳에서 처리하는 웹앱입니다.
아이티센 그룹 사업기획·프리세일즈 업무(입찰 공고 분석과 Go/No-Go 검토)를 대상으로 하며, 기존 CrewAI 파이프라인(`run.py`)을 화면에서 실행하고 결과를 보여줍니다.

- 로컬 전용: 서버는 `127.0.0.1`에만 열립니다.
- 디자인: 원티드 Montage 디자인 시스템, Pretendard 글꼴, ITCEN 로고.

---

## 1. 실행

처음 한 번 화면을 빌드합니다 (Node.js 20.19 이상).

```bash
cd web && npm install && npm run build && cd ..
```

서버를 켭니다. 브라우저가 `http://127.0.0.1:8000`으로 열립니다.

```bash
.venv/bin/python run_web.py          # macOS
.venv\Scripts\python.exe run_web.py  # Windows
```

`.env`에 넣는 키:

| 키 | 용도 | 없으면 |
|---|---|---|
| `OPENAI_API_KEY` | live 실행 (CrewAI + OpenAI) | offline(규칙 기반)만 가능 |
| `G2B_BID_NOTICE_KEY` | 나라장터 입찰공고 수집 | `G2B_SERVICE_KEY`로 대신 시도 |
| `G2B_SERVICE_KEY` | 나라장터 낙찰정보(시장조사) | 시장조사 꺼짐 |

키 값은 화면·로그·응답 어디에도 나오지 않습니다.

---

## 2. 기술 스택

| 영역 | 사용 |
|---|---|
| 백엔드 | FastAPI 0.143.0, uvicorn. 검토는 `run.py`를 하위 프로세스로 실행하고 진행 상황을 SSE로 전달 |
| 프론트엔드 | React 18.3.1, Vite 8.3.4, TypeScript 5.9.3 |
| 디자인 | `@montage-ui/core`·`@montage-ui/icon` 4.1.3, Montage CSS 변수만 사용 (`--semantic-*`, `--radius-*`) |
| 설정 | `config/*.yaml` (공고 필터·체크리스트·Agent 프롬프트 등) |
| 테스트 | 백엔드 `python tests/test_*.py`, 프론트 `node --test` |

---

## 3. 역할과 권한

앱을 열면 **시작 화면에서 역할을 고릅니다.** 고른 역할은 그 탭을 닫을 때까지 유지되고, 상단 오른쪽 **역할 변경**으로 다시 고릅니다.

| 동작 | 사업기획 담당자 | 사업본부장 |
|---|:-:|:-:|
| 공고 수집, 확인 필요 공고 포함/제외, 검토 시작, 전체 검토 시작, PDF 업로드 | ✓ | |
| 체크리스트 수정, 결재 올리기·다시 올리기, 다시 실행 | ✓ | |
| Go / 보류 / No-Go 결정, 반려 | | ✓ |
| 판단 기준 세트 만들기·고치기·지우기 | ✓ | ✓ |
| 적용할 판단 기준 고르기 | | ✓ |

- 화면 버튼과 서버 API가 같은 권한표를 씁니다. 요청마다 `X-Role` 헤더를 보내고, 맞지 않으면 서버가 403으로 막습니다.
- 지금은 보안 인증이 아니라 역할 구분입니다. 실제 운영에서는 시작 화면 자리를 사내 계정 로그인으로 바꿉니다.

---

## 4. 화면

왼쪽 메뉴: 대시보드 · 공고함 · 검토 · 결재함 · 의사결정 이력 · 판단 기준. 모든 화면은 주소가 있어 새로고침·뒤로가기·링크 공유가 됩니다 (예: `#/notices`, `#/run/<id>`).

### 대시보드 (`#/dashboard`)
- 최근 수집 정보(수집 시각·기간·키워드)
- 카드: 수집한 공고, 규칙 필터 통과, 담당자 확인 필요, 결재 대기
- 처리 흐름 막대: 1. 수집 → 2. 필터 통과 → 4~7. Agent 검토 → 8. 결재 대기 → 9. 결정 완료
- AI 권고 분포(Go / 조건부 Go / 보류 / No-Go)와 본부장 결정 일치율
- 결재 대기 목록, 마감 임박 검토 대상 목록

### 공고함 (`#/notices`)
- **지금 수집**: 나라장터 용역 입찰공고를 최근 7일/30일 범위로 가져와 규칙으로 판정합니다.
- 탭: **검토 대상 / 담당자 확인 필요 / 제외 / 검토 중·완료**
- 판정 근거를 공고마다 보여줍니다 (예: "마감 임박 / (2026-10-28)", "제한경쟁 / — 공고문 원문 확인").
- 확인 필요 공고는 사유를 적고 **포함/제외**합니다 (HITL-2).
- **검토 시작**: 나라장터 첨부에서 공고문 PDF와 RFP PDF를 받아 바로 실행합니다. RFP가 HWP뿐이면 PDF 업로드 화면으로 넘어갑니다.
- **전체 검토 시작 (N)**: 아직 시작하지 않은 검토 대상을 마감 순으로 한 건씩 차례로 실행합니다.
  - offline / live 선택 (live는 비용 경고 표시), 시장조사는 끈 채 실행
  - RFP PDF가 없는 공고는 건너뛰고 "RFP 업로드 대기"로 남김
  - 진행률·현재 공고 표시, **중지**(지금 건이 끝나면 멈춤), 끝나면 완료·대기·실패 요약
- **＋ PDF로 새 검토**: 수집되지 않은 공고도 RFP PDF(필수)와 공고문 PDF(선택)를 올려 검토합니다.
- 검색(공고명·기관), 필터(마감 구간·예산 구간), 정렬(마감 임박순·예산 큰 순)
- 정보 상자: 최근 수집, 수집 조건, 적용 중인 판단 기준과 기준 칩

### 검토 (`#/reviews`, `#/job/<id>`, `#/run/<id>`)
- **목록**: 웹·CLI로 만든 모든 보고서와 결재 상태. 검색(공고명·공고번호), 필터(AI 권고·결재 상태).
- **진행 화면**: Agent 1 → (2 ∥ 3) → 4 → 체크리스트 단계를 실시간으로 표시. 실행 로그는 접혀 있다가 실패하면 펼쳐집니다. 이미 끝난 실행 주소를 열면 "진행 중인 실행이 아니에요" 안내.
- **결과 화면**
  - 요약 카드: 보고서 권고, 사업금액(40억 기준 막대), 체크리스트 1단계·2단계 점수
  - 결재 카드: 상태와 이력, 결재 올리기 / 다시 올리기 / 반려 / 다시 실행(offline·live)
  - 결정 카드: 결정할 수 있거나 결정된 건은 분석 탭 위에 표시
  - 탭: 요약(다음 할 일 강조) · RFP 분석 · 역량 매칭 · 시장 조사 · Go/No-Go 체크리스트
  - 근거 쪽 번호를 누르면 RFP PDF 원문이 해당 쪽으로 열림
  - 내려받기: 보고서 MD·JSON, 체크리스트 결과

### 결재함 (`#/approvals`)
- 담당자: **보완 요청**(반려 사유와 요청 방식: 수기 보완/재실행), 올린 결재 대기
- 본부장: **결재 대기** 건
- 검색(공고명·공고번호), 필터(AI 권고·예산 구간)

### 의사결정 이력 (`#/decisions`)
- 본부장 결정·사유·반려 횟수, AI 권고와 다른 결정 표시

### 판단 기준 (`#/criteria`)
1. **공고 규칙 필터**: 검토 대상 / 담당자 확인 필요 / 제외 세 칸과 판정 우선순위(제외 › 확인 필요 › 검토 대상)
2. **Go/No-Go 체크리스트**: 1단계 참가 가능 여부(K1~K4), 0~10점 구간 막대, 2단계 점수 항목(S1~S5)
3. **근거 데이터와 한계**: 조회 규모(용역 낙찰 351,025건 › 그룹 낙찰 93건), 출처·기간·그룹사 판별·수집일, 한계 카드 3장(빠진 것 / 영향 / 대응)

**판단 기준 세트**
- "기본" 세트는 `config/notice_filter.yaml` + `config/checklist.yaml`이며 화면에서 고칠 수 없습니다.
- 기본(또는 다른 세트)을 복제해 이름 붙인 새 세트를 만들고 저장합니다. 고칠 수 있는 값: 금액 하한, 마감 경고 일수, 분야 키워드, 제외·확인 필요 계약방식, S2 키워드, S3 핵심 기관, Go·검토 점수 구간.
- 본부장이 **이 기준 적용**으로 고른 세트 하나가 이후 수집과 새 검토에 쓰입니다. 적용 중인 세트는 지울 수 없습니다.
- 검토를 시작할 때 그 시점 기준을 건 폴더에 스냅샷하므로, 나중에 기준을 바꿔도 이미 판정한 건은 그대로입니다.

---

## 5. 업무 흐름 (3주차 To-Be)

1. **담당자 — 공고함**: 지금 수집 → 규칙 필터 결과 확인 → 확인 필요 공고 포함/제외
2. **담당자 — 검토 시작**(한 건 또는 전체): 첨부 자동 확보, RFP가 HWP면 PDF 업로드
3. **Agent 실행**: RFP 분석 → 역량 매칭 ∥ 시장 조사 → 보고서 → 체크리스트 판정
4. **담당자 — 결과 화면**: 체크리스트 확인·수정 → **결재 올리기** (결재 대기 중에는 체크리스트 잠김)
5. **본부장 — 결재함**: **결정**(Go/보류/No-Go, 사유 필수) 또는 **반려**(사유 + 수기 보완/재실행)
6. **담당자 — 보완 요청**: 체크리스트 수정 후 다시 올리기, 또는 다시 실행
7. 결정은 **의사결정 이력**과 **대시보드**에 쌓임

결재 상태: 검토 완료 → 결재 대기 → 결정 완료 (반려 시 보완 요청 → 다시 결재 대기)

---

## 6. 화면 품질

- **좁은 화면**: 1100px 이하 카드 2열, 900px 이하 아이콘 사이드바, 640px 이하 1열·검색창 별도 줄·제목 한 줄. 표와 탭 줄은 가로 스크롤.
- **키보드**: 표의 공고명은 버튼이라 Tab으로 이동해 Enter·Space로 엽니다. PDF 선택 영역도 Enter·Space로 엽니다. 아이콘만 남은 메뉴는 이름을 `aria-label`로 제공합니다.
- **상태 표시 통일**: 불러오는 중, 빈 목록(`Empty`), 불러오기 실패와 다시 시도(`LoadError`)를 모든 목록이 같은 모양으로 씁니다.
- **캐시**: `index.html`은 `no-cache`라 다시 빌드하면 새로고침 한 번으로 새 화면이 보입니다.

---

## 7. 보안·운영 장치

- 서버는 `127.0.0.1`에만 바인딩. `Host`·`Origin` 검사로 다른 사이트에서 보낸 요청(CSRF)과 DNS 리바인딩을 막습니다.
- 상태를 바꾸는 API는 역할 헤더를 검사합니다(없거나 틀리면 403).
- 업로드는 PDF 시그니처와 50MB 한도를 확인합니다. 폴더·세트 이름은 경로 밖으로 나가지 못하게 검사합니다.
- 검토는 한 번에 하나만 실행됩니다(동시 실행 요청은 409).
- live 실행은 비용이 들기 때문에 실행 방식은 항상 사용자가 고르고, live에는 경고를 띄웁니다.

---

## 8. API

| 메서드 | 경로 | 내용 | 권한 |
|---|---|---|---|
| GET | `/api/status` | 키 보유 여부, 실행 중인 검토 | |
| GET | `/api/dashboard` | 대시보드 숫자 | |
| GET | `/api/notices` | 수집 공고와 판정 | |
| POST | `/api/notices/collect` | 지금 수집 | 담당자 |
| PUT | `/api/notices/{id}/review` | 확인 필요 공고 포함/제외 | 담당자 |
| POST | `/api/notices/{id}/start` | 공고 검토 시작 | 담당자 |
| POST | `/api/notices/start-all` | 전체 검토 시작 | 담당자 |
| GET | `/api/notices/batch` | 전체 검토 진행 상태 | |
| POST | `/api/notices/batch/stop` | 전체 검토 중지 | 담당자 |
| POST | `/api/runs` | PDF 업로드로 검토 시작 | 담당자 |
| GET | `/api/jobs/{id}/events` | 진행 상황 (SSE) | |
| GET | `/api/runs` · `/api/runs/{id}` | 보고서 목록·상세 | |
| GET | `/api/runs/{id}/files/{name}` | 보고서·PDF 내려받기 | |
| PUT | `/api/runs/{id}/checklist` | 체크리스트 수정 | 담당자 |
| PUT | `/api/runs/{id}/workflow` | 결재 올리기·다시 올리기·반려 | 담당자·본부장 |
| PUT | `/api/runs/{id}/decision` | 결정 | 본부장 |
| POST | `/api/runs/{id}/rerun` | 다시 실행 | 담당자 |
| GET | `/api/approvals` · `/api/decisions` | 결재함·의사결정 이력 | |
| GET | `/api/criteria?set=` | 판단 기준(세트별) | |
| PUT · DELETE | `/api/criteria/sets/{name}` | 기준 세트 저장·삭제 | 담당자·본부장 |
| PUT | `/api/criteria/active` | 적용 세트 변경 | 본부장 |

---

## 9. 저장 위치

| 데이터 | 위치 |
|---|---|
| 수집 공고와 담당자 판단 | `outputs/notices/notices.json` |
| 웹 검토 결과 | `outputs/web/<실행시각>/` (CLI 결과 `outputs/offline`, `outputs/live`도 목록에 표시) |
| 결재 상태·이력 | 건 폴더 `workflow.json` |
| 결정·체크리스트 수정값·실행 방식 | 보고서 옆 `<보고서>.decision.json`, `.checklist.json`, `.run.json` |
| 검토 시작 시점의 판단 기준 | 건 폴더 `criteria.json` |
| 판단 기준 세트·적용 세트 | `outputs/criteria/sets/<이름>.json`, `outputs/criteria/active.json` |

보고서 JSON 자체는 고치지 않고, 사람의 판단은 옆 파일에 따로 남깁니다.

---

## 10. 코드 구조

```
src/bid_review/web/
  app.py         FastAPI 앱: 권한, 미들웨어, 모든 API, 전체 검토 대기열
  runs.py        보고서 목록·상세, 결재 상태 전이, 결정·체크리스트 저장
  jobs.py        run.py 하위 프로세스 실행, [STAGE] 줄 → SSE 이벤트
  notices.py     나라장터 공고 수집·저장, 첨부 받기
  dashboard.py   대시보드 숫자 계산
  criteria.py    판단 기준 세트 저장·병합·스냅샷
src/bid_review/notice_filter.py   공고 규칙 판정
src/bid_review/checklist.py       Go/No-Go 체크리스트 평가
run_web.py                        서버 실행

web/src/
  App.tsx        역할·화면 전환·주소 연동
  route.ts       화면 ↔ 주소(#/...)
  api.ts         API 호출 (X-Role 헤더)
  role.ts        역할 권한표, 결과 화면 권한
  notices.ts     공고 동작·검색·정렬
  filters.ts     검색·마감/예산 구간
  criteria.ts    판단 기준 편집 값 변환·검사
  stages.ts      진행 단계 상태
  summary.ts     요약 카드 계산
  screens/       RoleSelect, Dashboard, Notices, NewReview, JobProgress, ReviewHome,
                 RunResult, Approvals, Decisions, Criteria
  components/    Sidebar, TopBar, InfoBar, FilterBar, BatchStart, WorkflowCard, DecisionCard,
                 SummaryCards, StageTracker, DownloadMenu, Empty, LoadError, IconDashboard,
                 IconCriteria, tabs/(Summary·Rfp·Capability·Market·Checklist)
```

---

## 11. 테스트

```bash
# 백엔드 (가짜 실행·가짜 나라장터 응답 사용, 비용 없음)
for f in tests/test_*.py; do .venv/bin/python $f; done

# 프론트엔드
cd web && npm test && npm run build
```

| 백엔드 | 내용 |
|---|---|
| `test_app`, `test_app_v2` | API 전반, 역할 권한, 공고 흐름, 결재, 다시 실행 |
| `test_app_criteria`, `test_criteria` | 판단 기준 세트, 경로 검사, 스냅샷 |
| `test_app_batch` | 전체 검토 시작·대기·중지 |
| `test_runs`, `test_workflow`, `test_jobs`, `test_dashboard` | 보고서·결재 전이·실행·대시보드 |
| `test_notices`, `test_notice_filter`, `test_checklist` | 수집·규칙 판정·체크리스트 |

| 프론트 | 내용 |
|---|---|
| `role.test.ts` | 권한표, 역할 저장 |
| `route.test.ts` | 화면 ↔ 주소 |
| `notices.test.ts`, `filters.test.ts` | 공고 동작, 검색·필터·정렬 |
| `criteria.test.ts` | 판단 기준 입력 검사 |
| `stages.test.ts`, `summary.test.ts` | 진행 단계, 요약 계산 |

---

## 12. 알려진 한계

- 전체 검토 진행 상태는 서버 메모리에만 있어 서버를 다시 켜면 진행 표시가 사라집니다(만든 보고서는 남음).
- 판단 기준 세트 이름은 바꿀 수 없습니다(복제 후 새 이름으로 저장).
- 체크리스트 항목(K1~K4, S1~S5) 자체를 추가·삭제하는 기능은 없습니다.
- 그 밖에 미뤄 둔 작은 문제는 `docs/변경사항_2026-10-27.md`에 정리돼 있습니다.

## 관련 문서

- 실행 방법 상세: `실행안내.md`
- 변경 이력: `docs/변경사항_2026-10-27.md`
- 설계·계획: `docs/superpowers/specs/`, `docs/superpowers/plans/`
- 체크리스트 근거: `docs/go_no_go_checklist.md`
