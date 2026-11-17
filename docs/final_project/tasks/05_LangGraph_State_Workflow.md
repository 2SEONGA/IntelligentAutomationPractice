# Task 05 — LangGraph State·Workflow 설계

상태: 완료 — 사용자 선택 A 반영  
작성일: 2026-11-11

## 설계 목표

LangGraph가 입찰 검토 흐름을 통제한다. 핵심정보 검증 세 범주를 독립적으로 평가하고, 부족한 항목만 다시 검색하며, 불확실한 건은 사람 검토로 멈춰 세운다. 검증 통과 전에는 T2·T3 분석이 실행되지 않도록 경로를 막는다.

## State 설계 초안

| State 필드 | 내용 | 갱신 노드 |
|---|---|---|
| `case_id` | 검토 건 식별자 | 입력/초기화 |
| `documents[]` | 파일 ID, 유형, 공고번호·차수, 파일 해시, 수집/개정일, 페이지 수, 추출 품질 | 입력/문서 준비 |
| `rfp_extraction` | 기존 T1의 기본정보·요구조건과 각 값의 원문 후보/출처 | T1 추출 |
| `claims_by_category` | amount / eligibility / schedule별 검증할 원자 주장 목록 | 검증 항목 정리 |
| `evidence_by_category` | 범주별 RAG 근거 레코드, 인용·출처·판본 | RAG 검색 |
| `verification_by_category` | 세 범주 각각의 검사, 결과, 누락·충돌·사유 | 검증 노드 |
| `search_queries[]` | 검색 질의와 필터, 검색 대상 판본, 라운드 | RAG 검색 |
| `retry_count_by_category` | 각 범주가 수행한 보강 검색 횟수 | 라우팅/검색 |
| `pending_categories[]` | 추가 검색 또는 사람 판단이 필요한 범주·항목 | 통합/라우팅 |
| `human_review_request` | 사람에게 묻는 질문, 후보 값, 인용·페이지, 해결 가능한 조치 | HITL 노드 |
| `human_review_response` | 사람의 수정·확인·반려, 근거 또는 지시 | HITL 재개 |
| `verified_facts` | 검증 통과 또는 사람이 확인한 정보와 확인 주체 | 승인 병합 |
| `downstream_results` | T2/T3/T4 결과 | 후속 분석 노드 |
| `final_status` | completed / needs_human / blocked / failed | 종료 노드 |

State 업데이트는 원문 추출값, 검증된 사실, 사람 확인 사실을 구분해 저장한다. 사람의 수정이 AI 검증 결과처럼 표시되지 않도록 확인 출처를 보존한다.

## 노드

| 노드 | 역할 |
|---|---|
| `prepare_documents` | 입력 PDF 검사, 문서·공고 버전 식별, 원문 해시, 개인정보 비노출 텍스트 생성, 추출 품질 표시 |
| `extract_t1` | 기존 RFP extractor를 호출해 후보 정보·요구조건·페이지 근거를 만든다. 낮은 품질 페이지와 후보 충돌도 보존한다. |
| `build_claims` | 금액·참가자격·기간별 세부 검증 항목을 만든다. 참가자격은 조건 주체·예외 등 원자 단위로 나눈다. |
| `verify_amount` | Task 03의 금액 검증 기준으로 정규화·범위·VAT·후보 충돌을 검사한다. |
| `verify_eligibility` | 자격 조항 탐색 범위·조건 주체·단서·예외·부록 완전성을 별도 검사한다. 회사 적격성은 판단하지 않는다. |
| `verify_schedule` | 사업 수행기간·제안/입찰/공동수급 마감 유형을 나누고 날짜·기준 사건·순서를 검사한다. |
| `retrieve_evidence` | 미확정 항목만 대상으로 질의 확장·페이지/섹션 확장·판본 필터를 적용해 RAG 검색한다. |
| `reverify_pending` | 추가 검색 결과만큼 범주별 검증을 다시 수행한다. 확정된 다른 범주 결과는 덮어쓰지 않는다. |
| `request_human_review` | 사람 검토 화면에 항목, 후보, 원문 근거, 미해결 이유, 질문을 제시하고 그래프를 일시 중단한다. |
| `apply_human_response` | 사람의 확인·수정·추가 근거를 기록하고 영향받은 검증 항목만 다시 확인한다. |
| `merge_verified_facts` | 확인된 값만 T2/T3 입력으로 만들고, 미확정 정보는 상태와 함께 제한적으로 전달한다. |
| `run_capability_and_market` | 기존 T2/T3 분석 기능을 호출한다. T1.5의 사업 정보 근거는 고치거나 제거하지 않는다. |
| `write_report` | T1·T1.5·T2·T3 결과와 미확정 항목을 근거 연결 상태로 보고서화한다. |
| `finish` | 완료/보류/실패 상태와 산출물 위치를 기록한다. |

## Edge와 조건부 Routing

```text
START
  → prepare_documents
  → extract_t1
  → build_claims
  → [verify_amount ∥ verify_eligibility ∥ verify_schedule]
  → aggregate_verification
       ├─ 모두 confirmed ─────────→ merge_verified_facts
       ├─ retrieve_more 있고 재검색 여유 → retrieve_evidence → reverify_pending ─┐
       │                                                                         └→ aggregate_verification
       ├─ conflict / human_review / 재검색 한도 도달 → request_human_review
       │   → apply_human_response
       │      ├─ 새 근거/값 제공 → 해당 항목 reverify → aggregate_verification
       │      ├─ 사람 확인으로 확정 → merge_verified_facts (사람 확인 표기)
       │      └─ 해결 불가/반려 → finish (보류 또는 확인 불가)
       └─ 문서 추출 치명 오류 → finish (실패/원문 확인 필요)
  → run_capability_and_market
  → write_report
  → finish
  → END
```

세 검증 노드는 독립된 검증 항목과 결과를 만든다. 병렬 실행 여부는 구현 시 State reducer 안정성과 기존 코드의 스레드 안전성을 확인해 결정한다. 병렬화를 지원하기 어렵다면 같은 세 가지 검증 노드를 순차 실행해도 Conditional Routing과 재검색/HITL의 그래프 이점은 유지된다.

## 분기 기준과 종료 조건

- **후속 통과:** 세 범주에서 필수 검증 항목이 각각 `confirmed`이거나, 사람이 확인해 출처가 기록된 상태다.
- **재검색:** `retrieve_more` 항목이 있고 문서·판본의 추가 검색 여지가 남아 있다. 범주별로 최대 1회 보강 검색부터 시작한다.
- **사람 검토:** 판본/문서 충돌, 법적 해석, 표/OCR 실패, 문서 범위 불완전, 재검색 후에도 근거 부족, 원본 증빙 요구가 남아 있다.
- **보류 종료:** 사람이 확인할 수 없는 상태거나 자료가 제공되지 않으면 미확정 상태와 필요한 다음 조치를 남기고 종료한다.
- **완료 종료:** 검증 사실과 출처가 보고서에 반영되고 T2/T3/T4 처리가 끝났다.
- 무한 루프를 피하려고 검색 횟수는 범주별로 제한한다. 사람 확인 뒤 재개는 해당 항목만 재검증하며 사용자 응답이 없다고 자동 승인하지 않는다.

## Human-in-the-Loop 입출력

사람에게는 “검토해 주세요”만 표시하지 않는다. 다음을 제공한다.

- 확인할 항목과 T1 추출값
- 문서명·판본·페이지가 달린 후보 근거와 인용
- 충돌 값 또는 찾지 못한 필수 조건
- 답변 선택: 값/조건 확인, 다른 판본 선택, 원문 추가 제공, 담당자에게 이관, 미해결로 보류

사람 응답은 원문과 구분해 출처가 `human_confirmed`인 별도 기록으로 남는다. 최종 Go/No-Go 결정은 기존 사람 승인 단계를 유지한다.

## LangGraph가 필요한 이유

- 세 유형의 검증 결과에 따라 후속 경로가 달라진다.
- 부족한 정보에 한해서만 RAG를 다시 실행한다.
- 문제가 해결되지 않으면 그래프를 멈추고 사람 확인 후 해당 항목부터 재개한다.
- 미확정 핵심정보가 후속 평가·보고서로 조용히 전파되는 것을 막는다.

단순 순차 체인으로는 이 조건별 반복, 중단, 사람 응답 후 재개를 명시적으로 관리하기 어렵다.

## 기술 경계 결정사항

**선택: A — LangGraph가 전체 workflow를 소유하고 기존 Python 분석기·도메인 로직을 노드에서 재사용한다.** 기존 CrewAI Agent 실행은 기말 MVP의 실행 경로에 넣지 않는다. 기존 CrewAI 코드·프롬프트·설정은 제거하지 않고 참고용/기존 CLI 경로로 보존한다.

### 선택 이유

- 과제의 필수 기술은 LangGraph이고 CrewAI는 선택이므로, 두 오케스트레이터를 동시에 유지할 필요가 없다.
- 이번 프로젝트의 핵심은 금액·참가자격·기간 검증의 정확도와 근거 추적이다. Crew를 LangGraph 노드로 분해하는 추가 작업보다 범주별 검증 기준과 검색 근거 품질에 개발 시간을 배정한다.
- 추가 검색, 충돌, 사람 검토, 사람 응답 후 재개를 한 그래프 State와 Edge에서 추적해 workflow의 제어 지점을 하나로 둔다.
- 프로젝트 연속성은 기존 데이터·PDF 분석·금액 파서·업무 규칙·웹 기능을 재사용해 유지한다. 프레임워크 자체보다 해결할 업무와 산출물의 연속성을 우선한다.
- 발표에서는 필수 구조를 중심에 두고, 검증 깊이를 위해 불필요한 orchestration 복잡도를 줄인 기술 선택으로 설명한다.

## 완료 기준

- [x] State 필드와 출처 구분
- [x] 주요 노드와 금액·자격·기간별 검증 단계
- [x] 추가 검색, 충돌, 추출 오류, Human Review의 조건부 경로
- [x] 종료·반복 제한 및 사람 확인 뒤 재개 흐름
- [x] LangGraph의 필요성과 RAG/검증기 관계 설명
- [x] LangGraph를 단독 workflow owner로 선택하고 이유 기록

## 다음 단계

Task 06에서 최소 시연 범위, 현재 CrewAI 구성의 재사용 방법, 팀 역할·일정을 정한다. 세 검증 범주의 깊이 기준을 우선 충족하고, UI/운영 기능은 남은 일정에 따라 단계화한다.

