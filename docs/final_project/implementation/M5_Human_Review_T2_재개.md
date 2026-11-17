# M5 구현 기록 — 사람 검토 승인 및 T2 재개

상태: API·웹 화면 코드 작성, 정적/빌드 확인 완료. 실제 승인 요청으로 회사 분석을 실행하는 업무 검토는 미실행  
작성일: 2026-11-12

## 요약

- **한 일:** 담당자가 사유를 남기고 승인하면 T2를 실행, 반려하면 종료하는 경로와 감사 기록(API·검토 탭)을 구현했다.
- **결과:** 승인은 T1.5 판정을 바꾸지 않고 override로 따로 기록. 결재 상신은 T1.5+T2 완료 시에만 허용.
- **남은 일:** 실제 승인 실행, 사내 인증(M8), LangGraph checkpoint 재개 방식은 아님.

## 목표

T1.5가 `human_review_required`로 종료되면 담당자가 페이지 인용과 미해결 사유를 확인한다. 담당자가 이유를 남기고 승인한 경우에만, 기존 T1 추출 결과로 T2 회사 역량 분석을 이어서 수행한다. 반려하면 T2는 실행하지 않는다.

## 변경 내용

- `src/bid_review/rag/workflow.py`
  - 그래프 State에 사람 결정(`approved`/`rejected`), 검토자, 사유 추가
  - T1.5 검증이 미완료여도 사람 승인과 사유가 있을 때만 T2 진입 허용
  - T2 결과에 `human_override` 기록을 남기고 T1.5 원래 판정 상태를 그대로 보존
  - 사람 반려 경로는 `human_rejected`로 끝나며 capability 분석 결과를 만들지 않음
- `src/bid_review/web/app.py`
  - `POST /api/runs/{run_id}/core-review`에 검증 실행·승인 후 T2·반려 동작 추가
  - 담당자(staff) 역할, 최신 보고서, 검토 가능한 상태, 필수 사유 확인
  - 승인 전에 PDF SHA 기반 document ID가 기존 T1.5 결과와 같은지 확인. RFP 원본이 바뀌었으면 재검증 요구
- `src/bid_review/web/runs.py`
  - `langgraph_review.json`과 `core_review_audit.json` 저장
  - 감사 이력에 실행 파일, 동작, 역할, 사유, 시각, T1.5 상태 기록
  - 검토 결과를 특정 보고서 파일에 묶어 새 보고서가 이전 승인을 물려받지 못하게 함
  - 결재 상신/재상신은 해당 보고서의 T1.5와 T2가 완료된 경우만 허용
- `web/src/components/tabs/CoreReviewTab.tsx`, `web/src/screens/RunResult.tsx`, `web/src/api.ts`, `web/src/types.ts`
  - 결과 화면의 `핵심정보 검증` 탭에서 범주/claim/check/citation/미해결 사항을 열람
  - 담당자가 검증 실행, 검토 사유 입력, 승인 후 T2 실행 또는 반려 가능
  - 승인 상태와 검토 이력 표시

## 업무상 의미

승인은 자동 판정을 `confirmed`로 바꾸지 않는다. T1.5의 보류·충돌 사실은 그대로 기록하고 담당자가 해당 위험을 확인한 후 T2 분석으로 넘어간 사실을 별도로 남긴다. T2 결과는 참고 분석이며, 최종 Go/보류/No-Go 결정은 기존 사업본부장 결정 절차에서 이뤄진다.

## 확인한 사항

- Python `compileall` 통과
- FastAPI 앱 생성 및 `/api/runs/{run_id}/core-review` 라우트 등록 확인
- `npm run build` 통과 (`tsc -b` 및 Vite production build)
- 실제 사람 승인 endpoint를 호출하거나 사업 분석을 승인 실행하지 않았다. 승인 판단은 담당자가 원문 근거를 확인한 후 내려야 한다.

## 남은 한계

- 역할 선택은 기존 데모와 같이 클라이언트 `X-Role` 헤더이므로 실제 인증·권한 통제가 아니다.
- 승인 후 T2 동작은 다시 T1/T1.5를 계산한 뒤 승인 State로 분기한다. LangGraph checkpoint로 중간 state를 그대로 재개하는 방식은 아니다. 원본 해시 비교로 PDF가 바뀐 경우에는 승인을 막는다.
- 담당자가 T1 필드값을 직접 수정하는 화면은 없다. 현재 승인은 추출된 T1 값을 수용하고 T2를 수행한다는 명시적 확인이다.
- T2 결과는 별도 `langgraph_review.json`에서 확인하며, 기존 보고서 생성/시장 조사/요약 리포트까지 LangGraph로 완전히 통합한 것은 아니다.
- 세 RFP의 T1.5 결과를 원문과 수동 대조하고 승인 후 T2 산출물을 검토하는 작업은 남았다.

## 다음 순서

1. 검토 화면에서 한 실제 공개 RFP에 대해 T1.5를 실행하고 근거 인용/검색 결과를 수동 대조한다.
2. 담당자 검토 사유를 넣어 T2 분석을 승인 실행하고 capability JSON과 audit를 확인한다.
3. 실제 값 수정 요구가 있으면 후보 교정 인터페이스와 재검증 규칙을 추가한다.
4. 의미 검색 공급자 선택 후 LlamaIndex/임베딩을 연결한다.
5. 최종 웹 업무 경로가 LangGraph 한 곳에서 시작·완료되도록 기존 CLI/CrewAI 실행 경로를 정리한다.
