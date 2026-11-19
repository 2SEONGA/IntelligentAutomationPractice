"""Rank supplemental T1.5 candidates for human review, never as a fact verdict.

Retrieval ranks (BM25 and cosine) are deliberately not compared with each other.
Field-local values and substantive conditions take precedence over retrieval order.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from ..money import parse_krw
from .verification import AMOUNT_FIELD_QUERIES, SCHEDULE_FIELD_QUERIES, T15Verifier, VerifiedClaim


MAX_CITATIONS = 3
_MONEY = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*(?:천억|백억|십억|억|천만|백만|십만|만|천|백|십|원)"
    r"(?:\s*\d[\d,]*(?:\.\d+)?\s*(?:천억|백억|십억|억|천만|백만|십만|만|천|백|십|원))*"
)
_PERIOD = re.compile(r"(?<!\d)\d+(?:\.\d+)?\s*(?:개월|년간|일간|주간)|(?<!\d)\d{1,3}\s*(?:일|주|년)(?!\d)")
_CONDITION = re.compile(r"허용|불허|금지|불가|제한|가능|필수|등록|신고|갖춘|보유|제출|하여야|해야|없는\s*자")
_TEMPLATE = re.compile(r"작성\s*(?:요령|방법)|기재\s*(?:요령|방법)|입력하세요|기재하세요|작성하십시오|\[서식|\[별지")
_TOC = re.compile(r"[·.…ㆍ‥]{3,}\s*\d*\s*$")
_LABELS = {**AMOUNT_FIELD_QUERIES, **SCHEDULE_FIELD_QUERIES}
_EXTRA_LABELS = {
    "budget_krw": ("소요예산", "소요금액", "총 재원"),
    "contract_period": ("사업기간", "과업기간", "용역기간"),
    "proposal_deadline": ("제안서 접수 마감", "제안서 제출 마감일시"),
}


@dataclass(frozen=True)
class SelectedEvidence:
    evidence: dict[str, Any]
    reason: str


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def evidence_queries(claim: VerifiedClaim) -> list[str]:
    """Use the same schedule-label aliases for retrieval and candidate association."""
    aliases = _EXTRA_LABELS.get(claim.field, ()) if claim.category == "schedule" else ()
    return list(dict.fromkeys([*claim.retrieval_queries, *aliases]))


def _field_contexts(text: str, field: str, queries: list[str]) -> list[str]:
    """Stop at the next field label so another amount/date cannot support this one."""
    compact = _squash(text)
    terms = set(_LABELS.get(field, ())) | set(_EXTRA_LABELS.get(field, ()))
    terms.update(queries)
    target = sorted({_squash(term) for term in terms if term.strip()}, key=len, reverse=True)
    all_labels = set(target)
    for name, labels in _LABELS.items():
        all_labels.update(_squash(term) for term in (*labels, *_EXTRA_LABELS.get(name, ())))
    if not target:
        return []
    pattern = re.compile("|".join(re.escape(term) for term in sorted(all_labels, key=len, reverse=True)))
    matches = list(pattern.finditer(compact))
    return [compact[hit.end():min(hit.end() + 180, matches[i + 1].start() if i + 1 < len(matches) else len(compact))]
            for i, hit in enumerate(matches) if hit.group() in target]


def _score(claim: VerifiedClaim, item: dict[str, Any]) -> tuple[int, str] | None:
    # Metadata/section names alone are insufficient: inspect the actual source text.
    text = "\n".join(line for line in str(item.get("text") or "").splitlines() if not _TOC.search(line))
    if not text.strip():
        return None
    contexts = _field_contexts(text, claim.field, claim.retrieval_queries)
    normalized = claim.normalized_value
    expected = normalized.get("amount_krw") if isinstance(normalized, dict) else claim.claim
    if claim.category == "amount":
        values = {value for context in contexts for match in _MONEY.finditer(context)
                  if (value := parse_krw(match.group())) is not None}
        if not values:
            return None
        # A different value remains visible for review; do not silently discard conflicts.
        same = isinstance(expected, int) and expected in values
        score, reason = (60 if same else 50), "대상 금액 항목과 원 단위로 읽을 수 있는 수치가 함께 있음"
        if same:
            reason += " (T1 값과 같은 수치 포함, 일치 확정은 아님)"
    elif claim.category == "schedule":
        dates = [date for context in contexts for date in T15Verifier._normalize_dates(context)]
        relative = claim.field == "contract_period" and any(_PERIOD.search(context) for context in contexts)
        if not dates and not relative:
            return None
        score, reason = 50, "대상 일정 항목에 유효한 날짜 또는 수행기간이 함께 있음"
    else:
        compact = _squash(text)
        terms = [_squash(q) for q in claim.retrieval_queries if q.strip()]
        if not any(term in compact for term in terms) or not _CONDITION.search(text) or len(compact) < 20:
            return None
        score, reason = 50, "검색한 조건의 주제와 등록·제한·허용 등 구체적인 조건 문구가 함께 있음"
        # Prefer clauses sharing the T1 claim's topic; preserve negations and exceptions verbatim.
        topics = re.findall(r"[가-힣A-Za-z]{3,}", str(claim.claim or ""))
        score += min(5, sum(_squash(topic) in compact for topic in topics))
    if _TEMPLATE.search(text):
        score -= 30
        reason += "; 서식·작성 안내가 섞여 있어 우선순위를 낮춤"
    return score, reason


def select_evidence_candidates(claim: VerifiedClaim, matches: list[dict[str, Any]],
                               limit: int = MAX_CITATIONS) -> list[SelectedEvidence]:
    """Deduplicate and compare candidates, returning at most three located source chunks.

    These are relevant *candidates*, including contradictory clauses/values. They
    do not confirm a claim, edit T1, or resolve VAT/total scope and legal conditions.
    A heading-only result is left unresolved rather than promoted to evidence.
    """
    unique: dict[str, dict[str, Any]] = {}
    for item in matches:
        if item.get("chunk_id"):
            unique.setdefault(item["chunk_id"], item)
    ranked: list[tuple[int, int, dict[str, Any], str]] = []
    seen_texts: set[str] = set()
    for order, item in enumerate(unique.values()):
        fingerprint = _squash(str(item.get("text") or ""))
        if not fingerprint or fingerprint in seen_texts:
            continue
        seen_texts.add(fingerprint)
        result = _score(claim, item)
        if result is not None:
            score, reason = result
            ranked.append((score, order, item, reason))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [SelectedEvidence(item, reason) for _, _, item, reason in ranked[:max(0, min(limit, MAX_CITATIONS))]]
