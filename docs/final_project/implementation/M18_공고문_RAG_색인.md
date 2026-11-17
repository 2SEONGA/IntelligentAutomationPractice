# M18 공고문 PDF RAG 색인

작성일: 2026-11-17  
상태: 완료 (라이브 수집·색인·LangGraph 실행 확인). 검색 순위 품질은 개선 여지 있음.

## 요약

- **한 일:** 세 사례의 나라장터 공고문 PDF를 공식 첨부파일정보 API로 내려받아 `notice_pdf` 문서로 RAG에 색인하고, T1.5 추가 검색이 같은 공고번호의 공고문도 찾게 했다. 수집 중 G2B가 RFP를 "공고서(변환본)"로 표기하는 사례를 발견해 첨부 분류를 고쳤다.
- **결과:** 공고문 2건 색인(176 chunk, 총 1,024 chunk). `data/input` RFP 3건 모두 G2B 첨부와 **바이트 동일** 확인 → 코퍼스의 판본 메모가 "byte-compared 안 함"에서 "byte-identical"로 바뀜. RFP chunk 813개는 변화 없음. 테스트 15개 파일, 웹 빌드 통과.
- **남은 일:** 추가 검색이 최상위 1건만 인용으로 붙여 긴 chunk 속 정답 줄이 밀리는 경우가 있음(아래 한계). CDW는 별도 공고문 PDF가 없음.

## 변경 내용

- `scripts/fetch_notice_attachments.py` (신규): `config/rag_sources.yaml`의 각 사례에 대해 첨부정보 API를 조회하고 공고문을 `data/notice_attachments/<공고번호>-<차수>/notice.pdf`로 저장한다. `attachment.json`에 파일명·URL·문서구분·SHA-256·조회/저장 시각을 남기고, RFP 첨부는 저장하지 않고 해시만 기록한다. 실행할 때마다 이전 `notice.pdf`를 지워 잘못 분류된 파일이 남지 않게 한다.
- `src/bid_review/rag/corpus.py`
  - `index_pdf(document_type=, title=)`로 공고문을 `document_type="notice_pdf"`, 자체 `document_id`, `related_notice_no`와 함께 색인한다.
  - `data/input` RFP의 SHA-256이 G2B RFP 첨부 해시와 같으면 `source_version_note`에 대조 결과를 기록한다.
  - manifest에 `notice_pdf_source_count`와 새 첨부 정책을 기록한다.
- `src/bid_review/rag/workflow.py`: 추가 검색(`retrieve_targeted_evidence`)이 RFP 범위 검색에 더해 같은 공고번호의 `notice_pdf` chunk를 최대 3건 찾는다.
- `src/bid_review/rag/verification.py`: 기존 색인에서 API 필드 chunk를 찾는 검색의 상한을 5→50으로 늘렸다. 같은 `notice_no`의 공고문 chunk가 결과를 밀어내지 않게 하기 위함이다.
- `src/bid_review/web/notices.py`: 첨부 분류에서 **파일명이 문서구분보다 우선**하고, 이름이 모호한 PDF만 문서구분(`atchDocDivNm`)으로 정한다. 공고문 바이트 비교 후보는 최종 `kind == "notice"`만 사용한다.
- `web/src/components/tabs/CoreReviewTab.tsx`: `document_type == "notice_pdf"` 인용도 공고문 PDF 페이지로 연결한다(이전에는 RFP 페이지로 잘못 연결될 수 있었음).

## 발견한 문제: "공고서(변환본)"로 표기된 RFP

CDW(R26BK01751619)의 첨부는 `공고서` HWP와 `공고서(변환본)` PDF 두 건이고, 파일명은 모두 "CDW구축 제안요청서 수정"이다. 기존 분류는 문서구분을 우선해 이 RFP를 공고문으로 저장했다. 저장된 파일은 `data/input`의 CDW RFP와 바이트가 같았다(70쪽, SHA-256 `e8b13677…`). 이 상태로 두면 공고문 바이트 비교가 RFP를 공고문으로 보고 `byte_match`를 낼 수 있었다. 파일명 우선 규칙으로 고쳤고 테스트에 이 사례를 추가했다.

## 라이브 확인 (2026-11-17)

| 사례 | 공고문 | RFP 판본 |
|---|---|---|
| 상수도관 R26BK01762350 | 색인 75 chunk (SHA-256 `463d3304…`) | G2B 첨부와 바이트 동일 (`c1ef595d…`) |
| 통합정보시스템 R26BK01747679 | 색인 101 chunk (`efb0e363…`) | G2B 첨부와 바이트 동일 (`f0efb832…`) |
| CDW R26BK01751619 | 별도 공고문 PDF 없음 | G2B "공고서(변환본)" 첨부와 바이트 동일 (`e8b13677…`) |

실행 폴더 `notice.pdf` 없이 LangGraph를 실행해도 공고문 근거가 추가 검색 인용으로 붙는다. 상수도관은 `estimated_price_krw`·`proposal_deadline`, 통합정보시스템은 `estimated_price_krw`·`bid_deadline`에 공고문 페이지 인용이 붙었고, 상태는 모두 `human_review`로 남았다(자동 확정 없음).

## 한계

- 재검증은 검색어별 최상위 1건만 인용으로 붙인다. 상수도관 `proposal_deadline`에는 p.1 "제안서 제출 마감일시" 줄 대신 p.3 제출 유의사항이 붙었다. p.1은 "입찰에 부치는 사항" 전체가 한 chunk여서 BM25 점수가 낮게 나온다. 정답 줄은 M14 공고문 추출 경로(실행 폴더 `notice.pdf`)로는 정확히 잡히고 있다. 개선하려면 공고문 표 항목(`o …`)을 행 단위로 쪼개거나 인용 후보를 여러 건 붙이면 된다.
- 공고문 재수집은 수동이다(`scripts/fetch_notice_attachments.py` 실행 → `build_rag_corpus.py` → `build_rag_search_index.py`).

## 실행

```bash
.venv/bin/python scripts/fetch_notice_attachments.py
.venv/bin/python scripts/build_rag_corpus.py
.venv/bin/python scripts/build_rag_search_index.py
```
