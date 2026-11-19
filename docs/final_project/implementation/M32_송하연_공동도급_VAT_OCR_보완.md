# M32 송하연 보완 — 공동도급 금지 우선 확인 · VAT 예외처리 · 40억 기준 VAT · OCR 보완

출처: `기말_팀프로젝트_팀원별_보완사항.md` 2장(송하연).

## 1. `agent01_rfp.py` `_basic_info()` — 공동도급 금지 조건 우선 확인
- `JOINT_FORBID_RE`: 공동도급/공동수급/공동계약 + 불허·허용하지 않음·불가·금지·할 수 없음.
  `공동수급체 구성원은 … 할 수 없다`, `공동수급을 허용하며, 하도급은 불허` 같은 문장은 매칭하지 않는다.
- `_basic_info()`가 금지 조항을 **가장 먼저** 찾아, 있으면 허용 요약(`공동이행방식 허용`)을 만들지 않고
  금지 조항 원문 줄·페이지를 근거로 `공동도급(공동수급) 불허 — 단독 입찰만 가능`을 남긴다.
- 실제 효과: 상수도관 RFP가 이전에는 28쪽 서식 안내만 보고 `공동이행방식 허용`, 이제 4쪽 `공동도급은 허용하지 않음` 근거로 불허.
- 병합: 한쪽 문서라도 금지하면 금지를 우선하고, 다른 쪽이 허용이면 `DocumentConflict(joint_venture_policy)`.

## 2. VAT 포함 금액 예외처리 (`src/bid_review/vat.py`)
- `vat_basis()`로 원문 발췌의 VAT 포함/별도를 읽고, `compare()`로 기준을 맞춘 뒤 비교한다.
  - `same_after_vat`: 107,000,000원(VAT 별도) ↔ 117,700,000원(VAT 포함) → **충돌 아님**(선택 사유에 기록)
  - `vat_unknown` / `basis_mismatch`: VAT 기준을 모르거나 표기가 반대 → **확인 필요**
  - `different`: 기준을 맞춰도 다름 → 실제 충돌
- 반영 위치
  - T1 `_choose()`: 금액 후보가 VAT 표기만 다르면 자동 확정(기준 모르는 후보가 있으면 기존처럼 확인 필요)
  - T1 `merge_document_results()`: RFP↔공고문 사업금액·추정가격 비교
  - T1.5 `verification._verify_amounts()`: 나라장터 배정예산(VAT 포함)·추정가격(VAT 별도)과 RFP 금액 교차 확인
- 공고문 `기초금액 117,700,000원 부가가치세 포함`(콜론 없음)도 사업금액 후보로 읽도록 보완.

## 3. 계약기간 불일치 (통합정보시스템)
- `계약기간` 레이블을 계약기간 후보로 읽고, 병합 시 종료일이 다르면 `DocumentConflict(contract_period)`.
  통합정보시스템: RFP 2027년 2월 15일 ↔ 공고문 2026년 2월 15일 → 충돌로 표시, 판본 확인 필요.

## 4. 공고함 40억 기준의 VAT 기준
- 결정값: **VAT 포함** (`config/notice_filter.yaml` `min_amount_vat_basis: included`).
  기존 필터가 배정예산(VAT 포함)을 그대로 40억과 비교해 왔으므로 배정예산이 있는 공고의 선별 결과는 같다.
- 필드별 VAT 기준 `amount_field_vat_basis`: 배정예산=포함, 추정가격=별도(국가계약법 시행령 제2조).
  배정예산이 없어 추정가격을 쓰면 1.1배로 환산해 비교한다(예: 추정가격 37억 → 40.7억 VAT 포함 → 검토 대상).
- VAT 기준을 모르는 금액 필드 또는 잘못된 기준 설정은 **확인 필요**.
- 화면: 공고함·판단 기준·대시보드에 `40억 이상 (VAT 포함)` 표시. 팀이 VAT 별도로 바꾸면 YAML 한 줄만 고친다.

## 5. OCR 기반 PDF 처리 (`src/bid_review/ocr.py`)
- `config/settings.yaml` `rfp.ocr.mode`: `off` | `fallback`(기본, OCR 필요 페이지만) | `always`.
- 텍스트가 짧거나 깨진 페이지를 OCR로 다시 읽고, 기존 텍스트보다 의미 있는 글자가 20% 이상 많을 때만 채택
  → 그 텍스트로 기본정보·요구사항을 추출하고 LLM 전달 JSON(`document_context.ocr_applied_pages`)에 표시.
- 엔진(pytesseract + tesseract `kor`)이 없으면 기존 pypdf·pdfplumber 동작 그대로.
- 비교: `python scripts/compare_ocr_extraction.py data/input/*.pdf --json ocr_compare.json`
  (현재 OCR 필요 페이지: 상수도관 25쪽, CDW 2·3쪽, 통합정보시스템 1·2·54~57·62쪽)

## 테스트
- `python tests/test_vat_joint_ocr.py`, `python tests/test_notice_filter.py`, `cd web && npm test`
