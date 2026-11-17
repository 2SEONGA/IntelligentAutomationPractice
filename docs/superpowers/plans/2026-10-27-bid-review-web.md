# 공공 SI 입찰 검토 웹페이지 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 프리세일즈 담당자가 브라우저 한 화면에서 RFP PDF 업로드 → Agent 실행(진행 표시) → 결과·체크리스트 판정 → 사업본부장 결정 기록 → 보고서 다운로드까지 끝내는 로컬 웹앱을 만든다.

**Architecture:**
- **백엔드:** FastAPI가 기존 CLI `run.py`를 하위 프로세스로 실행하고, stdout 줄을 SSE로 흘린다. `crew.py`에 추가하는 `[STAGE]` 줄로 진행 노드를 갱신한다.
- **결과 읽기:** 결과는 `outputs/` 아래 보고서 파일 단위로 읽는다. 체크리스트 평가는 순수 함수 모듈이 맡는다. 결정·수정값은 보고서 옆 JSON에 저장한다.
- **프론트엔드:** React + Vite + Montage 단일 화면. 빌드 결과를 FastAPI가 서빙한다.

**Tech Stack:**
- Python 3.12, FastAPI 0.143.0, uvicorn 0.54.0(설치됨), python-multipart 0.0.32(설치됨), httpx 0.28.1(설치됨, 테스트용)
- React 18, Vite, TypeScript, `@montage-ui/core`·`@montage-ui/icon` **4.1.3**(둘 다 같은 버전), Pretendard v1.3.9

**Spec:** `docs/superpowers/specs/2026-10-27-bid-review-web-design.md` (구현자는 이 계획과 함께 반드시 읽는다. 화면 문구·배치·반목표는 스펙이 기준이다.)

## Global Constraints

- **서버 주소:** `127.0.0.1`에만 바인딩한다.
- **키 보호:** 키 값(`OPENAI_API_KEY`, `G2B_SERVICE_KEY`)은 응답·SSE·로그·저장 파일 어디에도 내보내지 않는다. 로그 줄은 `.env`의 키 값이 있으면 `***`로 바꾼다.
- **데이터 원칙:** 근거 없는 값을 채우지 않는다. 자동 판정 근거가 없으면 K는 `"확인 필요"`, S는 `None`이다.
- **권고 표시:** AI 권고와 사람의 결정은 항상 다른 칸에 이름을 붙여 표시한다. 보고서 JSON의 `final_decision`은 수정하지 않는다.
- **동시 실행:** 한 번에 실행 1개만 허용한다. 실행 중에 새 실행을 요청하면 409를 돌려준다.
- **업로드 검사:** `.pdf` 확장자 + 내용 앞 5바이트 `%PDF-` + 50MB 이하. 저장 이름은 `rfp.pdf`, `notice.pdf`로 고정한다.
- **나라장터 스위치:** `market_api=true`는 `mode=live`이면서 공고문이 있을 때만 받는다. 아니면 400을 돌려준다.
- **고정 버전:** npm `@montage-ui/core@4.1.3`, `@montage-ui/icon@4.1.3`(정확한 버전, `^` 없이). pip `fastapi==0.143.0`.
- **기존 검사 유지:** 기존 검사가 계속 통과해야 한다. `tests/test_config.py`, `tests/test_g2b.py`, 그리고 offline 회귀(결과 파일 기준, stdout 제외)다.
- **테스트 방식:** 기존 `tests/` 관례대로 pytest 없이 `if __name__ == "__main__":` assert 스크립트로 쓴다. 실행은 `.venv/bin/python tests/<file>.py`다.
- **언어:** UI 문구는 한국어다. 상태값 문자열은 기존 그대로 쓴다(`충족`, `부분 충족`, `미충족`, `확인 필요`, `보류`).

## Review Focus

1. **실행 중 새로고침·탭 재접속:**
   - 기대: SSE에 다시 접속하면 지금까지의 로그와 노드 상태를 먼저 받고 이어서 받는다. 진행 중인 실행은 목록 "진행 중"에 남는다.
   - 테스트: Task 4 `test_replay_after_reconnect`
2. **스캔본·내용 부족 PDF:**
   - 기대: 품질 게이트에서 멈추면 Agent 2~4가 `skip`으로 표시되고, 화면이 "실행 중"에 멈춰 있지 않다.
   - 테스트: Task 1 `test_gate_hold_emits_skip`
3. **live 도중 실패(키 오류·한도 초과):**
   - 기대: `error` 이벤트가 오고, 실행 중이던 노드가 `fail`로 바뀌고, "다시 시도"가 뜬다. 그다음 새 실행이 가능하다(실행 잠금이 풀림).
   - 테스트: Task 4 `test_failure_releases_lock`
4. **한글·긴 보고서 경로:**
   - 기대: `run_id` 왕복이 정확하고, `..`·절대경로는 거부된다.
   - 테스트: Task 3 `test_run_id_roundtrip_and_traversal`
5. **로그에 키가 섞여 출력:**
   - 기대: `***`로 가려진다.
   - 테스트: Task 4 `test_mask_secrets`

---

### Task 1: `crew.py`에 `[STAGE]` 진행 표시 줄 추가

**Files:**
- Modify: `src/bid_review/crew.py` (`_task_callback`, `run`)
- Test: `tests/test_stages.py`

**Interfaces:**
- Produces:
  - stdout 줄 형식 `[STAGE] <node> <state>`. `node ∈ {agent1, agent2, agent3, agent4}`, `state ∈ {start, done, skip}`.
  - 이 줄은 `--verbose`와 상관없이 항상 출력하고, 즉시 flush한다.
  - 모듈 함수 `_stage(node: str, state: str) -> None`.

**출력 위치 (`run`)**

| 시점 | 출력 |
|---|---|
| PDF 추출 직전(메서드 시작부) | `agent1 start` |
| 품질 게이트 보류(`write_quality_gate_hold` 반환 전) | `agent1 done`, 그리고 `agent2/3/4 skip` |
| 공고문 게이트(`[문서 입력 확인]` 분기) | 위와 같음 |
| offline | `[규칙 기반] 구조화 결과` 직후 `agent1 done`, 그다음 역량 매칭 앞뒤 `agent2 start/done`, 시장 근거 앞뒤 `agent3 start/done`, 보고서 작성 앞뒤 `agent4 start/done` |

**출력 위치 (live, `_task_callback`)**
- 이 줄들은 `if not self._live_log_enabled: return` **앞에** 둔다.
- step 1 완료: `agent1 done`, `agent2 start`, `agent3 start`
- step 2·3 완료: 각각 `agent2 done`, `agent3 done`. 둘 다 끝난 시점에 `agent4 start`를 한 번 출력한다(완료 집합으로 추적하고, kickoff마다 초기화).
- step 4 완료: `agent4 done`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_stages.py`

```python
def stages(stdout: str) -> list[str]:
    return [l.removeprefix("[STAGE] ") for l in stdout.splitlines() if l.startswith("[STAGE] ")]

def test_offline_emits_all_stages_in_order():
    out = run_cli("--mode", "offline", "--rfp", "data/input/통합정보시스템 고도화 구축 용역.pdf")  # --verbose 없이
    assert stages(out) == ["agent1 start", "agent1 done", "agent2 start", "agent2 done",
                           "agent3 start", "agent3 done", "agent4 start", "agent4 done"]

def test_live_callbacks_emit_stages_without_verbose():
    crew = BidReviewCrew(verbose=False)
    # 각 step 콜백을 pydantic=None, raw="{}"인 가짜 output으로 호출한다 (검증 실패 줄은 무시).
    # 호출 순서: 1, 3, 2, 4
    assert stages(captured) == ["agent1 done", "agent2 start", "agent3 start", "agent3 done",
                                "agent2 done", "agent4 start", "agent4 done"]

def test_gate_hold_emits_skip():
    # 텍스트 없는 1쪽짜리 PDF(tempfile, pypdf PdfWriter.add_blank_page)로 offline 실행
    assert stages(out)[-3:] == ["agent2 skip", "agent3 skip", "agent4 skip"]
```

빈 PDF가 품질 게이트 보류(`write_quality_gate_hold`)가 아니라 예외로 끝나면, 실제로 그 분기를 타는 입력을 찾아 쓴다. 이 테스트가 지키려는 것은 "게이트에서 멈추면 2~4가 skip으로 끝난다"이다. 예외로 끝나는 경우는 Task 4의 `error`·`fail` 처리가 맡는다.

`run_cli(*args)`는 `.venv` python `run.py`를 `--output <tempdir>`로 subprocess 실행해 stdout을 반환하는 테스트 내부 헬퍼다.

- [ ] **Step 2: 실행해서 실패 확인** — `.venv/bin/python tests/test_stages.py` → `[STAGE]` 줄이 없어 AssertionError
- [ ] **Step 3: `_stage`와 출력 줄 구현** — 위 표 위치에 넣는다. `print(f"[STAGE] {node} {state}", flush=True)`
- [ ] **Step 4: 통과 확인** — `.venv/bin/python tests/test_stages.py` → 통과. 이어서 `tests/test_config.py`, `tests/test_g2b.py`도 통과
- [ ] **Step 5: offline 회귀** — 변경 전 `outputs/offline`을 백업하고 다시 실행한 뒤, 결과 JSON·MD를 타임스탬프 정규화 후 비교 → 차이 0개
  - 비교 대상에서 보고서 파일은 `입찰검토보고서_*`로 매칭한다. manifest와 섞지 않는다.

---

### Task 2: 체크리스트 평가 모듈

**Files:**
- Create: `src/bid_review/checklist.py`
- Modify: `config/checklist.yaml` — 자동 판정용 키를 추가한다(아래)
- Test: `tests/test_checklist.py`

**Interfaces:**
- Consumes:
  - `config/checklist.yaml`(`load_config("checklist")`)
  - `data/market/itcen_group_awards_3y.json`의 `awards`
  - 보고서 JSON, `01_rfp_analysis.json`, `02_capability_matching.json`(모두 dict)
- Produces:

```python
Item = TypedDict("Item", {"id": str, "title": str, "kind": Literal["knockout", "scoring"],
                          "ai": str | int | None, "value": str | int | None, "overridden": bool,
                          "evidence": str, "source_page": int | None})

def evaluate(report: dict, rfp: dict | None, capability: dict | None,
             checklist: dict, awards: list[dict]) -> list[Item]: ...
def apply_overrides(items: list[Item], overrides: dict[str, str | int | None]) -> list[Item]: ...
def summarize(items: list[Item], checklist: dict) -> dict:
    # {"knockout_result": "통과"|"보류"|"No-Go", "knockout_passed": int, "score": int,
    #  "undetermined": int, "recommendation": "Go 권고"|"사업본부장 검토"|"No-Go 권고"|"보류"}
```

**`config/checklist.yaml`에 추가할 키**
- `S2`에 `keywords: {2: [유지관리, 유지보수, 운영, 정보자원, 인프라, 클라우드, 재해복구, DR, 차세대, 재구축, 통합구축], 1: [구축, 고도화, 개발, ISP, 컨설팅]}`
- `S3`에 `core_agencies: [한국교육학술정보원, 한국지역정보개발원, 국민건강보험공단, 국가정보자원관리원, 한국지능정보사회진흥원, 국세청, 인사혁신처]`
- 기관 비교는 부분 문자열 포함으로 한다. 예: `행정안전부 국가정보자원관리원 광주센터` ⊃ `국가정보자원관리원`.

**판정 규칙** (스펙 3.4 표가 기준)
- **K1:** `capability.eligibility_matches[].status`를 집계한다. `미충족`이 하나라도 있으면 미충족, 전부 `충족`이면 충족, 그 밖과 빈 목록은 `확인 필요`.
- **K4:** `staffing_matches`에 같은 규칙.
- **K2·K3:** 항상 `확인 필요`.
- **S1:** `report.project_scale.amount_krw`. ≥100억 → 2, ≥40억 → 1, 그 미만 → 0.
- **S2:** `rfp.project_summary`의 사업명과 범위로 판정한다. 2점 키워드가 있으면 2, 1점 키워드가 있으면 1, 없으면 None.
- **S3:** 핵심 기관이면 2, awards의 `dminsttNm`에 기관이 있으면 1, 없으면 0.
- **S4:** 같은 기관 awards 중 사업명 정규화 유사도 > 0.6이 있으면 2, 아니면 0. 정규화는 숫자·연도·기호·`단계/사업/용역`을 지운 뒤 `difflib.SequenceMatcher`로 비교한다.
- **S5:** S4에서 찾은 가장 최근 직전 차수의 `prtcptCnum`. 1 → 2, 2 → 1, 3 이상 → 0. 없으면 None.
- **발주기관:** `rfp.basic_info.procuring_entity.value`를 쓰고, 없으면 `rfp.project_summary.buyer`. 둘 다 없으면 S3·S4·S5는 None.
- **summarize:** `미충족` > `확인 필요` > 통과 순으로 판정한다. 통과일 때만 점수 구간(`decision.go`, `decision.review`)으로 권고한다. None은 점수에 넣지 않고 `undetermined`로 센다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_checklist.py`

```python
def test_amount_boundaries():   # S1: 39.9억→0, 40억→1, 99.9억→1, 100억→2, 금액 없음→None
def test_knockout_aggregation():  # K1: [충족,충족]→충족, [충족,부분 충족]→확인 필요, [충족,미충족]→미충족, []→확인 필요
def test_agency_history():
    # buyer="행정안전부 국가정보자원관리원" → S3=2
    # buyer="한국인터넷진흥원" + awards에 같은 기관 2건 → S3=1
    # S4: 과거 "2025년 자치단체 공통기반 및 재해복구시스템 유지관리"
    #     현재 "2027년 자치단체 공통기반 및 재해복구시스템 유지관리" → S4=2, S5=과거 prtcptCnum 기반
    # buyer 없음 → S3·S4·S5 None
def test_summarize_priority():
    # K 하나 미충족 → No-Go 권고 / K 확인 필요 → 보류
    # 전부 충족 + 점수 6 → Go 권고, 5 → 사업본부장 검토, 3 → No-Go 권고, None 2개 → undetermined=2
def test_overrides():           # K2를 "충족"으로 고치면 value만 바뀌고 ai는 유지, overridden=True. 판정이 다시 계산됨
def test_real_cdw_output():     # outputs/offline/002_* 최신 결과로 evaluate → S1=0, K2="확인 필요", summarize=보류
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python tests/test_checklist.py` → ImportError
- [ ] **Step 3: `checklist.py` 구현과 yaml 키 추가**
- [ ] **Step 4: 통과 확인** — `test_checklist.py`, `test_config.py` 통과

---

### Task 3: 실행 목록·저장소 (`runs.py`)

**Files:**
- Create: `src/bid_review/web/__init__.py`, `src/bid_review/web/runs.py`
- Test: `tests/test_runs.py`

**Interfaces:**
- Consumes: Task 2 `evaluate`, `apply_overrides`, `summarize`
- Produces:

```python
OUTPUTS: Path  # PROJECT_ROOT / "outputs"
def encode_run_id(report_path: Path) -> str          # outputs 기준 상대경로 → base64url(패딩 제거)
def decode_run_id(run_id: str) -> Path               # 결과가 outputs 아래가 아니거나 보고서 이름 규칙이 아니면 ValueError
def list_runs() -> list[dict]
    # 최신순. 각 항목: {"id", "project_name", "created_at"(ISO), "mode"("offline"|"live"|"web"),
    #  "ai_recommendation", "decision"|None}
def load_run(run_id: str) -> dict
    # {"id", "report", "rfp"|None, "capability"|None, "market"|None, "details_available": bool,
    #  "checklist": {"items": [...], "summary": {...}}, "decision": dict|None,
    #  "files": {"report_md", "report_json", "pdf"|None}}
def save_overrides(run_id: str, overrides: dict) -> dict      # <보고서>.checklist.json 저장 후 load_run의 checklist 반환
def save_decision(run_id: str, decision: str, reason: str) -> dict
    # decision ∈ {"Go","보류","No-Go"}, reason.strip() 필수. 아니면 ValueError
    # 이전 값은 decision_history로 보존
def resolve_file(run_id: str, name: str) -> Path
    # name ∈ {"report.md", "report.json", "rfp.pdf"}만 허용
```

**규칙**
- **목록 범위:** `outputs/**/입찰검토보고서_*.json`.
- **`mode`:** 경로의 첫 폴더(`offline`/`live`/`web`)로 정한다. 그 밖이면 manifest의 `mode`를 쓴다.
- **`created_at`:** 파일명의 `YYYY-MM-DD_HHMMSS`(KST)에서 뽑는다.
- **`details_available`:** 같은 폴더에서 가장 최근 보고서일 때만 True다. 그때만 debug 01~03을 읽고 체크리스트를 평가한다. 아니면 rfp/capability/market은 None이고, 체크리스트는 report만으로 평가한다.
- **`rfp.pdf`:**
  - 웹 실행이면 폴더의 `rfp.pdf`.
  - 아니면 `rfp.project_summary.evidence[0].document` 파일명으로 `data/input/`에서 찾는다.
  - 둘 다 없으면 None. 해석한 경로는 `outputs/` 또는 `data/input/` 아래인지 확인한다.
- **`decision.json` 형식:** 스펙 3.5. `decided_at`은 KST ISO8601이고 `checklist_snapshot`을 포함한다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_runs.py`. 임시 `OUTPUTS`를 쓰고(monkeypatch로 모듈 변수 교체), 실제 산출물 파일을 복사해 만든다.

```python
def test_run_id_roundtrip_and_traversal():
    # 한글 경로 보고서 → encode → decode가 같은 경로
    # base64url("../../etc/x.json"), 절대경로, 보고서 이름 규칙이 아닌 파일 → ValueError
def test_list_runs_latest_only_has_details():
    # 한 폴더에 보고서 2개 → 목록 2건 최신순, 오래된 것은 load_run 결과 details_available False
def test_decision_save_and_history():
    # 저장 2번 → decision.json의 decision은 두 번째 값, decision_history 길이 1
    # reason="" → ValueError, decision="Maybe" → ValueError
def test_overrides_persist():
    # save_overrides({"K2": "충족"}) 뒤 load_run → K2 value=충족, ai=확인 필요
def test_pdf_resolution():
    # 웹 폴더 rfp.pdf 우선, CLI는 data/input 파일명 매칭, 없으면 None
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python tests/test_runs.py`
- [ ] **Step 3: `runs.py` 구현**
- [ ] **Step 4: 통과 확인**

---

### Task 4: 실행 작업 관리자 (`jobs.py`)

**Files:**
- Create: `src/bid_review/web/jobs.py`
- Test: `tests/test_jobs.py`

**Interfaces:**
- Produces:

```python
class JobBusy(Exception): ...

@dataclass
class Job:
    id: str; output_dir: Path; events: list[dict]
    status: Literal["running", "done", "error"]; report_run_id: str | None

class JobManager:
    def __init__(self, command_builder: Callable[[Path, dict], list[str]], secrets: list[str]): ...
    def start(self, output_dir: Path, options: dict) -> Job        # 실행 중이면 JobBusy
    def current(self) -> Job | None
    def get(self, job_id: str) -> Job | None
    async def stream(self, job_id: str) -> AsyncIterator[dict]     # 쌓인 events 먼저, 이어서 새 이벤트, done/error에서 끝

def build_command(output_dir: Path, options: dict) -> list[str]
    # [sys.executable, "run.py", "--mode", m, "--rfp", str(output_dir/"rfp.pdf"),
    #  ("--notice-pdf", …), ("--allow-market-api"), "--output", str(output_dir),
    #  "--save-intermediate", "--verbose"]
def parse_line(line: str) -> dict
    # "[STAGE] agent2 start" → {"type": "stage", "node": "agent2", "state": "start"}
    # 그 밖 → {"type": "log", "text": line}
def mask(line: str, secrets: list[str]) -> str     # 길이 8 이상인 secret만 치환
```

**규칙**
- **실행 방식:** `subprocess.Popen(..., cwd=PROJECT_ROOT, stdout=PIPE, stderr=STDOUT, text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})`로 띄우고, 백그라운드 스레드에서 줄 단위로 읽는다.
- **이벤트 기록:** 모든 줄은 `mask` → `parse_line`을 거쳐 `events`에 쌓인다.
- **종료 처리:**
  - 종료 코드 0이고 `outputs` 안에 보고서가 생겼으면 `done` 이벤트를 보낸다. 내용은 `{"type": "done", "run_id": encode_run_id(새 보고서)}`.
  - 그 밖(종료 코드 ≠ 0, `[실패]` 줄, 보고서 없음)이면 `error` 이벤트를 보낸다. 내용은 `{"type": "error", "message": 마지막 로그 몇 줄 요약}`. 이때 `start` 상태로 남은 노드마다 `fail` stage 이벤트를 먼저 보낸다.
- **잠금:** 종료 후 실행 잠금을 푼다.
- **`stream`:** 0.2초 간격으로 새 이벤트를 확인하는 단순 루프로 충분하다(`ponytail:` 주석).

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_jobs.py`. `command_builder`로 가짜 명령(`sys.executable -c "print('[STAGE] agent1 start'); print('key=sk-secret-123456'); …"`)을 주입한다.

```python
def test_parse_and_mask():
    # parse_line("[STAGE] agent1 done") → stage 이벤트, 일반 줄 → log
def test_mask_secrets():
    # mask("key=sk-secret-123456", ["sk-secret-123456"]) == "key=***"; 짧은 secret("abc")은 무시
def test_busy_and_done():
    # 가짜 명령이 tmp output에 '입찰검토보고서_2026-10-27_120000_abcdef.json'을 쓰고 종료
    # 실행 중 start → JobBusy, 끝나면 done 이벤트에 run_id
def test_failure_releases_lock():
    # 가짜 명령 exit 1 → 마지막 이벤트 error, agent1 fail stage 포함, 이어서 start 가능
def test_replay_after_reconnect():
    # 완료된 job을 stream 하면 처음부터 모든 이벤트 재생 후 끝남
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/python tests/test_jobs.py`
- [ ] **Step 3: `jobs.py` 구현**
- [ ] **Step 4: 통과 확인**

---

### Task 5: FastAPI 앱과 실행 진입점

**Files:**
- Create: `src/bid_review/web/app.py`, `run_web.py`
- Modify: `pyproject.toml` — dependencies에 `"fastapi==0.143.0"`, `"uvicorn>=0.54"`, `"python-multipart>=0.0.32"` 추가
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: Task 3 `runs.*`, Task 4 `JobManager`, `build_command`, `JobBusy`
- Produces: `create_app(job_manager: JobManager | None = None) -> FastAPI` 하나와 스펙 3.6의 API 전부.

| 엔드포인트 | 응답 |
|---|---|
| `GET /api/status` | `{"live_available": bool, "market_available": bool, "checklist_version": str, "running_job_id": str\|None}` |
| `POST /api/runs` | `{"job_id"}` 201 / 409 실행 중 / 400 검사 실패(`{"detail": 한국어 메시지}`) |
| `GET /api/jobs/{job_id}/events` | `text/event-stream`. 각 이벤트는 `data: <json>\n\n` |
| `GET /api/runs` | `list_runs()` |
| `GET /api/runs/{run_id}` | `load_run()`. ValueError는 404 |
| `GET /api/runs/{run_id}/files/{name}` | `FileResponse`. PDF는 `inline`, md·json은 `attachment` |
| `PUT /api/runs/{run_id}/checklist` | body `{"overrides": {...}}` |
| `PUT /api/runs/{run_id}/decision` | body `{"decision", "reason"}`. ValueError는 400 |

- **진행 중 job 식별:** 진행 중 job은 `job_id`로, 완료된 결과는 `run_id`로 구분한다. `done` 이벤트의 `run_id`로 결과 화면에 넘어간다.
- **live 가능 여부:** `.env`를 `dotenv_values`로 읽어 키가 비어 있지 않은지만 본다.
- **업로드 저장:** `POST /api/runs`는 `outputs/web/<YYYYMMDD_HHMMSS_6hex>/`를 만들고 `rfp.pdf`, `notice.pdf`(+ `upload.json`: 원래 파일명, 모드)를 저장한다.
- **정적 파일:** `web/dist`가 있으면 `/`에 `StaticFiles(html=True)`로 마운트한다(API 라우트 뒤에).
- **`run_web.py`:** `run.py`처럼 `os.chdir(ROOT)`와 `sys.path`를 설정한다. `web/dist`가 없으면 "먼저 `cd web && npm install && npm run build`"를 안내하고 종료한다. 있으면 `uvicorn.run(create_app(), host="127.0.0.1", port=8000)`을 실행하고, 시작 후 `webbrowser.open`으로 브라우저를 연다.

- [ ] **Step 1: pyproject 수정 후 설치** — `.venv/bin/pip install -e .` → `fastapi 0.143.0` 설치 확인
- [ ] **Step 2: 실패하는 테스트 작성** — `tests/test_app.py`, `fastapi.testclient.TestClient` 사용. Task 4처럼 가짜 명령을 쓰는 JobManager를 주입한다.

```python
def test_status_has_no_secret_values():   # 응답 본문에 .env 키 값 문자열이 없음
def test_upload_validation():
    # .txt → 400 / 확장자만 pdf인 텍스트 → 400 / 50MB 초과 → 400
    # offline + market_api=true → 400 / live + market_api + 공고문 없음 → 400
def test_run_flow():
    # 정상 PDF(tests용 1쪽 PDF) 업로드 → 201 job_id
    # 실행 중 두 번째 → 409
    # events 스트림 끝에 done(run_id), GET /api/runs/{run_id} 200
def test_decision_and_checklist_endpoints():   # 400(사유 없음), 200 후 GET에 반영
def test_files_endpoint_rejects_unknown_names():   # name="../.env" → 404
```

- [ ] **Step 3: 실패 확인** — `.venv/bin/python tests/test_app.py`
- [ ] **Step 4: `app.py`, `run_web.py` 구현**
- [ ] **Step 5: 통과 확인** — `test_app.py`, 그리고 Task 1~4 테스트 모두 재실행해 통과

---

### Task 6: 프론트엔드 골격 — Montage 테마, 상단 바, 왼쪽 목록

**Files:**
- Create: `web/package.json`, `web/vite.config.ts`, `web/tsconfig.json`, `web/index.html`, `web/src/main.tsx`, `web/src/App.tsx`, `web/src/api.ts`, `web/src/types.ts`, `web/src/components/TopBar.tsx`, `web/src/components/RunList.tsx`, `web/public/itcen-logo.svg`
- Modify: `.gitignore` — `web/node_modules/`, `web/dist/` 추가

**Interfaces:**
- Consumes: Task 5 API
- Produces:
  - `web/src/types.ts`: 백엔드 응답 타입 `Status`, `RunSummary`, `RunDetail`, `ChecklistItem`, `ChecklistSummary`, `Decision`, `JobEvent`(필드명은 Task 3·4·5 Interfaces와 같음).
  - `web/src/api.ts`: `getStatus()`, `listRuns()`, `getRun(id)`, `startRun(form: FormData)`, `jobEvents(jobId, onEvent): () => void`(EventSource, 해제 함수 반환), `saveChecklist(id, overrides)`, `saveDecision(id, decision, reason)`, `fileUrl(id, name)`.
  - `App`의 화면 상태: `{view: "new"} | {view: "job", jobId} | {view: "run", runId}`.

**구성**
- **로고:** `web/public/itcen-logo.svg`는 `https://itcen.com/resources/itcen/assets/svg/logos/logo.svg`를 받아 그대로 저장한다.
- **글꼴:** `index.html`에 Pretendard CSS 2개를 넣는다(스펙 3.2의 jsDelivr 주소).
- **진입점:** `main.tsx`에 `ThemeProvider`와 `import "@montage-ui/core/global.css"`.
- **개발 서버:** `vite.config.ts`에서 `/api`를 `http://127.0.0.1:8000`으로 프록시한다.
- **`RunList`:**
  - 맨 위에 "＋ 새 공고 검토" 버튼.
  - "진행 중"(status의 `running_job_id`), 그다음 "검토한 공고".
  - 배지 규칙(스펙 3.1 ③): 결정이 있으면 `"{결정} 결정"`, 없으면 `"AI: {권고}"`. 색은 보류 cautionary / Go positive / No-Go negative / 진행 중 primary.
- **컴포넌트 이름:** Montage 컴포넌트는 설치한 4.1.3의 실제 export를 확인해서 쓴다(`node_modules/@montage-ui/core/dist` 타입 선언). 없는 것만 Montage 토큰으로 직접 만든다.

- [ ] **Step 1: Vite 프로젝트 생성과 의존성 설치**
  - `npm create vite@latest web -- --template react-ts`
  - `cd web && npm install @montage-ui/core@4.1.3 @montage-ui/icon@4.1.3 --save-exact`
- [ ] **Step 2: 골격 구현** — 위 파일들
- [ ] **Step 3: 빌드 확인** — `cd web && npm run build` → 타입 오류 0, `web/dist/index.html` 생성
- [ ] **Step 4: 실제 화면 확인**
  - `.venv/bin/python run_web.py`로 띄운다.
  - 브라우저에서 상단 로고·제목이 보이는지 확인한다.
  - 기존 보고서 9건(offline 폴더 3개 × 날짜 2개 + live 3건)이 최신순으로 "AI: 보류" 배지와 함께 보이는지 확인한다.

---

### Task 7: 새 공고 검토 화면과 실행 진행 화면

**Files:**
- Create: `web/src/screens/NewReview.tsx`, `web/src/screens/JobProgress.tsx`, `web/src/components/StageTracker.tsx`, `web/src/stages.ts`, `web/src/stages.test.ts`

**Interfaces:**
- Consumes: Task 6 `api.startRun`, `api.jobEvents`, `types.JobEvent`, `getStatus`
- Produces:

```ts
type NodeId = "agent1" | "agent2" | "agent3" | "agent4" | "checklist";
type NodeState = "wait" | "run" | "done" | "skip" | "fail";
function reduceStage(state: Record<NodeId, NodeState>, ev: {node: NodeId, state: string}): Record<NodeId, NodeState>
function paceQueue(events: StageEvent[], lastShownAt: number, now: number, minMs = 600): {show: StageEvent[], rest: StageEvent[]}
// 노드 상태 변경 사이에 최소 600ms. 화면 연출만 담당
```

**구성**
- **`NewReview`** (스펙 3.1 ① 문구·규칙 그대로):
  - 제안요청서 끌어다 놓기 + 파일 선택. 선택 후에는 파일 카드(이름·크기, "변경")로 바뀐다.
  - 공고문 선택 입력.
  - 실행 방식 segmented control. `live_available`이 false면 live를 비활성화하고 사유를 표시한다.
  - 나라장터 Switch. live + 공고문 + `market_available`일 때만 켤 수 있다.
  - 고정 정보 한 줄과 "검토 시작".
  - 400·409 응답의 `detail`은 SectionMessage(negative)로 보여준다.
  - 성공하면 `{view: "job"}`으로 바꾼다.
- **`JobProgress`:**
  - `StageTracker`(Agent 1 → 2·3 위아래 병렬 → 4 → 체크리스트)와 로그 패널(어두운 배경, 자동 스크롤)을 보여준다.
  - `agent4 done`을 받으면 체크리스트 노드를 `done`으로 바꾼다.
  - `done` 이벤트가 오면 페이싱 큐가 빈 뒤 `{view: "run", runId}`로 넘어가고 목록을 새로 고친다.
  - `error`가 오면 SectionMessage와 "다시 시도"(→ new 화면, 이전 파일 선택 유지)를 보여준다.
  - 새로고침해도 `running_job_id`로 이 화면에 복귀한다.

- [ ] **Step 1: 실패하는 테스트 작성** — `web/src/stages.test.ts` (Node 24 내장 `node:test`, 타입 스트리핑 사용)

```ts
test("parallel nodes run together", () => { /* agent2 start, agent3 start 후 둘 다 run */ });
test("skip and fail are terminal", () => { /* skip 뒤 start가 와도 skip 유지 */ });
test("pace holds back bursts", () => { /* now-lastShownAt=100 → show [] / 700 → 1개만 show */ });
```

- [ ] **Step 2: 실패 확인** — `cd web && node --test src/stages.test.ts` → 모듈 없음
- [ ] **Step 3: `stages.ts`, 화면 구현**
- [ ] **Step 4: 통과 확인** — `node --test src/stages.test.ts` 통과, `npm run build` 통과
- [ ] **Step 5: 실제 실행 확인**
  - `data/input/통합정보시스템 고도화 구축 용역.pdf`를 offline으로 올린다.
  - 노드가 1 → 2·3 → 4 → 체크리스트 순서로 0.6초 간격으로 바뀌고, 로그가 흐르는지 본다.
  - 결과 화면으로 넘어가는지, 진행 중 새로고침해도 진행 화면으로 돌아오는지 확인한다.

---

### Task 8: 결과 화면 — 요약 카드, 탭, 근거 링크

**Files:**
- Create: `web/src/screens/RunResult.tsx`, `web/src/components/SummaryCards.tsx`, `web/src/components/tabs/{SummaryTab,RfpTab,CapabilityTab,MarketTab}.tsx`, `web/src/components/PageLink.tsx`

**Interfaces:**
- Consumes: Task 6 `getRun`, `fileUrl`, `types.RunDetail`
- Produces: `PageLink({runId, page, pdfAvailable})`. `pdfAvailable`이면 `fileUrl(runId, "rfp.pdf") + "#page=" + page`를 새 탭으로 연다. 아니면 `p.N` 글자만 보여준다.

**구성** (스펙 3.1 ③ 기준)
- **머리:** 사업명, 실행 일시·모드, "보고서 내려받기 (MD·JSON)".
- **카드 4장:**
  1. 보고서 권고 + 아래 작게 체크리스트 권고
  2. 사업금액 + 40억 대비 막대(100% 상한). 금액이 없으면 "원문 확인 필요"
  3. 1단계 `knockout_passed / 4`
  4. 2단계 `score / 10` + 권고 구간 + `undetermined > 0`이면 "n개 미정"
- **권고 불일치:** 두 권고가 다르면 SectionMessage(cautionary) "보고서 권고와 체크리스트 권고가 다릅니다. 근거를 확인하세요"를 띄운다.
- **탭:**
  - `details_available`이 false면 RFP·역량·시장 탭을 비활성화하고 "상세 산출물은 이후 실행으로 덮어써졌습니다"를 안내한다.
  - 표의 열 구성은 구현자가 정한다(스펙 4).
  - `source_page`·`pdf_page`가 있는 값에는 `PageLink`를 붙인다.

- [ ] **Step 1: 구현**
- [ ] **Step 2: 빌드 확인** — `npm run build` 통과
- [ ] **Step 3: 실제 화면 확인**
  - CDW 최신 결과를 연다: 금액 10.7억, 막대 약 27%, 보고서 권고 보류.
  - `p.4 ↗`를 누르면 `data/input`의 원본 PDF 4쪽이 열리는지 본다.
  - 같은 폴더의 옛 보고서는 상세 탭이 비활성화되는지 본다.

---

### Task 9: 체크리스트 탭과 사업본부장 결정

**Files:**
- Create: `web/src/components/tabs/ChecklistTab.tsx`, `web/src/components/DecisionCard.tsx`

**Interfaces:**
- Consumes: Task 6 `saveChecklist`, `saveDecision`, `types.ChecklistItem`, `ChecklistSummary`, `Decision`

**구성**
- **`ChecklistTab`:**
  - 1단계 표: 항목, AI 판정 근거, segmented control(충족·확인 필요·미충족).
  - 2단계 표: 항목, 근거, 0·1·2 선택 + "미정" 표시.
  - 고친 칸은 "수정됨" 배지를 달고, AI 값을 작게 함께 보여준다.
  - K가 `확인 필요`이면 "담당자 확인 필요" 강조를 보여준다.
  - 바꾸면 즉시 `saveChecklist`를 호출하고, 응답의 summary로 카드와 판정을 갱신한다.
- **`DecisionCard`:**
  - Go·보류·No-Go segmented control, 사유 TextArea(필수, 비면 저장 버튼 비활성화), "결정 저장".
  - 저장되면 Toast를 띄우고 목록 배지를 갱신한다.
  - 기존 결정이 있으면 값·시각을 보여주고, 이력 개수를 표시한다.
- **보고서 내려받기:** report.md, report.json을 받는다. 결정과 체크리스트가 있으면 `<보고서>.decision.json`, `<보고서>.checklist.json`도 각각 받을 수 있게 한다(링크 4개 메뉴).

- [ ] **Step 1: 구현**
- [ ] **Step 2: 빌드 확인** — `npm run build` 통과
- [ ] **Step 3: 실제 화면 확인 (CDW 결과)**
  - K2를 충족, K3를 충족으로 바꾼다 → 1단계가 통과로 바뀌고 점수 구간 권고가 나온다.
  - 새로고침해도 유지되는지 본다.
  - 사유 없이는 저장이 안 되는지 본다.
  - "보류" + 사유로 저장 → 목록 배지가 "보류 결정"으로 바뀌는지 본다.
  - 다시 "No-Go"로 저장 → 이력 1건이 표시되는지 본다.

---

### Task 10: 문서와 시연 점검

**Files:**
- Modify: `README.md`, `실행안내.md`, `docs/변경사항_2026-10-27.md`

**문서 내용**
- **`실행안내.md`에 "웹페이지로 실행" 절 추가:**
  - 처음 한 번: `cd web && npm install && npm run build`
  - 실행: `.venv/bin/python run_web.py` (Windows는 `.venv\Scripts\python.exe run_web.py`)
  - 접속 주소: `http://127.0.0.1:8000`
- **`README.md`:** 한 줄 링크.
- **변경사항 문서:** "5. 웹페이지" 절을 추가한다. 구조, 새 파일, `crew.py`의 `[STAGE]` 줄, 결정·체크리스트 저장 위치를 적는다.

- [ ] **Step 1: 문서 갱신**
- [ ] **Step 2: 전체 자동 검사**
  - `.venv/bin/python tests/test_config.py`, `test_g2b.py`, `test_stages.py`, `test_checklist.py`, `test_runs.py`, `test_jobs.py`, `test_app.py`
  - `cd web && node --test src/stages.test.ts && npm run build`
  - 모두 통과해야 한다.
- [ ] **Step 3: 시연 점검 (offline, PDF 3개 각각)**
  - 업로드 → 진행 → 결과 → 체크리스트 수정 → 결정 저장 → 내려받기.
  - 결과 하나라도 실패하면 해당 Task로 돌아간다.
- [ ] **Step 4: live 1건 (키가 있을 때, 비용 발생 — 사용자에게 먼저 묻는다)**
  - CDW PDF를 live로 실행해서, 노드가 실제 Agent 순서대로 갱신되는지 확인한다.
