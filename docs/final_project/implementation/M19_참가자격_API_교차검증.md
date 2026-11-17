# M19 참가자격 API·공고문 교차검증

작성일: 2026-11-17  
상태: 완료 (라이브 3건 실행 확인).

## 요약

- **한 일:** 나라장터 면허(업종) 제한정보와 공동수급 방식을 RFP 참가자격 claim과 색인된 공고문(M18) 본문에 대조하는 `g2b_license_limit`, `g2b_joint_contract` claim을 T1.5 참가자격 범주에 추가했다.
- **결과:** 상수도관·통합정보시스템은 두 항목 모두 `confirmed`. CDW는 RFP에 업종 세부명이 없고 별도 공고문 PDF도 없어 `human_review`로 남았다. 테스트 15개 파일, 웹 빌드 통과.
- **남은 일:** 면허 그룹(`lmtGrpNo`)의 AND/OR 의미와 `bidPrtcptLmtYn`(참가제한 여부) 의미는 해석하지 않음. 지역 제한은 이번 3건에 해당 행이 없어 미구현.

## 변경 내용

- `src/bid_review/web/notices.py`
  - `fetch_notice_license_limits()`: `BidPublicInfoService02/getBidPblancListInfoLicenseLimit02`, 공고번호·차수 기준. 다른 차수 행은 제외한다.
  - `fetch_notice_by_number()`가 `license_limits`를 함께 반환한다. 실패 시 `unavailable`로 기록하고 조회는 계속한다.
- `src/bid_review/rag/retriever.py`: `notice_pdf_chunks(notice_no)` — 공고의 공고문 PDF chunk 전체를 쪽 순서로 반환한다.
- `src/bid_review/rag/verification.py`: `api_eligibility_claims(notice_record, rfp_claims, notice_texts)`
  - **업종 제한:** 각 면허명(`/코드` 앞부분)이 RFP 참가자격 claim 또는 공고문 쪽 텍스트에 들어 있는지 공백을 무시하고 확인한다. 모두 있으면 `confirmed`, 하나라도 없으면 `human_review`. 업종 제한(`indstrytyLmtYn=Y`)인데 세부 면허를 못 받으면 `human_review`.
  - **공동수급:** API `cmmnSpldmdMethdNm`이 불허이고 RFP/공고문 조항도 불허("허용하지 않", "불허", "불가", "금지", "할 수 없")면 `confirmed`. 허용 여부가 엇갈리면 `conflict`. 조항을 못 찾으면 `human_review`. 둘 다 허용이면 이행방식 확인을 위해 `human_review`.
  - 두 claim 모두 공고별 상세 URL과 API 명세를 citation으로 남긴다.
- `src/bid_review/rag/workflow.py`: `license_limits`를 공고 레코드와 함께 T1.5에 전달.

## 라이브 확인 (2026-11-17)

| 공고 | API 업종 제한 | 업종 대조 | API 공동수급 | 공동수급 대조 |
|---|---|---|---|---|
| 상수도관 R26BK01762350 | 소프트웨어사업자(컴퓨터관련서비스사업)/1468 | confirmed — RFP condition_4, 공고문 p.2 | (없음)공동수급불허 | confirmed — RFP condition_7 "공동도급은 허용하지 않음" |
| 통합정보시스템 R26BK01747679 | 소프트웨어사업자(패키지소프트웨어개발.공급사업)/1426 | confirmed — 공고문 p.1 | (없음)공동수급불허 | confirmed — 공고문 p.1 "공동수급(도급 및 하도급)… 허용하지 않음" |
| CDW R26BK01751619 | 소프트웨어사업자(컴퓨터관련서비스사업)/1468 | human_review — RFP p.38은 "소프트웨어사업자 신고"만 있고 세부 업종이 없음 | (없음)공동수급불허 | human_review — RFP에서 조항 미발견 |

통합정보시스템은 RFP 참가자격 절이 아니라 **공고문에만** 업종·공동수급 조항이 있었다. M18 색인이 없었다면 두 항목 모두 담당자 확인으로 남았을 것이다.

## 구현 중 발견한 문제

같은 쪽의 공고문 chunk를 `공고문 p.N` 키로 모으면서 뒤 chunk가 앞 chunk를 덮어써, 통합정보시스템의 공동수급 조항이 누락됐다. 같은 쪽 chunk를 이어 붙이도록 고치고 테스트를 추가했다.

## 판단 원칙

- API와 문서가 일치해도 회사가 그 자격을 **충족하는지**는 판정하지 않는다(T2·담당자 영역).
- 의미가 공식적으로 확인되지 않은 플래그(`bidPrtcptLmtYn`: 상수도관은 RFP에 대기업 참여 제한이 있는데도 `N`)는 근거로 쓰지 않는다.
