# 공공 SI 입찰 참여 검토 시스템

VS Code 실행 준비와 오류 수정이 반영된 작업본입니다.

- 먼저 [실행안내](실행안내.md)를 확인하세요.
- 입력: `data/input`의 PDF 3개와 `data/company/company` 회사 JSON
- 키 없는 실행: `.venv\Scripts\python.exe run.py --mode offline --save-intermediate`
- 실제 CrewAI 실행: `.env`에 본인 키를 넣고 F5에서 2번 선택
- 흐름: Agent 1 → Agent 2·3 병렬 → Agent 4 (1·2·3 결과 종합)
- 이번 회차 변경 사항: [docs/변경사항_2026-10-27.md](docs/변경사항_2026-10-27.md)
- 웹페이지: 처음 한 번 `cd web && npm install && npm run build`, 실행은 `.venv/bin/python run_web.py` → http://127.0.0.1:8000 ([실행안내](실행안내.md#웹페이지로-실행))
  - 메뉴: 대시보드 · 공고함(나라장터 수집·규칙 필터) · 검토 · 결재함 · 의사결정 이력 · 판단 기준(기본 + 사용자 기준 세트, 본부장이 적용), 시작 화면에서 담당자/본부장 역할 선택
  - 공고 수집 키: `.env`에 `G2B_BID_NOTICE_KEY` (조달청_나라장터 입찰공고정보서비스)
  - 웹 전체 기능·화면·API·저장 위치 정리: [WEB_README.md](WEB_README.md)

CrewAI Task의 병렬 실행과 context 연결은 [공식 문서](https://docs.crewai.com/v1.15.28/en/concepts/tasks)를 참고했습니다.

## 설정 파일 (YAML)

Agent 프롬프트·회사 정책·판단 키워드는 코드가 아니라 `config/`의 YAML에서 관리합니다.

| 파일 | 내용 |
|---|---|
| `config/agents.yaml` (신규) | Agent 4개의 role·goal·backstory |
| `config/tasks.yaml` (신규) | Task 4개의 지시문·기대 출력 |
| `config/policy.yaml` (신규) | 분석 대상 법인(㈜아이티센엔텍), 나라장터 검색어, 공통 검토 원칙 9개, 샘플 데이터 안내문 |
| `config/checklist.yaml` (신규) | Go/No-Go 체크리스트 (참가 가능 4항목 + 점수 5항목, 해설은 `docs/go_no_go_checklist.md`) |
| `config/settings.yaml` (항목 추가) | 금액 기준, 요구사항 분류 키워드, 참가자격·인력·제출 용어, 실적 판단 용어, 점수 가중치, PDF 근거 한도, 나라장터 API 설정 |

### 코드 쪽 변경

- `crew.py`: Agent·Task 프롬프트 문자열(77줄)을 `agents.yaml`·`tasks.yaml` 참조로 교체
- `policy.py`, `agent01_rfp.py`, `agent02_match.py`, `agent04_report.py`: 상수 이름은 유지하고 값만 YAML에서 읽음 (다른 코드는 변경 없음)
- 정규식(요구사항 ID·날짜)과 상태값(`충족`/`부분 충족` 등), Task 이름, 산출물 경로는 Pydantic 모델·코드 로직과 직접 연결되어 있어 코드에 유지

### 전환 검증

- YAML에서 만든 프롬프트 8종(Agent 4 + Task 4)이 기존 코드 문자열과 글자 단위로 동일
- 키워드·정책 상수 값 동일
- offline 재실행 결과 21개 파일이 타임스탬프 외 차이 0개
- live 모드는 재실행하지 않았으나, live가 쓰는 프롬프트는 위와 같이 동일

### YAML을 고칠 때

- `agents.yaml`·`tasks.yaml`의 `$target_legal_entity`, `$common_agent_rules`, `$g2b_search_terms`는 `policy.yaml` 값으로 채워집니다.
- `{notice_summary}`, `{rfp_summary}`, `{company_summary}`, `{market_summary}`는 CrewAI가 실행 시 채우는 입력이므로 지우지 않습니다.
- 수정 후 `python tests/test_config.py`로 변수명 오타·누락 입력을 확인합니다 (`config OK`가 나오면 정상).
- `rfp` 키워드는 항상 기본 `config/settings.yaml`에서 읽습니다. 다른 설정 파일 경로를 지정해도 키워드는 바뀌지 않습니다.

## Go/No-Go 체크리스트

- 해설: [docs/go_no_go_checklist.md](docs/go_no_go_checklist.md) / 설정: `config/checklist.yaml`
- 근거: 나라장터 3년치 용역 낙찰 351,025건 중 아이티센 그룹 낙찰 93건 (`data/market/itcen_group_awards_3y.json`)
- 재수집: `.venv/bin/python scripts/collect_g2b_awards.py` (`.env`의 `G2B_SERVICE_KEY` 필요, 약 20분)
