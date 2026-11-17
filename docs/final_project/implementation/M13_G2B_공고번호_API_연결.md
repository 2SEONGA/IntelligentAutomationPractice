# M13 G2B 공고번호 단건 조회 연결 기록

작성일: 2026-11-16  
상태: 코드와 웹 검토 항목 연결 완료. 현재 실행환경의 네트워크 차단으로 실시간 API 응답은 확인하지 못함.

## 요약

- **한 일:** 공고번호·차수 단건 API 조회 노드, 금액·입찰마감 필드 citation, 첨부 링크 UI를 구현했다.
- **결과:** 빌드는 통과했으나 당시 라이브 호출은 ConnectionError. 이후 M15에서 엔드포인트 이름 오류(`02` 접미사 누락)를 발견·수정해 라이브 동작 확인.
- **남은 일:** 없음(M15에서 해소).

## 목표

T1.5가 공고번호와 차수로 나라장터 구조화 공고 레코드를 직접 조회하고, 공고별 링크·API 명세·첨부 링크를 근거로 제시하게 한다. `bidClseDt`는 입찰 마감으로만 분류하고 제안서 제출기한과 동일시하지 않는다.

## 변경 내용

- `src/bid_review/web/notices.py`
  - 공식 서비스 Base URL을 `BidPublicInfoService02`로 변경했다.
  - 날짜 범위 조회 제한을 7일로 맞췄다.
  - `fetch_notice_by_number(bid_ntce_no, bid_ntce_ord)`를 추가해 공고번호·차수 단건 조회, 공고 상세 URL, 배정예산·추정가격·입찰마감 및 API 첨부 링크를 반환한다.
  - 서비스 키는 프로세스 안에서만 사용하며 반환 데이터/오류에 포함하지 않는다.
- `src/bid_review/rag/workflow.py`
  - `t1_extract → g2b_notice_lookup → t15_verify` 노드를 연결했다.
  - 조회 성공·미발견·접근 불가를 State에 기록한다. 조회 실패 시 로컬 캐시를 최신 API 값처럼 재사용하지 않는다.
- `src/bid_review/rag/verification.py`
  - API 응답의 `asignBdgtAmt`, `presmptPrce`, `bidClseDt`를 해당 공고 URL 및 API 명세 링크와 함께 citation으로 만든다.
  - API 금액 필드와 RFP 금액 종류를 혼동하지 않으며 VAT/의미 차이는 자동 확정하지 않는다.
  - `bidClseDt`를 별도 `g2b_bid_closing_time` claim으로 남기며 제안서 기한과 구분한다.
  - 조회 상태와 첨부 메타데이터를 `CoreInfoVerification`에 저장한다.
- `web/src/components/tabs/CoreReviewTab.tsx`
  - API 조회 상태, 공고별 상세 링크와 API 명세 링크를 분리 표시한다.
  - API가 반환한 공고문/제안요청서 첨부 링크를 보여주고, 파일 본문을 실제 확인하기 전까지 내용 확인 완료로 보지 않는다고 안내한다.
- `docs/final_project/우선순위.md`
  - M1~M13 요약 및 현재 상태를 갱신했다.

## 확인 결과

- 웹 `npm run build`: 성공 (`tsc -b`, Vite production build).
- 실시간 단건 조회 시도: `ConnectionError`. 서비스 키나 원 요청 URL은 출력하지 않았다.
- 자동 테스트는 추가하거나 실행하지 않았다.
- 그러므로 라이브 API 응답·첨부목록은 확인되지 않았다. 기존 로컬 notice cache의 필드값을 이번 실시간 응답으로 간주하지 않는다.

## 남은 작업

1. 네트워크가 허용되는 로컬 환경에서 해당 공고 단건 API를 재조회하고 응답 필드·첨부목록을 확인한다.
2. 첨부 공고문 PDF를 다운로드·해시 기록·RAG 색인해 `proposal_deadline`을 실제 문서 인용과 연결한다.
3. 검토 탭에서 API 상태와 첨부 링크를 확인한다. 현재 실행 환경은 localhost 포트 bind를 차단한다.
4. 담당자가 `g2b_crosscheck_budget_krw` 출처 확인을 완료하고 T2 진행을 별도로 승인하면 Agent 2 결과 화면 노출을 확인한다.
