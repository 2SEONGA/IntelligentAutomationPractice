"""Select business-description text before embedding a past RFP (no LLM calls)."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from .corpus import _privacy_filter


PROFILE_VERSION = 3  # M41: "사업 추진 배경"·"주요 과업 내용" 표제 인식
MIN_BODY_CHARS = 80
TOC_LEADER = re.compile(r"[·.…ㆍ‥\-]{5,}\s*\d{0,3}\s*$|[·…]{3,}|\t\s*\d{1,3}\s*$")
_TOPIC = re.compile(
    r"(?:사업(?:의)?(?:개요|목적|범위|배경|내용)|사업의배경및목적|사업(?:공간적|내용적)범위|"
    r"추진(?:배경|목적)|과업(?:개요|범위|내용|목적)|제안요청내용|구축(?:목적|범위)|추진배경및필요성|"
    r"사업추진(?:배경|목적)|주요과업(?:내용)?)"  # "사업 추진 배경"·"주요 과업 내용"(R26BK01550694)
)
_PREFIX = re.compile(r"^(?:[IVXLC]+[.、)]?|\d+(?:\.\d+)*(?:절|장|[.、)])|\d+\s+(?=[가-힣A-Za-z])|[가-하][.、)])\s*")
_ADMIN = re.compile(
    r"^(?:입찰(?:참가|안내|유의|방법|방식)|사업자선정|추진방식|적용규정|참가자격|제안(?:안내|서작성|서평가|서제출)|제안요청일반|"
    r"계약(?:조건|일반|특수|사항)|일반(?:사항|조건)|사업추진일반사항|제출서류|평가(?:방법|기준)|"
    r"요구사항(?:분류|목록|총괄|정의|구분)|(?:별지|별첨|붙임|서식|양식)|보안(?:서약|확약)|비밀유지|청렴|지체상금|대금지급|손해배상)"
)
_FORM = re.compile(r"심의결과서|심의위원회|소요예산적정성|원안승인|작성요령|반드시기재")
_ARTICLE = re.compile(r"^제\s*\d+\s*조\s*[（(]")
_LEGAL = re.compile(r"[‘'\"“”]?(?:갑|을)[’'\"“”](?:은|는|이|의|에게|과)|계약상대자|지체상금|손해배상|계약의해제|계약보증")
_BUSINESS = re.compile(r"시스템|플랫폼|데이터|정보화|인프라|네트워크|서비스|스마트|소프트웨어|구축|개발|고도화|통합|유지관리|운영|연계")
_OUTLINE = re.compile(r"^(?:[IVXLC]+[.、)]?\s+|\d+(?:\.\d+)*(?:절|장|[.、)])\s*|\d+\s+(?=[가-힣A-Za-z])|[가-하][.、)]\s*)")


class ProfileExtractionError(ValueError):
    """No defensible business description: quarantine rather than embed boilerplate."""


@dataclass(frozen=True)
class ProfileBody:
    text: str
    method: str
    # PDF physical pages; HWP/HWPX use the reader's numbered 50-line blocks.
    source_blocks: tuple[int, ...]


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _title(line: str) -> str:
    text = unicodedata.normalize("NFKC", line).strip(" \t□○◦■◆◇▶▷-<>【】[]")
    for _ in range(3):
        updated = _PREFIX.sub("", text).strip()
        if updated == text:
            break
        text = updated
    return _compact(text)


def _topic_heading(line: str) -> bool:
    if TOC_LEADER.search(line) or _FORM.search(_compact(line)):
        return False
    if re.search(r"\s+\d{1,3}\s*$", line) and not re.search(r"[:：]", line):
        return False  # Contents entries such as "Ⅰ 사업 개요 1" have no dot leaders.
    text = _title(line)
    match = _TOPIC.match(text)
    # Avoid treating an ordinary sentence containing "사업 내용" as a heading.
    return bool(match and (len(text) <= 45 or text[match.end():].startswith((":", "："))))


def _admin_heading(line: str) -> bool:
    return bool(_ARTICLE.match(line.strip()) or (len(line.strip()) < 90 and
                (_ADMIN.match(_title(line)) or _FORM.search(_compact(line)))))


def _outline_only(line: str) -> bool:
    text = line.strip()
    if TOC_LEADER.search(text) or _compact(text).strip("-<>【】[]") == "목차":
        return True
    # Colon-delimited field values and long prose are substantive, unlike bare numbered titles.
    return bool(len(text) < 80 and _OUTLINE.match(unicodedata.normalize("NFKC", text))
                and not re.search(r"[:：]|(?:한다|한다\.|함\.|됨\.|있다|있음|하여야|해야)", text))


def _usable(line: str) -> bool:
    compact = _compact(line)
    return bool(len(compact) >= 14 and not _outline_only(line)
                and not _LEGAL.search(compact) and not _admin_heading(line) and not _garbled(line))


def _garbled(line: str) -> bool:
    korean = re.findall(r"[가-힣]", line)
    pairs = re.findall(r"([가-힣])\1", line)
    return len(pairs) >= 8 and len(pairs) * 2 / max(1, len(korean)) > 0.35


def extract_profile_body(pages: list[str], max_chars: int = 2500) -> ProfileBody:
    """Choose bounded heading sections; fail explicitly if only a TOC/contract remains.

    Look across page/block boundaries. A later repeated heading with actual prose
    wins over an early contents entry. Never use arbitrary front pages as a fallback.
    """
    rows: list[tuple[int, str]] = []
    for number, raw in enumerate(pages, 1):
        # Preserve tabs until TOC detection; filter contacts only afterwards.
        clean, _ = _privacy_filter("\n".join(line for line in raw.splitlines()
                                         if not TOC_LEADER.search(line) and not _garbled(line)))
        rows.extend((number, line) for line in clean.splitlines() if line.strip())

    sections: list[list[tuple[int, str]]] = []
    for index, row in enumerate(rows):
        if not _topic_heading(row[1]):
            continue
        block = [row]
        for later in rows[index + 1:index + 121]:
            if _topic_heading(later[1]) or _admin_heading(later[1]):
                break
            if _outline_only(later[1]):
                continue
            block.append(later)
            if sum(len(line) for _, line in block) >= max_chars * 2:
                break
        substantive = [line for _, line in block[1:] if _usable(line)]
        # Inline "사업목적: ..." can itself contain the substantive description.
        if re.search(r"[:：]", row[1]) and _usable(row[1]):
            substantive.append(row[1])
        if len(_compact("".join(dict.fromkeys(substantive)))) >= MIN_BODY_CHARS and _BUSINESS.search(" ".join(substantive)):
            sections.append(block)

    method = "business_sections"
    if not sections:
        # Heading-less documents: require several actual technical descriptions,
        # and suppress complete administrative sections rather than just their titles.
        fallback: list[tuple[int, str]] = []
        in_admin = False
        for row in rows:
            if _topic_heading(row[1]):
                in_admin = False
            elif _admin_heading(row[1]):
                in_admin = True
            if not in_admin and _usable(row[1]) and _BUSINESS.search(row[1]):
                fallback.append(row)
        unique_fallback: dict[str, tuple[int, str]] = {}
        for row in fallback:
            unique_fallback.setdefault(_compact(row[1]), row)
        fallback = list(unique_fallback.values())
        body = "\n".join(line for _, line in fallback)
        if len(_compact(body)) < MIN_BODY_CHARS * 2 or len(fallback) < 2:
            raise ProfileExtractionError("실제 사업 설명을 확인하지 못했습니다. 목차·계약/서식 또는 본문 부족 자료를 확인해 주세요.")
        sections, method = [fallback], "technical_paragraphs"

    selected: list[tuple[int, str]] = []
    seen: set[str] = set()
    for section in sections:
        for number, line in section:
            key = _compact(line)
            if key and key not in seen and not _LEGAL.search(key):
                seen.add(key)
                selected.append((number, line))
    text, used_blocks, remaining = [], [], max_chars
    for number, line in selected:
        if remaining <= 0:
            break
        text.append(line[:remaining])
        used_blocks.append(number)
        remaining -= len(line) + 1
    return ProfileBody("\n".join(text), method, tuple(dict.fromkeys(used_blocks)))
