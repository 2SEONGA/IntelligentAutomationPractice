# M3 구현 기록 — LangGraph 조건부 업무 흐름

상태: 그래프/CLI 작성 및 3개 원본 PDF 실행 확인 완료. 검토 승인 후 재개 및 웹 통합은 남음  
작성일: 2026-11-11

## 요약

- **한 일:** LangGraph로 T1 → T1.5 → (추가 검색 / 사람 검토 / T2) 조건부 workflow와 CLI를 만들었다.
- **결과:** 3개 원본에서 실행 확인. T1.5가 confirmed일 때만 T2로 진행하도록 이중 보호.
- **남은 일:** 승인 후 T2 재개(M5에서 구현), 웹 통합.

## 목표와 구성

CrewAI는 기존 프로젝트에 보존하고, 최종 업무 workflow의 제어는 LangGraph 단독으로 담당하도록 한 A안을 반영한다. 기존 T1 추출기와 T2 역량 매칭기를 재사용하고 그 사이에 T1.5를 둔다.

```text
START → T1 RFP 추출 → T1.5 핵심정보 검증
                         ├─ 누락/불명확 + 재검색 횟수 남음 → 근거 추가 검색 → T1.5 재검증
                         ├─ T1.5 모든 범주 확인 완료 → T2 회사 역량 매칭 → END
                         └─ 충돌/의미 판단/재검색 한도 → 사람 검토 → END
```

## 변경 파일

- `src/bid_review/rag/workflow.py`: StateGraph 정의, 노드·조건부 edge, T1/T1.5/T2 서비스 연결
- `scripts/run_langgraph_review.py`: 단일 RFP 입력, 선택적 회사 profile/공고번호 입력, 결과 JSON 저장 및 상태 요약
- `pyproject.toml`: `langgraph>=1.0,<2` 의존성 추가
- `requirements-windows-py312.txt`: Windows 설치 목록에도 LangGraph 포함

## State와 전이

- 입력: `pdf_path`, `company_path`, `notice_no`, `retrieval_limit`
- 산출/추적: T1 결과 `rfp`, 범주별 T1.5 결과 `verification`, 추가 검색 `supplemental_evidence`, `retrieval_round`, `decision`, `routing_reasons`, T2 `capability`
- 반복 검색: 미확인 또는 추가 근거가 필요한 T1.5 claim의 검색어를 모아 원본 PDF ID에 한정하여 검색한다. 최대 재검색 횟수는 CLI에서 0~3회로 제한한다. 검색 hit를 claim과 연결해 citation으로 남기며, 새로운 값/법적 조건을 추출한 것으로 오인하지 않게 해당 건은 사람 검토로 바꾼다.
- T2 보호: 모든 범주의 coverage가 확인되고 T1.5 종합 상태가 `confirmed`일 때만 회사 분석을 실행한다. T2 함수 안에서도 동일 상태를 재확인한다.
- 종료: T2 완료 또는 담당자 검토 필요 상태로 종료한다.

## 실행 예시

먼저 프로젝트 의존성을 설치하고 M1 검색 코퍼스/SQLite 색인을 생성한 뒤 실행한다.

```bash
.venv/bin/python scripts/build_rag_corpus.py
.venv/bin/python scripts/build_rag_search_index.py
.venv/bin/python scripts/run_langgraph_review.py \
  --rfp data/input/원본파일.pdf \
  --company data/company/company.json \
  --output outputs/langgraph_review.json
```

## 확인한 사항과 제한

- `compileall` 문법 확인은 통과했다.
- 현재 `.venv`에 LangGraph 1.2.20이 설치되어 있고 `StateGraph` import 및 graph compile/invoke가 동작했다. 사용자가 확인한 `langgraph.__file__ is None`은 설치 오류가 아니다. Python이 `langgraph` namespace package로 읽어서 파일 경로 속성은 비어 있고, `pip show langgraph`에서 설치 경로와 버전이 확인된다.
- 실제 RFP 3건 모두 graph에 입력했다. 세 건 모두 오류 없이 실행해 담당자 검토로 종료했고, T1.5 후 추가 검색 1회를 수행했다. 범주 상태는 세 건 모두 사람 검토가 필요한 상태이며, T2는 검증 gate에 의해 실행되지 않았다. 결과는 `outputs/langgraph_reviews/*.json`에 저장했다.
- 이 실행은 graph 경로의 동작 확인이며, T1.5 판정 정확도 평가는 아니다.
- 현재는 담당자가 JSON을 검토하고 승인/수정/반려한 뒤 LangGraph를 재개하는 Human-in-the-Loop UI/명령이 없다. 지금은 검토 분기와 사유를 JSON으로 저장하고 종료한다.
- 추가 검색 evidence는 재검증 추적에 함께 보존하지만, 새로운 숫자/법적 조건을 검색 결과만으로 자동 추출·확정하지 않는다.
- 검색은 현재 SQLite FTS 기반이다. 임베딩/의미 검색과 LlamaIndex 통합은 비용/실행환경 선택 후 진행한다.

## 다음 단계

담당자의 검토 결정을 기록하고 승인 후 T2를 재개하는 기능을 만든다. 이후 3개 PDF별 결과를 수동 대조해 후보 누락, 과도한 보류, 충돌 처리를 보정한 다음 기존 웹 검토화면에 T1.5 근거·상태·사람 검토 사유를 연결한다.
