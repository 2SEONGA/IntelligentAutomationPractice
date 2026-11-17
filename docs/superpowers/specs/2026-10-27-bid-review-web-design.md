# 공공 SI 입찰 검토 웹페이지 설계

작성일: 2026-10-27 · 대상 코드: 이 저장소의 `bid_review` 파이프라인 (CrewAI 1.15.28)

## 1. 무엇을 왜 만드나

아이티센 그룹 사업기획·프리세일즈 담당자가 나라장터 공공 SI 입찰 공고를 받아 Go/No-Go를 검토하는 업무를, **웹페이지 한 곳에서 처음부터 끝까지** 처리하게 한다.

- **사용 흐름:** 제안요청서 PDF를 올린다 → Agent 4개를 실행한다 → 결과와 체크리스트 판정을 본다 → 사업본부장 결정을 기록한다 → 보고서를 내려받는다.
- **쓰는 자리:** 팀 발표 때 화면에 띄워 실제로 시연한다. 시연용 모형이 아니라 실제로 파이프라인을 돌리는 도구여야 한다.
- **발표 메시지:** "Agent 1 → 2·3 병렬 → 4"의 협업이 진행되는 모습이 화면에서 보여야 한다.

## 2. 목표 / 비목표 / 반목표

**목표**
- 같은 화면에서 PDF 업로드, 실행, 결과 확인, 체크리스트 판정, 결정 기록, 보고서 다운로드를 모두 할 수 있다.
- offline(규칙 기반, 무료)과 live(CrewAI + OpenAI) 둘 다 웹에서 실행할 수 있다.
- 지난 실행 결과(CLI로 만든 `outputs/` 포함)를 목록에서 다시 열 수 있다.
- `config/checklist.yaml`의 체크리스트로 항목별 판정과 점수를 보여주고, 담당자가 고칠 수 있다.

**비목표 (이번에 안 함)**
- 로그인·권한, 여러 사용자, 서버 배포 — 로컬 PC에서만 실행한다.
- 웹에서 YAML 설정 편집.
- 모바일 화면 최적화 (발표용 노트북·프로젝터 1280px 이상 기준).
- 여러 검토를 동시에 실행 — 한 번에 하나만 실행한다.

**반목표 (기술적으로 돌아가도 이렇게 되면 실패)**
- AI 권고가 최종 결정처럼 보이는 화면. 최종 결정은 항상 사람(사업본부장)이 하고, 화면에 그렇게 표시한다.
- 근거 없는 값을 채워 넣는 것. 데이터가 없으면 `확인 필요`로 둔다(기존 파이프라인 원칙과 같음).
- API 키(OpenAI, 나라장터)가 화면·응답·로그·저장 파일에 노출되는 것.
- 발표 도중 멈춘 것처럼 보이는 화면. 진행 상황이 끊기지 않고 보여야 한다.

## 3. 정해진 것

### 3.1 화면 구성 (사용자가 고름: B안)

화면 틀은 상단 바, 왼쪽 공고 목록, 오른쪽 작업 영역으로 이루어진다. 승인된 시안은 `.superpowers/brainstorm/71305-1793104972/content/flow-montage.html`이다.

- **상단 바:** 아이티센 로고(공식 SVG, `itcen.com/resources/itcen/assets/svg/logos/logo.svg`), 제목 "공공 SI 입찰 검토", 오른쪽에 "사업기획 · 프리세일즈".
- **왼쪽 목록:**
  - 맨 위에 "＋ 새 공고 검토" 버튼이 있다.
  - 그 아래 검토한 공고가 최신순으로 나온다. 각 항목에는 사업명, 상태 배지(AI 권고 또는 저장된 결정), 날짜, 실행 방식이 보인다.
  - 진행 중인 검토는 "진행 중" 칸에 따로 보인다.
- **오른쪽 작업 영역:** 아래 세 상태 중 하나를 보여준다.

**① 새 공고 검토**
- 제안요청서 PDF(필수)를 올린다. 끌어다 놓기와 파일 선택 모두 된다. 올린 뒤에는 파일명·크기·쪽수와 "변경" 버튼을 보여준다.
- 입찰공고문 PDF(선택)를 올린다. 비어 있으면 "참가자격·공고번호가 '확인 필요'로 남아 보류가 나올 수 있어요"라고 안내한다.
- 실행 방식은 segmented control로 고른다: `offline · 규칙 기반 (무료)` / `live · CrewAI + OpenAI`.
  - `.env`에 `OPENAI_API_KEY`가 없으면 live를 비활성화하고 이유를 표시한다.
- 나라장터 시장조사 스위치가 있다. **live 모드이고**, 공고문 PDF가 있고, `G2B_SERVICE_KEY`가 있을 때만 켤 수 있다. 켜면 CLI에 `--allow-market-api`를 넘긴다.
  - `cli.py`는 offline에서 이 옵션을 오류로 막는다. 그래서 offline에서는 스위치를 비활성화하고 "live에서만 사용 가능"이라고 표시한다. (Claude의 결정)
- 고정 정보 한 줄을 보여준다: 회사 역량 자료(`data/company/company`), 체크리스트 판(`config/checklist.yaml`의 `version`).
- 끝으로 "검토 시작" 버튼이 있다.

**② Agent 실행**
- 진행 추적 막대를 보여준다: `AGENT 1 RFP 분석` → (`AGENT 2 역량 매칭`, `AGENT 3 시장 조사`를 위아래로 병렬) → `AGENT 4 보고서 작성` → `체크리스트 Go/No-Go`.
  - 각 노드의 상태는 대기 / 실행 중 / 완료 / 건너뜀 / 실패 중 하나다.
- 그 아래에 실행 로그를 실시간으로 흘린다(어두운 터미널 스타일). CLI가 출력하는 줄을 그대로 보여준다.
- 완료되면 자동으로 ③으로 넘어간다. 실패하면 오류 메시지와 "다시 시도" 버튼을 보여준다.

**③ 결과 + ④ 결정**
- **머리:** 사업명, 실행 일시·방식, "보고서 내려받기 (MD·JSON)" 버튼.
- **권고가 두 가지다 (Claude의 결정).** 보고서 권고(Agent 4의 `decision_status`)와 체크리스트 권고(3.4 규칙)는 서로 다를 수 있다. 두 값을 각각 이름을 붙여 따로 보여주고, 하나로 합치지 않는다.
  - 둘이 다르면 그 아래에 "보고서 권고와 체크리스트 권고가 다릅니다. 근거를 확인하세요"라는 SectionMessage(cautionary)를 띄운다.
  - 왼쪽 목록 배지는 저장된 결정이 있으면 그것을 쓴다(예: "No-Go 결정"). 없으면 보고서 권고를 "AI: 보류"처럼 앞에 `AI:`를 붙여 보여준다.
- **요약 카드 4장:**
  - 보고서 권고(`decision_status`)와 그 이유 한 줄, 그 아래 작게 체크리스트 권고
  - 사업금액, 그리고 과거 낙찰 하한 40억 대비 막대
  - 체크리스트 1단계 충족 수 / 4
  - 체크리스트 2단계 점수 / 10과 권고 구간
- **탭 5개:**

| 탭 | 내용 |
|---|---|
| 요약 | `executive_summary`, `key_findings`, `bid_conditions`, `next_actions` |
| RFP 분석 | `debug/01_rfp_analysis.json`의 기본정보·요구사항·품질 게이트 |
| 역량 매칭 | `debug/02_capability_matching.json` |
| 시장 조사 | `debug/03_market_research.json` (유사사업 표, 출처 링크) |
| Go/No-Go 체크리스트 | 아래 3.4 |

- 근거 페이지(`source_page`)가 있는 값에는 `p.N ↗` 링크를 붙인다. 누르면 올린 PDF를 새 탭에서 그 쪽으로 연다(`…/rfp.pdf#page=N`).
- **사업본부장 결정 카드:** Go / 보류 / No-Go segmented control, 결정 사유 입력(필수), "결정 저장" 버튼.
  - 저장된 결정은 왼쪽 목록 배지에 반영된다. 예: "No-Go 결정".
  - AI 권고와 저장된 결정은 서로 다른 칸에 표시해서 섞이지 않게 한다.

### 3.2 디자인 시스템 (사용자가 정함: 원티드 Montage)

- `@montage-ui/core`, `@montage-ui/icon`을 쓴다. 공식 저장소는 `wanteddev/montage-web`, MIT 라이선스.
  - 두 패키지는 반드시 **같은 버전으로 고정**한다(공식 README의 경고).
  - 버전은 공개된 지 2주 이상 지난 4.x 중 최신을 고른다. 4.1.5는 2026-10-27 공개라서 너무 새롭다.
- 진입점에 `ThemeProvider`와 `@montage-ui/core/global.css`를 넣는다.
- 글꼴은 Pretendard(jsDelivr `orioncactus/pretendard@v1.3.9` dynamic subset)를 쓴다.
- 쓰는 컴포넌트: `TopNavigation`, `Button`, `SegmentedControl`, `Switch`, `Tab`, `Table`, `Card`, `ContentBadge`, `ProgressTracker`, `SectionMessage`, `TextArea`, `Toast`(또는 `Snackbar`), `Skeleton`, `Loading`.
  - 실제 export 이름은 설치한 버전에서 확인한다.
  - Montage에 없는 것(파일 끌어다 놓기 영역, 로그 패널)만 Montage 토큰으로 직접 만든다.
- 색은 Montage 토큰을 그대로 쓴다. 메인은 `blue 50 #0066FF`이고, 아이티센 블루(#317CFF)로 바꾸지 않는다.
  - 상태 배지: 보류 = cautionary(orange), Go = positive(green), No-Go = negative(red), 진행 중 = primary(blue).

### 3.3 기술 구조 (Claude의 결정 — 사용자는 "기술 스택을 같이 정하자"고 했고, 아래 안에 이의가 없었다)

```
브라우저 (React + Vite + TypeScript + Montage)
   │  fetch / EventSource(SSE)
FastAPI (Python, 127.0.0.1)  ── subprocess ──▶  run.py (기존 CLI)  ──▶  outputs/web/<run_id>/
   │                                                                     (보고서·debug JSON)
   └─ 체크리스트 평가 (bid_review/checklist.py) · 결정 저장 (decision.json)
```

- **백엔드:** FastAPI + uvicorn.
  - 기존 파이프라인은 **CLI를 하위 프로세스로 실행**한다(`.venv` python `run.py --mode … --rfp … [--notice-pdf …] [--allow-market-api] --output outputs/web/<run_id> --save-intermediate --verbose`).
  - `--verbose`는 필수다. 단계 출력이 `self.verbose`일 때만 나오기 때문이다.
  - 이유 1: 진행 상황이 지금 `print`로만 나오므로, stdout 줄을 그대로 SSE로 흘리면 파이프라인 코드를 고칠 필요가 없다.
  - 이유 2: CrewAI 실행이 서버 프로세스를 죽이지 않는다.
  - 이유 3: 웹 결과와 CLI 결과의 폴더 구조가 같아진다.
- **프론트엔드:** React 18 + Vite + TypeScript. 라우팅은 화면이 하나라서 상태로만 처리한다(라우터 없음). 차트 라이브러리는 쓰지 않는다(막대는 CSS로).
- **실행:**
  - 발표·사용: `python run_web.py` 한 줄로 FastAPI가 빌드된 프론트(`web/dist`)까지 서빙하고 브라우저를 연다.
  - 개발: `npm run dev`(Vite, `/api` 프록시)와 uvicorn을 따로 띄운다.
- **바인딩:** `127.0.0.1`만 쓴다. 외부 접속은 받지 않는다.

### 3.4 체크리스트 평가 (Claude의 결정)

새 모듈 `src/bid_review/checklist.py`가 실행 결과 폴더를 읽어 `config/checklist.yaml`의 항목마다 자동 판정을 만든다. 자동 판정할 근거가 없으면 `확인 필요`(K) 또는 `null`(S, 점수 미정)로 둔다. 숫자를 지어내지 않는다.

| 항목 | 자동 판정 규칙 | 근거 표시 |
|---|---|---|
| K1 참가자격 | `02_capability_matching.json`의 `eligibility_matches`를 본다. 하나라도 `미충족`이면 미충족, 모두 `충족`이면 충족, 그 밖에는 `확인 필요`. 항목이 없으면 `확인 필요`. | 각 match의 근거·`source_page` |
| K2 대기업 참여제한 | 항상 `확인 필요`로 시작한다. 하한 금액이 고시 원문 확인 대상이라 자동으로 판정하지 않는다. 사업금액과 "과거 낙찰 하한 40억" 비교를 근거 칸에 적는다. | `project_scale` |
| K3 공동수급·지역 | 항상 `확인 필요`로 시작한다. 현재 분석에 해당 필드가 없다. | — |
| K4 필수 인증·인력 | `staffing_matches`에 K1과 같은 규칙을 적용한다. | 각 match의 근거 |
| S1 사업 규모 | `project_scale.amount_krw` 기준: ≥100억 → 2, ≥40억 → 1, 그 미만 → 0. 금액이 없으면 null. | `project_scale.source_page` |
| S2 사업 유형 | 사업명·범위 키워드를 checklist.yaml 2점/1점 유형과 맞춰 본다. 판단이 안 서면 null. | 매칭된 키워드 |
| S3 발주기관 | `basic_info.procuring_entity`가 핵심 7개 기관이면 2, `data/market/itcen_group_awards_3y.json`에 3년 내 수주 이력이 있으면 1, 없으면 0. 발주기관이 없으면 null. | 나라장터 데이터 출처 |
| S4 후속 사업 | 같은 기관에 대해 사업명 유사도가 0.6을 넘는 과거 낙찰이 데이터에 있으면 2, 아니면 0. 발주기관이 없으면 null. | 일치한 과거 공고명·낙찰일 |
| S5 예상 경쟁 | S4에서 찾은 직전 차수 낙찰의 `prtcptCnum`(참여업체 수)을 쓴다: 1 → 2, 2 → 1, 3 이상 → 0. 직전 차수가 없으면 null. 03 시장조사에는 이 필드가 없어서 쓰지 않는다. (Claude의 결정) | 직전 차수 공고명·낙찰일 |

- K2·K3는 사람이 확인하기 전까지 늘 `확인 필요`다. 그래서 담당자가 화면에서 확인하기 전까지 체크리스트 권고는 대개 **보류**다.
  - 이는 "근거 없이 채우지 않는다"는 원칙에 따른 의도된 결과다.
  - 화면에서는 K 항목 옆에 "담당자 확인 필요"를 눈에 띄게 표시해서, 무엇을 확인하면 판정이 나아가는지 보이게 한다. (Claude의 결정)

**판정 규칙 (checklist.yaml `decision`)**
- K 중 하나라도 `미충족` → No-Go 권고. `확인 필요`가 있으면 → 보류.
- 그 외에는 점수 합으로 권고한다: 6점 이상 Go, 4~5점 사업본부장 검토, 3점 이하 No-Go.
- null 항목은 0으로 세지 않는다. "n개 항목 미정"으로 따로 표시한다.

**담당자 수정**
- 담당자는 화면에서 K(충족·확인 필요·미충족)와 S(0·1·2)를 고칠 수 있다.
- 고친 값은 `outputs/web/<run_id>/checklist_overrides.json`에 저장한다. AI 값과 고친 값을 둘 다 보여준다(고친 칸 표시).

### 3.5 결정 기록 (Claude의 제안 — 사용자가 추가 승인)

- `outputs/<…>/<run_dir>/decision.json`에 저장한다: `{decision: "Go"|"보류"|"No-Go", reason: str, decided_at: ISO8601(KST), checklist_snapshot: {...}}`.
- 사유는 필수다. 다시 저장하면 덮어쓰되, 이전 값은 `decision_history`에 쌓는다.
- 기존 보고서 JSON의 `final_decision: "미결정"`은 건드리지 않는다. 결정은 별도 파일로 둔다. 파이프라인 산출물을 사람이 고친 것으로 오해하지 않게 하기 위해서다.
- 보고서 내려받기 묶음에 `decision.json`과 체크리스트 결과를 포함한다.

### 3.6 API

| 메서드·경로 | 하는 일 |
|---|---|
| `GET /api/status` | live 가능 여부(`OPENAI_API_KEY` 존재), 나라장터 가능 여부(`G2B_SERVICE_KEY` 존재), 체크리스트 버전, 실행 중인 run_id. 키 값은 절대 보내지 않는다. |
| `POST /api/runs` (multipart) | `rfp`(PDF, 필수), `notice`(PDF, 선택), `mode`, `market_api`(bool). 이미 실행 중이면 409. 파일 검사(아래 3.7) 후 run_id를 만들고 하위 프로세스를 시작한다. |
| `GET /api/runs/{id}/events` (SSE) | `log`(stdout 한 줄), `stage`(노드 상태 변경), `done`, `error` 이벤트. 다시 접속하면 지금까지의 로그를 먼저 보낸다. |
| `GET /api/runs` | `outputs/` 아래 모든 실행(offline·live·web)을 최신순으로 반환한다: id, 사업명, 날짜, 모드, AI 권고, 저장된 결정. |
| `GET /api/runs/{id}` | 보고서 JSON, debug 01~03 JSON, 체크리스트 평가(자동값 + 수정값), 결정. |
| `GET /api/runs/{id}/files/{name}` | 보고서 MD·JSON, 원본 PDF. 해당 실행 폴더 안의 파일과, 그 실행의 원본 RFP(`data/input/` 아래 `.pdf`)만 허용한다. |
| `PUT /api/runs/{id}/checklist` | 담당자 수정값 저장. |
| `PUT /api/runs/{id}/decision` | 결정 저장. |

### 진행 상황 표시 (Claude의 결정)

**문제:** 기존 출력 문구로는 단계를 정확히 알 수 없다.
- live에는 Agent 1 시작 줄이 없다.
- `[4/4]`는 Agent 4가 끝날 때만 찍힌다.
- offline은 단계 줄이 한꺼번에 찍힌다.

**해결:** `crew.py`에 **기계용 단계 표시 줄**을 추가한다.
- 형식: `[STAGE] <node> <state>`
  - `node`: `agent1` | `agent2` | `agent3` | `agent4`
  - `state`: `start` | `done` | `skip` | `fail`
- 이 줄은 `--verbose` 여부와 상관없이 항상 출력한다.
- 넣을 위치:

| 실행 방식 | 위치 |
|---|---|
| live | RFP Task 시작 전, 각 Task callback(완료), 2·3 병렬 시작, Agent 4 시작 |
| offline | 기존 `[규칙 기반]` 줄 앞뒤 |
| 공통 | 품질 게이트·공고문 게이트에서 멈출 때 `agent2/3/4 skip` |

- **offline 시연:** offline은 몇 초 만에 끝나서 단계가 한꺼번에 바뀐다. 화면은 받은 단계 이벤트를 노드마다 최소 0.6초씩 보여주며 차례로 바꾼다. 화면 쪽 연출이고 결과에는 영향이 없다.
- **화면 처리:** `[STAGE]` 줄은 로그 패널에 보이지 않고, 진행 추적 막대만 갱신한다.
- **회귀 기준:** offline 회귀의 "결과 동일"은 결과 파일(보고서·debug JSON·manifest) 기준이다. stdout은 비교 대상이 아니다.
- **오류 처리:** 프로세스 종료 코드가 0이 아니거나 `[실패]` 줄이 나오면 `error` 이벤트를 보낸다. 그때 실행 중이던 노드는 `fail`로 바꾼다.

### CLI로 만든 기존 실행 다루기 (Claude의 결정)

- 실행 하나 = 보고서 파일 하나(`입찰검토보고서_*.json`)다. 같은 폴더에 여러 날짜의 보고서가 있으면 각각 목록에 나온다.
- `debug/`와 manifest는 매번 덮어써진다. 그래서 상세 탭(RFP 분석·역량 매칭·시장 조사)과 체크리스트 자동 판정은 그 폴더에서 **가장 최근 보고서에만** 붙인다.
  - 옛 보고서는 요약 탭만 보여주고, "상세 산출물은 이후 실행으로 덮어써졌습니다"라고 안내한다.
- `run_id`는 `outputs/` 기준 보고서 상대경로를 base64url로 인코딩한 값이다. 한글 경로를 URL에 안전하게 넣기 위해서다.
  - 웹으로 실행한 것도 같은 규칙을 쓴다. 웹 실행 폴더 이름은 `outputs/web/YYYYMMDD_HHMMSS_<6hex>`다.
- 결정과 체크리스트 수정값은 보고서 옆에 `<보고서 이름>.decision.json`, `<보고서 이름>.checklist.json`으로 저장한다. CLI 폴더(`outputs/offline/...`)에 써도 된다(`outputs/`는 git 제외 대상).
  - 이 이름 규칙이 3.4의 `checklist_overrides.json`과 3.5의 `decision.json`보다 우선한다.
- 보고서 목록을 찾는 범위는 `outputs/` 아래 모든 `입찰검토보고서_*.json`이다.

### 근거 페이지 링크 (Claude의 결정)

- **웹 실행:** 실행 폴더의 `rfp.pdf`로 연결한다.
- **CLI 실행:** manifest·보고서의 `source_document` 경로가 `data/input/` 아래 PDF이면 그 파일을 연다.
  - 파일 API는 `outputs/` 아래와 `data/input/` 아래 `.pdf`만 허용한다.
  - 원본을 찾을 수 없으면 링크를 숨기고 쪽 번호만 글자로 보여준다.

### 3.7 입력 검사와 보안

- 업로드는 `.pdf` 확장자이면서 파일 앞부분이 `%PDF-`인 것만 받는다. 크기는 50MB 이하다.
- 저장 이름은 `rfp.pdf` / `notice.pdf`로 고정해 경로 조작을 막는다. 원래 파일명은 메타데이터로만 보관한다.
- `run_id`는 서버가 만든다(규칙은 3.6 "CLI로 만든 기존 실행 다루기"). 디코딩한 경로와 파일 경로 API의 경로는 `resolve()` 뒤 `outputs/`(또는 PDF는 `data/input/`) 아래인지 확인한다.
- 하위 프로세스에는 인자 리스트로 넘긴다(`shell=False`). 환경 변수는 서버 환경을 그대로 상속하고, 키를 인자로 넘기지 않는다.
- 로그를 SSE로 보내기 전에, `.env`에 있는 키 값과 일치하는 문자열을 `***`로 바꾼다(방어적 처리).

## 4. 남겨 둔 것 (구현자가 정함)

- 컴포넌트 파일 나누기, 상태 관리 방식(React 기본 상태로 충분하다고 봄), 스타일 작성 방식(Montage가 Emotion 기반).
- 탭 안 표의 세부 열 구성과 긴 텍스트 접기 방식.
- 시연용으로 PDF 3개 중 하나를 고르는 "예시로 시작" 버튼을 둘지 여부. 둔다면 `data/input`에서 고른다.
- `outputs/` 목록을 매번 스캔할지 간단히 캐시할지.

## 5. 확인 방법

- **백엔드 테스트(pytest 없이 assert 스크립트, 기존 `tests/` 방식):**
  - 체크리스트 평가 규칙(경계값 40억·100억, null 처리, K `미충족` 우선, K1·K4의 02 매칭 집계, S5의 직전 차수 참여업체 수)
  - `[STAGE]` 줄 파싱과 offline·live 양쪽에서 4개 노드가 모두 start→done(또는 skip)을 받는지 (offline은 실제 실행으로 확인)
  - 보고서 경로 ↔ `run_id` 왕복과 경로 조작(`..`) 거부
  - 업로드 검사(PDF 아닌 파일 거부, 경로 조작 거부)
  - 로그 키 마스킹
- **수동 시연 점검:**
  - offline으로 PDF 3개를 각각 웹에서 끝까지 진행한다(업로드 → 결과 → 체크리스트 수정 → 결정 저장 → 다운로드).
  - CLI로 만든 기존 `outputs/offline` 결과가 목록에 나오는지 본다.
  - live는 키가 있을 때 1건만 돌린다(비용).
- 기존 `tests/test_config.py`, `tests/test_g2b.py`, offline 회귀(결과 동일)가 계속 통과해야 한다.
