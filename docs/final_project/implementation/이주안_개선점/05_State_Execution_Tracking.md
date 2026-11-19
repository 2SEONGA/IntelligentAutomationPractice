# 보완 5 — LangGraph State 및 공고별 실행 정보 관리 강화

작성자: 이주안  
작업 시작일: 2026-11-19  
최종 수정일: 2026-11-19  
상태: **기능 구현 및 관련 테스트 80개 통과 — 실제 상수도관 RFP의 식별·이력 저장 확인**

## 요약

- **목적:** 공고번호, 차수, PDF 문서 버전, 실행 ID를 구분해 서로 다른 공고의 자료가 한 실행에서 섞이는 일을 줄이고, 각 단계의 실패 사유와 사용 근거를 추적한다.
- **구현 결과:** `case_identity`와 `execution_history` State를 추가했다. Node 실행 이력에 검증·평가 상태, 검색 근거 ID, 재시도 횟수, 담당자 수정 항목을 기록한다.
- **확인 결과:** 사용자 Mac의 `.venv`에서 신규 테스트 8개와 기존 관련 테스트 72개가 모두 `OK`로 끝났다. 건너뛴 테스트는 없었다.
- **실제 자료:** 새 실행 `d3ac048beaf841bf9ef52c6ba2f11d70`에서 공고 식별 정보와 T1→T1.5→분기 이력이 별도 JSON 파일로 저장됐다. 공고 충돌과 실행 오류는 없었고 Human Review에서 `paused`됐다.

## 수정 전 동작

기존 `BidReviewState`에는 공고번호·RFP 분석 결과·T1.5 검증 상태·추가 검색 횟수·회사 역량 분석·유사사업 후보 등이 저장됐다. HITL 실행에는 `run_id`, 입력 파일 해시, 담당자 검토 이력도 존재했다. 그러나 이 정보가 공고 식별자와 문서 버전별 실행 경위로 한곳에 묶여 있지 않았고, 각 Node의 상태·원인·검색 근거가 공통 형식의 실행 기록으로 남지는 않았다.

RAG 검색은 이미 `document_id`와 `notice_no`를 사용해 범위를 좁혔다. 이번 보완에서는 검색 결과의 공고번호·차수·해시도 현재 실행과 대조한다. 기존 검색 자체가 공고 구분 없이 동작했다고 해석해서는 안 된다.

## 변경 내용

### 1. 공고·문서·실행 식별 분리

`case_identity`에 다음을 별도로 기록한다.

| 항목 | 의미 |
|---|---|
| `case_id` | `공고번호:차수`; 공고번호가 없으면 PDF 문서 ID에 기반한 임시 식별자 |
| `notice_no`, `notice_order` | 현재 실행에서 사용하는 공고번호와 차수 |
| `input_notice_no`, `extracted_notice_no`, `notice_pdf_number` | 입력값, RFP 추출값, 별도 공고문 PDF 추출값을 각각 보존 |
| `rfp`, `notice_pdf`, `company` | RFP·공고문 PDF·회사 원자료의 경로, 내용 SHA-256 전체 해시, 앞 16자리 `document_id` |
| `api_notice_no`, `api_notice_order` | 조회된 나라장터 API 레코드의 식별자 |
| `run_id` | 같은 공고를 다시 실행해도 새로 부여하는 실행 식별자 |
| `conflicts`, `status` | 입력·PDF·API·검증 인용 사이의 공고 식별 충돌 |

`run_hitl_review.py`는 기존처럼 `thread_id`를 `run_id`로 사용한다. 단일 실행 함수 `run_bid_review()`는 새 실행 ID를 만든다. 파일 내용이 달라지면 PDF 해시·문서 ID도 달라져 같은 공고의 다른 판본을 구분할 수 있다. 공고문 PDF의 첨부 버전 대조 결과는 `case_identity.attachment_version_comparison`에 기록한다.

### 2. 다른 공고의 근거 차단

입력 공고번호와 RFP·공고문 PDF에서 읽은 공고번호가 다르거나, 조회된 API 공고번호·차수가 다르면 `conflicts`에 사유를 남긴다. 다른 공고로 조회된 API 레코드는 T1.5의 사실 근거로 사용하지 않는다. T1.5 인용의 공고번호·차수도 검사한다.

추가 RAG 검색에서는 현재 RFP의 문서 ID·해시와 공고번호·차수를 확인한다. 별도 공고문 검색 결과는 현재 공고번호·차수와, 입력 공고문 PDF가 있으면 그 문서 ID·해시까지 대조한다. 식별 충돌이 있으면 T2 자동 진행을 막고 Human Review로 보낸다. **공고 자체가 서로 다르면 담당자 `approve`로 우회할 수 없고, 원문과 입력을 맞춰 새 실행을 시작해야 한다.**

이 검사는 현재 입력과 연결된 검색 후보의 식별자를 비교한다. 공고문 원본의 진위나 나라장터 첨부 최신판 여부를 자동 확정하지는 않는다.

### 3. Node별 실행 기록

`execution_history`에는 Node가 완료될 때마다 순번·UTC 시각·`run_id`·`case_id`·문서 ID·문서 해시·Node 이름을 추가한다. 상황에 따라 다음 정보를 함께 기록한다.

- T1.5 검증 및 T2·T3 평가의 상태와 오류 검사 ID 또는 분기 사유
- 조건부 분기의 선택 작업과 대상 항목
- RAG 검색의 `chunk_id`, 회사 근거의 `record_id`, 유사사업 후보의 공고 식별자
- 재검색 횟수와 항목별 재분석 횟수·실패 사유
- 담당자 승인·수정·반려, 담당자 이름, 수정한 필드명과 검토 메모
- T4 생성·검증 상태와 실패 검사 ID

큰 원문 본문은 이력에 중복 저장하지 않고 기존 RFP·검증·분석 State에 보존한다. 기존 `review_history`도 유지하며, 담당자 수정의 변경 전후 값은 해당 감사 기록에서 확인한다. 중단 중인 Human Review Node는 응답 후 완료될 때 한 번 기록한다.

### 4. 별도 실행 파일 저장

HITL의 `outputs/hitl/<thread_id>/`에 기존 `status.json`과 함께 `case_identity.json`, `execution_history.json`을 저장한다. 보고서 산출물 저장 경로에서도 두 JSON 파일과 식별 정보를 포함한 `diagnostic.md`를 남긴다. 전체 State는 기존 `state.json`에 계속 보존한다.

## Workflow 및 State 변경

기존 T1 → T1.5 → 원인별 분기 → T2 → T3 → 평가 → T4 경로를 유지한다. 각 Node의 결과를 State에 반영할 때 실행 이력을 함께 누적한다. 식별 충돌이 확인된 실행은 T1.5에서 담당자 검토로 이동한다.

| 신규 State | 내용 |
|---|---|
| `case_identity` | 공고번호·차수, 원본 문서·버전, API 식별자 및 충돌 사유 |
| `execution_history` | 단계별 상태·실패 사유·사용한 근거·재시도·수정 이력 |

## 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `src/bid_review/rag/state_history.py` | 신규: 식별자·문서 버전 생성, 인용·검색 결과 대조, 실행 기록 |
| `src/bid_review/rag/workflow.py` | 식별 정보 저장·검증, 모든 Node의 실행 이력 기록, 근거 범위 검사 |
| `src/bid_review/rag/conditional_routing.py` | 식별 충돌 시 Human Review로 분기 |
| `src/bid_review/rag/hitl_review.py` | 식별 충돌을 담당자 승인으로 우회하지 못하도록 검사 |
| `src/bid_review/rag/report_export.py` | 보고서 산출물의 식별·실행 이력 JSON 및 진단 표시 |
| `scripts/run_hitl_review.py` | 중단·재개 실행 폴더에 식별·실행 이력 JSON 저장 |
| `tests/test_state_history.py` | 신규: 공고·실행·버전 분리, 혼입 차단, 실행 이력, 중단·재개 연결 |

## 실행 및 검증 방법

프로젝트 루트에서 보완 1~4가 적용된 `.venv`를 활성화한 후 실행한다.

```bash
python tests/test_state_history.py
python tests/test_conditional_routing.py
python tests/test_hitl.py
python tests/test_agent23_evaluation.py
python tests/test_t4_report.py
python tests/test_report_validation.py
python tests/test_report_export.py
```

사용자 Mac의 `.venv`에서 신규 8개와 기존 관련 72개, 합계 **80개가 모두 `OK`**였고 건너뛴 테스트는 없었다. 신규 테스트에는 실제 그래프의 중단·재개 후 이력 연결 검사가 포함된다.

실제 PDF 확인은 새로운 `thread_id`로 시작한다.

```bash
python scripts/run_hitl_review.py start \
  --rfp "data/input/AI 영상 기반 상수도관 노후도 분석 및 GIS 연동 소프트웨어 개발 용역.pdf" \
  --notice-no "R26BK01762350" \
  --company "data/company/company"
```

실행 후 출력된 새 폴더의 `case_identity.json`과 `execution_history.json`을 확인한다. `paused`는 Human Review에 도착했음을 뜻하며, `case_identity.conflicts`가 비어 있는지와 `execution_history`의 마지막 Node·분기 사유를 함께 점검한다.

## 한계 및 다음 단계

1. 실제 상수도관 PDF에서 공고 ID `R26BK01762350:000`, RFP 문서 ID `c1ef595d8723e0c8`, 식별 상태 `identified`, 공고 충돌 `[]`를 확인했다. 기록된 Node는 `t1_extract` → `g2b_notice_lookup` → `t15_verify` → `t15_route`이며 실행 오류는 `[]`였다. `g2b_notice_lookup` Node 실행 기록 자체가 나라장터 API 조회 성공을 뜻하지는 않는다.
2. 테스트의 고정 입력과 대역은 실제 나라장터 응답·공고문 내용의 정확성을 보증하지 않는다.
3. 다른 공고의 근거를 걸러도, 같은 공고 안에서 서로 다른 판본의 조항 충돌은 기존 T1.5·담당자 검토로 확인해야 한다.
4. 팀 브랜치 통합 전 개인 브랜치에서 변경 파일을 병합하고 전체 테스트와 신규 실행을 확인한다.

**완료 판단:** 식별·이력 관리 코드, 관련 테스트 80개, 실제 PDF의 Human Review 이전 식별·이력 저장까지 확인했다. 검토 뒤 T2→T4 전체 경로와 서로 다른 판본의 실제 충돌 사례는 후속 통합 검증 대상으로 남긴다.
