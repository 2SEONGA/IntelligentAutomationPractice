# M1 구현 기록 — RAG 코퍼스와 로컬 기초 검색

상태: 완료  
작성일: 2026-11-11

## 요약

- **한 일:** 원본 RFP 3건을 페이지·절·표 단위로 분할하고 연락처 행을 제거해 SQLite FTS5 키워드 검색 색인을 구축했다.
- **결과:** 848 chunk(본문 813 + 나라장터 metadata 35), 페이지·공고번호 인용형 검색 CLI 동작.
- **남은 일:** 의미 검색은 M4에서 추가됨, 공고 첨부 판본 대조는 M18에서 RFP 3건 바이트 동일 확인, OCR·개인정보 추가 점검.

## 목표

원본 RFP에서 페이지·섹션·표 근거를 보존한 검색 코퍼스를 만들고, 문서 근거를 찾아 페이지 인용으로 반환하는 로컬 검색 경로를 구현한다. 외부 임베딩/생성 API 없이 자료 구조와 키워드 검색 기반을 먼저 완성한다.

## 변경 파일

- `src/bid_review/rag/corpus.py`: 기존 PDF 페이지 추출을 재사용, 연락처 정보 필터링, 조항·표 chunk 생성, 해시·페이지·공고판본 메타데이터 기록
- `src/bid_review/rag/retriever.py`: SQLite FTS5 색인 생성과 한국어/영어 토큰 검색, 문서/공고 ID 필터, 출처 chunk 반환
- `scripts/build_rag_corpus.py`: 원본 PDF에서 JSONL 코퍼스와 manifest 생성
- `scripts/build_rag_search_index.py`: 코퍼스의 로컬 키워드 검색 DB 생성
- `scripts/search_rag.py`: 페이지/절 인용이 포함된 검색 결과 CLI
- `config/rag_sources.yaml`: 원본 PDF와 관련 나라장터 공고번호·차수·제목 연결
- 생성물: `outputs/rag/chunks.jsonl`, `outputs/rag/manifest.json`, `outputs/rag/retrieval.sqlite3`

## 생성 결과

- 원본 PDF: 3건, 총 176페이지
- 코퍼스: 848개 chunk (PDF 본문 813개 + 나라장터 구조화 필드 35개)
- 연락처 행 제거: 44건
- 색인 실패: 0건
- 생성된 코퍼스에서 전화번호/이메일 형식 문자열 추가 점검: 0건 발견
- 해당 3건의 나라장터 공고번호를 연결함. 공고 첨부의 실제 PDF 본문은 아직 다운로드·색인하지 않았으며, 제공된 원본과 최신/수정 첨부가 동일 판본이라고 간주하지 않음. manifest의 `source_count`는 PDF와 구조화 공고 metadata의 합계, `pdf_source_count`는 PDF만 센다.

## 직접 사용 예

```bash
.venv/bin/python scripts/build_rag_corpus.py
.venv/bin/python scripts/build_rag_search_index.py
.venv/bin/python scripts/search_rag.py '사업예산 부가가치세' --limit 2
```

금액 질의는 “사업예산/부가가치세”를 포함한 원문 chunk를 찾아 페이지·절·공고번호와 함께 반환했다. 참가자격·공동수급·제안서 제출 일정 검색도 각 문서의 관련 페이지를 반환하는 것을 확인했다. 이 검색은 현재 정확한 단어에 의존하는 기초 검색이며, 의미 유사어 검색과 범주별 verifier는 다음 마일스톤에서 연결한다.

## 설계 근거

- PDF 추출은 기존 `RFPExtractor._extract_pages`를 재사용해 페이지 품질 경고와 표 행을 유지한다.
- 전화·이메일과 연락처 라벨이 있는 행은 검색 텍스트에서 제외한다. 원본 PDF는 수정하지 않는다.
- 공고 첨부 파일 URL은 출처 연결용 메타데이터이며, 해당 URL을 내려받았거나 페이지 근거로 검증한 것처럼 표현하지 않는다.
- 로컬 검색은 비용 없는 기초 검색이다. 임베딩 기반 검색은 embedding provider 선택 뒤 붙인다.
- LangGraph graph 구현은 M3에서 진행한다. 의미 임베딩과 LlamaIndex는 provider 결정 후 별도 연결한다: [LangGraph Python Quickstart](https://docs.langchain.com/oss/python/langgraph/quickstart), [LlamaIndex Installation](https://developers.llamaindex.ai/python/framework/getting_started/installation/), [LlamaIndex Vector Stores](https://developers.llamaindex.ai/python/framework/module_guides/storing/vector_stores/).

## 남은 작업

- LlamaIndex 검색 인덱스와 의미 검색 제공자 연결
- 원본·정정/재공고 첨부 판본을 실제 다운로드 자료와 대조
- 코드의 개인정보 필터가 이메일/전화번호 외 개인정보(예: 단독 성명)를 모두 검출한다고 보장하지 않음. 검색·발표 자료 반영 전 페이지별 점검/추가 마스킹 정책 필요
- 원본과 공고 첨부의 판본 대조 및 OCR이 필요한 이미지 페이지 확인은 미완료

## 다음 단계

M2/M3에서 상세 검증기와 LangGraph 분기 흐름을 완성한다. Embedding provider가 정해지면 LlamaIndex 의미 검색을 키워드 검색과 함께 붙인다.

