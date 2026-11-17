# 판단 기준 세트 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기본 판단 기준은 그대로 두고, 이름 붙인 기준 세트를 만들어 저장·재사용하고, 본부장이 고른 하나를 공고 수집과 새 검토에 적용한다.

**Architecture:** 새 모듈 `web/criteria.py`가 세트 파일을 읽고 쓰고, 기본 YAML에 세트 값을 덮어써 병합한다. 공고 수집과 체크리스트 평가는 `load_config` 대신 이 모듈을 거친다. 검토 시작 때 체크리스트 설정을 건 폴더에 스냅샷해서, 이미 판정한 건은 고정한다.

**Tech Stack:** FastAPI, React 18 + Montage, `node --test`, 파이썬 스크립트형 테스트(`python tests/test_*.py`).

**Spec:** `docs/superpowers/specs/2026-10-28-criteria-sets-design.md`

## Global Constraints

- 기본 세트 이름은 정확히 `"기본"`이다. 기본 세트는 쓰거나 지울 수 없다(400).
- 저장 위치: `runs.OUTPUTS / "criteria" / "sets" / "<이름>.json"`, `runs.OUTPUTS / "criteria" / "active.json"`. 테스트가 경로를 바꿀 수 있도록 호출할 때 `runs.OUTPUTS`를 읽는다.
- 적용 세트 변경은 `director`만 할 수 있다. 세트 쓰기·지우기는 `staff`, `director` 모두 할 수 있다.
- 파일 쓰기는 기존 `notices._save`처럼 임시 파일을 쓴 뒤 교체한다.
- 새 npm/pip 의존성은 추가하지 않는다.

## Review Focus

- 이름에 `../x`, `a/b`, 앞뒤 공백, NFD 한글이 들어온 경우: 경로 밖에 쓰지 않고, NFC로 정규화해 같은 세트로 취급해야 한다.
- 적용 중인 세트를 지우려는 경우: 409로 막아야 한다. 적용 세트 파일이 사라진 경우: "기본"으로 돌아가야 한다.
- `go <= review`, 음수 금액, 문자열 숫자, 빈 키워드 항목: 400이어야 한다(빈 항목은 걸러낸다).
- 다시 실행이 `JobBusy`로 시작되지 못한 경우: 기존 스냅샷이 바뀌면 안 된다.
- CLI 보고서나 이전 웹 보고서(스냅샷 없음)는 계속 기본 세트로 평가해야 한다.

---

### Task 1: 세트 저장·병합 모듈

**Files:**
- Create: `src/bid_review/web/criteria.py`
- Test: `tests/test_criteria.py`

**Interfaces:**
- Produces:
  - `DEFAULT = "기본"`
  - `CriteriaError(ValueError)`
  - `list_sets() -> list[dict]`: `[{name, default: bool, active: bool, updated_at}]`, 기본이 맨 앞
  - `active_name() -> str`
  - `resolve(name: str | None = None) -> dict`: `{"name", "notice_filter", "checklist"}`. `None`이면 적용 세트로 판정하고, 없는 이름이면 `KeyError`
  - `values_of(name) -> dict`: 편집 화면 초기값
  - `save_set(name, values) -> dict`
  - `delete_set(name) -> None`: 적용 중이면 `runs.Conflict`
  - `set_active(name) -> None`: 없는 이름이면 `KeyError`
  - `snapshot(folder: Path) -> None`: `folder/"criteria.json"`에 `{name, checklist}`를 쓴다
  - `checklist_for(folder: Path) -> tuple[str, dict]`: 스냅샷이 있으면 스냅샷, 없으면 `("기본", 기본 checklist)`

- [ ] **Step 1: Write the failing tests** (`tests/test_criteria.py`, `runs.OUTPUTS`를 임시 폴더로 바꿔서)
  - `resolve()` 결과가 `"기본"`이고 `notice_filter["min_amount_krw"] == 4000000000`이다.
  - `save_set("공격적", {"min_amount_krw": 2000000000, "core_agencies": ["조달청"], "decision": {"go": 5, "review": 3}})` 뒤 `resolve("공격적")`의 금액이 20억이다. S3 `core_agencies`는 `["조달청"]`, `decision`은 `{go:5, review:3}`이고, `ict_classes`는 기본값 그대로다.
  - `save_set("기본", …)`, `save_set("../x", …)`, `save_set("a/b", …)`, `save_set(" ", …)`는 `CriteriaError`를 낸다. `save_set(" 공격적 ", …)`는 `"공격적"`으로 저장된다.
  - `{"decision": {"go": 4, "review": 4}}`, `{"min_amount_krw": -1}`, `{"min_amount_krw": "1"}`, `{"deadline_warn_days": 31}`, `{"unknown": 1}`은 `CriteriaError`를 낸다.
  - `{"field_keywords": ["AI", " ", ""]}`는 `["AI"]`로 저장된다.
  - `set_active("공격적")` 뒤 `delete_set("공격적")`은 `runs.Conflict`를 낸다. `set_active("없음")`은 `KeyError`를 낸다.
  - active 파일이 가리키는 세트 파일을 지우면 `active_name() == "기본"`이 된다.
  - `snapshot(folder)` 뒤 적용 세트를 바꿔도 `checklist_for(folder)`는 스냅샷 시점의 이름과 설정을 돌려준다. 빈 폴더는 `("기본", …)`을 돌려준다.
- [ ] **Step 2: Run** `.venv/bin/python tests/test_criteria.py` → Expected: ImportError (모듈 없음)
- [ ] **Step 3: Implement** `criteria.py`
  - 편집 키 표(spec의 "고칠 수 있는 값")를 상수로 두고, 키별로 검증한다.
    - 정수: `type(v) is int`
    - 목록: 문자열만 받고, strip한 뒤 빈 항목을 걸러낸다
  - 병합은 `copy.deepcopy(load_config(...))` 위에 덮어쓴다. S2 `keywords`는 `{2: [...], 1: [...]}` 키가 정수라는 점에 주의한다(JSON에서는 `"2"`, `"1"`로 받는다).
- [ ] **Step 4: Run** `.venv/bin/python tests/test_criteria.py` → Expected: OK

### Task 2: 수집·체크리스트·API 연결

**Files:**
- Modify: `src/bid_review/web/notices.py` (`collect`)
- Modify: `src/bid_review/web/runs.py` (`_checklist`, `save_overrides`)
- Modify: `src/bid_review/web/app.py` (`start_job`, `/api/criteria`, 새 엔드포인트 3개, `collect`)
- Test: `tests/test_app_criteria.py`

**Interfaces:**
- Consumes: Task 1의 전부
- Produces:
  - 공고 `judged.criteria_set: str`
  - `load_run(...)["checklist"]`에 `criteria_set: str`
  - `GET /api/criteria?set=` 응답에 `sets`, `active`, `selected`, `values`(선택 세트의 편집값) 추가
  - `PUT /api/criteria/sets/{name}` body `{values}` → `{name}`
  - `DELETE /api/criteria/sets/{name}` → `{ok: true}`
  - `PUT /api/criteria/active` body `{name}` → `{active}`
  - 오류 코드: `CriteriaError` → 400, `KeyError` → 404, `Conflict` → 409

- [ ] **Step 1: Write failing tests** (`tests/test_app_criteria.py`, `test_app_v2`의 `Commands`, `FakeSession` 방식을 재사용)
  - `PUT /api/criteria/active`
    - staff이면 403
    - director이면 200
  - `PUT /api/criteria/sets/공격적`
    - staff이면 200
    - 기본 세트에 쓰면 400
  - 적용 세트를 `min_amount_krw: 1`로 두고 수집하면, 기본 세트에서 제외되던 저금액 공고가 `검토 대상`이 되고 `judged.criteria_set == "공격적"`이다.
  - 검토를 시작한 뒤 적용 세트를 바꿔도 `GET /api/runs/{id}`의 `checklist.criteria_set`과 `summary`는 그대로다.
  - 다시 실행이 `JobBusy`로 409를 받으면 건 폴더의 `criteria.json` 내용은 바뀌지 않는다.
  - `GET /api/criteria?set=없음`이면 404
  - 적용 중인 세트를 DELETE하면 409
- [ ] **Step 2: Run** `.venv/bin/python tests/test_app_criteria.py` → Expected: 404/AssertionError
- [ ] **Step 3: Implement**
  - `notices.collect`: `rules = criteria.resolve()["notice_filter"]`로 바꾸고, `judged`에 `criteria_set`을 넣는다.
  - `runs._checklist`, `runs.save_overrides`: `criteria.checklist_for(path.parent)`를 쓴다.
  - `start_job`: `manager.start` 성공 **뒤에** `criteria.snapshot(folder)`를 호출한다. 체크리스트는 결과를 읽을 때 계산하므로, 실행이 끝나기 전에만 쓰면 된다.
  - `app.collect`의 `default_days`, `collect_keywords`도 `criteria.resolve()`에서 읽는다.
- [ ] **Step 4: Run** 백엔드 전체 테스트: `for f in tests/test_*.py; do .venv/bin/python $f || echo FAIL $f; done` → Expected: FAIL 없음

### Task 3: 판단 기준 화면 — 세트 선택·편집·적용

**Files:**
- Create: `web/src/criteria.ts`, `web/src/criteria.test.ts`
- Modify: `web/src/types.ts` (`Criteria`에 `sets`, `active`, `selected`, `values` 추가, `CriteriaValues` 타입), `web/src/api.ts`, `web/src/role.ts` (`applyCriteria: "director"`)
- Modify: `web/src/screens/Criteria.tsx` (세트 칩, 동작 버튼, 편집 패널)
- Modify: `web/src/screens/Notices.tsx` (InfoBar에 "기준 세트" 값), `web/src/components/tabs/ChecklistTab.tsx` ("판단 기준: 이름")

**Interfaces:**
- Consumes: Task 2의 API
- Produces:
  - `parseList(text: string): string[]`: 쉼표나 줄바꿈으로 나누고, strip한 뒤 빈 항목을 걸러낸다. 중복은 처음 것만 남긴다.
  - `toValues(form: CriteriaForm): CriteriaValues`: 억 단위 입력을 원으로 바꾼다(`Math.round(eok * 1e8)`).
  - `formErrors(form): string[]`: 이름이 비었거나 "기본"인 경우, `go <= review`인 경우, 금액이 음수이거나 숫자가 아닌 경우를 잡는다.

- [ ] **Step 1: Write failing tests** (`criteria.test.ts`)
  - `parseList("AI, 클라우드\n,AI ")`는 `["AI","클라우드"]`
  - `toValues`의 금액 `"20"`은 `2000000000`
  - `formErrors`
    - 이름 `"기본"` → 오류 1개
    - `go: 4, review: 4` → 오류 1개
    - 정상 폼 → `[]`
- [ ] **Step 2: Run** `cd web && npm test` → Expected: criteria.test 실패
- [ ] **Step 3: Implement** `criteria.ts`를 만들고 화면에 연결한다.
  - 편집 패널은 기존 `inline-panel` 클래스를 재사용한다.
  - 적용 버튼은 `can(role, "applyCriteria")`일 때만 보인다. `Criteria`가 `role` prop을 받도록 `App.tsx`에서 넘긴다.
- [ ] **Step 4: Run** `cd web && npm test && npm run build` → Expected: 모두 통과, `✓ built`

### Task 4: 문서·브라우저 확인

**Files:**
- Modify: `README.md`, `실행안내.md` (판단 기준 메뉴 행, 저장 위치), `docs/변경사항_2026-10-27.md` (7절 "판단 기준 세트")

- [ ] **Step 1:** 서버를 재시작하고 브라우저에서 확인한다.
  - 담당자: 세트 만들기 → 편집 → 적용 버튼이 없다.
  - 본부장: 적용 → 공고함 정보 상자에 세트 이름이 보인다 → 적용 중인 세트는 지울 수 없다.
- [ ] **Step 2:** 시험으로 만든 `outputs/criteria/`를 지운다. offline 회귀(결과 파일 30개) 차이가 0이어야 한다.
