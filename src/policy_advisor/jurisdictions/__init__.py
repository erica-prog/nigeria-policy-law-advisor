"""Jurisdictions as data, not strings (ADR-001, decision 2).

`taxonomy.json` names the countries, jurisdictions, bodies (courts, agencies,
legislatures) and practice areas the platform knows. A jurisdiction id such as
`ng-lagos` is what the API accepts, what new chunks store, and what filters
compare on. The two values the prototype stored, `federal` and `lagos`, are
aliases of `ng-federal` and `ng-lagos`, so rows written before this module
existed keep matching without a data migration.

`locator_schemes.json` is the companion grammar: how a citation locator is
written per document family, so the faithfulness check can tell a pinpoint
inside a retrieved passage from a different passage altogether. See
`locators.py`.

Both files are package data so they ship with the code. A practising lawyer
has not yet reviewed them (`reviewed_by_lawyer` is false in the taxonomy); the
human-readable guides per country live in `jurisdictions/<country>/` at the
repository root.
"""

import json
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources


class UnknownJurisdiction(ValueError):
    pass


@dataclass(frozen=True)
class Jurisdiction:
    id: str
    country: str
    level: str
    region: str | None
    name: str
    aliases: tuple[str, ...] = ()
    bodies: tuple[str, ...] = ()

    @property
    def storage_values(self) -> tuple[str, ...]:
        """Every value a chunk's `jurisdiction` column may hold for this
        jurisdiction: the id plus its legacy aliases."""
        return (self.id, *self.aliases)


@dataclass(frozen=True)
class Body:
    id: str
    name: str
    kind: str
    jurisdiction: str


@dataclass(frozen=True)
class Taxonomy:
    countries: dict[str, str]
    jurisdictions: dict[str, Jurisdiction]
    bodies: dict[str, Body]
    practice_areas: dict[str, str]
    reviewed_by_lawyer: bool = False
    _aliases: dict[str, str] = field(default_factory=dict, repr=False)

    def resolve(self, value: str | None) -> Jurisdiction | None:
        """The jurisdiction for an id or alias; None for None or empty."""
        if value is None or not str(value).strip():
            return None
        key = str(value).strip().lower()
        canonical = self._aliases.get(key, key)
        try:
            return self.jurisdictions[canonical]
        except KeyError:
            raise UnknownJurisdiction(f"unknown jurisdiction {value!r}") from None

    def ids(self) -> list[str]:
        return sorted(self.jurisdictions)

    def accepted_values(self) -> list[str]:
        """Ids and aliases, for error messages and API documentation."""
        return sorted(set(self.jurisdictions) | set(self._aliases))


def _read_json(name: str) -> dict:
    with (
        resources.files("policy_advisor.jurisdictions.data")
        .joinpath(name)
        .open("r", encoding="utf-8") as f
    ):
        return json.load(f)


@lru_cache
def load_taxonomy() -> Taxonomy:
    raw = _read_json("taxonomy.json")
    jurisdictions: dict[str, Jurisdiction] = {}
    aliases: dict[str, str] = {}
    for jid, j in raw["jurisdictions"].items():
        jurisdictions[jid] = Jurisdiction(
            id=jid,
            country=j["country"],
            level=j["level"],
            region=j.get("region"),
            name=j["name"],
            aliases=tuple(j.get("aliases", [])),
            bodies=tuple(j.get("bodies", [])),
        )
        for alias in j.get("aliases", []):
            if alias in aliases or alias in raw["jurisdictions"]:
                raise ValueError(f"taxonomy alias {alias!r} is ambiguous")
            aliases[alias] = jid
    bodies = {
        bid: Body(id=bid, name=b["name"], kind=b["kind"], jurisdiction=b["jurisdiction"])
        for bid, b in raw["bodies"].items()
    }
    for j in jurisdictions.values():
        if j.country not in raw["countries"]:
            raise ValueError(f"jurisdiction {j.id} names unknown country {j.country}")
        for bid in j.bodies:
            if bid not in bodies or bodies[bid].jurisdiction != j.id:
                raise ValueError(
                    f"jurisdiction {j.id} lists body {bid!r} that does not belong to it"
                )
    return Taxonomy(
        countries={code: c["name"] for code, c in raw["countries"].items()},
        jurisdictions=jurisdictions,
        bodies=bodies,
        practice_areas={pid: p["name"] for pid, p in raw["practice_areas"].items()},
        reviewed_by_lawyer=bool(raw.get("reviewed_by_lawyer", False)),
        _aliases=aliases,
    )


def normalize_jurisdiction(value: str | None) -> str | None:
    """Canonical id for an id or legacy alias; None stays None. Raises
    `UnknownJurisdiction` for anything else, so a typo never becomes a
    silent filter that matches nothing."""
    j = load_taxonomy().resolve(value)
    return j.id if j else None


def storage_values(value: str | None) -> tuple[str, ...]:
    """The values a `jurisdiction` column may hold for this id or alias, for
    SQL and in-memory filters. Empty for None."""
    j = load_taxonomy().resolve(value)
    return j.storage_values if j else ()
