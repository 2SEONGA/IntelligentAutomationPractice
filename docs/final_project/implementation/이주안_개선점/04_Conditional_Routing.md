# 보완 4 — 재검색·재분석 Conditional Routing 개선

작성자: 이주안  
작업 시작일: 2026-11-19  
최종 수정일: 2026-11-19  
상태: **기능 구현 및 관련 로컬 테스트 완료 — 실제 상수도관 RFP의 Human Review 분기 확인, 재검색·재분석 분기의 실제 PDF 실행 확인은 남음**

## 요약

- **목적:** T1.5 검증 실패를 모두 같은 방식으로 다루지 않고, 실패한 항목과 원인에 따라 재검색·T1 재분석·담당자 검토로 분기한다.
- **구현 결과:** `t15_route`와 `t1_reanalyze` Node를 추가했다. 기존 `targeted_retrieval`에는 실패한 항목의 검색어만 전달하고, 재시도 횟수를 State에 기록한다.
- **검증 결과:** 보완 4 테스트 7개와 기존 HITL·Agent 2·3 평가·보고서 생성·검증·저장 테스트 65개를 합쳐 **관련 테스트 72개가 사용자 Mac의 `.venv`에서 모두 통과**했다.
- **실제 자료 확인:** 상수도관 RFP의 새 실행에서 `human_review` 분기가 선택되고 실행이 `paused`로 저장됐다. 실행 오류는 없었다. 해당 실행의 재검색 횟수는 0회, 재분석 횟수는 0회였다.

## 수정 전 동작

기존 그래프는 `t15_verify` 결과가 확정되지 않으면 추가 검색 가능 여부를 확인했다. `retrieve_more` 항목이 있고 검색 횟수가 남았다면 `targeted_retrieval`로 이동하고, 그렇지 않으면 `human_review`로 이동했다. 검색 후에는 `t15_reverify`에서 다시 검사했다.

- 분기 기준이 주로 검증 상태와 전체 재검색 횟수였고, 원문 값의 해석 오류와 원문 근거 부족을 별도 재작업 경로로 구분하지 않았다.
- 추가 검색은 검증 결과의 `retrieve_more` claim에서 검색어를 모았으나, 별도 분기 계획에 실패 항목과 선택 사유를 기록하지 않았다.
- 서로 다른 문서의 충돌, 자동 해석 실패, 검색 횟수 초과는 결국 담당자 검토로 이어졌지만 원인별 분기 결과가 State에 구조화되지 않았다.

## 변경 내용

### 1. 검증 실패 원인별 분기 계획

`conditional_routing.py`의 `plan_verification()`이 T1.5의 category·claim별 `status`, 검사 `check_id`, 검색어를 읽어 작업 대상을 정한다.

| 원인 | 분기 | 처리 |
|---|---|---|
| 원문 인용·항목 근거 부족 (`retrieve_more`) | `targeted_retrieval` | 해당 실패 claim의 검색어만 사용해 RAG 재검색 |
| T1의 금액 단위 변환 또는 날짜 정규화 문제 | `t1_reanalyze` | 원본 PDF를 T1으로 다시 분석하되 문제 항목만 State에 반영 |
| 서로 다른 문서·후보의 값 충돌 또는 담당자 판단 필요 | `human_review` | 한쪽 문서의 값을 자동 선택하지 않고 담당자에게 전달 |
| 검색 한도 초과·검색어 없음·재분석 후 미해결·재분석 실패 | `human_review` | 자동 시도를 멈추고 남은 사유를 기록 |

동일 실행에 담당자 판단이 필요한 항목과 근거 부족 항목이 함께 있으면 **담당자 검토가 우선**이다. 따라서 이번 실제 상수도관 실행에서 재검색하지 않았다는 사실만으로 재검색 경로에 결함이 있다고 판단하지 않는다. 재검색 가능 횟수는 기존 `retrieval_limit`을 따른다.

### 2. 항목별 재검색 및 재검증

`targeted_retrieval`이 `routing_plan.targets`의 검색어를 사용한다. 기존처럼 문서 식별자와 공고번호로 검색 범위를 제한한다. 검색 결과가 있어도 그것을 T1의 확정값으로 자동 대체하지 않으며, `t15_reverify`를 통해 원문 후보를 다시 검사한다. 재검증 뒤에는 `t15_route`에서 다음 경로를 다시 결정한다.

### 3. T1 해석 오류 재분석

`amount_normalization` 또는 `date_normalization` 검사에 문제가 있으면 `t1_reanalyze`로 보낸다. T1 추출기는 원본 PDF를 다시 읽지만, State에는 문제가 된 금액·일정 항목만 반영한다. 같은 항목의 자동 재분석은 최대 한 번이다.

금액 재계산은 **기존 T1.5에서 원문 인용이 확인됐고 재추출한 후보의 페이지와 문구가 동일할 때만** 수행한다. 같은 출처의 재추출 후보 값이 재계산값과 다르면 미선택 사유를 남긴다. 재분석 후에는 `t15_verify`를 다시 거쳐야 하며, 같은 오류가 남거나 실행 자체가 실패하면 Human Review로 이동한다. 날짜 표현이 여전히 모호하면 날짜를 임의로 확정하지 않는다.

### 4. 충돌과 담당자 확인

공고 API와 RFP 등 출처 간 불일치, 서로 다른 후보의 상충, 법적 참가자격 해석처럼 사람의 판단이 필요한 항목은 자동 재분석으로 덮어쓰지 않는다. `human_review`는 기존 Checkpointer 기반 중단·재개 기능을 사용하며, 담당자의 승인·수정·반려 처리도 유지한다.

## Workflow 및 State 변경

```text
T1 추출 → 나라장터 조회 → T1.5 검증 → 원인별 분기(t15_route)
                                      ├─ 근거 부족 → 항목별 RAG 재검색 → T1.5 재검증 → 원인별 분기
                                      ├─ T1 해석 오류 → T1 재분석 → T1.5 재검증 → 원인별 분기
                                      ├─ 충돌·재시도 종료 → Human Review
                                      └─ 확인 완료/담당자 진행 승인 → T2 → T3 → 보완 3 평가 → T4
```

| State 항목 | 내용 |
|---|---|
| `routing_plan` | 선택한 `action`, 대상 category·field·검색어, 분기 사유 |
| `reanalysis_attempts` | `category:field`별 T1 재분석 횟수 |
| `reanalysis_error` | T1 재분석 실패 시 담당자 검토에 전달할 사유 |
| `retrieval_round`·`retrieval_limit` | 기존 재검색 횟수와 한도 |
| `routing_reasons` | 현재 검증 사유와 보완 4 분기 사유 |

T2·T3 결과 평가와 T4 보고서 생성·검증 경로는 유지한다. 이번 분기는 **T1.5에서 확인하는 T1 추출 결과**에 적용된다. 보완 3의 Agent 2·3 평가 결과에 따라 T2·T3을 자동 재실행하는 기능은 포함하지 않았다.

## 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `src/bid_review/rag/conditional_routing.py` | 신규: claim별 실패 원인 분류와 다음 작업 계획 |
| `src/bid_review/rag/workflow.py` | `t15_route`, `t1_reanalyze` Node·State·조건부 경로 추가; 대상 검색어 사용 |
| `tests/test_conditional_routing.py` | 신규: 재검색, 재분석, 충돌, 한도 초과, 기존 승인, 그래프 연결 테스트 |

## 실행 및 확인 방법

보완 1~3을 적용한 프로젝트 루트에서 가상환경을 활성화한 뒤 실행한다. 그래프 구조가 바뀌었으므로 실제 문서 확인에는 이전에 중단된 실행을 재사용하지 않고 새 `thread_id`로 시작한다.

### 관련 테스트

```bash
python tests/test_conditional_routing.py
python tests/test_hitl.py
python tests/test_agent23_evaluation.py
python tests/test_t4_report.py
python tests/test_report_validation.py
python tests/test_report_export.py
```

### 실제 RFP 실행

```bash
python scripts/run_hitl_review.py start \
  --rfp "data/input/AI 영상 기반 상수도관 노후도 분석 및 GIS 연동 소프트웨어 개발 용역.pdf" \
  --notice-no "R26BK01762350" \
  --company "data/company/company"
```

실행별 결과는 `outputs/hitl/<thread_id>/status.json`에 저장한다. `state.routing_plan`에는 마지막으로 선택한 분기와 대상 항목이, `state.retrieval_round`·`state.reanalysis_attempts`에는 자동 재작업 횟수가 기록된다. `paused`라면 검토 요청·응답 파일도 같은 폴더에 저장된다.

## 검증 결과

### 사용자 Mac의 `.venv` 테스트

| 실행 명령 | 통과 수 | 확인 범위 |
|---|---:|---|
| `python tests/test_conditional_routing.py` | 7개 | 원인별 분기 정책·그래프 연결 |
| `python tests/test_hitl.py` | 20개 | 저장·중단·재개·승인·수정·반려 |
| `python tests/test_agent23_evaluation.py` | 10개 | T2·T3 근거 평가 및 보고서 전달 |
| `python tests/test_t4_report.py` | 10개 | T4 생성·차단·연결 |
| `python tests/test_report_validation.py` | 13개 | 보고서 값·근거 보존 검사 |
| `python tests/test_report_export.py` | 12개 | 보고서·진단 파일 저장 |
| **합계** | **72개** | **모두 `OK`; 건너뛴 테스트 없음** |

`crewai.rag`의 `ImportWarning`이 출력됐지만 위 테스트는 모두 `OK`로 끝났다. 테스트는 분기 규칙과 연결·회귀를 확인한 것이며 실제 PDF에서 모든 종류의 오류를 유도해 시험한 것은 아니다.

### 실제 상수도관 RFP

- 새 실행 `thread_id`: `c8b67a742f1e4fd7856d1128fe2e7a6b`
- 저장 파일: `outputs/hitl/c8b67a742f1e4fd7856d1128fe2e7a6b/status.json`
- 실행 상태: `paused`; T1.5 검증 상태: `human_review`; 실행 오류: `[]`
- 마지막 분기: `human_review` — 금액의 나라장터 대조, 참가자격 조항, 입찰 마감 시각 등 담당자 확인 항목 존재
- 재검색 횟수: `0`; 재분석 횟수: `{}`

이는 **실제 RFP에서 담당자 검토 분기와 저장된 중단 상태를 확인한 결과**다. 이번 사례에서 재검색·재분석 Node가 실행됐거나 T2·T3·T4에 도달했다고 해석하지 않는다. 검토 응답 파일의 `approve`는 자격 충족이나 최종 Go 결정을 뜻하지 않으며, 미확인 조건을 검토하지 않은 채 진행 승인하지 않는다.

## 한계 및 다음 단계

1. **실제 재검색·재분석 경로:** 두 경로는 분기 테스트를 통과했으나, 상수도관 실데이터는 담당자 확인 경로로 바로 이동했다. 원문 근거만 부족한 사례와 T1의 값 해석 오류 사례로 각 경로의 실행 결과를 따로 확인할 필요가 있다.
2. **재분석의 범위:** T1 추출기를 다시 실행하지만 State에 반영하는 것은 문제 항목이다. 같은 원본을 한 번 더 읽어도 해결되지 않는 모호성은 담당자에게 넘긴다.
3. **후속 Agent 평가:** T2·T3 평가의 `needs_review`는 보완 3의 보류 사유로 전달되며 이번 단계에서 자동 재검색·재분석하지 않는다.
4. **팀 통합:** 개인 브랜치의 다른 보완과 최종 통합 확인을 마친 뒤 팀 브랜치에 합친다. 이전 그래프에서 중단된 체크포인트는 새 그래프의 실험에 사용하지 않는다.

**완료 판단:** 보완 4의 원인별 분기 코드와 관련 테스트는 완료했고, 실제 상수도관 PDF에서는 Human Review 분기까지 확인했다. 실제 PDF의 재검색·재분석 실행과 T1→T4 전체 경로는 후속 통합 검증으로 남긴다.
