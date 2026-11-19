# 보완 3 — Agent 2·3 후속 분석 근거 평가

작성자: 이주안  
작성일: 2026-11-19  
상태: 구현 및 로컬 단위 테스트 완료; 실제 RFP 경로는 프로젝트 환경에서 확인 필요

## 요약

T2 회사 역량 분석과 T3 유사사업 검색 사이의 결과를 T4에 전달하기 전에 `t23_evaluate` Node로 평가한다. `agent23_evaluation` State에 항목별 결과를 기록하고, T4 초안과 파일에도 전달한다.

- T2: 회사 분석 법인명과 제공 회사 원자료의 법인명을 대조하고, `충족` 판단에 사용한 `record_id`·문서·절·발췌 및 수행실적의 발주처·기간·금액·범위 및 개별 실적의 `performing_legal_entity`를 확인한다. 개별 수행 법인 정보가 없으면 `needs_review`다. 법적 자격이나 증빙 원본의 진위를 확정하지 않는다.
- T3: 검색 후보의 공고 식별자·공고 URL·보관된 제안요청서 원문 발췌를 대조한다. 유사도는 관련성의 최종 판정이 아니므로 담당자 검토 대상으로 남긴다. 기존 색인에는 낙찰 결과의 공고·차수·출처 연결 정보가 없어, 낙찰정보가 붙은 후보는 `needs_review`로 표시한다. 보고서에는 낙찰자·낙찰금액을 계속 반영하지 않는다.
- 근거 부족: 평가 상태 `needs_review`, 보고서 사전 권고 `보류`, 항목별 사유 표시. T1.5 승인 여부 및 T4 구조화 검증과 구분한다.

## 변경 파일

- `src/bid_review/rag/agent23_evaluation.py`: 후속 분석 평가 Node
- `src/bid_review/rag/workflow.py`: T3 → 평가 → T4 연결, State 추가
- `src/bid_review/rag/report_nodes.py`: 평가 결과를 보고서 상세에 보존하고 근거 부족 시 보류 표시
- `src/bid_review/rag/report_validation.py`: 평가 결과 전달·검토 상태 대조
- `src/bid_review/rag/report_export.py`: 평가 파일 및 보고서의 검토 사유 저장
- `tests/test_agent23_evaluation.py`: 단위·보고서 연동 테스트
- `tests/test_t4_report.py`: 새 그래프 연결 확인

## 확인

```bash
python tests/test_agent23_evaluation.py
python tests/test_t4_report.py
python tests/test_report_validation.py
python tests/test_hitl.py
```

단위 테스트 10개는 외부 API 없이 통과했다. 위 나머지 기존 테스트와 실제 RFP 실행은 사용자 프로젝트에서 확인한다. 상수도관 RFP가 T1.5에서 중단된다면 평가 Node가 미실행인 것이 정상이다. T1.5 검증을 우회하여 T2·T3·평가를 실행하지 않는다.
보완 3 구현 및 기능 테스트 완료. 실제 RFP의 T2·T3 평가 경로 실행 확인은 T1.5 검토 이후 진행 예정.
