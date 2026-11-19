# 보완 6 — Conditional Routing 및 Workflow 테스트 강화

작성자: 이주안  
작업 시작일: 2026-11-19  
최종 수정일: 2026-11-19  
상태: **통합 테스트 코드 작성 완료 — 사용자 Mac `.venv`에서 실제 그래프 테스트 확인 필요**

## 요약

- **목적:** 보완 1~5로 구성된 그래프가 정상 처리와 오류·승인 상황마다 다른 경로를 선택하는지 확인한다.
- **구현 결과:** 운영 `create_bid_review_graph()`를 실행하는 다섯 사례를 추가했다. PDF 추출·외부 API·검색 결과는 고정하고, 실제 StateGraph와 T4 생성·검증, HITL 중단·재개 연결은 사용한다.
- **검사 항목:** 실행된 Node 순서, `execution_history` 순번, `routing_plan`, 검색 횟수, 검증·분석 결과, 사람 승인 후 최종 상태를 사례별로 검사한다.
- **확인 범위:** 작성 환경에는 `langgraph`가 없어 통합 테스트 다섯 개는 실행되지 않았다. 사용자 Mac의 `.venv`에서 모두 `OK`이고 건너뛰기가 없는지 확인해야 한다.

## 수정 전 동작

보완 1~5에는 T4 생성·검증, HITL 재개, T2·T3 평가, 원인별 재검색·재분석, 공고별 이력에 대한 테스트가 각각 있었다. 그러나 **정상 진행, 근거 부족, 충돌, 검색 횟수 초과, 담당자 승인**이라는 업무 상황별로 운영 그래프 전체를 실행하고 실제 Node 순서·분기·State 변화를 한곳에서 대조하는 테스트는 부족했다.

## 추가한 다섯 상황

| 상황 | 입력 조건 | 기대 경로와 최종 상태 |
|---|---|---|
| 정상 처리 | T1.5 `confirmed` | T1 → T1.5 → T2 → T3 → T2·T3 평가 → T4 생성·검증. 검색 및 Human Review 없음 |
| RAG 근거 부족 | T1.5 `retrieve_more`, 검색 후보 없음, 검색 한도 1 | 해당 항목 재검색 → 재검증 → 한도 도달 → Human Review. T2 미실행 |
| 원문 정보 충돌 | T1.5 교차 검증 `conflict` | 추가 검색 없이 Human Review. 다른 문서의 값을 자동 채택하지 않음 |
| 재검색 횟수 초과 | 근거 부족이지만 검색 한도 0 | 검색 Node를 실행하지 않고 Human Review |
| 담당자 승인 | T1.5 `human_review`, 저장된 중단 지점에서 `approve` | 원래 T1을 반복하지 않고 T2 → T3 → 평가 → T4까지 재개. 승인 이력과 담당자 기록 보존 |

## 테스트 방식과 기록

`tests/test_workflow_scenarios.py`는 실제 운영 그래프를 생성한다. PDF 추출기·T1.5 응답·회사 역량 결과·외부 검색은 외부 서비스 호출 없이 재현 가능한 값으로 바꾼다. 담당자 승인 사례는 실제 LangGraph `InMemorySaver`와 `interrupt()`·`Command(resume=...)`를 사용한다.

모든 사례에서 `execution_history` 순번이 누락 없이 이어지는지 확인한다. 각 테스트는 중요한 Node의 실행 또는 미실행, 검색 호출 수, `retrieval_round`, `routing_plan.action`, `decision`, 승인 감사 기록과 T4 검증 결과 존재 여부를 확인한다. 이전 테스트의 상태나 SQLite 파일을 공유하지 않도록 입력·체크포인트를 사례별로 분리한다.

## 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `tests/test_workflow_scenarios.py` | 신규: 운영 그래프의 다섯 가지 업무 상황 통합 테스트 |
| `docs/final_project/implementation/leejuahn/06_Workflow_Scenarios.md` | 신규: 조건·검증 항목·결과 기록 |

운영 `src/` 코드는 이번 보완에서 변경하지 않는다. 테스트 중 실제 코드 결함이 발견되면 원인을 확인한 다음 해당 파일을 수정하고 이 문서에 추가한다.

## 실행 방법

프로젝트 루트의 `.venv`에서 실행한다.

```bash
python tests/test_workflow_scenarios.py
```

정상이라면 **`Ran 5 tests ... OK`**이며 `skipped`가 없어야 한다. 테스트 출력의 `crewai.rag` ImportWarning만으로 실패라고 판단하지 않는다.

기존 기능 회귀 확인은 아래 테스트를 이어 실행한다.

```bash
python tests/test_state_history.py
python tests/test_conditional_routing.py
python tests/test_hitl.py
python tests/test_agent23_evaluation.py
python tests/test_t4_report.py
python tests/test_report_validation.py
python tests/test_report_export.py
```

기존 관련 테스트 80개는 **보완 6 적용 전** 사용자 Mac에서 모두 통과했다. 다섯 통합 테스트까지 통과하면 관련 테스트 합계는 85개다.

## 검증 결과와 한계

작성 환경에서 Python 문법 검사는 통과했다. LangGraph가 없어 다섯 통합 테스트는 건너뛰었으므로, 작성 환경의 `OK (skipped=5)`를 통합 검증 통과로 해석하지 않는다. 사용자 Mac에서 실행 결과를 확인한 뒤 이 문서의 상태와 통과 수를 갱신한다.

실제 상수도관 RFP는 앞서 T1.5에서 Human Review로 이동했다. 이 고정 사례 테스트는 다른 분기를 의도적으로 재현하지만 실제 나라장터·OpenAI 응답 품질이나 해당 RFP의 입찰 자격을 검증하지 않는다. 원본 근거가 부족하거나 문서끼리 충돌하는 실제 사례를 검토 없이 승인하지 않는다.

**완료 판단:** 다섯 상황의 운영 그래프 테스트가 사용자 프로젝트에서 모두 통과하고 기존 80개 테스트에 회귀가 없음을 확인하면 보완 6의 코드 검증이 완료된다. 그 후 개인 브랜치 정리와 팀 통합을 진행한다.
