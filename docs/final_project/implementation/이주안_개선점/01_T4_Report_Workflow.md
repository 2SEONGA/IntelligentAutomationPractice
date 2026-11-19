# 보완 1 — T4 보고서 생성·검증 연결

작성자: 이주안  
작업 시작일: 2026-11-18  
최종 수정일: 2026-11-19  
상태: **기능 구현 및 로컬 테스트 완료 — 실제 PDF의 T1→T4 전체 실행 확인은 남음**

## 요약

- **목적:** T3 유사사업 검색 이후 종료되던 LangGraph에 T4 보고서 생성과 후속 검증을 연결하고, 보고서·검증 결과·근거 자료를 파일로 저장한다.
- **구현 결과:** 기존 `ReportWriter`를 재사용하는 T4 Node, 구조화 보고서 검증 Node, 실행별 산출물 저장 기능을 추가했다.
- **검증 결과:** 생성·연결 10개, 보고서 검증 13개, 파일 저장 12개 등 **관련 테스트 35개가 로컬에서 모두 통과**했다.
- **실제 자료 확인:** 상수도관 RFP는 T1.5에서 `human_review_required`로 종료됐다. 전체 State와 진단 파일 저장은 확인했으며, 해당 PDF의 T4 생성·검증·보고서 저장 경로는 아직 확인하지 못했다.

## 수정 전 동작

기존 LangGraph는 T1 추출, 나라장터 공고 조회, T1.5 검증을 수행하고, 검증 결과에 따라 추가 검색·재검증 또는 담당자 검토로 분기했다. 후속 분석이 가능한 경우 T2 회사 역량 분석과 T3 유사사업 검색을 순차 실행한 뒤 종료했다.

- LangGraph 내부에 최종 보고서를 생성하는 T4 Node가 없었다.
- T3는 검색 후보 목록을 반환했으며, 기존 보고서 생성기가 요구하는 시장분석 모델로 변환하는 연결이 필요했다.
- 담당자 검토 경로는 확인 필요 상태를 기록한 뒤 종료했다. 중단·재개 기능은 이번 보완 범위에 포함하지 않았다.

수정 전 실행 결과는 `outputs/langgraph_baseline/water_before.json`에 저장했다.

## 변경 내용

### 1. T3 검색 결과를 보고서 입력 형식으로 변환

`report_adapter.py`에 `build_market_result()`를 추가했다.

- T3 검색 후보를 기존 `MarketResearchResult`·`SimilarProject` 모델로 변환한다.
- 사업명·공고 출처 URL·유사도 값을 검사하고, 누락 또는 잘못된 값은 사유와 함께 기록한다.
- 동일 사업·재공고 의심 후보는 비교 목록에서 제외한다.
- 검색 실패와 후보 없음을 구분하고, 미확인 사항과 해석 한계를 보존한다.
- 낙찰정보의 공고·차수·출처 연결을 별도로 검증하기 전에는 낙찰자·낙찰금액을 반영하지 않는다.

### 2. T4 보고서 생성 Node 연결

`report_nodes.py`의 `generate_t4_report()`를 `workflow.py`에 `t4_report` Node로 등록했다.

- 기존 규칙 기반 `ReportWriter.write()`를 재사용한다. 이번 작업에서 별도의 LLM 보고서 작성 호출을 추가하지는 않았다.
- T1.5 검증 통과 또는 사유가 있는 담당자 승인 여부와 T2 완료 상태를 다시 확인한다.
- RFP 분석·회사 역량 분석·변환된 시장분석 결과로 보고서 초안을 생성한다.
- 요약 보고서는 `report`에, 사업금액·참가자격·회사 분석·시장분석과 근거를 포함한 상세 자료는 `report_details`에 저장한다.
- 담당자 승인으로 진행한 경우에도 기존 미확인 항목이 자동 확정된 것으로 표현하지 않는다.
- 사업본부장의 최종 결정은 기존 모델 정책대로 `미결정`으로 유지한다.

### 3. 보고서 검증 Node 추가

`report_validation.py`의 `validate_t4_report()`를 `t4_validate` Node로 연결했다.

검사 항목은 다음과 같다.

- 기존 분석 모델과 보고서 모델의 형식 적합성
- 사업 개요·기본정보·참가자격·회사 분석·시장분석이 보고서 상세에 보존됐는지 여부
- 요약 보고서의 사업금액과 T1 사업금액의 일치 여부
- 금액 근거 문서·페이지·발췌의 보존 여부
- 유사사업 정보와 미확인 사항이 원래 T3 결과와 일치하는지 여부
- 필수 요약 영역과 허용된 근거 참조 식별자의 존재 여부
- 담당자 확인사항과 회사 증빙의 한계가 남아 있는지 여부

**검증 범위는 구조화 데이터의 일치와 근거 보존이다.** 자연어 요약 전체의 의미, 원문의 진위, 실제 수행실적의 적격성 또는 법적 참가자격 충족을 확정하는 검사는 아니다.

### 4. 보고서·검증 결과·근거 파일 저장

`report_export.py`의 `export_review_artifacts()`와 실행 CLI를 연결했다.

- 저장 직전에 보고서 검증을 다시 수행한다.
- 실행마다 새 하위 폴더를 만들어 이전 실행의 산출물을 보존한다.
- `passed`·`needs_review`이면 상태를 명시한 Markdown 보고서와 JSON·근거 파일을 저장한다.
- `failed`·`not_run`이면 보고서 파일을 만들지 않고 State·검증 결과·진단 파일을 저장한다.
- 보고서에서 참조하는 T1·T2·시장분석 JSON을 실제 `debug/` 파일로 저장한다.

## Workflow 및 State 변경

후속 분석 경로를 다음과 같이 확장했다.

**T2 회사 역량 분석 → T3 유사사업 검색 → T4 보고서 생성 → 보고서 검증 → 종료**

T1.5의 추가 검색·재검증·담당자 검토 경로는 유지했다. 보고서 검증 결과는 State에 기록하며, 검증 실패 후 자동 재작성·사람 검토로 재분기하는 기능은 이번 단계에서 추가하지 않았다.

| State 항목 | 내용 |
|---|---|
| `market` | 보고서 입력 형식으로 변환한 시장·유사사업 결과 |
| `report` | 기존 보고서 모델에 따른 요약·권고 |
| `report_details` | 사업 개요·참가자격·회사 분석·시장분석과 근거 |
| `t4_status` | T4 생성·검증 진행 상태 |
| `report_validation` | 항목별 검사 결과·종합 상태·검사 범위 |

보고서 검증 상태는 다음처럼 구분한다.

| 상태 | 의미 |
|---|---|
| `passed` | 검사 범위 내 구조화 값·근거 보존 확인 |
| `needs_review` | 값 대조 후에도 근거·판단 보완이 필요한 항목이 남음 |
| `failed` | 값 불일치·필수 항목 누락·모델 형식 오류 등 발견 |
| `not_run` | T4 미도달 또는 생성 차단·실패로 내용 검증 미실행 |

`generated_pending_validation`은 보고서 생성 직후의 중간 상태다. 검증이 실행되면 `t4_status`는 결과에 따라 `generated_consistency_checked`, `generated_review_required`, `generated_validation_failed`로 변경된다.

## 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `src/bid_review/rag/report_adapter.py` | 신규: T3 검색 후보를 보고서 입력 모델로 변환 |
| `src/bid_review/rag/report_nodes.py` | 신규: 기존 ReportWriter 호출 및 보고서 상세 보존 |
| `src/bid_review/rag/report_validation.py` | 신규: 구조화 데이터 일치·근거 보존 검사 |
| `src/bid_review/rag/report_export.py` | 신규: 보고서·검증·근거·진단 파일 저장 |
| `src/bid_review/rag/workflow.py` | State 항목과 T4 생성·검증 Node 및 연결 추가 |
| `scripts/run_langgraph_review.py` | 실행 후 산출물 저장·상태 출력, 공고문 PDF 입력 옵션 추가 |
| `tests/test_t4_report.py` | 신규: 생성·차단·State 보존·그래프 연결 검사 |
| `tests/test_report_validation.py` | 신규: 데이터 변경·근거 누락 등 검증 검사 |
| `tests/test_report_export.py` | 신규: 저장·상태 표시·실행별 분리·CLI 연결 검사 |

## 실행 방법

프로젝트 루트에서 가상환경을 활성화하고 실행한다. 최초 실행 전에는 검색 코퍼스와 SQLite 색인이 준비돼 있어야 한다.

### 로컬 테스트

```bash
python tests/test_t4_report.py
python tests/test_report_validation.py
python tests/test_report_export.py
```

테스트는 고정 입력과 외부 호출 대역을 사용하며, 실제 OpenAI·나라장터 응답 품질을 평가하지 않는다.

### 실제 RFP 실행

아래는 로컬에서 실행 확인한 입력 구성이다. 별도 공고문·공고번호·회사 자료를 지정하지 않은 비교 사례다.

```bash
python scripts/run_langgraph_review.py \
  --rfp "data/input/AI 영상 기반 상수도관 노후도 분석 및 GIS 연동 소프트웨어 개발 용역.pdf" \
  --output "outputs/langgraph_baseline/water_after.json"
```

필요하면 `--notice-pdf`, `--notice-no`, `--notice-order`, `--company` 옵션으로 같은 공고의 추가 입력을 지정한다. 이 옵션을 모두 넣은 실제 실행 결과는 이번 기록에서 확인하지 않았다.

## 산출물

`--output` 경로에는 전체 State를 저장하고, 해당 파일명에 `_reports`를 붙인 폴더 아래 실행별 `review_*` 폴더를 생성한다.

상수도관 사례의 저장 위치는 다음과 같다.

```text
outputs/langgraph_baseline/water_after.json
outputs/langgraph_baseline/water_after_reports/review_*/
```

| 조건 | 실행별 저장 파일 |
|---|---|
| 모든 정상 반환 사례 | `state.json`, `report_validation.json`, `diagnostic.md`, `manifest.json` |
| 보고서 검증이 `passed` 또는 `needs_review` | 위 파일과 함께 `report.md`, `report.json`, `report_details.json`, `debug/01_rfp_analysis.json`, `debug/02_capability_matching.json`, `debug/03_market_research.json` |

`needs_review` 보고서는 미확인 사항이 포함된 사전 검토 자료이며, 검토 완료본 또는 최종 입찰 결정이 아니다.

## 검증 결과

### 로컬 테스트

| 실행 명령 | 통과 수 | 확인 범위 |
|---|---:|---|
| `python tests/test_t4_report.py` | 10개 | 보고서 생성·입력 차단·후보 해석 유지·그래프 연결 |
| `python tests/test_report_validation.py` | 13개 | 금액·점수·근거 변경, 항목 누락, 미확인 사항 삭제, State 보존 |
| `python tests/test_report_export.py` | 12개 | 보고서·근거 저장, 검증 실패·미실행 처리, 기존 결과 보존, CLI 연결 |
| **합계** | **35개** | **모두 `OK` 확인** |

### 실제 상수도관 RFP

- 최종 실행 처리 상태: `human_review_required`
- T1.5 상태: `human_review`
- T4 생성 상태: `not_run`
- 보고서 검증 상태: `not_run`
- `water_after.json`과 실행별 `diagnostic.md` 저장 확인
- 금액 범위·참가자격 해석·일정 등 확인 필요 사항으로 후속 분석에 진입하지 않음

이 결과는 **실제 자료에서 검증 경로를 유지하고 진단을 저장한 동작 확인**이다. 해당 PDF로 T1부터 T4까지 모두 실행했다고 해석하지 않는다.

## 한계 및 다음 단계

1. **실제 자료의 전체 경로 확인:** 고정 입력으로 T4 생성·검증·저장을 확인했지만, 실제 상수도관 PDF는 T1.5에서 종료됐다. 입력 보완과 담당자 검토를 거친 후 후속 실행을 확인해야 한다.
2. **검증 범위:** 구조화 값·근거 보존을 검사한다. 자연어 전체의 의미와 원문 사실의 정확성은 별도 검토가 필요하다.
3. **앞 단계 결과의 영향:** T1이 잘못 선택한 값을 보고서가 그대로 보존하면 전달 일치 검사를 통과할 수 있다. 공동도급 조건 선택, 금액 기준, 날짜 근거 선택 등 T1·T1.5 보완은 관련 담당자와 함께 진행한다.
4. **다음 보완:** 보완 2에서 Checkpointer·`interrupt()`·`Command(resume=...)`를 적용해 담당자 승인·수정·반려 후 저장된 실행을 이어간다. 수정한 값은 관련 검증을 다시 수행하도록 설계한다.

**완료 판단:** 보완 1의 T4 생성·구조화 보고서 검증·파일 저장 기능과 로컬 테스트는 완료했다. 실제 PDF의 T1→T4 전체 실행 확인은 후속 통합 검증으로 남긴다.
