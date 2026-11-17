# 입찰 검토 웹 v2 (공고 수집 · 대시보드 · 역할별 결재) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** v1 웹앱에 3주차 To-Be 전 과정(① 나라장터 공고 수집 → ② 규칙 필터 → ②-1 담당자 확인 → ③ 첨부 확보 → Agent 검토 → ⑧ 본부장 결재·반려 → ⑨ 이력)과 대시보드, 역할별 권한을 더한다.

**Architecture:**
- **백엔드:** 순수 모듈 3개를 새로 만든다.
  - `notice_filter.py`: 공고 판정
  - `web/dashboard.py`: 집계
  - `web/runs.py`의 결재 상태 전이
- **입출력 모듈:** `web/notices.py`가 수집·저장·첨부 다운로드를 맡는다.
- **API:** `web/app.py`가 위 모듈을 API로 노출하고 `X-Role` 헤더로 권한을 검사한다.
- **프론트엔드:** v1 화면을 "검토" 메뉴 안으로 옮기고, 메뉴 6개짜리 셸과 새 화면 5개를 추가한다.

**Tech Stack:** v1과 같다. FastAPI 0.143.0, React 18.3.1, Vite 8.3.4, TypeScript 5.9.3, `@montage-ui/core`·`icon` 4.1.3. 새 의존성은 없다.

**Spec:** `docs/superpowers/specs/2026-10-27-bid-review-web-v2-design.md` (v2, 바인딩). v1 스펙 `…-bid-review-web-design.md`의 규칙은 v2가 바꾸지 않은 범위에서 유지된다.

## Global Constraints

- **권한:**
  - 역할 값은 `staff`(사업기획 담당자) / `director`(사업본부장)다.
  - 상태를 바꾸는 모든 API는 `X-Role` 헤더를 검사한다. 헤더가 없거나 값이 틀리면 **403**이다(fail closed).
  - 4.1 권한 표가 기준이다.
- **역할 전환은 시연용이다.** 판단 기준 화면과 README에 "보안 인증 아님"을 적는다.
- **공고 키:** `.env`의 `G2B_BID_NOTICE_KEY`를 쓰고, 없으면 `G2B_SERVICE_KEY`. 키 값은 응답·로그·저장 파일에 쓰지 않는다.
- **수집 API:** `https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoServcPPSSrch`, `inqryDiv=1`, `numOfRows=999`. 31일 넘는 기간은 31일 단위로 나눈다.
- **필터 기준:** 금액 하한 **4,000,000,000원**, 마감 임박 **3일**, 분야 `ict_classes: ["ICT 서비스"]`. 모두 `config/notice_filter.yaml` 값이다.
- **판정 우선순위:** `제외 > 확인 필요 > 검토 대상`. 판정할 값이 없으면 "확인 필요"이고, "제외"로 하지 않는다.
- **결재 상태값(한국어 그대로):** `검토 완료`, `결재 대기`, `보완 요청`, `결정 완료`.
  - 저장은 건 폴더의 `workflow.json`이다.
  - 반려 요청 값은 `재실행` / `수기 보완`이다.
- **저장 위치:**
  - 공고: `outputs/notices/notices.json` (임시 파일에 쓴 뒤 교체해서 원자적으로 쓴다)
  - 공고에서 시작한 실행: `outputs/web/<YYYYMMDD_HHMMSS_6hex>/` (v1과 같음)
- **대시보드 숫자는 실제 데이터로만 계산한다.** 데이터가 없으면 0이다.
- **기존 검사 유지:** v1 테스트(백엔드 7개 파일, 프론트 9개), offline 회귀(결과 파일 30개 차이 0)가 계속 통과해야 한다.
- **테스트 방식:** 기존 관례대로 백엔드는 `tests/test_*.py` assert 스크립트, 프론트는 `node --test src/*.test.ts`.
- **live 실행 금지:** 사용자 승인 없이 live 모드를 실행하지 않는다(비용).

## Review Focus

1. **역할 헤더가 없거나 위조된 요청:**
   - 기대: 상태 변경 API가 전부 403이다. 조회 API는 열려 있다.
   - 테스트: Task 4 `test_mutations_require_role`
2. **수집 도중 API 오류·키 없음:**
   - 기대: 사용자에게 이유를 보여주고, 기존 `notices.json`이 깨지거나 비지 않는다.
   - 테스트: Task 2 `test_collect_error_keeps_store`
3. **"결재 올리기"를 두 번 누르거나, 역할 전환 직후 이미 처리된 건을 다시 처리:**
   - 기대: 두 번째 요청은 409와 현재 상태를 돌려준다.
   - 테스트: Task 3 `test_double_transition_conflicts`
4. **첨부가 없음 / HWP만 있음 / PDF 주소가 HTML 오류 페이지를 돌려줌:**
   - 기대: 그 파일은 건너뛰고, RFP가 없으면 업로드 화면(`needs_upload`)으로 간다.
   - 테스트: Task 2 `test_download_skips_non_pdf`
5. **검토를 시작한 공고를 다시 수집:**
   - 기대: 판정은 새로 고쳐지지만 HITL-2 기록과 `case_folder` 연결은 남는다.
   - 테스트: Task 2 `test_recollect_preserves_review_and_case`

---

### Task 1: 공고 규칙 필터 (`notice_filter.py` + YAML)

**Files:**
- Create: `src/bid_review/notice_filter.py`, `config/notice_filter.yaml`
- Test: `tests/test_notice_filter.py`

**Interfaces:**
- Produces:

```python
VERDICTS = ("검토 대상", "확인 필요", "제외")
def judge(notice: dict, rules: dict, now: datetime) -> dict
    # notice: 나라장터 응답 item 그대로. 반환:
    # {"verdict": str, "reasons": [str], "rules": {"amount"|"deadline"|"field"|"contract"|"conditions": {"verdict": str, "reason": str}},
    #  "amount_krw": int | None, "deadline": str | None}
def amount_of(notice: dict) -> int | None     # asignBdgtAmt, 없으면 presmptPrce (빈 문자열·0은 없음)
```

**`config/notice_filter.yaml` 키**
- `min_amount_krw: 4000000000`
- `deadline_warn_days: 3`
- `ict_classes: ["ICT 서비스"]`
- `field_keywords` (기본값은 수집 키워드와 같음)
- `collect_keywords: ["정보시스템", "차세대", "클라우드", "인공지능", "AI"]`
- `default_days: 30`
- `exclude_contract_terms: ["수의계약"]`
- `confirm_contract_terms: ["제한경쟁"]`
- 지역제한·업종제한 판정 필드와 값: Step 1에서 정한다.

**규칙** (스펙 4.3·4.8 표)
- **금액:** ≥ min → 검토 대상, < min → 제외, 없음 → 확인 필요.
- **마감:** `bidClseDt`가 `now`보다 이전이면 제외, 비었거나 `deadline_warn_days` 이내면 확인 필요.
- **분야:** `pubPrcrmntLrgClsfcNm ∈ ict_classes`면 통과. 분류가 비어 있으면 공고명에 `field_keywords`가 있는지 본다. 둘 다 아니면 제외.
- **계약:** `cntrctCnclsMthdNm + sucsfbidMthdNm`에 `exclude_contract_terms`가 있으면 제외.
- **조건:** `confirm_contract_terms`, 업종제한, 지역제한이 하나라도 있으면 확인 필요.

- [ ] **Step 1: 실제 응답으로 지역·업종 제한 필드 정하기**
  - 30일·"정보시스템"으로 실제 API를 한 번 호출한다.
  - `rgnLmtBidLocplcJdgmBssNm`, `jntcontrctDutyRgnNm1~3`, `indstrytyLmtYn`, `prdctClsfcLmtYn`, `bidPrtcptLmtYn` 값의 분포를 출력한다.
  - "제한 있음"을 나타내는 필드·값을 정해 YAML 주석에 근거(건수)와 함께 적는다.
  - 원장에 Ruling으로 남긴다.
- [ ] **Step 2: 실패하는 테스트 작성** — `tests/test_notice_filter.py`

```python
def test_amount_boundaries():   # 39.9억→제외, 40억→검토 대상, asignBdgtAmt "" + presmptPrce 50억→검토 대상, 둘 다 없음→확인 필요
def test_deadline():            # now 기준 어제→제외, 2일 뒤→확인 필요, 10일 뒤→검토 대상, ""→확인 필요
def test_field():               # "ICT 서비스"→통과 / 분류 "건설"+키워드 이름→제외 / 분류 ""+이름 "차세대…"→통과
def test_contract_and_conditions():  # "수의계약"→제외 / "제한경쟁"→확인 필요 / Step 1에서 정한 지역제한 값→확인 필요
def test_priority_and_reasons():
    # 금액 미달+조건 확인 필요 → 최종 "제외", reasons에 두 사유 모두
    # 사유 문구 예: "40억 미만(0.4억)"
```

  - 테스트용 공고는 실제 응답 필드 이름을 쓴 최소 dict로 만든다.
- [ ] **Step 3: 실패 확인** — `.venv/bin/python tests/test_notice_filter.py` → ImportError
- [ ] **Step 4: 구현**
- [ ] **Step 5: 통과 확인** — `test_notice_filter.py`, `test_config.py` 통과

---

### Task 2: 공고 수집·저장·첨부 확보 (`web/notices.py`)

**Files:**
- Create: `src/bid_review/web/notices.py`
- Test: `tests/test_notices.py`

**Interfaces:**
- Consumes: Task 1 `judge`, `amount_of`
- Produces:

```python
STORE: Path   # runs.OUTPUTS / "notices/notices.json" (호출 시점에 계산, 테스트가 runs.OUTPUTS를 바꿀 수 있게)
class NoticeError(Exception): ...            # 사용자에게 보일 한국어 메시지
def notice_id(item: dict) -> str             # f"{bidNtceNo}-{bidNtceOrd}"
def collect(days: int, keywords: list[str], session=None, now=None) -> dict
    # 키워드별·31일 구간별 호출 → 중복 합치기 → judge → 저장소에 병합 저장
    # 반환: {"collected": n, "total": m, "by_verdict": {...}, "at": iso}
def load_notices() -> dict
    # {"runs": [{"at","days","keywords","count"}], "notices": {id: Notice}}
def set_review(nid: str, include: bool, reason: str, role: str) -> dict
    # HITL-2: reason.strip() 필수. verdict를 "검토 대상"/"제외"로 덮고 review 기록
def attach_case(nid: str, case_folder: str) -> None
def fetch_attachments(nid: str, folder: Path, session=None) -> dict
    # PDF만 받아 notice.pdf / rfp.pdf로 저장 → {"rfp": bool, "notice": bool, "skipped": [이름]}
def classify(names: list[str]) -> dict[str, str]   # 파일명 → "notice" | "rfp" | "pdf" | "other"
```

**Notice 저장 필드**
- 원본 일부: `bidNtceNo`, `bidNtceOrd`, `bidNtceNm`, `ntceInsttNm`, `dminsttNm`, `asignBdgtAmt`, `presmptPrce`, `bidClseDt`, `cntrctCnclsMthdNm`, `pubPrcrmntLrgClsfcNm`, `bidNtceDtlUrl`, 첨부 이름·주소 목록
- 판정 결과 `judge(...)` 전체
- `review`(없으면 None), `case_folder`(없으면 None)

**규칙**
- 병합할 때 `review`와 `case_folder`는 유지한다. `review`가 있으면 최종 `verdict`는 review 값이 우선한다.
- API 오류 응답(`ResponseError` 봉투, 403, `resultCode≠00`)이면 `NoticeError`를 던지고, 저장 파일은 건드리지 않는다.
- 저장은 임시 파일에 쓴 뒤 `os.replace`로 교체한다.
- 키는 `G2B_BID_NOTICE_KEY`, 없으면 `G2B_SERVICE_KEY`. 둘 다 없으면 `NoticeError("…키가 없어요")`.
- **classify:** 이름(NFC)에 `공고문`/`입찰공고`가 있으면 notice. `.pdf`이면서 `제안요청`/`과업`/`RFP`가 있으면 rfp. 그 밖의 `.pdf`는 pdf, 나머지는 other.
- **RFP 선택:** rfp가 없으면 첫 번째 pdf를 RFP로 쓴다.
- **다운로드 검사:** 받은 내용이 `%PDF-`로 시작하지 않으면 건너뛰고 `skipped`에 넣는다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_notices.py`. 가짜 세션을 쓴다(v1 `test_g2b.py`의 FakeSession 방식, 31일 넘는 구간은 07 오류).

```python
def test_collect_merges_and_judges():   # 키워드 2개에 같은 공고 → 1건, by_verdict 합계 = total
def test_recollect_preserves_review_and_case():
    # set_review(include=True)·attach_case 후 다시 collect → review·case_folder 유지, verdict "검토 대상"
def test_collect_error_keeps_store():   # 기존 저장 후 오류 응답 → NoticeError, 파일 내용 동일
def test_review_requires_reason():      # reason "  " → ValueError
def test_classify():
    # "(공고문)x.pdf"→notice, "제안요청서.pdf"→rfp, "과업내용서.hwpx"→other, "붙임1.pdf"→pdf
def test_download_skips_non_pdf():
    # 첨부: 공고문 pdf(정상), 제안요청서 pdf(HTML 응답), hwpx
    # → notice True, rfp False, skipped에 제안요청서
```

- [ ] **Step 2: 실패 확인** — ImportError
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인**
- [ ] **Step 5: 실제 API 1회** — 키로 `collect(30, 기본 키워드)`를 실행해 건수와 판정 분포를 출력하고, 원장에 기록한다. 실제 결과는 `outputs/notices/`에 남는다.

---

### Task 3: 검토 건·결재 상태 (`web/runs.py` 확장)

**Files:**
- Modify: `src/bid_review/web/runs.py`
- Test: `tests/test_workflow.py`

**Interfaces:**
- Consumes: v1 `runs` 함수들, Task 2 `load_notices`
- Produces:

```python
STATUSES = ("검토 완료", "결재 대기", "보완 요청", "결정 완료")
class Conflict(Exception): ...           # 현재 상태를 담은 409용
def case_of(run_id: str) -> Path        # 보고서가 든 폴더
def current_report(case: Path) -> Path  # 폴더의 가장 최근 보고서
def workflow(case: Path) -> dict        # {"status", "history": [...]}, 파일 없으면 {"status": "검토 완료", "history": []}
def transition(run_id: str, action: str, role: str, reason: str = "", request: str = "") -> dict
    # action: submit(staff, 검토 완료→결재 대기) | resubmit(staff, 보완 요청→결재 대기)
    #         | reject(director, 결재 대기→보완 요청, reason 필수, request ∈ {"재실행","수기 보완"})
    #         | rerun(staff, 보완 요청→검토 완료)  ← 다시 실행 시작 시 app이 호출
    # 현재 보고서가 아닌 run_id, 허용 안 된 전이 → Conflict / 역할 불일치 → PermissionError / 입력 오류 → ValueError
def list_cases() -> list[dict]
    # 폴더마다 1건: {"case_id", "run_id"(현재 보고서), "project_name", "mode", "via_web", "notice_id"|None,
    #  "status", "ai_recommendation", "checklist_recommendation", "decision"|None, "rejections": int,
    #  "submitted_at"|None, "decided_at"|None, "amount_krw"|None}
def rerun_inputs(run_id: str) -> dict | None
    # {"rfp_path": str, "notice": bool}. 원본 RFP가 없으면 None
```

**v1 함수 변경**
- `save_decision(run_id, decision, reason)`:
  - 상태가 `결재 대기` 또는 `결정 완료`일 때만 허용한다. 아니면 Conflict.
  - 성공하면 workflow를 `결정 완료`로 바꾸고 history에 남긴다.
  - 역할 검사는 app이 한다.
- `save_overrides`: 상태가 `검토 완료` 또는 `보완 요청`일 때만 허용한다. 아니면 Conflict.
- `load_run` 응답에 `workflow`, `is_current`, `rerun_available`을 추가한다.

**history 항목:** `{"action", "from", "to", "role", "at"(KST), "report"(보고서 이름), "reason"?, "request"?}`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_workflow.py`. v1 `test_runs.py`의 sandbox 방식을 쓴다.

```python
def test_happy_path():          # submit → reject(수기 보완) → resubmit → save_decision → 결정 완료, history 4건
def test_double_transition_conflicts():   # submit 두 번 → 두 번째 Conflict(현재 상태 "결재 대기" 포함)
def test_role_rules():          # director가 submit → PermissionError, staff가 reject → PermissionError
def test_reject_needs_reason_and_request():   # reason "" 또는 request "기타" → ValueError
def test_old_report_cannot_transition():      # 같은 폴더 옛 보고서 run_id → Conflict
def test_locks():
    # 결재 대기 중 save_overrides → Conflict
    # 검토 완료에서 save_decision → Conflict
def test_list_cases_one_per_folder():
    # 보고서 2개 폴더 → 1건, run_id는 최신
    # rejections는 reject 횟수, notice_id는 upload.json에서
def test_rerun_inputs():        # 웹 폴더 → rfp.pdf, notice 여부 / CLI 폴더 → data/input 경로 / 없음 → None
```

- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** — 상태 전이 표는 상수 dict 하나로 둔다.
- [ ] **Step 4: 통과 확인** — `test_workflow.py`와 v1 `test_runs.py` 통과. v1의 결정 테스트는 결재 대기를 먼저 만들도록 바꾸고, 그 변경을 원장에 Ruling으로 남긴다.

---

### Task 4: 권한과 새 API (`web/app.py`, `web/dashboard.py`, `jobs.build_command`)

**Files:**
- Create: `src/bid_review/web/dashboard.py`
- Modify: `src/bid_review/web/app.py`, `src/bid_review/web/jobs.py` (`build_command`가 `options["rfp_path"]`를 받음)
- Test: `tests/test_dashboard.py`, `tests/test_app_v2.py`

**Interfaces:**
- Consumes: Task 1~3 전부
- Produces:
  - `compute_dashboard(notices: dict, cases: list[dict], now: datetime) -> dict` (스펙 3.2·4.7):
    - `cards`: `{collected_recent, collected_total, last_run, passed, confirm, approvals_pending, reviews_total, reviews_linked}`
    - `funnel`: `[{"stage", "count"}]` × 5 (공고에 연결된 건만)
    - `distribution`: `{"Go", "조건부 Go", "보류", "No-Go"}`
    - `agreement`: `{"matched", "total"}`
    - `pending`: 결재 대기 cases
    - `deadlines`: 검토 대상 공고를 마감 순으로 정렬, `d_day` 포함
  - API: 스펙 4.6 표 전체.
    - `GET /api/status`에 `notice_available`, `role_note`를 추가한다.
    - `POST /api/runs`에 선택 필드 `folder_id`를 추가한다. `outputs/web/` 아래 기존 폴더 이름만 허용한다.
  - 역할 의존성: `require_role(*roles)`. `X-Role` 헤더를 읽어 표에 맞지 않으면 `HTTPException(403, "사업본부장만 …")`.

**권한 표** (스펙 4.1)

| 역할 | 허용 동작 |
|---|---|
| staff | collect, notice review, notice start, POST runs, checklist PUT, workflow submit/resubmit, rerun |
| director | decision PUT, workflow reject |
| 모두 | GET 전부 |

**동작 규칙**
- 수집은 동시에 하나만 한다(전역 Lock). 실행 중이면 409.
- `NoticeError`는 502이고 detail에 그 메시지를 담는다.
- **`notices/{id}/start`:**
  1. 폴더를 만든다.
  2. `fetch_attachments`로 첨부를 받는다.
  3. `upload.json`에 `{rfp_name, notice_name, mode, market_api, notice_id}`를 쓴다.
  4. `attach_case`로 공고와 폴더를 잇는다.
  5. rfp가 있으면 `JobManager.start`하고 `{job_id}`를 돌려준다. 없으면 `{needs_upload: true, folder_id, notice_pdf}`를 돌려준다.
- **`rerun`:**
  - `rerun_inputs`가 None이면 409.
  - `transition(rerun)` 후 `JobManager.start(case_folder, {mode, notice, market_api, rfp_path})`.
  - mode는 요청 body에서 받는다(기본 offline). live인데 키가 없으면 400.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `tests/test_dashboard.py`: 고정 notices·cases로 각 수치를 assert한다.
    - 공고 연결 없는 case는 funnel에서 빠지고 distribution에는 들어간다.
    - 수집 전에는 모두 0이다.
  - `tests/test_app_v2.py`: v1 `test_app.py` 방식(가짜 JobManager, 임시 OUTPUTS)을 쓰고, 수집은 `notices`의 session을 가짜로 바꾼다.

```python
def test_mutations_require_role():
    # 헤더 없음/"admin" → 상태 변경 엔드포인트 전부 403, GET은 200
    # director로 collect → 403, staff로 decision → 403
def test_notice_flow_to_job():
    # collect → 목록 → start(PDF 둘 다) → job_id → 끝나면 case에 notice_id
def test_notice_start_needs_upload_then_runs_with_folder():
    # 첨부 HWP뿐 → needs_upload+folder_id → POST /api/runs(folder_id) → 같은 폴더에서 실행
    # folder_id="../x" → 400
def test_workflow_endpoints():   # submit/reject/resubmit/decision 순서, 잘못된 순서 409
def test_rerun_requires_supplement_state_and_inputs():
def test_dashboard_endpoint_numbers():
```

  - v1 `tests/test_app.py`는 상태 변경 요청에 `X-Role` 헤더를 붙이도록 고친다(원장 Ruling).
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — 백엔드 테스트 전체(v1 7개 + 새 5개) 통과, offline 회귀 30개 차이 0

---

### Task 5: 프론트 셸 — 역할 전환과 메뉴 6개

**Files:**
- Create: `web/src/role.ts`, `web/src/role.test.ts`, `web/src/components/Sidebar.tsx`, `web/src/components/RoleSwitch.tsx`, `web/src/screens/ReviewHome.tsx`
- Modify: `web/src/App.tsx`, `web/src/api.ts`, `web/src/types.ts`, `web/src/components/TopBar.tsx`
- Delete: `web/src/components/RunList.tsx` (내용은 `ReviewHome`의 목록으로 옮김)

**Interfaces:**
- Consumes: Task 4 API
- Produces:
  - `role.ts`:
    - `type Role = "staff" | "director"`
    - `loadRole(): Role` (localStorage를 try/catch로 읽음, 기본 staff)
    - `saveRole(r)`
    - `can(role: Role, action: Action): boolean`. `Action`은 4.1 표의 동작 문자열 union이다. 화면 버튼은 모두 이것으로 판단한다.
  - `api.ts`: 모든 fetch에 `X-Role` 헤더를 붙인다(현재 역할을 모듈 변수로 둔다). v2 API 함수를 추가하고, 이름은 엔드포인트와 맞춘다.
    - `collectNotices`, `listNotices`, `reviewNotice`, `startNotice`, `getDashboard`, `transition`, `rerun`, `listApprovals`, `listDecisions`, `getCriteria`
  - `View`에 `dashboard | notices | review | approvals | decisions | criteria`를 추가한다. 기존 `new | job | run`은 review 메뉴 안의 하위 상태로 둔다.
- **메뉴 배지:** 스펙 3.1(공고함 = 검토 대상 수, 검토 = 진행 중, 결재함 = 역할별 수). 화면을 바꿀 때마다, 그리고 결재·수집 동작 뒤에 다시 받는다.

- [ ] **Step 1: 실패하는 테스트 작성** — `role.test.ts`: `can("director", "collect") === false`, `can("director", "decide") === true`, `can("staff", "decide") === false` 등 표 전체, `loadRole()` 기본값
- [ ] **Step 2: 실패 확인** — `node --test src/role.test.ts`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과·빌드** — `npm test`, `npm run build`
- [ ] **Step 5: 브라우저 확인**
  - 역할을 바꾸면 결재함 배지 문구가 바뀌는지 본다.
  - 새로고침해도 역할이 유지되는지 본다.
  - 검토 메뉴에서 v1 목록·업로드·결과가 그대로 동작하는지 본다.

---

### Task 6: 대시보드 화면

**Files:**
- Create: `web/src/screens/Dashboard.tsx`

**구성** (스펙 3.2, 승인 시안 ①)
- 카드 4장과 "전체 검토 n건 (공고 연결 m건)" 보조 문구
- 처리 흐름 막대 5단계(CSS 높이 비례)
- 권고 분포 막대와 범례, 일치율
- 결재 대기 표. 본부장일 때만 "결정" 버튼이 있고 결과 화면으로 간다.
- 마감 임박 표(D-day 배지)
- 수집 전이면 안내 문구와 "공고함으로" 버튼

- [ ] **Step 1: 구현**
- [ ] **Step 2: 빌드 확인**
- [ ] **Step 3: 브라우저 확인**
  - 수집 전 상태(빈 `outputs/notices`)에서 0과 안내가 나오는지 본다.
  - Task 2 실제 수집 뒤 숫자가 `GET /api/dashboard`와 같은지 본다.

---

### Task 7: 공고함 화면

**Files:**
- Create: `web/src/screens/Notices.tsx`, `web/src/components/ReasonDialog.tsx`
- Modify: `web/src/screens/NewReview.tsx` (`folderId`, `noticePrefilled` props → 공고문 칸을 "공고에서 받음"으로 표시하고 FormData에 `folder_id`)

**구성** (스펙 3.3, 승인 시안 ②)
- **머리:** 기간 SegmentedControl(7일/30일), 키워드 표시, "지금 수집"
  - `can(role, "collect")`일 때만 활성화한다.
  - 수집 중에는 Loading을 보여준다.
  - 끝나면 결과 요약 SectionMessage를 보여준다.
  - 키가 없으면 비활성 사유를 보여준다.
- **탭 4개:** 건수 포함
- **표:** 공고명(나라장터 링크), 기관·계약방법, 예산(억), 마감, 판정 배지와 근거, 첨부(PDF n / HWP n), 동작
- **ReasonDialog:** 포함/제외 사유를 입력받는다(필수). Montage `Modal`을 쓴다.
- **"검토 시작":**
  - 실행 방식을 고르는 작은 대화상자(offline 기본, live 비용 안내)를 띄운다.
  - 응답이 `job_id`면 검토 메뉴의 진행 화면으로 간다.
  - `needs_upload`면 업로드 화면으로 간다(`folderId` 전달, "RFP가 HWP뿐이라 PDF로 올려 주세요").
- 본부장이면 동작 칸에 "담당자 권한"만 보여준다.

- [ ] **Step 1: 구현**
- [ ] **Step 2: 빌드 확인**
- [ ] **Step 3: 브라우저 확인 (실제 수집 데이터)**
  - 수집 → 탭별 건수가 `by_verdict`와 같은지
  - 확인 필요 1건 포함(사유) → 검토 대상 탭으로 옮겨지는지
  - PDF 첨부가 있는 공고 "검토 시작(offline)" → 진행 → 결과, 공고함 "검토 중·완료" 탭에 나오는지
  - RFP PDF 없는 공고 → 업로드 화면 → PDF 올림 → 같은 폴더에서 실행되는지

---

### Task 8: 결재 — 결과 화면 확장과 결재함

**Files:**
- Create: `web/src/components/WorkflowCard.tsx`, `web/src/screens/Approvals.tsx`
- Modify: `web/src/screens/RunResult.tsx`, `web/src/components/DecisionCard.tsx`, `web/src/components/tabs/ChecklistTab.tsx`

**구성** (스펙 3.4·3.5·4.5)
- **WorkflowCard** (결과 화면 머리 아래):
  - 상태 배지와 이력 타임라인(역할·시각·사유·요청)
  - 담당자 버튼: "결재 올리기"(검토 완료) / "다시 결재 올리기"(보완 요청) / "다시 실행…"(보완 요청, 실행 방식 선택)
  - 본부장 버튼: "반려…"(결재 대기, ReasonDialog + 요청 방식 선택)
  - 옛 보고서(`is_current=false`)에는 버튼 없이 "현재 보고서로 이동" 링크
- **DecisionCard:** 본부장이고 상태가 결재 대기·결정 완료일 때만 입력 가능하다. 아니면 이유를 표시한다("결재 대기 상태에서 본부장이 결정합니다").
- **ChecklistTab:** 담당자이고 상태가 검토 완료·보완 요청일 때만 수정할 수 있다. 아니면 읽기 전용으로 두고 이유를 표시한다.
- **Approvals:**
  - 본부장: 결재 대기 표
  - 담당자: 보완 요청 표(반려 사유·요청)와 "내가 올린 결재 대기" 표
  - 행을 누르면 결과 화면으로 간다.
- 409 응답이면 "이미 처리된 건이에요: 현재 상태 X"를 보여주고 다시 불러온다.

- [ ] **Step 1: 구현**
- [ ] **Step 2: 빌드 확인**
- [ ] **Step 3: 브라우저 확인** — 스펙 6장 시연 시나리오 1~5를 모두 따라간다(본부장 역할에서 API를 직접 호출하면 403인지 `fetch`로 확인).

---

### Task 9: 의사결정 이력과 판단 기준 화면

**Files:**
- Create: `web/src/screens/Decisions.tsx`, `web/src/screens/Criteria.tsx`

**구성**
- **Decisions** (스펙 3.6):
  - 위쪽에 결정별 건수
  - 표: 결정일, 공고명, 예산, AI 권고, 체크리스트 권고, 결정, 사유, 반려 횟수
  - AI 권고와 결정이 다르면 배지를 붙인다(v1 `recommendationsDiffer` 재사용).
- **Criteria** (스펙 3.7):
  - 공고 필터 규칙 표, 체크리스트 1·2단계 표와 점수 구간, 근거 데이터 요약
  - "값은 `config/notice_filter.yaml`, `config/checklist.yaml`을 고쳐 바꿉니다"
  - "역할 전환은 시연용이며 보안 인증이 아닙니다"

- [ ] **Step 1: 구현**
- [ ] **Step 2: 빌드 확인**
- [ ] **Step 3: 브라우저 확인** — Task 8 시나리오 뒤 이력 표에 그 결정이 나오는지, 기준 표 값이 YAML과 같은지 확인한다.

---

### Task 10: 문서와 전체 점검

**Files:**
- Modify: `README.md`, `실행안내.md`, `docs/변경사항_2026-10-27.md` (v2 절 추가: 메뉴, 역할, 수집 키, 결재 흐름, 새 파일, 확인 방법)

- [ ] **Step 1: 문서 갱신** — `.env`에 `G2B_BID_NOTICE_KEY`를 넣는 방법도 적는다.
- [ ] **Step 2: 전체 자동 검사**
  - 백엔드: `for t in config g2b stages checklist runs jobs app notice_filter notices workflow dashboard app_v2; do .venv/bin/python tests/test_$t.py; done`
  - 프론트: `cd web && npm test && npm run build`
  - offline 회귀 30개 차이 0
- [ ] **Step 3: 시연 점검** — 스펙 6장 시나리오를 처음부터 끝까지 한 번 더 진행한다. 시험으로 만든 결정·결재 파일 중 사용자의 기존 CLI 결과 폴더에 생긴 것은 정리한다.
