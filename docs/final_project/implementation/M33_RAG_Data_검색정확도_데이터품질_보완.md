# M33 RAG / Data 보완 — 검색 정확도 · 전처리 · OCR · 공동도급 · VAT

작성일: 2026-11-19 · 담당: 송하연(RAG / Data) · 브랜치: `data`

## 요약

- **가장 큰 문제였던 것:** SQLite FTS의 `unicode61` 토크나이저는 한국어를 띄어쓰기 단위로만 자른다. 그래서 `사 업 금 액`, `계약기간은`, `원(부가가치세` 같은 원문을 `사업금액`·`계약기간`·`부가가치세`로 찾지 못했다. T1.5 재검색어 55개 중 30개가 결과 0건이었다.
- **고친 것:** 공백을 없앤 정규화 본문 위에 trigram 색인을 추가하고, 같은 뜻의 표기를 묶고, 목차·빈 서식을 감점하는 재정렬을 넣었다. 기존 `search_keyword()` 반환 형식은 그대로다.
- **결과(같은 5개 문서·55개 질의):** Evidence Recall@3이 0.327에서 0.927로, Precision@3이 0.139에서 0.370으로 올랐다(문서 구조상 달성 가능한 최대 0.509). 인용 쪽 정확도는 전·후 모두 1.0이다.
- **평가하지 못한 것:** 의미 검색(OpenAI 키 없음), 실제 OCR 정확도(OCR 엔진 미설치), T2·T3 의미 검색 결과(임베딩 필요). 아래 "평가 한계"에 숫자 없이 적었다.

## 1. 데이터 흐름 (확인 결과)

```
data/input/*.pdf, data/notice_attachments/*/notice.pdf
  → agent01_rfp.RFPExtractor._extract_pages  (pypdf·pdfplumber, OCR 선택)
  → rag/corpus.index_pdf  (머리글·쪽번호 정리 → 개인정보 필터 → 절 분할·병합 → 메타데이터)   outputs/rag/chunks.jsonl
  → rag/retriever.build_keyword_index  (evidence_fts + evidence_compact)                 outputs/rag/retrieval.sqlite3
  → rag/semantic.build_semantic_index  (OpenAI 임베딩)                                    outputs/rag/embeddings.npy
  → T1.5 verification._find_quote / _keyword_pages / workflow.retrieve_targeted_evidence (search_keyword, 보조 search_semantic)
  → T2 workflow → company_retriever (회사 자료 별도 색인)
  → T3 workflow → past_projects.search_similar_projects (과거 사업 별도 색인)
```

## 2. 변경 내용

### 2.1 검색 (`rag/retriever.py`)

| 항목 | 수정 전 | 수정 후 |
|---|---|---|
| 후보 찾기 | unicode61 토큰 완전 일치 | 공백·기호를 뺀 정규화 본문(`compact_text`)에 trigram FTS(`evidence_compact`) 부분 일치 |
| 검색어 | 입력 그대로 | 같은 뜻 표기를 OR로 묶음: 부가가치세·부가세·VAT / 공동수급·공동도급·공동계약 / 계약·사업·과업·수행·용역기간 / 사업금액·예산·사업비 |
| 정렬 | bm25 | 검색어 출현 + 검색어 바로 뒤 값(금액·날짜·허용/불허·포함/별도) 가점, 목차 −6·빈 서식 −4·표지 −1 감점(삭제는 하지 않음) |
| 중복 | 그대로 | 같은 문서·같은 쪽에서 내용이 같거나 포함되는 chunk(본문/표 행)는 하나만 |
| 범위 | document_id, notice_no | + `document_type`(rfp_pdf/notice_pdf/g2b_notice_metadata), `document_version`(공고 차수) |

- 예전 DB(`evidence_compact` 없음)는 기존 bm25 검색 뒤 같은 재정렬로 동작한다. 그러나 정확도 개선을 받으려면 색인을 다시 만들어야 한다(아래 5장).
- 호출부(`workflow.py`, `verification.py`)는 고치지 않았다. 같은 함수를 부르므로 개선이 그대로 전달된다.

### 2.2 전처리·청크 (`rag/corpus.py`, `agent01_rfp._is_toc_page`)

- **목차 판정:** 예전에는 "목차"라는 단어가 쪽 어디에 있든 숫자 5개 이상이면 목차로 봤다. 그래서 "제안서는 목차에 따라 작성" 같은 본문 5개 쪽을 목차로 오분류했고, 정작 `- 목 차 -` 형식의 실제 목차(상수도관 2·25쪽, CDW 2·3쪽)는 놓쳤다. 이제 단독 표제 줄이 있거나, 점선·쪽 번호로 끝나는 줄이 30% 이상일 때만 목차로 본다.
- **쪽 번호·반복 머리글/바닥글 제거:** 5개 문서에서 쪽 번호 줄 201개, 반복 머리글·바닥글 69줄을 지웠다(152,034자 → 149,975자). 참가자격·공동도급·계약·금액·기간·제n조 같은 말이 든 줄은 반복되더라도 지우지 않는다.
- **chunk 역할(`retrieval_role`):** body / toc / form_template / cover. 지우지 않고 표시만 하며, 정렬에서 감점한다. 결과는 body 326, toc 12, form_template 18, cover 8개다.
- **짧은 절 병합:** 같은 쪽의 짧은 절을 600자까지 이어 붙인다(최대 1,200자, 표 chunk는 따로). chunk 수는 992개에서 364개로, 중앙값은 75자에서 578자로, 30자 미만 chunk는 230개에서 3개로 바뀌었다. 상수도관 3쪽은 이제 사업명·사업금액(부가가치세 별도)·과업기간·사업범위가 한 chunk에 있다.
- **메타데이터 추가:** `source_file`, `document_version`(공고 차수, 없으면 파일 해시 앞 8자), `section_path`, `chunk_index`, `retrieval_role`, `chunk_schema_version=2`, `ocr_applied`. 기존 필드는 모두 유지했다.
- **정제 전후 비교:** `manifest.json`의 `cleaning`(줄·글자 수)과 `retrieval_roles`, 문서별 `cleaning.repeated_lines`로 확인할 수 있다.
- **T3 공통 점검(`text_quality_flags`, `scripts/check_past_project_profiles.py`):** 읽기 전용이다. 과거 사업 152개 프로필 중 12개를 검토 대상으로 표시했다(계약 조항 비중이 큰 것 5, 300자 미만 6, 읽기 오류 1). 원본·색인은 고치지 않았다.
- **T3 PDF 읽기 오류:** `past_projects._rfp_pages`는 pdfminer가 한 쪽의 글꼴(DescendantFonts)을 못 읽으면 문서 전체에서 예외를 냈다(R26BK01765343). 이제 쪽 단위로 pypdf를 다시 시도하고, 그래도 안 되면 빈 쪽으로 둔다.

### 2.3 OCR (`ocr.py`, `agent01_rfp._try_ocr`, `models.PageAnalysis`)

- **채택 기준:** 이전에는 "의미 있는 글자가 20% 이상 많으면 채택"했다. 이제 다음을 모두 만족해야 채택한다.
  1. OCR 결과가 의미 있는 글자 20자 이상이다.
  2. 깨짐 비율(기호·대체 문자·한 글자 토큰)이 0.35 미만이다.
  3. 기존 추출의 금액·날짜를 하나도 잃지 않는다.
  4. 종합 점수(글자×(1−깨짐) + 문장 줄·금액·날짜·조항·VAT 가점)가 10% 이상 높다.
- **상한:** `max_pages`는 이제 "채택 수"가 아니라 "실제 OCR 실행 수"를 센다. 이전에는 채택되지 않으면 계속 실행될 수 있었다.
- **기록:** 쪽별로 `ocr_attempted`, `ocr_succeeded`, `ocr_decision`(채택/미채택 사유, "엔진 없음", "상한 도달"), `ocr_seconds`를 남긴다. 요약은 `pdf_context.page_analysis_summary`의 `ocr_attempted_pages`, `ocr_failed_pages`, `ocr_seconds`에 있다.
- **OCR 미설치 환경:** 기존 추출 그대로 동작하고, OCR이 필요한 쪽에 "OCR 엔진 없음 — 기존 추출 유지"를 기록한다.
- **표 안 정보 누락 점검(5개 문서):** 표 행과 본문에서 레이블과 값이 갈라진 사례 2건을 찾았다. 공고문 `가격개찰일시`, `입찰 등록`이다. 다만 T1 추출 대상 필드가 아니어서 T1 결과는 같았다. 값이 있는 표 행을 T1에 추가하는 변경을 시험했지만 5개 문서 결과가 하나도 바뀌지 않아 되돌렸다. RAG에는 표 chunk(`표 추출`)로 레이블-값 관계가 보존된다.

### 2.4 공동도급 판정 (`agent01_rfp.classify_joint_clause`)

| 원문 | 분류 | 정책 요약 |
|---|---|---|
| 공동도급 불허 | joint_forbidden | 공동도급(공동수급) 불허 — 단독 입찰만 가능 |
| 공동수급의 허용 여부: 허용하지 아니함 | joint_forbidden | 불허 |
| 공동수급자는 중복으로 참여할 수 없음 | duplicate_participation | 공동수급체 중복 참여 제한 (금지 아님) |
| 공동수급 허용, 하도급 불허 | joint_allowed + subcontract_forbidden | 공동도급(공동수급) 허용; 하도급 불허 |
| 공동수급체 구성원은 … 3개사 이하 | member_restriction | (구성원 제한) |
| 공동수급(도급 및 하도급), 분합 납품은 허용하지 않음 | joint_forbidden (괄호 보충설명 제외 후 판정) | 불허 |

- 병합 단계는 이전에 정책 문자열에 "불허" 글자가 있는지로 금지를 판정했다. 그래서 "허용; 하도급 불허"를 금지로 오인했다. 이제는 금지 정책 문구로 시작하는지로 판정한다. RFP와 공고문이 다르면 `DocumentConflict`로 양쪽 원문 근거를 남기고, 금지 쪽을 우선한다.
- T1.5 `api_eligibility_claims`의 공동수급 교차 확인도 같은 분류 함수를 쓰게 바꿨다. 판정 구조는 바꾸지 않고, 금지/허용 판별만 교체했다.

### 2.5 VAT 비교 (`vat.compare`)

| 비교 | 결과 | 처리 |
|---|---|---|
| 107,000,000원(VAT 별도) ↔ 117,700,000원(VAT 포함) | same_after_vat | 일치(충돌 아님) |
| 107,000,000원(VAT 미상) ↔ 107,000,000원(VAT 포함) | vat_unknown | VAT 기준 확인 필요 |
| 107,000,000원(VAT 포함) ↔ 107,000,000원(VAT 별도) | basis_mismatch | 기준 불일치 확인 필요 |
| 같은 숫자, 양쪽 VAT 미상 | same_basis_unknown | 충돌은 아님. 검증 완료로 기록하지 않음(T1.5 교차 확인은 담당자 확인) |
| 같은 숫자, 같은 VAT 기준 명시 | same | 일치 |

이전에는 한쪽이 VAT 미상이어도 숫자가 같으면 `same`(일치)이었다. 문서에 없는 VAT 기준은 추정하지 않는다. 다만 나라장터 필드(배정예산=포함, 추정가격=별도)는 법령 정의에 따른 설정값이다.

### 2.6 공고함 40억 원 기준 (`notice_filter.judge`, 웹)

- 기준은 그대로 **VAT 포함 40억 원 이상**이다.
- 판정 결과에 원금액 `amount_krw`(기존 필드 그대로)와 함께 다음 필드를 추가했다.
  - `amount_basis_krw`: 기준과 같은 VAT로 환산한 금액, 모르면 `null`
  - `amount_source_field`: 배정예산/추정가격
  - `amount_vat_basis`: 원금액의 VAT 기준
  - `amount_criterion_basis`: 40억 기준의 VAT 기준
- 공고함의 금액 필터·금액 정렬·표시, 서버 정렬(`/api/notices`), 대시보드 마감 목록이 모두 `amount_basis_krw`를 쓴다. 원금액과 다르면 아래에 "추정가격 37.0억 VAT 별도 환산"을, VAT 기준을 모르면 "VAT 기준 확인 필요"를 작게 표시한다.
- 이미 저장된 예전 판정(새 필드 없음)은 `amount_krw`로 대신 표시한다. 다음 수집 때 새 필드가 채워진다.

### 2.7 T2 회사 자료 검색 (`company_retriever.ADMINISTRATIVE_CLAUSES`)

- "지식재산권"이라는 단어만으로 행정 조항(산출물 귀속)으로 제외하던 규칙을 고쳤다. 이제 귀속·소유·권리 조항일 때만 제외한다.
- 실제 RFP 3개 요구사항 233건 기준으로 제외 대상이 27건에서 26건이 됐다. 바뀐 1건은 CDW `TECH-054` "기밀보안 체계 및 대책, 지식재산권 존중여부 명시"이며, 회사 역량과 대조해야 할 보안 요구사항이다. 나머지 제외 26건(보안서약서·실적 인정 규칙·귀속 조항·참가자격 법조항)은 그대로다.
- 회사 자료의 출처 메타데이터(`source`, `retrieval_notice`: "실적 인정·자격 충족을 뜻하지 않음")는 바꾸지 않았다.

## 3. 성능 검증

### 3.1 검색 정확도 (`scripts/evaluate_retrieval.py`)

- **데이터:** RFP 3개와 공고문 PDF 2개(5개 문서), 정보 항목 5종(사업금액·VAT 조건·계약기간·공동도급·참가자격) × T1.5가 실제로 쓰는 재검색어 2~3개. 원문에 해당 조건 문구가 있는 20개 문서-항목 쌍, 질의 55개다. 정답이 없는 5쌍은 제외했다(예: CDW·통합정보시스템 RFP의 공동도급 조항).
- **범위:** `workflow.retrieve_targeted_evidence`와 같이 document_id로 제한하고 match_mode=all로 검색한다. K=3은 workflow가 실제로 쓰는 값이고, K=5도 함께 쟀다.
- **정답 기준(사람 검토 필요):** 사람이 붙인 라벨이 아니다. 항목마다 "실제 조건 문구" 정규식(예: 레이블+금액, 금액+VAT 포함/별도, 기간+연도)을 정하고, 그 문구를 담고 정답 쪽에 있는 chunk를 관련 있는 근거로 본다. 결과 JSON의 `rows[].hits`를 사람이 확인해야 한다.
- **Citation Correctness:** 검색 결과 chunk의 줄 80% 이상이 인용한 PDF 쪽 원문에 실제로 있는지 본다.

| 구성 | chunk 수 | Recall@3 | Precision@3 | Recall@5 | Precision@5 | 결과 0건 질의 | 인용 정확도 |
|---|---|---|---|---|---|---|---|
| 수정 전 (HEAD a6cb961) | 992 | 0.327 | 0.139 | 0.327 | 0.087 | 30/55 | 1.000 (53건) |
| 검색기만 교체(청크 그대로) | 992 | 0.927 | 0.358 | 0.927 | 0.262 | — | 1.000 |
| 청크만 교체(검색기 그대로) | 364 | 0.309 | 0.127 | 0.327 | 0.084 | — | 1.000 |
| **수정 후** | 364 | **0.927** | **0.370** | 0.927 | 0.236 | **2/55** | 1.000 (177건) |

- **Precision 상한:** 문서마다 정답 chunk가 1~2개뿐이라 Precision@3 상한이 0.509, @5 상한이 0.371이다. 수정 후 Precision@5(0.236)는 검색기만 바꾼 경우(0.262)보다 낮다. chunk를 병합해 정답 chunk 수 자체가 줄었기 때문이다.
- **개선의 주된 원인:** 검색기다. 청크 병합만으로는 검색 정확도가 오르지 않았다(0.327 → 0.309). 병합의 효과는 문맥 유지(금액·VAT·기간이 한 chunk)와 짧은 chunk 제거에 있다.
- **항목별 Recall@3 (수정 전 → 후):**
  - 사업금액 0.33 → 1.00
  - VAT 조건 0.50 → 0.67
  - 계약기간 0.20 → 1.00
  - 공동도급 0.17 → 1.00
  - 참가자격 0.40 → 1.00
- **VAT 조건 실패 4건:** 모두 원문과 반대 표기를 검색한 경우다. 예를 들어 "부가가치세 포함"으로 검색했는데 원문은 "별도"다. 검색 오류라기보다 질의가 원문에 없는 경우다.
- **상위 3개 중 목차·빈 서식 비율:** 6.2%(3/48)에서 1.5%(2/134)로 줄었다. 수정 전 값은 새 역할 판정을 사후 적용한 근사치다.
- **문서 유형 섞임:** 공고번호로만 검색하면 RFP와 공고문 chunk가 함께 나온다(R26BK01747679, R26BK01762350). 이제 `document_type="notice_pdf"`로 공고문만 고를 수 있다. 수정 전에는 이 필터가 없었다.

### 3.2 Agent별 데이터 전달 (LangGraph 회귀, 3개 RFP, 키 없는 실행)

| RFP | 최종 분기(전/후) | 색인 인용된 T1.5 항목(전→후) | retrieve_more(전→후) | 보조 검색 근거 chunk(전→후) |
|---|---|---|---|---|
| 상수도관 | human_review_required / 동일 | 12/17 → 14/17 | 5 → 3 | 6 → 8 |
| 통합정보시스템 | human_review_required / 동일 | 12/18 → 15/18 | 5 → 3 | 3 → 5 |
| CDW | human_review_required / 동일 | 10/17 → 11/17 | 6 → 5 | 2 → 5 |

- **T1.5:** 재검색으로 찾은 근거가 `supplemental_evidence`로 검증기에 전달되고, 해당 항목은 `retrieve_more`에서 담당자 확인(`human_review`)으로 넘어간다. 자동 확정으로 바뀐 항목은 없다(confirmed 수 동일).
- **T2:** 회사 자료 의미 검색은 OpenAI 키가 없어 실행하지 못했다. 행정 조항 제외 규칙만 실제 요구사항 233건으로 확인했다(2.7).
- **T3:** 과거 사업 의미 검색은 임베딩 색인이 없어 실행하지 못했다. 프로필 품질만 152건을 점검했다(2.2).

### 3.3 OCR

- 이 환경에는 OCR 엔진(pytesseract·tesseract kor)이 없다. 실제 OCR 정확도, 누락 감소, OCR 처리 시간은 **측정하지 못했다.**
- OCR 필요로 판정된 쪽은 상수도관 25쪽, CDW 2·3쪽, 통합정보시스템 1·2·54~57·62쪽이고, 공고문은 0쪽이다. 시도 0회, 실패 0회, OCR 시간 0초(엔진 없음)다.
- 채택 기준, 시도 상한, 기록, 추출 후보 전달은 가짜 OCR 엔진으로 단위 테스트했다(`tests/test_vat_joint_ocr.py`).
- 엔진을 설치한 환경에서는 `python scripts/compare_ocr_extraction.py data/input/*.pdf --json outputs/eval/ocr_compare.json`으로 쪽별 지표, 채택 사유, 시간을 비교한다.

### 3.4 테스트

- `pytest`: 125개 통과, 3개 실패다. 실패 3개(`test_app_v2` 2개, `test_runs` 1개)는 변경 전 코드에서도 같은 환경에서 똑같이 실패한다. 나라장터 키 미설정, live 실행 결과 없음 때문이다.
- `web`: `npm test` 28개 통과, `tsc -b` 통과.
- 신규·보강 테스트: `tests/test_vat_joint_ocr.py` 14개, `tests/test_notice_filter.py`, `tests/test_semantic.py`(TECH-054), `web/src/notices.test.ts`(환산 금액 정렬·표시).

## 4. 평가 한계

- 검색 정답은 정규식으로 자동 도출했으므로 사람 검토가 필요하다. M25 평가 라벨(`outputs/eval/labels_2026-11-17.txt`)과 평가 세트(`eval_items.json`)가 저장소에 없고, 의미 검색 재평가에는 OpenAI 키가 필요해 재사용하지 못했다.
- 의미 검색(T1.5 보조, T2 회사, T3 과거 사업)의 수정 전후 비교는 하지 못했다.
- 공고 API 메타데이터 chunk(`outputs/notices/notices.json`)가 이 환경에 없어 API 레코드 검색은 평가에서 빠졌다.

## 5. 색인 재생성 (필수)

chunk 스키마(v2)와 검색 색인 구조가 바뀌었다. `outputs/`는 Git에 없으므로 각자 다시 만든다.

```
python scripts/build_rag_corpus.py          # outputs/rag/chunks.jsonl, manifest.json (schema_version 2)
python scripts/build_rag_search_index.py    # outputs/rag/retrieval.sqlite3 (+ evidence_compact trigram 표)
python scripts/build_semantic_index.py      # OpenAI 키 필요 — chunk가 바뀌어 기존 embeddings.npy는 자동으로 '오래됨' 처리
python scripts/evaluate_retrieval.py --json outputs/eval/retrieval_eval.json
```

- `build_semantic_index.py`를 다시 돌리기 전에는 의미 검색이 `SemanticUnavailable`로 꺼지고 키워드 검색만 쓴다. 기존 동작 그대로 안전하게 대체된다.
- 회사 자료 색인(`build_company_rag_index.py`)과 과거 사업 색인(`build_past_projects.py`)은 이번 변경과 무관해 다시 만들 필요가 없다.

## 6. 팀원 공유 사항

- **LangGraph 담당(이주안):**
  - State, Node, Routing은 고치지 않았다.
  - `search_keyword()`는 반환 형식이 같고 선택 인자 `document_type`·`document_version`이 추가됐다. `retrieve_targeted_evidence`의 공고문 검색(`notice_no` + 결과 필터)은 `document_type="notice_pdf"`로 바꾸면 limit=10 뒤 필터보다 정확하다(권장).
  - T1.5 공동수급 교차 확인의 금지/허용 판별만 `classify_joint_clause`로 바꿨다.
- **T3 데이터 정제 담당(인진은):**
  - `scripts/check_past_project_profiles.py`가 표시한 12건을 확인해 달라. 대상은 계약 조항형 R26BK01589082·01592918·01628181·01686217·01826775, 짧은 본문 6건, 읽기 오류였던 R26BK01765343이다.
  - R26BK01765343은 이제 읽히므로, 과거 사업 색인을 다시 만들면 포함된다.
  - `_is_toc_page`로 `- 목 차 -` 형식 목차(점선 없음)를 잡을 수 있다. 다만 `project_profile`에는 적용하지 않았다. 적용하면 과거 사업 임베딩을 모두 다시 만들어야 해서 담당자 판단으로 남긴다.
- **데이터 구조 변경:**
  - chunk 메타데이터 필드 추가(2.2)
  - `PageAnalysis.ocr_attempted/ocr_succeeded/ocr_decision/ocr_seconds`
  - 공고 판정 `amount_basis_krw` 등 4개 필드
  - `vat.compare`의 새 상태 `same_basis_unknown`
- **병합 시 충돌 가능 파일:**
  - `src/bid_review/agent01_rfp.py`(목차 판정, 공동도급, OCR)
  - `src/bid_review/rag/verification.py`(금액 교차 확인, 공동수급 교차 확인 블록)
  - `src/bid_review/rag/retriever.py`
  - `src/bid_review/rag/corpus.py`
  - `src/bid_review/rag/past_projects.py`(`_rfp_pages`만)
  - `src/bid_review/rag/company_retriever.py`(행정 조항 정규식 1줄)
  - `web/src/screens/Notices.tsx`
