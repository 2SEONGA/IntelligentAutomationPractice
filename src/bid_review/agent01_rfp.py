from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

from .models import (
    DocumentConflict, EligibilityRequirement, Evidence, ExtractionIssue, FieldSelectionReason, MandatoryStatus, PageAnalysis,
    ProjectSummary, RFPAnalysisResult, RFPBasicInfo, RFPBasicInfoCandidates,
    RFPFieldCandidate, RFPQualityGate, RFPQualityIssue, Requirement, ReviewStatus,
    StructuredRequirement, UnclassifiedRequirement,
)
from .money import parse_krw
from . import ocr, vat
from .hwp_text import HWP_LINES_PER_PAGE, hwp_pages
from .pdf_chars import dedupe_overprint, file_sha256, remember_plain_texts
from .settings import load_settings


PAGE_TYPES = ("cover_page", "table_of_contents", "body_page", "table_heavy_page", "appendix_page")
# ponytail: keyword lists load from the default config/settings.yaml at import (static methods use them);
# a custom --settings path does not swap these. Move into RFPExtractor instances if that is ever needed.
_RFP_KEYWORDS = load_settings()["rfp"]
CATEGORY_RULES: dict[str, tuple[str, ...]] = {k: tuple(v) for k, v in _RFP_KEYWORDS["category_keywords"].items()}
LEGAL_TERMS = tuple(_RFP_KEYWORDS["legal_terms"])
STAFFING_TERMS = tuple(_RFP_KEYWORDS["staffing_terms"])
SUBMISSION_TERMS = tuple(_RFP_KEYWORDS["submission_terms"])
GENERIC_TITLES = set(_RFP_KEYWORDS["generic_titles"])
GENERIC_ENTITY_HEADERS = set(_RFP_KEYWORDS["generic_entity_headers"])
REQ_ID = re.compile(r"\b([A-Za-z]{1,5}(?:FR|SR|PR|IR)?[-_]\d{2,4})\b")
# 공동도급(공동수급) 금지 조항. "공동수급을 허용하며, 하도급은 불허"처럼 다른 대상의 금지를 잡지 않도록
# 공동도급 단어와 금지 표현 사이에 하도급·허용 표현이 끼면 매칭하지 않는다.
_JOINT_FORBID_PATTERN = re.compile(
    r"공동\s*(?:도급|수급|계약|이행)(?!\s*(?:체|협정|표준|대표|구성|자))"
    r"(?:(?!하도급|허용(?!\s*(?:여부|하지|되지|않|아니))|가능하며|가능함|공동\s*수급\s*체|구성원|중복)[^.。\n]){0,30}?"
    r"(?:허용하지\s*(?:않|아니)|허용되지\s*(?:않|아니)|불허|불가|금지|할\s*수\s*없|인정하지\s*(?:않|아니))"
)
_PARENTHESES = re.compile(r"\([^()]{0,40}\)")
JOINT_ALLOWED_RE = re.compile(r"공동\s*(?:수급|도급|계약)(?!\s*(?:체|협정|자))[^.。\n]{0,20}?"
                              r"(?:허용(?!\s*(?:여부|하지|되지|않|아니))|(?<!불)가능(?!\s*(?:하지|여부)))|공동이행\s*방식")
JOINT_DUPLICATE_RE = re.compile(r"공동\s*수급\s*(?:체|자|업체)?[^.。\n]{0,40}중복[^.。\n]{0,20}(?:참여|참가|입찰)")
JOINT_MEMBER_RE = re.compile(r"공동\s*수급\s*체?\s*(?:의\s*)?구성원[^.。\n]{0,80}(?:제한|이상|이하|불가|할\s*수\s*없|자격|지분)")
SUBCONTRACT_FORBID_RE = re.compile(r"하도급[^.。\n]{0,15}(?:불허|허용하지\s*(?:않|아니)|불가|금지|할\s*수\s*없)")


class _JointForbid:
    """공동도급 '자체' 금지 판정. 괄호 안 보충 설명("공동수급(도급 및 하도급)")은 지우고 본다."""

    @staticmethod
    def search(text: str):
        return _JOINT_FORBID_PATTERN.search(_PARENTHESES.sub(" ", text or ""))


JOINT_FORBID_RE = _JointForbid()
JOINT_FORBIDDEN_POLICY = "공동도급(공동수급) 불허 — 단독 입찰만 가능"


def classify_joint_clause(text: str) -> set[str]:
    """공동도급 관련 조항 분류(M33). 서로 다른 제한을 '공동도급 금지'로 뭉뚱그리지 않는다.

    - joint_forbidden: 공동도급(공동수급) 자체 금지   예) 공동도급 불허 / 공동수급의 허용 여부: 허용하지 아니함
    - joint_allowed: 공동도급 허용(공동이행방식 등)   예) 공동수급 허용, 하도급 불허
    - duplicate_participation: 공동수급체 중복 참여 제한  예) 공동수급자는 중복으로 참여할 수 없음
    - member_restriction: 공동수급체 구성원 제한(수·지분·자격)
    - subcontract_forbidden: 하도급 금지
    """
    plain = _PARENTHESES.sub(" ", text or "")
    labels: set[str] = set()
    if _JOINT_FORBID_PATTERN.search(plain):
        labels.add("joint_forbidden")
    if JOINT_ALLOWED_RE.search(plain):
        labels.add("joint_allowed")
    if JOINT_DUPLICATE_RE.search(plain):
        labels.add("duplicate_participation")
    if JOINT_MEMBER_RE.search(plain):
        labels.add("member_restriction")
    if SUBCONTRACT_FORBID_RE.search(plain):
        labels.add("subcontract_forbidden")
    return labels


JOINT_TERMS = ("공동수급", "공동이행", "공동도급", "공동계약")
DATE_PATTERN = re.compile(r"(?:20\d{2}[./년\- ]+\d{1,2}[./월\- ]+\d{1,2}(?:일)?)(?:\s*(?:오전|오후)?\s*\d{1,2}:?\d{0,2})?")


@dataclass
class PageText:
    number: int
    text: str
    layout_text: str = ""
    table_rows: list[str] = field(default_factory=list)
    page_type: str = "body_page"
    rationale: str = ""
    ocr_required: bool = False
    ocr_reason: str | None = None
    ocr_applied: bool = False


def _clean(value: str) -> str:
    return re.sub(r"[ \t]+", " ", value.replace("\x00", " ").replace("\u00a0", " ").replace("\u3000", " ")).strip()


def _symbol_ratio(value: str) -> float:
    compact = re.sub(r"\s+", "", value)
    if not compact:
        return 1.0
    return sum(not char.isalnum() and not ("가" <= char <= "힣") for char in compact) / len(compact)


def _is_repeated(value: str) -> bool:
    compact = re.sub(r"\s+", "", value)
    if len(compact) < 8:
        return False
    if re.search(r"(.{2,20})\1{2,}", compact):
        return True
    for width in range(2, min(24, len(compact) // 3 + 1)):
        unit = compact[:width]
        if compact == unit * (len(compact) // width) and len(compact) // width >= 3:
            return True
    return False


# T1 쪽 추출 결과(프로세스 안). 키는 파일 SHA-256·설정이라 파일이 바뀌면 다시 읽는다. 최근 문서 몇 개만 둔다.
_PAGE_CACHE: dict[tuple, tuple] = {}
_PAGE_CACHE_MAX = 8
APPENDIX_MARKERS = ("별지", "서식", "붙임", "첨부")
# 별첨·별표·양식은 본문에서도 "[별첨]의 평가기준", "별첨으로 제출"처럼 자주 인용되므로, 쪽 머리(앞 3줄)에
# "[별표 1]", "<양식 2>", "별첨 3." 같은 표제로 나올 때만 서식 쪽으로 본다(CDW 64·66·67·70쪽 <별표 n>).
APPENDIX_LABEL = re.compile(r"^\s*[\[<【〔(（]\s*(?:별첨|별표|양식)\s*(?:제?\s*\d+\s*호?)?\s*[\]>】〕)）]"
                            r"|^\s*(?:별첨|별표|양식)\s*제?\s*\d+\s*호?\s*(?:$|[.:：\s])")
APPENDIX_LABEL_LINES = 3
TOC_HEADING = re.compile(r"^[-<\[(【]?\s*(?:목\s*차|차\s*례|contents|table\s*of\s*contents)\s*[-<>\])】]?$", re.I)
TOC_ENTRY = re.compile(r"(?:[·.…‥ㆍ]{4,}|\s{6,})\s*\d{1,3}\s*$")


def _is_toc_page(text: str) -> bool:
    """목차 쪽 판정. '목차'라는 단어가 본문에 나오는 것만으로는 목차로 보지 않는다
    (예: "제안서는 목차에 따라 작성" 같은 작성 요령 본문). 단독 표제 줄('- 목 차 -')이 앞부분에 있거나,
    점선·쪽 번호로 끝나는 줄이 30% 이상(5줄 이상)일 때만 목차로 본다."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    if any(TOC_HEADING.match(line) for line in lines[:6]):
        return True
    entries = sum(bool(TOC_ENTRY.search(line)) for line in lines)
    return entries >= 5 and entries / len(lines) >= 0.3


class RFPExtractor:
    """PDF를 분석하되 원본 근거가 없는 값은 임의로 만들지 않습니다."""

    def __init__(self, settings: dict):
        self.settings = settings

    @staticmethod
    def _ocr_available() -> bool:
        return ocr.engine_available()

    def _try_ocr(self, layout_page, best_text: str, reason: str | None, attempts: int) -> tuple[str | None, dict]:
        """설정에 따라 페이지를 OCR한다. 반환: (채택한 OCR 텍스트 또는 None, 실행 기록).

        max_pages는 '채택한 쪽 수'가 아니라 'OCR을 실제로 실행한 쪽 수' 상한이다.
        """
        options = ocr.config(self.settings)
        record = {"attempted": False, "succeeded": False, "decision": None, "seconds": 0.0}
        mode = options["mode"]
        if mode == "off" or (mode == "fallback" and reason is None):
            return None, record
        if not self._ocr_available():
            record["decision"] = "OCR 엔진 없음(pytesseract·tesseract 미설치) — 기존 추출 유지" if reason else None
            return None, record
        if attempts >= int(options["max_pages"]):
            record["decision"] = f"문서당 OCR 시도 상한 {options['max_pages']}쪽 도달 — 기존 추출 유지"
            return None, record
        started = time.perf_counter()
        text = ocr.ocr_page(layout_page, options["lang"], int(options["resolution"]), int(options["psm"]))
        record.update(attempted=True, seconds=round(time.perf_counter() - started, 3), succeeded=bool(text and text.strip()))
        cleaned = "\n".join(_clean(line) for line in (text or "").splitlines() if _clean(line))
        adopted, why = ocr.choose(best_text, cleaned)
        record["decision"] = ("채택: " if adopted else "미채택: ") + why
        return (cleaned if adopted else None), record

    @staticmethod
    def _classify_page(number: int, text: str, table_rows: list[str]) -> tuple[str, str]:
        compact = re.sub(r"\s+", "", text)
        if number == 1:
            return "cover_page", "첫 페이지로 표지 우선 처리"
        if _is_toc_page(text):
            return "table_of_contents", "목차 표제 줄 또는 점선·쪽 번호로 끝나는 줄 비율로 목차 확인"
        if any(marker in text for marker in APPENDIX_MARKERS):
            return "appendix_page", "별지·서식·붙임 표식 확인"
        if len(table_rows) >= 3 or ("|" in text and len(re.findall(r"\s{3,}", text)) >= 5):
            # 표 중심 쪽은 표 행 단위로 요구사항을 읽으므로, 머리 표제(<별표 n>)가 있어도 표 쪽으로 둔다.
            return "table_heavy_page", "추출된 표 행 또는 다열 레이아웃 비중이 높음"
        top = [line for line in text.splitlines() if line.strip()][:APPENDIX_LABEL_LINES]
        if any(APPENDIX_LABEL.search(line) for line in top):
            return "appendix_page", "쪽 머리의 별첨·별표·양식 표제 확인"
        return "body_page", "본문 텍스트 중심 페이지"

    @staticmethod
    def _table_rows(page) -> list[str]:
        rows: list[str] = []
        try:
            for table in page.find_tables():
                # 병합 셀이 덮은 칸은 None으로 온다. 좌표상 위 셀이 이 행까지 세로로 이어질 때만 그 값을 채워
                # 상위 구분(예: 기술능력 평가 > 정량평가)을 행마다 남긴다. 가로 병합 칸은 비워 둔다.
                owner: dict[int, tuple[float, str]] = {}
                for row, texts in zip(table.rows, table.extract()):
                    cells = []
                    for index, (box, text) in enumerate(zip(row.cells, texts)):
                        if box is not None:
                            owner[index] = (box[3], " ".join(str(text or "").split()))
                            cells.append(owner[index][1])
                        else:
                            bottom, value = owner.get(index, (0.0, ""))
                            cells.append(value if bottom > row.bbox[1] + 1 else "")
                    if sum(bool(cell) for cell in cells) >= 2:
                        rows.append(" | ".join(cells))
        except Exception:
            pass
        return list(dict.fromkeys(rows))

    def _ocr_reason(self, text: str, page_type: str, table_rows: list[str]) -> str | None:
        """해당 페이지에서 OCR/원문 확인이 필요한 이유를 반환합니다."""
        minimum = self.settings["rfp"]["low_text_page_characters"]
        compact = re.sub(r"\s+", "", text)

        # 표지(그림자 글자)·목차(점선)·빈 제출 서식(반복 머리글)은 반복·기호가 정상이다. 시연 RFP의 OCR 필요 10쪽이 모두 이 오탐이었다.
        structural = page_type in {"cover_page", "table_of_contents", "appendix_page"}
        # 표지는 원래 글자가 적다(통합정보 표지: 겹친 그림자 글자를 거르면 38자). 글자가 하나도 없을 때(스캔본)만 OCR 대상.
        short_limit = 1 if page_type == "cover_page" else minimum
        if len(compact) < short_limit and page_type != "table_of_contents":
            reason = "추출 텍스트가 너무 짧음"
        elif page_type == "table_heavy_page" and not table_rows:
            reason = "표 비중이 높지만 표 구조를 추출하지 못함"
        elif not structural and (_symbol_ratio(text) > 0.35 or _is_repeated(text)):
            reason = "반복 문자 또는 기호 비율이 높아 레이아웃 텍스트가 깨짐"
        else:
            return None

        if not self._ocr_available():
            reason += "; 현재 환경에 OCR 엔진이 없어 자동 OCR을 실행하지 않음"
        return reason

    @staticmethod
    def _page_text(page, layout_page) -> tuple[str, str, object, bool]:
        """한 쪽의 (pypdf 텍스트, 레이아웃 텍스트, 표·OCR 외 분석에 쓸 pdfplumber 쪽, 겹친 글자 여부).

        겹쳐 찍은 글자가 있는 쪽은 pypdf도 글자를 겹쳐 읽으므로 걸러낸 pdfplumber 텍스트로 바꾼다.
        """
        text_page, overprinted = dedupe_overprint(layout_page)
        try:
            simple = _clean((text_page.extract_text() if overprinted else page.extract_text()) or "")
        except Exception:
            simple = ""
        try:
            layout = _clean(text_page.extract_text(layout=True) or "")
        except Exception:
            layout = ""
        return simple, layout, text_page, overprinted

    def _extract_hwp_pages(self, path: Path) -> tuple[list[PageText], list[ExtractionIssue], list[PageAnalysis], bool]:
        """HWP·HWPX 공고문/제안요청서. 쪽 정보가 없어 50줄을 한 쪽으로 보고, 표·OCR 판정은 하지 않는다."""
        pages: list[PageText] = []
        analyses: list[PageAnalysis] = []
        for number, chunk in enumerate(hwp_pages(path), start=1):
            text = "\n".join(_clean(line) for line in chunk.splitlines() if _clean(line))
            # HWP에는 표지 쪽이 따로 없다. 첫 50줄을 표지로 보면 모든 줄이 사업명 후보가 된다(실제 HWPX 공고문에서 확인).
            page_type, rationale = self._classify_page(0 if number == 1 else number, text, [])
            rationale += f" (HWP {HWP_LINES_PER_PAGE}줄 단위 쪽)"
            analyses.append(PageAnalysis(page_number=number, page_type=page_type, rationale=rationale,
                                         extraction_methods=["hwp_text"], table_count=0, ocr_required=False))
            pages.append(PageText(number, text, text, [], page_type, rationale))
        return pages, [], analyses, False

    def _page_cache_key(self, path: Path) -> tuple:
        """같은 파일 내용(SHA-256)·같은 추출 설정·같은 OCR 상태일 때만 같은 키."""
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rfp = self.settings.get("rfp", {})
        options = json.dumps({"low": rfp.get("low_text_page_characters"), "ocr": rfp.get("ocr")}, sort_keys=True, default=str)
        # 테스트·환경에 따라 OCR 함수와 엔진 유무가 바뀌면 다른 결과가 나오므로 키에 넣는다.
        # 파일 이름도 넣는다: 추출 이슈의 근거(Evidence.document)에 이름이 들어간다.
        return digest, path.name, options, self._ocr_available(), id(ocr.ocr_page)

    def _extract_pages(self, pdf_path: Path) -> tuple[list[PageText], list[ExtractionIssue], list[PageAnalysis], bool]:
        """PDF·HWP 쪽 추출. 같은 프로세스에서 같은 파일을 다시 분석하면(예: T1.5 재해석 노드) 저장해 둔 결과를 쓴다.

        추출은 결정적(같은 입력 → 같은 출력)이므로 결과가 바뀌지 않는다. 돌려줄 때마다 깊은 복사본을 준다.
        """
        path = Path(pdf_path)
        key = self._page_cache_key(path)
        if key not in _PAGE_CACHE:
            if len(_PAGE_CACHE) >= _PAGE_CACHE_MAX:
                _PAGE_CACHE.pop(next(iter(_PAGE_CACHE)))  # 가장 먼저 넣은 것부터 버린다
            _PAGE_CACHE[key] = self._extract_pages_uncached(path)
        return copy.deepcopy(_PAGE_CACHE[key])

    def _extract_pages_uncached(self, pdf_path: Path) -> tuple[list[PageText], list[ExtractionIssue], list[PageAnalysis], bool]:
        """pypdf와 pdfplumber로 PDF를 읽어 페이지 목록 및 추출 이슈를 만듭니다. HWP·HWPX는 hwp_text로 읽습니다.

        pypdf 텍스트는 일부 쪽에서 pdfplumber 레이아웃보다 길어 채택되므로(시연 CDW 2쪽 등) 빼지 않는다.
        """
        if Path(pdf_path).suffix.lower() in (".hwp", ".hwpx"):
            return self._extract_hwp_pages(Path(pdf_path))
        reader = PdfReader(str(pdf_path))
        pages: list[PageText] = []
        issues: list[ExtractionIssue] = []
        analyses: list[PageAnalysis] = []
        needs_ocr = False
        ocr_attempts = 0
        plain_texts: list[str | None] = []  # T3 질의 프로필이 같은 PDF를 다시 읽지 않게(남은 업무 4번)

        with pdfplumber.open(str(pdf_path)) as layout_pdf:
            for number, page in enumerate(reader.pages, start=1):
                layout_page = layout_pdf.pages[number - 1]
                simple_text, layout_text, text_page, overprinted = self._page_text(page, layout_page)

                table_rows = self._table_rows(text_page)
                try:
                    plain_texts.append(text_page.extract_text() or "")  # = past_projects._rfp_pages의 쪽 텍스트
                except Exception:
                    plain_texts.append(None)
                best_text = layout_text if len(layout_text) > len(simple_text) else simple_text
                page_type, rationale = self._classify_page(number, best_text, table_rows)
                reason = self._ocr_reason(best_text, page_type, table_rows)
                # OCR 보완: 텍스트가 짧거나 깨진 페이지는 페이지 이미지를 OCR로 다시 읽어, 더 나으면 그 텍스트를 쓴다.
                ocr_text, ocr_record = self._try_ocr(layout_page, best_text, reason, ocr_attempts)
                ocr_attempts += int(ocr_record["attempted"])
                ocr_applied = ocr_text is not None
                if ocr_applied:
                    best_text = ocr_text
                    page_type, rationale = self._classify_page(number, best_text, table_rows)
                    rationale += " (OCR 텍스트 기준)"
                    reason = self._ocr_reason(best_text, page_type, table_rows)
                    if reason and page_type == "table_heavy_page" and "표 구조" in reason:
                        reason = None  # 표 구조는 못 읽어도 OCR로 본문 텍스트를 확보했다.
                ocr_required = reason is not None
                needs_ocr |= ocr_required

                if ocr_required:
                    issues.append(ExtractionIssue(
                        issue_type="OCR_OR_MANUAL_REVIEW_REQUIRED",
                        message=f"{number}쪽 {reason}.",
                        evidence=[Evidence(document=pdf_path.name, pdf_page=number, section="페이지 전체", excerpt=best_text[:240])],
                    ))

                analyses.append(PageAnalysis(
                    page_number=number, page_type=page_type, rationale=rationale,
                    extraction_methods=["pypdf_text", "pdfplumber_layout"] + (["pdfplumber_dedupe_chars"] if overprinted else [])
                    + (["pdfplumber_table"] if table_rows else [])
                    + (["tesseract_ocr"] if ocr_applied else []),
                    table_count=len(table_rows), ocr_required=ocr_required, ocr_reason=reason, ocr_applied=ocr_applied,
                    ocr_attempted=ocr_record["attempted"], ocr_succeeded=ocr_record["succeeded"],
                    ocr_decision=ocr_record["decision"], ocr_seconds=ocr_record["seconds"],
                ))
                pages.append(PageText(
                    number, best_text, layout_text, table_rows,
                    page_type, rationale, ocr_required, reason, ocr_applied,
                ))
        remember_plain_texts(file_sha256(pdf_path), plain_texts)
        return pages, issues, analyses, needs_ocr

    @staticmethod
    def _lines(page: PageText, include_tables: bool = False) -> list[str]:
        lines = [_clean(line) for line in page.text.splitlines() if _clean(line)]
        if include_tables:
            lines.extend(page.table_rows)
        return list(dict.fromkeys(lines))

    @staticmethod
    def _mandatory(text: str) -> MandatoryStatus:
        if re.search(r"선택|옵션", text):
            return MandatoryStatus.OPTIONAL
        if re.search(r"필수|반드시|의무|하여야|해야|제출", text):
            return MandatoryStatus.REQUIRED
        return MandatoryStatus.NEEDS_REVIEW

    @staticmethod
    def _category(text: str) -> str:
        for category, keywords in CATEGORY_RULES.items():
            if any(keyword.lower() in text.lower() for keyword in keywords):
                return category
        return "기타 요구사항"

    @staticmethod
    def _base_score(page_type: str, labelled: bool) -> float:
        score = {"body_page": 60, "table_heavy_page": 55, "appendix_page": 35, "cover_page": 35, "table_of_contents": 10}[page_type]
        # A labelled field on a real cover/basic-info table is useful, but a
        # table-of-contents label must remain supplementary evidence.
        return score + (45 if labelled and page_type != "table_of_contents" else 25 if labelled else 0)

    @classmethod
    def _candidate(cls, value: str | int | None, page: PageText, excerpt: str, labelled: bool = False, source_document: str = "rfp_pdf") -> RFPFieldCandidate:
        score = cls._base_score(page.page_type, labelled)
        if isinstance(value, str) and DATE_PATTERN.search(value):
            score += 10
        if isinstance(value, str) and re.search(r"(?:대학교|대학|공사|공단|부|청|원|시|군|구)$", _clean(value)):
            score += 10
        if isinstance(value, int):
            score += 10
        date_range = isinstance(value, str) and bool(re.search(r"20\d{2}[./년\- ]+\d{1,2}[./월\- ]+\d{1,2}", value))
        if isinstance(value, str) and (_is_repeated(value) or (_symbol_ratio(value) > 0.30 and not date_range)):
            score -= 80
        confidence = "high" if score >= 80 else "medium" if score >= 50 else "low"
        return RFPFieldCandidate(value=value, source_page=page.number, source_excerpt=excerpt[:500], confidence=confidence, score=score, page_type=page.page_type, source_document=source_document)

    @staticmethod
    def _is_valid_candidate(field: str, candidate: RFPFieldCandidate) -> tuple[bool, str | None]:
        value = candidate.value
        if value is None or candidate.source_page is None or not candidate.source_excerpt:
            return False, candidate.invalid_reason or "근거 페이지 또는 원문 인용이 없는 값입니다."
        if isinstance(value, int):
            return (value >= 0, "금액이 유효하지 않습니다." if value < 0 else None)
        normalized = _clean(value)
        date_like_fields = {"proposal_deadline", "bid_deadline", "proposal_submission_start", "joint_venture_deadline", "contract_period"}
        if not normalized or len(re.sub(r"\s+", "", normalized)) < 2 or (_symbol_ratio(normalized) > 0.30 and field not in date_like_fields):
            return False, "점선·밑줄·기호 또는 빈칸으로 구성된 값입니다."
        if _is_repeated(normalized):
            return False, "동일 단어 또는 문장이 반복된 값입니다."
        if field == "project_name" and normalized.lower() in GENERIC_TITLES:
            return False, "일반 문서 제목만 있어 사업명으로 채택할 수 없습니다."
        if field == "project_name" and (normalized.endswith(("개요", "목차", "안내")) or normalized in {"사업", "과업"}):
            return False, "섹션명 또는 지나치게 짧은 값이라 사업명으로 채택할 수 없습니다."
        if field == "project_name" and re.fullmatch(r"20\d{2}[./]\s*\d{1,2}", normalized):
            return False, "연월 표기만 있어 사업명으로 채택할 수 없습니다."
        if field in {"procuring_entity", "demanding_entity"}:
            # PDF 표 머리글의 띄어쓰기(예: "비 고")를 정규화해 기관명으로 오인하지 않는다.
            compact_entity = re.sub(r"\s+", "", normalized)
            header_tokens = {re.sub(r"\s+", "", x) for x in GENERIC_ENTITY_HEADERS}
            header_tokens.update({"사업", "발주", "수행업체", "계약담당", "담당자"})
            if (compact_entity in header_tokens
                    or "협의하여" in normalized
                    or normalized.endswith(("한다.", "합니다."))):
                return False, "표 머리글 또는 문장 조각이므로 실제 기관명으로 확인할 수 없습니다."
        if field == "contract_period" and (re.search(r"YYYY|MM\.DD", normalized, re.I) or normalized.endswith(("기재", "기재함"))):
            return False, "서식의 작성 안내 문구라 실제 계약기간이 아닙니다."
        if field in {"proposal_deadline", "bid_deadline", "proposal_submission_start", "joint_venture_deadline"} and not DATE_PATTERN.search(normalized):
            return False, "날짜·시간 형식이 아닌 제출기한 값입니다."
        if field == "notice_number":
            compact = re.sub(r"\s+", "", normalized)
            if not re.search(r"\d", compact):
                return False, "공고번호 형식의 숫자가 없습니다."
            if len(compact) < 6 or re.fullmatch(r"\d{1,5}", compact):
                return False, "페이지·표 번호로 보이는 지나치게 짧은 공고번호입니다."
            if not (re.search(r"[A-Za-z가-힣]", compact) or re.search(r"[-_/]", compact) or len(compact) >= 8):
                return False, "공고번호 형식 또는 충분한 길이·문자 조합을 확인하지 못했습니다."
        return True, None

    def _choose(self, field: str, candidates: list[RFPFieldCandidate]) -> tuple[RFPFieldCandidate, str]:
        valid: list[RFPFieldCandidate] = []
        for item in candidates:
            accepted, reason = self._is_valid_candidate(field, item)
            item.invalid_reason = None if accepted else reason
            if accepted:
                valid.append(item)
        if not valid:
            first = candidates[0] if candidates else RFPFieldCandidate()
            return (
                RFPFieldCandidate(source_page=first.source_page, source_excerpt=first.source_excerpt,
                                  source_document=first.source_document, page_type=first.page_type,
                                  confidence="low", score=first.score, invalid_reason=first.invalid_reason or "후보 없음"),
                f"유효 후보가 없어 확정하지 않음: {first.invalid_reason or '후보 없음'}",
            )
        if field in {"budget_krw", "estimated_price_krw"} and len({item.value for item in valid}) > 1:
            reconciled = _vat_consistent(valid)
            if reconciled is not None:
                return reconciled
            first = valid[0]
            reason = "금액 후보가 달라 자동 확정하지 않음: 총액·연차별 금액·VAT 조건 원문 확인 필요"
            return RFPFieldCandidate(source_page=first.source_page, source_excerpt=first.source_excerpt,
                                     source_document=first.source_document, invalid_reason=reason), reason
        chosen = sorted(valid, key=lambda item: (-item.score, item.source_page or 9999))[0]
        if chosen.score < 80 and len(valid) == 1:
            chosen.invalid_reason = "단일 저신뢰 후보라 자동 확정하지 않음"
            return (
                RFPFieldCandidate(source_page=chosen.source_page, source_excerpt=chosen.source_excerpt,
                                  source_document=chosen.source_document, page_type=chosen.page_type,
                                  confidence="low", score=chosen.score, invalid_reason=chosen.invalid_reason),
                f"단일 저신뢰 후보라 확정하지 않음: {chosen.invalid_reason}",
            )
        return chosen, f"{chosen.page_type} {chosen.source_page}쪽의 레이블 인접 후보를 점수 {chosen.score:.0f}로 선택"

    @staticmethod
    def _label_value(line: str, labels: str) -> str | None:
        # Table rows use `|`; regular PDF text usually uses a colon.
        match = re.search(rf"(?:{labels})\s*(?:[:：]|\|)\s*(.+)", line)
        if not match:
            return None
        value = _clean(match.group(1))
        return _clean(value.split("|", 1)[0]) if "|" in value else value

    @staticmethod
    def _organization_name(value: str) -> str:
        """Keep the organisation, not the individual contact details, from a labelled contact row."""
        normalized = _clean(re.sub(r"\([^)]*\)", " ", value))
        match = re.search(r"(조달청|[가-힣]{2,}(?:시청|군청|구청|공사|공단|대학교|대학|지원본부|교육청|원))", normalized)
        return match.group(1) if match else normalized

    @staticmethod
    def _demanding_entity_name(value: str) -> str:
        """Repair common PDF spacing duplication without relying on an agency name."""
        normalized = _clean(value)
        normalized = re.sub(r"^(.+?)\s+\1", r"\1", normalized)
        normalized = re.sub(r"((?:교육청|시청|군청|구청|공사|공단))(?=[가-힣])", r"\1 ", normalized)
        return _clean(normalized)

    @staticmethod
    def _joint_venture_forbid(pages: list[PageText]) -> tuple[PageText, str] | None:
        """공동도급 금지 조항을 먼저 찾는다(목차 제외). 찾으면 (페이지, 원문 줄)."""
        for page in pages:
            if page.page_type == "table_of_contents":
                continue
            for line in RFPExtractor._lines(page, include_tables=True):
                cleaned = _clean(line)
                if JOINT_FORBID_RE.search(cleaned):
                    return page, cleaned
        return None

    @staticmethod
    def _joint_venture_summary(text: str) -> str | None:
        """Create a short policy only from clauses actually present on the notice page.

        공동도급 금지 조건을 허용 조건보다 먼저 확인한다. 금지 조항이 있으면 '공동이행방식 허용' 같은
        허용 요약을 만들지 않는다(공동수급 서식 안내 문구만 보고 허용으로 오판하던 문제).
        """
        compact = re.sub(r"\s+", " ", text)
        if not any(term in compact for term in JOINT_TERMS):
            return None
        if JOINT_FORBID_RE.search(compact):
            policy = JOINT_FORBIDDEN_POLICY
            line_labels = [(line, classify_joint_clause(line)) for line in text.splitlines() if line.strip()]
            labels = set().union(*(found for _, found in line_labels)) if line_labels else set()
            # 공동수급협정서 서식 안내 줄의 '공동이행방식'은 허용 근거로 세지 않는다.
            if any("joint_allowed" in found and not re.search(r"협정서|서식", line) for line, found in line_labels):
                policy += " (허용 문구도 함께 있어 원문 확인 필요)"
            if "subcontract_forbidden" in labels:
                policy += "; 하도급 불허"
            return policy
        items: list[str] = []
        if "공동이행" in compact:
            items.append("공동이행방식 허용")
        elif JOINT_ALLOWED_RE.search(_PARENTHESES.sub(" ", compact)):
            items.append("공동도급(공동수급) 허용")
        members = re.search(r"대표사를? 포함하여?\s*(\d+)개사\s*이하", compact)
        if members:
            items.append(f"대표사 포함 최대 {members.group(1)}개사")
        share = re.search(r"(?:최소\s*)?지분율(?:은)?\s*(\d+\s*%)\s*이상", compact)
        if share:
            items.append(f"구성원별 최소 지분율 {share.group(1)}")
        if re.search(r"구성원은? 각각.*?입찰참가자격을? 모두", compact):
            items.append("모든 구성원이 입찰참가자격 충족")
        if SUBCONTRACT_FORBID_RE.search(compact):
            items.append("하도급 불허")
        if JOINT_DUPLICATE_RE.search(compact):
            items.append("공동수급체 중복 참여 제한")
        return "; ".join(items) or None

    def _add_field(
        self, bucket: dict, field: str, value, page: PageText,
        line: str, source_document: str, labelled: bool = True,
    ) -> None:
        """후보값 1개와 출처 페이지, 신뢰도 점수를 함께 저장합니다."""
        bucket[field].append(self._candidate(
            value, page, line, labelled=labelled, source_document=source_document,
        ))

    def _collect_names_and_organizations(
        self, bucket: dict, page: PageText, line: str, source_document: str,
    ) -> None:
        """사업명·발주기관·수요기관·공고번호·기간·목적을 후보로 수집합니다."""
        project = self._label_value(line, r"사\s*업\s*명|과\s*업\s*명|공\s*고\s*명|용\s*역\s*명|계\s*약\s*명")  # 수의계약 공고 "1. 계 약 명 :"
        if project:
            self._add_field(bucket, "project_name", project, page, line, source_document)
        elif (page.page_type == "cover_page" and not re.search(r"제\s*안\s*요\s*청\s*서|목\s*차", line)  # 표지 "제 안 요 청 서"
              and "··" not in line and 6 <= len(line) <= 90):
            self._add_field(bucket, "project_name", line, page, line, source_document, labelled=False)

        entity = self._label_value(line, r"발\s*주\s*기\s*관|주\s*관\s*기\s*관|발\s*주\s*처")
        if entity:
            self._add_field(bucket, "procuring_entity", entity, page, line, source_document)

        contact = self._label_value(line, r"계\s*약\s*담\s*당(?:자|기관)?")
        if contact:
            self._add_field(bucket, "procuring_entity", self._organization_name(contact), page, line, source_document)
        elif "계약담당" in line and "조달청" in line:
            self._add_field(bucket, "procuring_entity", "조달청", page, line, source_document)

        demand = self._label_value(line, r"수\s*요\s*기\s*관")
        if demand:
            self._add_field(bucket, "demanding_entity", self._demanding_entity_name(demand), page, line, source_document)

        notice = self._label_value(line, r"입찰공고번호|공고번호|조달청공고번호")
        if notice:
            number = re.search(r"[A-Za-z0-9][A-Za-z0-9\-]*\d[A-Za-z0-9\-]*", notice)
            self._add_field(bucket, "notice_number", number.group(0) if number else notice, page, line, source_document)

        period = self._label_value(line, r"사\s*업\s*기\s*간|과\s*업\s*기\s*간|계\s*약\s*기\s*간")
        if period:
            self._add_field(bucket, "contract_period", period, page, line, source_document)

        purpose = self._label_value(line, r"사\s*업\s*목\s*적|추\s*진\s*목\s*적|과\s*업\s*목\s*적")
        if purpose:
            self._add_field(bucket, "project_purpose", purpose, page, line, source_document)

    def _collect_deadlines(
        self, bucket: dict, page: PageText, line: str,
        source_document: str, joint_context: bool,
    ) -> None:
        """제출기한을 읽습니다. 날짜가 없으면 기존처럼 후보만 남깁니다."""
        normalized_line = re.sub(r"\s+", "", line)
        date = DATE_PATTERN.search(line)
        if "제안서" in normalized_line and "가격" in normalized_line and "제출시작" in normalized_line:
            self._add_field(bucket, "proposal_submission_start", date.group(0) if date else line, page, line, source_document)
        elif "제안서" in normalized_line and "가격" in normalized_line and "제출마감" in normalized_line:
            self._add_field(bucket, "proposal_deadline", date.group(0) if date else line, page, line, source_document)
        elif "공동수급협정서" in normalized_line and "제출" in normalized_line and date:
            self._add_field(bucket, "joint_venture_deadline", date.group(0), page, line, source_document)
        elif joint_context and any(label in normalized_line for label in ("제출기한", "제출마감")) and date:
            self._add_field(bucket, "joint_venture_deadline", date.group(0), page, line, source_document)
        elif any(label in normalized_line for label in ("제안서제출", "제안서접수", "제출기한")) and "공동수급" not in normalized_line:
            self._add_field(bucket, "proposal_deadline", date.group(0) if date else line, page, line, source_document)

        if any(label in normalized_line for label in ("입찰마감", "입찰서제출", "입찰등록마감")) and "제출시작" not in normalized_line:
            self._add_field(bucket, "bid_deadline", date.group(0) if date else line, page, line, source_document)

    def _collect_amounts_and_methods(
        self, bucket: dict, page: PageText, line: str, source_document: str,
    ) -> None:
        """금액·계약방식·평가방식 후보를 수집합니다."""
        normalized_line = re.sub(r"\s+", "", line)
        if "추정가격" in normalized_line:
            amount = parse_krw(line)
            if amount is not None:
                self._add_field(bucket, "estimated_price_krw", amount, page, line, source_document)
        elif re.search(r"(?:전체|총)?\s*사업금액\s*(?:[:：|])|추정금액\s*(?:[:：|])|사업예산\s*(?:[:：|])|사업비\s*(?:[:：|])|기초금액\s*(?:[:：|]|(?=\d))", line):
            amount = parse_krw(line)
            if amount is not None:
                self._add_field(bucket, "budget_krw", amount, page, line, source_document)

        method = self._label_value(line, r"계\s*약\s*방\s*법")
        if method:
            self._add_field(bucket, "contract_method", method, page, line, source_document)
        if "기술평가" in normalized_line and "가격평가" in normalized_line and "%" in line:
            self._add_field(bucket, "evaluation_method", _clean(line), page, line, source_document)

    def _basic_info(self, pages: list[PageText], source_document: str) -> tuple[RFPBasicInfo, RFPBasicInfoCandidates, dict[str, str]]:
        """모든 페이지에서 기본정보 후보를 모으고 가장 신뢰할 후보를 선택합니다."""
        bucket: dict[str, list[RFPFieldCandidate]] = {field: [] for field in RFPBasicInfo.model_fields}
        joint_source: PageText | None = None
        joint_text_parts: list[str] = []
        # 공동도급 금지 조건을 가장 먼저 확인한다. 금지 조항은 허용 요약보다 우선하며 그 원문 줄을 근거로 남긴다.
        joint_forbid = self._joint_venture_forbid(pages)

        for page in pages:
            joint_context = page.page_type != "table_of_contents" and any(term in page.text for term in JOINT_TERMS)
            if joint_context:
                joint_source = joint_source or page
                joint_text_parts.append(page.text)

            for line in self._lines(page, include_tables=page.page_type == "table_heavy_page"):
                self._collect_names_and_organizations(bucket, page, line, source_document)
                self._collect_deadlines(bucket, page, line, source_document, joint_context)
                self._collect_amounts_and_methods(bucket, page, line, source_document)

        if joint_forbid:
            forbid_page, forbid_line = joint_forbid
            policy = self._joint_venture_summary("\n".join(joint_text_parts) or forbid_line) or JOINT_FORBIDDEN_POLICY
            self._add_field(bucket, "joint_venture_policy", policy, forbid_page, forbid_line, source_document)
        elif joint_source:
            policy = self._joint_venture_summary("\n".join(joint_text_parts))
            if policy:
                self._add_field(bucket, "joint_venture_policy", policy, joint_source, policy, source_document)

        # 여러 줄에 걸쳐 서술된 사업 목적도 원본처럼 별도로 검사합니다.
        for page in pages:
            if page.page_type == "table_of_contents":
                continue
            purpose = re.search(r"(?:배경\s*및\s*목적|사업\s*목적)\s*(.*?)(?=\n\s*\d+\.?\s*(?:주요\s*사업|사업\s*내용)|\Z)", page.text, re.DOTALL)
            if purpose:
                value = _clean(purpose.group(1))
                if len(value) >= 12:
                    self._add_field(bucket, "project_purpose", value[:500], page, value[:500], source_document)

        candidates = RFPBasicInfoCandidates(**bucket)
        selected: dict[str, RFPFieldCandidate] = {}
        reasons: dict[str, str] = {}
        for name, items in bucket.items():
            selected[name], reasons[name] = self._choose(name, items)
        return RFPBasicInfo(**selected), candidates, reasons

    def _quality_gate(self, basic: RFPBasicInfo, needs_ocr: bool, document_kind: str, requirements: tuple[list, list, list, list] | None = None) -> RFPQualityGate:
        """RFP와 공고문의 필수정보 검증 기준이 다른 점을 구분해 검사합니다."""
        issues: list[RFPQualityIssue] = []
        if document_kind == "rfp_pdf":
            title_ok = self._is_valid_candidate("project_name", basic.project_name)[0]
            purpose_ok = self._is_valid_candidate("project_purpose", basic.project_purpose)[0]
            if not title_ok and not purpose_ok:
                issues.append(RFPQualityIssue(field="project_name", reason="의미 있는 사업명 또는 사업목적을 페이지 근거와 함께 확인하지 못했습니다.", source_page=basic.project_name.source_page))
            if requirements is not None and not any(requirements):
                issues.append(RFPQualityIssue(field="requirements", reason="페이지 근거가 있는 기술·인력·제출 요구사항을 추출하지 못했습니다.", source_page=None))
            # OCR 경고는 extraction_issues에 남긴다. 표지·별첨 일부의 OCR 필요가
            # 나머지 RFP 사전 검토 전체를 차단하지는 않는다.
            return RFPQualityGate(status="passed" if not issues else ("needs_ocr" if needs_ocr else "needs_manual_verification"), issues=issues)

        for field in ("notice_number", "procuring_entity", "proposal_deadline", "bid_deadline"):
            if field == "procuring_entity" and self._is_valid_candidate("demanding_entity", basic.demanding_entity)[0]:
                # Some notices label only the demand agency. Keep roles separate,
                # but accept it as the minimum verifiable agency for the notice gate.
                continue
            if field == "proposal_deadline" and self._is_valid_candidate("bid_deadline", basic.bid_deadline)[0]:
                continue
            if field == "bid_deadline" and self._is_valid_candidate("proposal_deadline", basic.proposal_deadline)[0]:
                continue
            candidate = getattr(basic, field)
            valid, reason = self._is_valid_candidate(field, candidate)
            if not valid:
                issues.append(RFPQualityIssue(field=field, reason=reason or "유효하지 않은 값입니다.", source_page=candidate.source_page))
        if requirements is not None and not requirements[0]:
            issues.append(RFPQualityIssue(field="legal_eligibility_requirements", reason="법적 참가자격 원문과 페이지 근거를 추출하지 못했습니다.", source_page=None))
        # OCR warnings remain page-level extraction issues. They fail the notice
        # gate only when a required field is already unverified; a damaged
        # appendix cannot erase a readable official notice table.
        if needs_ocr and issues:
            issues.append(RFPQualityIssue(field="pdf_text", reason="일부 페이지의 레이아웃 텍스트·표가 신뢰할 수 없어 OCR 또는 원본 확인이 필요합니다.", source_page=None))
        return RFPQualityGate(status="passed" if not issues else ("needs_ocr" if needs_ocr else "needs_manual_verification"), issues=issues)

    @staticmethod
    def _evidence(kind: str) -> list[str]:
        return {
            "eligibility": ["나라장터 입찰참가자격 및 관련 등록의 최신 증빙", "입찰참가 제한·영업정지 여부 확인서"],
            "staffing": ["투입인력 이력서·경력증명", "재직 및 실제 투입 가능 여부 증빙", "기술등급·자격증 사본"],
            "submission": ["제안서·가격제안서 및 공고 서식", "요구되는 법인·실적·인력 증빙 원본"],
            "technical": ["관련 기술 적용 실적 또는 프로젝트 산출물", "기술·보안·품질 인증서 원본 및 유효기간"],
        }[kind]

    @staticmethod
    def _legal(text: str) -> bool:
        return any(term in text for term in LEGAL_TERMS) or bool(re.search(r"업종.{0,20}(?:등록|코드|분류)|(?:등록|코드|분류).{0,20}업종", text))

    @staticmethod
    def _requirement_like(text: str) -> bool:
        return bool(REQ_ID.search(text) or re.match(r"^(?:[-•*]|[ㅇ◦▪]|\d+[.)]|[가-힣][.)])\s*", text) or any(term in text for term in (*LEGAL_TERMS, *STAFFING_TERMS, *SUBMISSION_TERMS)))

    def _units(self, page: PageText) -> list[tuple[str, str]]:
        if page.page_type in {"cover_page", "table_of_contents"}:
            return []
        lines = page.table_rows if page.page_type == "table_heavy_page" and page.table_rows else self._lines(page)
        units: list[tuple[str, str]] = []
        section = "본문"
        current = ""
        for line in lines:
            if "··" in line:
                continue
            if re.match(r"^(?:[ⅠⅡⅢⅣⅤ]|\d+\.|[가나다라마바사]\.)\s*", line) and len(line) < 100:
                section = line
            bullet = bool(re.match(r"^(?:[-•*]|[ㅇ◦▪]|\d+[.)]|[가-힣][.)])\s*", line))
            if bullet:
                if current:
                    units.append((current, section))
                current = line
            elif current and len(current) + len(line) <= 700:
                current += " " + line
            elif self._requirement_like(line):
                if current:
                    units.append((current, section))
                current = line
        if current:
            units.append((current, section))
        return units

    @staticmethod
    def _is_staffing_text(text: str) -> bool:
        """인력 요건만 분류. PMR/PSR 약어의 'PM' 부분일치는 무시."""
        direct_terms = (term for term in STAFFING_TERMS if term not in {"PM", "PL"})
        if any(term.lower() in text.lower() for term in direct_terms):
            return True
        return bool(re.search(r"(?<![A-Za-z])(?:PM|PL)(?![A-Za-z])", text, re.IGNORECASE))

    def _make_requirement(
        self, item_id: str, text: str, mandatory: MandatoryStatus,
        page: PageText, source_document: str, evidence_kind: str,
    ) -> StructuredRequirement:
        """인력·기술·제출 요구사항에 공통으로 들어가는 항목을 만듭니다."""
        return StructuredRequirement(
            id=item_id,
            requirement_text=text[:500],
            mandatory=mandatory == MandatoryStatus.REQUIRED,
            source_page=page.number,
            source_excerpt=text[:500],
            source_document=source_document,
            required_company_evidence=self._evidence(evidence_kind),
        )

    def _split_requirements(self, pages: list[PageText], document: str, source_document: str):
        """조건을 참가자격/인력/제출서류/기술/미분류로 나눕니다.

        같은 조건이 여러 곳에 중복되어도 한 번만 기록합니다.
        """
        eligibility: list[EligibilityRequirement] = []
        staffing: list[StructuredRequirement] = []
        submission: list[StructuredRequirement] = []
        technical: list[StructuredRequirement] = []
        unclassified: list[UnclassifiedRequirement] = []
        legacy: list[Requirement] = []
        counters = {"ELIG": 1, "STAFF": 1, "SUB": 1, "TECH": 1, "UNCL": 1}
        seen: set[tuple[str, str]] = set()

        def identifier(prefix: str) -> str:
            result = f"{prefix}-{counters[prefix]:03d}"
            counters[prefix] += 1
            return result

        for page in pages:
            for text, section in self._units(page):
                combined = f"{section} {text}"
                key = (page.page_type, re.sub(r"\s+", "", text))
                if len(text) < 12 or key in seen:
                    continue
                seen.add(key)

                mandatory = self._mandatory(text)
                source = Evidence(
                    document=document, source_document=source_document,
                    pdf_page=page.number, section=section, excerpt=text[:500],
                )

                if self._legal(combined):
                    # ① 입찰 참가자격
                    eligibility.append(EligibilityRequirement(
                        name=text[:160], details=text[:500],
                        mandatory_status=mandatory, evidence=source,
                        review_status=ReviewStatus.COMPLETE,
                    ))
                elif self._is_staffing_text(text):
                    # ② 인력·경력 요건
                    staffing.append(self._make_requirement(
                        identifier("STAFF"), text, mandatory, page, source_document, "staffing",
                    ))
                elif any(term in combined for term in SUBMISSION_TERMS):
                    # ③ 제출 서류
                    submission.append(self._make_requirement(
                        identifier("SUB"), text, mandatory, page, source_document, "submission",
                    ))
                else:
                    # ④ 기술 요구사항 / ⑤ 미분류 내용
                    category = self._category(combined)
                    requirement_id = REQ_ID.search(text)
                    if category != "기타 요구사항" or requirement_id or "요구사항" in combined:
                        item_id = requirement_id.group(1) if requirement_id else identifier("TECH")
                        technical.append(self._make_requirement(
                            item_id, text, mandatory, page, source_document, "technical",
                        ))
                        legacy.append(Requirement(
                            id=item_id, category=category, name=text[:160],
                            details=text[:500], mandatory_status=mandatory,
                            evidence=source, review_status=ReviewStatus.COMPLETE,
                        ))
                    elif self._requirement_like(combined):
                        unclassified.append(UnclassifiedRequirement(
                            id=identifier("UNCL"), requirement_text=text[:500],
                            mandatory=mandatory == MandatoryStatus.REQUIRED,
                            source_page=page.number, source_excerpt=text[:500],
                            source_document=source_document, required_company_evidence=[],
                            reason="표 제목·상위 문맥 또는 본문 조건을 함께 확보하지 못해 범주를 확정할 수 없습니다.",
                        ))
        return eligibility, staffing, submission, technical, unclassified, legacy

    def _labelled_eligibility_requirements(self, pages: list[PageText], document: str, source_document: str) -> list[EligibilityRequirement]:
        """Retain labelled legal clauses even when PDF table/indentation grouping is imperfect."""
        patterns = (
            r"소프트웨어사업자.{0,180}?(?:\(\s*\d{4}\s*\)|업종코드\s*[:：]?\s*\d{4})",
            r"정보통신공사업.{0,160}?(?:\(\s*\d{4}\s*\)|업종코드\s*[:：]?\s*\d{4})",
            r"직접생산\s*확인\s*증명서.{0,180}?\d{10}",
            r"중소기업.{0,160}?소상공인.{0,100}?확인서",
            r"대기업.{0,120}?중견기업.{0,120}?참여.{0,50}?(?:제한|불가|없)",
            r"상호출자제한.{0,120}?참여.{0,50}?(?:제한|불가|없)",
        )
        results: list[EligibilityRequirement] = []
        seen: set[str] = set()
        for page in pages:
            if page.page_type == "table_of_contents":
                continue
            text = re.sub(r"\s+", " ", page.text).strip()
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if not match:
                    continue
                clause = _clean(match.group(0))
                key = re.sub(r"\s+", "", clause)
                if key in seen:
                    continue
                seen.add(key)
                results.append(EligibilityRequirement(
                    name=clause[:160], details=clause[:500], mandatory_status=MandatoryStatus.REQUIRED,
                    evidence=Evidence(document=document, source_document=source_document, pdf_page=page.number, section="입찰참가자격", excerpt=clause[:500]),
                    review_status=ReviewStatus.COMPLETE,
                ))
        return results

    @staticmethod
    def _is_eligibility_section_heading(line: str) -> bool:
        compact = re.sub(r"\s+", "", line)
        if len(compact) > 180 or re.search(r"(?:\.{3,}|·{3,}|…{2,})", line):
            return False
        # Match a section title, not every sentence that happens to mention
        # eligibility (for example, sanctions in a security clause or appendix).
        return bool(re.match(
            r"^(?:(?:\d+(?:\.\d+)*|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)[.)]?)?(?:입찰참가자격|참가자격|입찰자격)(?=$|[（(])",
            compact,
        ))

    @staticmethod
    def _is_next_section_heading(line: str) -> bool:
        normalized = _clean(line)
        if len(normalized) > 120:
            return False
        if not re.match(r"^(?:\d+(?:\.\d+)*|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+)\s+", normalized):
            return False
        return any(term in normalized for term in (
            "입찰", "공동도급", "제출", "선정", "평가", "협상", "계약", "사업자", "낙찰",
        ))

    def _section_eligibility_requirements(
        self, pages: list[PageText], document: str, source_document: str,
    ) -> tuple[list[EligibilityRequirement], set[int]]:
        """Extract individual clauses from explicit bid-qualification sections."""
        section_lines: list[tuple[int, str]] = []
        section_pages: set[int] = set()
        in_section = False
        for page in pages:
            if page.page_type in {"table_of_contents", "appendix_page"}:
                continue
            # Table extraction can append unrelated page tables after the body
            # text and make them look like additional qualification clauses.
            lines = self._lines(page)
            for line in lines:
                if not in_section:
                    if self._is_eligibility_section_heading(line):
                        in_section = True
                        section_pages.add(page.number)
                    continue
                if self._is_next_section_heading(line):
                    in_section = False
                    continue
                # Do not absorb appendix templates or a new unrelated document section.
                section_lines.append((page.number, line))
                section_pages.add(page.number)

        if not section_lines:
            return [], set()

        clause_start = re.compile(r"^(?:[가나다라마바사아자차카타파하]\s*[.)、]|[○●▪•·‧]|[-–]\s|l\s|[①-⑳])")
        clauses: list[tuple[int, list[str]]] = []
        for page_number, line in section_lines:
            cleaned = _clean(line)
            if not cleaned or re.fullmatch(r"-?\s*\d{1,3}\s*-?", cleaned):
                continue
            if clause_start.match(cleaned):
                content = re.sub(r"^(?:[가나다라마바사아자차카타파하]\s*[.)、]|[○●▪•·‧]|[-–]\s|l\s|[①-⑳])\s*", "", cleaned)
                if content:
                    clauses.append((page_number, [content]))
            elif clauses:
                clauses[-1][1].append(cleaned)
            else:
                # Some PDFs omit bullet glyphs; retain the clause text for human review.
                clauses.append((page_number, [cleaned]))

        results: list[EligibilityRequirement] = []
        seen: set[str] = set()
        for page_number, parts in clauses:
            text = _clean(" ".join(parts))
            compact = re.sub(r"\s+", "", text)
            if len(compact) < 4 or compact in seen:
                continue
            seen.add(compact)
            evidence = Evidence(
                document=document, source_document=source_document,
                pdf_page=page_number, section="입찰참가자격 절", excerpt=text[:1600],
            )
            results.append(EligibilityRequirement(
                name=text[:240], details=text[:1600], mandatory_status=MandatoryStatus.REQUIRED,
                evidence=evidence, review_status=ReviewStatus.NEEDS_REVIEW,
            ))
        # Joint-bid restrictions are often placed in the neighboring bidding
        # method section rather than under the qualification heading.
        for page in pages:
            if page.page_type == "table_of_contents":
                continue
            for line in self._lines(page):
                cleaned = _clean(line)
                if not JOINT_FORBID_RE.search(cleaned):
                    continue
                key = re.sub(r"\s+", "", cleaned)
                if key in seen:
                    continue
                seen.add(key)
                results.append(EligibilityRequirement(
                    name=cleaned[:240], details=cleaned[:1600], mandatory_status=MandatoryStatus.REQUIRED,
                    evidence=Evidence(
                        document=document, source_document=source_document,
                        pdf_page=page.number, section="공동도급 참가 제한", excerpt=cleaned[:1600],
                    ), review_status=ReviewStatus.NEEDS_REVIEW,
                ))
        return results, section_pages

    def _deliverables(self, pages: list[PageText], source_document: str) -> list[StructuredRequirement]:
        """Keep deliverable clauses separate from general technical requirements."""
        results: list[StructuredRequirement] = []
        seen: set[str] = set()
        for page in pages:
            if page.page_type in {"cover_page", "table_of_contents"}:
                continue
            for line in self._lines(page, include_tables=page.page_type == "table_heavy_page"):
                normalized = _clean(line)
                key = re.sub(r"\s+", "", normalized)
                if "산출물" not in normalized or len(normalized) < 12 or key in seen:
                    continue
                seen.add(key)
                results.append(StructuredRequirement(
                    id=f"DEL-{len(results) + 1:03d}", requirement_text=normalized[:500],
                    mandatory=self._mandatory(normalized) == MandatoryStatus.REQUIRED,
                    source_page=page.number, source_excerpt=normalized[:500], source_document=source_document,
                    required_company_evidence=["제안 산출물 작성 책임자·템플릿·유사 산출물 원본"],
                ))
        return results

    def _analyze_requirements(self, pages: list[PageText], document: str, document_kind: str):
        """요구사항을 분류하고, 표에 있는 명시적 입찰자격과 산출물을 보충합니다."""
        eligibility, staffing, submission, technical, unclassified, legacy = self._split_requirements(
            pages, document, document_kind,
        )
        section_eligibility, section_pages = self._section_eligibility_requirements(
            pages, document, document_kind,
        )
        if section_pages:
            # Replace broad page-level candidates with condition-level items from
            # the qualification section, retaining unrelated qualification clauses.
            eligibility = [
                item for item in eligibility
                if item.evidence.pdf_page not in section_pages
            ]
            eligibility.extend(section_eligibility)
        explicit_eligibility = self._labelled_eligibility_requirements(pages, document, document_kind)
        existing_eligibility = {re.sub(r"\s+", "", item.details) for item in eligibility}
        eligibility.extend(
            item for item in explicit_eligibility
            if item.evidence.pdf_page not in section_pages
            and re.sub(r"\s+", "", item.details) not in existing_eligibility
        )
        # Put explicit qualification-section clauses first so T1.5 field IDs
        # follow the order a reviewer sees in the source document.
        eligibility.sort(key=lambda item: (
            item.evidence.section not in {"입찰참가자격 절", "공동도급 참가 제한"},
            item.evidence.pdf_page or 0,
        ))
        deliverables = self._deliverables(pages, document_kind) if document_kind == "rfp_pdf" else []
        return eligibility, staffing, submission, technical, unclassified, legacy, deliverables

    def _check_extraction_issues(
        self, issues: list[ExtractionIssue], pages: list[PageText], basic: RFPBasicInfo,
        quality: RFPQualityGate, legacy: list[Requirement], document: str,
    ) -> None:
        """품질 게이트 실패와 총괄표 요구사항 수 불일치를 이슈 목록에 남깁니다."""
        for issue in quality.issues:
            excerpt = getattr(basic, issue.field).source_excerpt if hasattr(basic, issue.field) else ""
            issues.append(ExtractionIssue(
                issue_type="BASIC_INFO_QUALITY_GATE",
                message=f"{issue.field}: {issue.reason}",
                evidence=[Evidence(document=document, pdf_page=issue.source_page, section="기본정보 품질 게이트", excerpt=excerpt)],
            ))

        for page in pages:
            for line in self._lines(page):
                for pattern in self.settings["rfp"].get("requirement_overview_patterns", []):
                    match = re.search(pattern, line, re.IGNORECASE)
                    if match and int(match.group(1)) != len(legacy):
                        issues.append(ExtractionIssue(
                            issue_type="REQUIREMENT_COUNT_MISMATCH",
                            message=f"총괄표/본문의 {match.group(1)}건과 상세 기술요구 추출 {len(legacy)}건이 다릅니다. 누락·중복을 검토하세요.",
                            evidence=[Evidence(document=document, pdf_page=page.number, section="요구사항 총괄", excerpt=line[:500])],
                        ))
        if not legacy:
            issues.append(ExtractionIssue(
                issue_type="NO_REQUIREMENTS_EXTRACTED",
                message="구조화된 기술요구사항을 찾지 못했습니다. 표/스캔 PDF는 OCR과 수동 확인이 필요합니다.",
                evidence=[],
            ))

    def _make_project_summary(self, basic: RFPBasicInfo, document: str, document_kind: str) -> ProjectSummary:
        """다른 Agent가 사용할 사업 개요를 기존 데이터 형태로 만듭니다."""
        summary_evidence = [
            Evidence(document=document, source_document=document_kind, pdf_page=c.source_page,
                     section="기본정보 후보", excerpt=c.source_excerpt)
            for c in (basic.project_name, basic.procuring_entity, basic.budget_krw, basic.contract_period)
            if c.value is not None and c.source_page is not None
        ]
        return ProjectSummary(
            project_name=basic.project_name.value if isinstance(basic.project_name.value, str) and self._is_valid_candidate("project_name", basic.project_name)[0] else None,
            buyer=basic.procuring_entity.value if isinstance(basic.procuring_entity.value, str) and self._is_valid_candidate("procuring_entity", basic.procuring_entity)[0] else None,
            budget_krw=basic.budget_krw.value if isinstance(basic.budget_krw.value, int) else None,
            budget_original_text=basic.budget_krw.source_excerpt or None,
            estimated_price_krw=basic.estimated_price_krw.value if isinstance(basic.estimated_price_krw.value, int) else None,
            estimated_price_original_text=basic.estimated_price_krw.source_excerpt or None,
            period=basic.contract_period.value if isinstance(basic.contract_period.value, str) else None,
            submission_deadline=basic.proposal_deadline.value if isinstance(basic.proposal_deadline.value, str) and self._is_valid_candidate("proposal_deadline", basic.proposal_deadline)[0] else None,
            scope=basic.project_purpose.value if isinstance(basic.project_purpose.value, str) else None,
            evidence=summary_evidence,
        )

    def analyze(self, pdf_path: str | Path, notice: dict | None = None, document_kind: str = "rfp_pdf") -> RFPAnalysisResult:
        """[진입점] PDF 1개를 읽고 구조화한 뒤 RFPAnalysisResult로 돌려줍니다.

        notice 인자는 이전 호출 방식과 호환되도록 그대로 유지합니다.
        """
        source = Path(pdf_path)

        # 1단계: PDF 페이지를 읽기
        extracted = self._extract_pages(source)
        # 예전 테스트에서 반환하던 2개/3개짜리 형식까지 지원합니다.
        if len(extracted) == 2:
            pages, issues = extracted
            analyses, needs_ocr = [], False
        elif len(extracted) == 3:
            pages, issues, needs_ocr = extracted
            analyses = []
        else:
            pages, issues, analyses, needs_ocr = extracted

        # 2단계: 사업명·기관·날짜·예산 등 기본정보 확인
        basic, candidates, selection_reasons = self._basic_info(pages, document_kind)

        # 3단계: 요구사항 추출·분류
        eligibility, staffing, submission, technical, unclassified, legacy, deliverables = self._analyze_requirements(
            pages, source.name, document_kind,
        )

        # 4단계: 품질 검사와 진단 기록
        quality = self._quality_gate(basic, needs_ocr, document_kind, (eligibility, staffing, submission, technical))
        self._check_extraction_issues(issues, pages, basic, quality, legacy, source.name)

        # 5단계: 기존 CrewAI 및 보고서 코드가 이해하는 결과 형태로 반환
        return RFPAnalysisResult(
            analysis_scope="notice_only" if document_kind == "notice_pdf" else "rfp_only",
            source_document=document_kind,
            project_summary=self._make_project_summary(basic, source.name, document_kind),
            basic_info=basic,
            basic_info_candidates=candidates,
            basic_info_selection_reasons=[FieldSelectionReason(field=field, reason=reason) for field, reason in selection_reasons.items()],
            page_analysis=analyses, quality_gate=quality, needs_ocr=needs_ocr,
            eligibility_requirements=eligibility, staffing_requirements=staffing,
            submission_requirements=submission, deliverable_requirements=deliverables,
            technical_requirements=technical, unclassified_requirements=unclassified,
            requirements=legacy, extraction_issues=issues,
        )



def _vat_consistent(candidates: list[RFPFieldCandidate]) -> tuple[RFPFieldCandidate, str] | None:
    """금액 후보들이 VAT 포함/별도 표기만 다르고 같은 금액이면 (선택 후보, 사유). 아니면 None.

    VAT 포함 표기 금액을 예외처리해, VAT 별도 금액과 숫자만 달라도 충돌로 보지 않는다.
    기준을 모르는 후보가 하나라도 있으면 임의로 맞추지 않는다(None → 기존처럼 확인 필요).
    """
    based = [(item, vat.vat_basis(item.source_excerpt)) for item in candidates if isinstance(item.value, int)]
    if len(based) != len(candidates) or any(basis == "unknown" for _, basis in based):
        return None
    anchor, anchor_basis = based[0]
    for item, basis in based[1:]:
        if vat.compare(anchor.value, anchor_basis, item.value, basis)[0] not in {"same", "same_after_vat"}:
            return None
    # VAT 별도 후보를 대표값으로 쓴다(추정가격·RFP 사업금액과 같은 기준). 없으면 점수 순.
    chosen = sorted(based, key=lambda pair: (pair[1] != "excluded", -pair[0].score, pair[0].source_page or 9999))[0][0]
    shown = ", ".join(f"{item.value:,}원({vat.BASIS_LABELS[basis]})" for item, basis in based)
    return chosen, f"금액 후보 {shown}는 VAT 표기 차이만 있어 같은 금액으로 판단하고 {chosen.value:,}원을 선택"


def _period_end(value: str) -> tuple[int, int, int] | None:
    """계약기간 문자열의 마지막 날짜(년, 월, 일). '계약일로부터 2027년 2월 15일까지' → (2027, 2, 15)."""
    dates = re.findall(r"(20\d{2})\s*[./년\-]\s*(\d{1,2})\s*[./월\-]\s*(\d{1,2})", value or "")
    return tuple(int(part) for part in dates[-1]) if dates else None  # type: ignore[return-value]


def _compare_amount(field: str, rfp_value: RFPFieldCandidate, notice_value: RFPFieldCandidate) -> tuple[str, str] | None:
    if not (isinstance(rfp_value.value, int) and isinstance(notice_value.value, int)):
        return None
    return vat.compare(rfp_value.value, vat.vat_basis(rfp_value.source_excerpt),
                       notice_value.value, vat.vat_basis(notice_value.source_excerpt))


def _title_is_consistent(left: str, right: str) -> bool:
    """공고문과 RFP의 사업명이 실질적으로 같은지 비교합니다."""
    compact_left = re.sub(r"\s+", "", left).replace("용역", "")
    compact_right = re.sub(r"\s+", "", right).replace("용역", "")
    if len(compact_left) >= 8 and len(compact_right) >= 8 and (compact_left in compact_right or compact_right in compact_left):
        return True
    match = SequenceMatcher(None, compact_left, compact_right).find_longest_match()
    if min(len(compact_left), len(compact_right)) >= 8 and match.size / min(len(compact_left), len(compact_right)) >= 0.55:
        return True
    def tokens(value: str) -> set[str]:
        ignored = {"용역", "사업", "제안요청서", "공고"}
        return {token for token in re.findall(r"[A-Za-z0-9가-힣]+", value) if token not in ignored}
    a, b = tokens(left), tokens(right)
    return bool(a and b and len(a & b) / max(len(a), len(b)) >= 0.60)


def merge_document_results(rfp: RFPAnalysisResult, notice: RFPAnalysisResult | None) -> RFPAnalysisResult:
    """RFP·공고문 결과를 병합합니다. 검증된 공고문 값만 우선합니다."""
    merged = rfp.model_copy(deep=True)
    merged.rfp_quality_gate = rfp.quality_gate
    if notice is None:
        merged.analysis_scope = "rfp_only"
        merged.notice_quality_gate = RFPQualityGate(
            status="not_provided",
            message="공고문 PDF가 제공되지 않아 공고번호·발주기관·법적 참가자격의 원본 확인이 필요합니다.",
        )
        merged.integration_quality_gate = RFPQualityGate(
            status="needs_manual_verification",
            issues=[RFPQualityIssue(field="notice_pdf", reason="공고문 PDF 미제공: 시장조사와 법적 참가자격 확정은 실행하지 않습니다.")],
        )
        merged.market_research_allowed = False
        return merged

    merged.analysis_scope = "notice_and_rfp"
    merged.notice_document = notice.project_summary.evidence[0].document if notice.project_summary.evidence else "notice_pdf"
    merged.notice_quality_gate = notice.quality_gate
    merged.page_analysis = rfp.page_analysis + notice.page_analysis
    merged.extraction_issues = rfp.extraction_issues + notice.extraction_issues
    conflicts: list[DocumentConflict] = []

    for field in ("project_name",):
        rfp_value = getattr(rfp.basic_info, field)
        notice_value = getattr(notice.basic_info, field)
        if not (rfp_value.value and notice_value.value):
            continue
        if not (RFPExtractor._is_valid_candidate(field, rfp_value)[0] and RFPExtractor._is_valid_candidate(field, notice_value)[0]):
            continue
        same = _title_is_consistent(str(rfp_value.value), str(notice_value.value)) if field == "project_name" else str(rfp_value.value) == str(notice_value.value)
        if not same:
            conflicts.append(DocumentConflict(
                field=field, rfp_value=rfp_value, notice_value=notice_value,
                reason="공고문과 RFP의 값이 달라 자동으로 한 값을 확정하지 않았습니다.",
            ))

    # 사업금액: VAT 포함/별도 기준을 맞춘 뒤 비교한다. 표기 차이(예: 107,000,000원 VAT 별도 ↔ 117,700,000원 VAT 포함)는
    # 충돌이 아니고, 기준을 모르는 약 10% 차이는 '확인 필요', 기준을 맞춰도 다르면 실제 충돌로 남긴다.
    vat_notes: list[FieldSelectionReason] = []
    vat_issues: list[RFPQualityIssue] = []
    for field in ("budget_krw", "estimated_price_krw"):
        rfp_value, notice_value = getattr(rfp.basic_info, field), getattr(notice.basic_info, field)
        compared = _compare_amount(field, rfp_value, notice_value)
        if compared is None:
            continue
        state, detail = compared
        if state in {"same_after_vat", "same_basis_unknown"}:
            # same_basis_unknown: 숫자는 같으나 VAT 기준 미확인 — 충돌은 아니지만 '확인됨'으로 기록하지 않는다.
            vat_notes.append(FieldSelectionReason(field=f"{field}_vat", reason=detail))
        elif state in {"vat_unknown", "basis_mismatch"}:
            vat_issues.append(RFPQualityIssue(field=field, reason=f"VAT 기준 확인 필요: {detail}", source_page=notice_value.source_page))
        elif state == "different":
            conflicts.append(DocumentConflict(
                field=field, rfp_value=rfp_value, notice_value=notice_value,
                reason=f"공고문과 RFP의 금액이 다릅니다: {detail}",
            ))

    # 계약기간: 종료일이 다르면 문서 판본 확인이 필요한 충돌(예: RFP 2027년 2월 15일 ↔ 공고문 2026년 2월 15일).
    rfp_period, notice_period = rfp.basic_info.contract_period, notice.basic_info.contract_period
    if isinstance(rfp_period.value, str) and isinstance(notice_period.value, str):
        rfp_end, notice_end = _period_end(rfp_period.value), _period_end(notice_period.value)
        if rfp_end and notice_end and rfp_end != notice_end:
            conflicts.append(DocumentConflict(
                field="contract_period", rfp_value=rfp_period, notice_value=notice_period,
                reason=(f"계약 종료일이 RFP {rfp_end[0]}년 {rfp_end[1]}월 {rfp_end[2]}일, 공고문 {notice_end[0]}년 "
                        f"{notice_end[1]}월 {notice_end[2]}일로 다릅니다. 실제 원문과 문서 판본(정정공고 여부)을 확인해 주세요."),
            ))

    # 공동도급: 한쪽이라도 금지하면 금지를 우선하고, 다른 쪽이 허용이면 충돌로 남긴다.
    rfp_jv, notice_jv = rfp.basic_info.joint_venture_policy, notice.basic_info.joint_venture_policy
    rfp_forbids = isinstance(rfp_jv.value, str) and rfp_jv.value.startswith(JOINT_FORBIDDEN_POLICY)
    notice_forbids = isinstance(notice_jv.value, str) and notice_jv.value.startswith(JOINT_FORBIDDEN_POLICY)
    if rfp_jv.value and notice_jv.value and rfp_forbids != notice_forbids:
        conflicts.append(DocumentConflict(
            field="joint_venture_policy", rfp_value=rfp_jv, notice_value=notice_jv,
            reason="공고문과 RFP의 공동도급 허용 여부가 다릅니다. 금지 조건을 우선 적용했으며 원문 확인이 필요합니다.",
        ))

    # Official notice fields win only when their own document gate is valid.
    if notice.quality_gate.passed:
        for field in (
            "notice_number", "procuring_entity", "demanding_entity", "proposal_submission_start",
            "proposal_deadline", "bid_deadline", "joint_venture_deadline", "estimated_price_krw",
        ):
            candidate = getattr(notice.basic_info, field)
            if field == "procuring_entity" and candidate.value is None:
                candidate = notice.basic_info.demanding_entity
            if candidate.value is not None:
                setattr(merged.basic_info, field, candidate)
                setattr(merged.basic_info_candidates, field, list(getattr(notice.basic_info_candidates, field)))
                notice_reason = next((item.reason for item in notice.basic_info_selection_reasons if item.field == field), "공고문 우선값")
                merged.basic_info_selection_reasons = [item for item in merged.basic_info_selection_reasons if item.field != field]
                merged.basic_info_selection_reasons.append(FieldSelectionReason(field=field, reason=notice_reason))
        if notice.basic_info.budget_krw.value is not None:
            merged.basic_info.budget_krw = notice.basic_info.budget_krw
            merged.basic_info_candidates.budget_krw = list(notice.basic_info_candidates.budget_krw)
        if notice.basic_info.joint_venture_policy.value is not None and not (rfp_forbids and not notice_forbids):
            merged.basic_info.joint_venture_policy = notice.basic_info.joint_venture_policy
        merged.eligibility_requirements = list(notice.eligibility_requirements)
        if not any(item.evidence.section == "공동도급 참가 제한" for item in merged.eligibility_requirements):
            # 공고문에 없어도 RFP의 공동도급 금지 조항은 참가자격에서 빠뜨리지 않는다.
            merged.eligibility_requirements.extend(
                item for item in rfp.eligibility_requirements if item.evidence.section == "공동도급 참가 제한"
            )

    merged.conflicts = conflicts
    merged.basic_info_selection_reasons.extend(vat_notes)
    integration_issues: list[RFPQualityIssue] = list(vat_issues)
    if not rfp.quality_gate.passed:
        integration_issues.extend(rfp.quality_gate.issues)
    if not notice.quality_gate.passed:
        integration_issues.extend(notice.quality_gate.issues)
    integration_issues.extend(RFPQualityIssue(field=item.field, reason=item.reason) for item in conflicts)
    status = "passed" if not integration_issues else ("needs_ocr" if rfp.quality_gate.status == "needs_ocr" or notice.quality_gate.status == "needs_ocr" else "needs_manual_verification")
    merged.integration_quality_gate = RFPQualityGate(status=status, issues=integration_issues)
    merged.quality_gate = merged.integration_quality_gate
    merged.market_research_allowed = merged.integration_quality_gate.passed and bool(
        merged.basic_info.project_name.value and (merged.basic_info.notice_number.value or merged.basic_info.procuring_entity.value)
    )
    merged.project_summary.buyer = merged.basic_info.procuring_entity.value if isinstance(merged.basic_info.procuring_entity.value, str) else None
    merged.project_summary.budget_krw = merged.basic_info.budget_krw.value if isinstance(merged.basic_info.budget_krw.value, int) else None
    merged.project_summary.budget_original_text = merged.basic_info.budget_krw.source_excerpt or None
    merged.project_summary.estimated_price_krw = merged.basic_info.estimated_price_krw.value if isinstance(merged.basic_info.estimated_price_krw.value, int) else None
    merged.project_summary.estimated_price_original_text = merged.basic_info.estimated_price_krw.source_excerpt or None
    merged.project_summary.submission_deadline = (
        merged.basic_info.proposal_deadline.value if isinstance(merged.basic_info.proposal_deadline.value, str) else None
    )
    return merged
