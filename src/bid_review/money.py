"""Korean public-procurement monetary expression parser."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


_EOK = Decimal("100000000")


def parse_krw(value: str) -> int | None:
    """Return a won integer for e.g. 3억, 1.07억, 10.7억 or 1,070,000,000원.

    It intentionally returns None rather than guessing for an unrecognised expression.
    """
    if not value:
        return None
    normalized = value.replace(" ", "").replace(",", "")
    units = {"천억": 100_000_000_000, "백억": 10_000_000_000, "십억": 1_000_000_000,
             "억": 100_000_000, "천만": 10_000_000, "백만": 1_000_000,
             "십만": 100_000, "만": 10_000, "천": 1_000, "백": 100, "십": 10, "원": 1}
    pattern = re.compile(r"(\d+(?:\.\d+)?)(천억|백억|십억|억|천만|백만|십만|만|천|백|십|원)")
    first = pattern.search(normalized)
    if not first:
        return None
    total, position, match = Decimal(0), first.start(), first
    try:
        while match and match.start() == position:
            total += Decimal(match.group(1)) * units[match.group(2)]
            position = match.end()
            if match.group(2) == "원":
                break
            match = pattern.match(normalized, position)
        # Do not silently drop an unsupported Korean-number suffix.
        tail = normalized[position:]
        if re.match(r"[일이삼사오육칠팔구십백천]+(?:만|억)", tail):
            return None
        return int(total.to_integral_value(rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None


def format_krw(amount: int | None) -> str:
    return f"{amount:,}원" if amount is not None else "확인 필요"
