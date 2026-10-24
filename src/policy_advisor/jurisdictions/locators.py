"""Locator schemes: the grammar of a citation per document family.

The faithfulness check used to accept a cited locator when it *started with* a
retrieved one. That let `Order 5 Rule 3(6)` through as a pinpoint of
`Order 5 Rule 3`, which is right, and also `Order 5 Rule 30`, which is a
different rule. Parsing both sides with the scheme's grammar separates the base
locator from the pinpoint, so support means: same scheme, same base. For
hierarchical schemes (contract clauses) a deeper number is a pinpoint of its
parent. A locator no scheme recognises is supported only by an exact match.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from policy_advisor.jurisdictions import _read_json


@dataclass(frozen=True)
class LocatorScheme:
    id: str
    description: str
    pattern: re.Pattern[str]
    example: str
    hierarchical: bool = False


@dataclass(frozen=True)
class ParsedLocator:
    scheme: str | None
    base: str
    pinpoint: str

    @property
    def recognised(self) -> bool:
        return self.scheme is not None


@lru_cache
def load_schemes() -> tuple[LocatorScheme, ...]:
    raw = _read_json("locator_schemes.json")
    schemes = []
    for s in raw["schemes"]:
        pattern = re.compile(s["pattern"])
        if "base" not in pattern.groupindex or "pin" not in pattern.groupindex:
            raise ValueError(f"locator scheme {s['id']} must define 'base' and 'pin' groups")
        if not pattern.match(s["example"]):
            raise ValueError(
                f"locator scheme {s['id']} does not match its own example {s['example']!r}"
            )
        schemes.append(
            LocatorScheme(
                id=s["id"],
                description=s["description"],
                pattern=pattern,
                example=s["example"],
                hierarchical=bool(s.get("hierarchical", False)),
            )
        )
    return tuple(schemes)


def parse_locator(text: str) -> ParsedLocator:
    cleaned = " ".join(text.split())
    for scheme in load_schemes():
        match = scheme.pattern.match(cleaned)
        if match:
            return ParsedLocator(
                scheme=scheme.id, base=match.group("base"), pinpoint=match.group("pin") or ""
            )
    return ParsedLocator(scheme=None, base=cleaned, pinpoint="")


def scheme_of(text: str) -> str | None:
    return parse_locator(text).scheme


def supports(available: str, cited: str) -> bool:
    """Whether a retrieved locator `available` supports a citation `cited`."""
    a, c = parse_locator(available), parse_locator(cited)
    if not a.recognised or not c.recognised or a.scheme != c.scheme:
        return a.base == c.base and a.pinpoint == c.pinpoint
    if a.base == c.base:
        return True
    if _scheme(a.scheme).hierarchical and c.base.startswith(a.base + "."):
        return True
    return False


def is_supported(cited: str, available_locators: set[str]) -> bool:
    return any(supports(available, cited) for available in available_locators)


def best_support(cited: str, available_locators: list[str]) -> str | None:
    """The retrieved locator that best supports `cited`: an exact match first,
    then the longest supporting base. None when nothing supports it."""
    if cited in available_locators:
        return cited
    candidates = [a for a in available_locators if supports(a, cited)]
    return max(candidates, key=len) if candidates else None


def _scheme(scheme_id: str | None) -> LocatorScheme:
    for scheme in load_schemes():
        if scheme.id == scheme_id:
            return scheme
    raise KeyError(scheme_id)
