# 보완 7 — 순차·병렬 Workflow 구현 및 성능 비교

작성자: 이주안  
작업 시작일: 2026-11-19  
최종 수정일: 2026-11-19  
상태: **순차·병렬 구조 및 비교 도구 구현, Mac 로컬 테스트 완료 — 실제 5건 성능 비교·기본 구조 선택 보류**

## 요약

- T1→T1.5 검증은 유지한다. 그 뒤 순차 모드는 T2→T3, 병렬 모드는 T2 회사 근거 검색·매칭과 T3 유사사업 검색을 동시에 시작한다.
- 병렬 모드에서 두 결과가 모두 끝나야 `t23_evaluate`→T4→보고서 검증으로 진행한다.
- 실행별 Node 시간과 OpenAI 임베딩 API 응답의 토큰 사용량을 기록하고, 동일 입력을 두 방식으로 반복 실행하는 비교 스크립트를 추가했다.
- Mac `.venv`에서 보완 7과 보완 6 관련 테스트 10개, 기존 기능 회귀 테스트 72개가 모두 통과했다.
- 실제 입력의 성능 및 내용 동등성 결과를 얻기 전에는 기본 실행 모드를 순차로 유지한다.

## 현재까지 확인한 결과

| 실행 명령 | 결과 | 확인 내용 |
|---|---:|---|
| `python tests/test_parallel_workflow.py` | 5개 `OK` | 두 그래프의 합류, 이력 병합, 비교 도구, 임베딩 사용량 처리 |
| `python tests/test_workflow_scenarios.py` | 5개 `OK` | 정상·근거 부족·원문 충돌·검색 횟수·담당자 승인 경로 |
| `python tests/test_hitl.py` | 20개 `OK` | 중단·재개와 승인·수정·반려 등 기존 동작 |
| `python tests/test_agent23_evaluation.py` | 10개 `OK` | T2·T3 근거 검증 |
| `python tests/test_conditional_routing.py` | 7개 `OK` | T1.5 원인별 분기 |
| `python tests/test_t4_report.py` | 10개 `OK` | T4 연결 및 생성 조건 |
| `python tests/test_report_validation.py` | 13개 `OK` | 보고서 데이터·근거 검증 |
| `python tests/test_report_export.py` | 12개 `OK` | 보고서·진단·근거 저장 |
| **합계** | **82개 `OK`** | **Mac 로컬 테스트 통과** |

CrewAI의 `ImportWarning`이 일부 테스트에 표시됐지만 테스트 실패는 없었다. 테스트에는 고정 입력과 외부 호출 대역이 포함돼 있으므로 실제 공고 5건의 실행 시간이나 결과 동등성을 입증하지는 않는다. 이전 상수도관 RFP 실행은 T1.5에서 담당자 검토로 중단됐으며, 이 사례만으로는 T2·T3 성능을 비교할 수 없다.

## 구조와 보호 조건

| 실행 모드 | T1.5 이후 | 합류 |
|---|---|---|
| `sequential` (기본) | 회사 근거 검색 → T2 매칭 → T3 검색 | T2·T3 평가 |
| `parallel` | 분기 → (회사 근거 검색 → T2 매칭) / (T3 검색) | 두 경로 완료 후 T2·T3 평가 |

T3 병렬 진입 시에도 공고 식별 충돌은 차단한다. T1.5의 `confirmed` 또는 사유가 기록된 담당자 승인 이후에만 진행한다. 순차 모드의 기존 T2 완료 조건은 유지한다. 평가와 T4의 기존 보호 조건도 유지한다. 담당자 확인이 필요한 실적·자격·유사성은 자동 확정하지 않는다.

두 Node가 동시에 전체 `execution_history`에 기록하면 동일 State 키가 충돌한다. 기록에는 이벤트 식별자를 붙이고, 기존 기록을 중복 없이 합친 뒤 순번을 다시 부여하도록 reducer를 추가했다. 각 Node 실행 시간(`duration_seconds`)은 기록되며, 병렬 Node의 순번은 결과 병합 순서에 따라 달라질 수 있다.

## 변경 파일

| 파일 | 내용 |
|---|---|
| `src/bid_review/rag/workflow.py` | 순차·병렬 모드, T1.5 이후 분기와 합류, Node 시간 |
| `src/bid_review/rag/state_history.py` | 동시 실행 이력 병합, 이벤트 식별자 |
| `src/bid_review/rag/usage_tracking.py` | 벤치마크 실행 중 임베딩 토큰 합산 |
| `src/bid_review/rag/semantic.py` | OpenAI 임베딩 응답의 사용량 전달 |
| `scripts/run_langgraph_review.py` | `--workflow-mode` 선택 |
| `scripts/compare_workflow_modes.py` | 최소 5건의 반복·쌍 비교와 결과 파일 |
| `tests/test_parallel_workflow.py` | 병렬 기록 병합과 그래프 합류 테스트 |

## 실행 순서

코드 적용과 위 로컬 테스트는 완료했다. 아래 실제 비교는 입력 확보 후 실행한다.

1. 서로 다른 공고번호와 같은 공고의 RFP 원문 PDF가 연결된 사례를 준비한다. `data/past_rfps/`의 원문과 `meta.json` 또는 팀의 기존 테스트 문서를 후보로 확인할 수 있다. 파일이 있다는 사실만으로 T1.5 통과를 가정하지 않는다.
2. 두 모드에 공통으로 쓸 회사·유사사업 검색 색인을 준비하고, 시험 중 자료를 수정하지 않는다.
3. `workflow_cases.json`을 만든다. 실제로 T1.5를 통과하거나 근거 있는 담당자 승인을 받은 **서로 다른 공고 최소 5건**을 넣는다. T1.5에 중단된 건은 성능 비교에 쓸 수 없다.
4. 새 출력 폴더를 지정하여 두 모드를 반복 실행한다.

```json
[
  {"id": "case-01", "rfp": "data/input/첫번째.pdf", "notice_no": "실제공고번호1", "company": "data/company/company"},
  {"id": "case-02", "rfp": "data/input/두번째.pdf", "notice_no": "실제공고번호2", "company": "data/company/company"},
  {"id": "case-03", "rfp": "data/input/세번째.pdf", "notice_no": "실제공고번호3", "company": "data/company/company"},
  {"id": "case-04", "rfp": "data/input/네번째.pdf", "notice_no": "실제공고번호4", "company": "data/company/company"},
  {"id": "case-05", "rfp": "data/input/다섯번째.pdf", "notice_no": "실제공고번호5", "company": "data/company/company"}
]
```

```bash
python tests/test_parallel_workflow.py
python tests/test_workflow_scenarios.py
python tests/test_hitl.py
python tests/test_agent23_evaluation.py
python tests/test_conditional_routing.py
python tests/test_t4_report.py
python tests/test_report_validation.py
python tests/test_report_export.py
python scripts/compare_workflow_modes.py --cases workflow_cases.json --output outputs/workflow_comparison_01 --repeats 2
```

단일 사례의 병렬 실행도 `python scripts/run_langgraph_review.py --rfp "...pdf" --workflow-mode parallel --output outputs/parallel_example.json`으로 확인한다. `run_hitl_review.py`의 기본 실행·재개 모드는 계속 순차다. 승인 사례의 병렬 경로는 그래프 테스트에서 확인하고, 운영 CLI의 HITL 모드 변경은 비교 결과를 보고 결정한다.

## 비교 결과 읽기

- `summary.json`: 유효 사례 수, 순차·병렬 평균 시간과 임베딩 토큰, 결과 동등성.
- `pairs.json`: 공고·반복별 실행 시간, 검증 상태, 달라진 결과 필드.
- `case_*/repeat_*/{sequential,parallel}/state.json`: 원본 State.
- 각 실행의 `measurement.json`: 총 시간, Node별 시간, 임베딩 응답 토큰·사용량 미반환 횟수.

프로그램은 두 실행이 모두 `t23_evaluate`와 `t4_validate`에 도달하고 검증 상태가 `passed` 또는 `needs_review`이며 입력 파일이 바뀌지 않은 경우만 유효 비교로 집계한다. 최소 5개 *서로 다른* 유효 사례가 필요하다. `result_equivalent`는 T2·T3 결과, 평가, 보고서, 검증, 결정의 구조화 값을 대조한다. 실행 ID, 기록 시각, 이벤트 순서는 비교하지 않는다. 실험 중 외부 공고 API 응답이나 색인 내용이 바뀔 수 있으므로 차이가 난 항목은 원본 `state.json`에서 확인한다.

**토큰 측정 범위 확인(M34, 2026-11-19):** LangGraph 경로(T1 규칙 추출, T1.5 규칙 검증, T2 `CapabilityMatcher` 규칙 매칭, T3 임베딩 검색, T4 규칙 기반 `ReportWriter`)는 LLM을 호출하지 않는다(`BidReviewCrew`는 회사 자료 로드 함수만 사용). 따라서 이 경로의 토큰은 OpenAI 임베딩 입력 토큰이 전부이고 출력 토큰은 구조상 0이다. CrewAI 보고서 경로를 비교에 넣을 경우에만 LLM 입력·출력 토큰 집계를 추가해야 한다.

**토큰 측정 범위:** 이 구현에서는 직접 호출하는 OpenAI 임베딩 API 응답의 `usage`만 합산한다. 다른 공급자의 호출이나 사용량이 반환되지 않은 요청을 임의로 추정하지 않는다. `requests_with_usage`가 0이면 표시된 0을 전체 토큰 사용량으로 해석하지 않는다. 캐시 상태가 달라지면 시간과 토큰 비교가 왜곡되므로 동일한 색인을 사전에 준비한다.

## 완료 판단

**현재 완료 범위:** 병렬 모드 구현, 기본 순차 모드 보존, 비교 도구 작성, Mac `.venv`에서 관련·회귀 테스트 82개 통과. 개인 작업 브랜치에 이 상태를 커밋하여 보존할 수 있다.

**남은 검증:** 실제 5건 이상에서 유효 비교를 완료한 뒤 기본 모드를 선택한다. 병렬에서 보고서·검증 결과가 일치하고 처리 시간이 일관되게 줄면 기본값 변경을 검토한다. 병렬을 채택하면 현재 순차로 동작하는 `run_hitl_review.py` 시작·재개 경로까지 통일하고 다시 검증해야 한다. 비교 결과를 얻기 전에는 성능 향상이나 토큰 절감을 주장하지 않는다.

## 브랜치 보존 및 후속 작업

실제 자료가 준비되기 전까지 `sequential`을 기본값으로 둔다. 개인 브랜치에 코드와 이 문서를 커밋해 두어도 보완 7의 성능 비교를 완료했다는 뜻은 아니다. 팀 브랜치와 합칠 때에는 아직 실행하지 않은 실제 성능 비교와 기본 구조 선택을 미결 항목으로 전달한다.

```bash
git branch --show-current
git status -sb
git add src/bid_review/rag/workflow.py src/bid_review/rag/state_history.py \
  src/bid_review/rag/usage_tracking.py src/bid_review/rag/semantic.py \
  scripts/run_langgraph_review.py scripts/compare_workflow_modes.py \
  tests/test_parallel_workflow.py \
  docs/final_project/implementation/이주안_개선점/07_Sequential_Parallel_Workflow.md
git diff --cached --stat
git commit -m "Add selectable sequential and parallel review workflows"
```

위 명령은 개인 브랜치와 변경 목록을 확인한 뒤 실행한다. `outputs/`의 로컬 결과 파일은 이 보완의 코드 커밋에 포함하지 않는다. 실제 공고가 확보되면 동일한 입력으로 두 모드를 반복 실행하고 `summary.json`, `pairs.json`의 유효 건수·시간·검증 결과를 문서에 추가한다.
