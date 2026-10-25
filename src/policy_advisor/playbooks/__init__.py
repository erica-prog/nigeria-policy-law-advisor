"""Playbooks: what the advisor does for a practice area in a country
(ADR-001 decision on playbooks; docs/23 slice P2-F; docs/29).

A playbook is a YAML file under `data/<practice_area>/<country>.yaml`. It
names the practice area and country from the taxonomy, the words that
trigger it, which mode it runs in, the tools the research agent may call
(an allow-list the policy enforces), a checklist the agent is told to work
through, the fields to extract, the sections of the answer, and the notice
the lawyer sees. Text adapted from the MIT `legal-ai-skills` plugin keeps
its attribution in the file.

The selector (`selector.py`) chooses a playbook and a mode for a message;
nothing in a playbook can widen what the tool policy allows, only narrow it.
"""

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from policy_advisor.jurisdictions import load_taxonomy

MODES = ("analysis", "research", "question")


class InvalidPlaybook(ValueError):
    pass


@dataclass(frozen=True)
class Playbook:
    id: str
    name: str
    practice_area: str
    country: str
    jurisdictions: tuple[str, ...]
    description: str
    triggers: tuple[str, ...]
    mode: str  # the mode when this playbook is chosen and the lawyer did not say
    allowed_tools: tuple[str, ...]
    checklist: tuple[str, ...]
    extraction_schema: dict[str, str]
    output_sections: tuple[str, ...]
    notice: str
    attribution: str = ""
    eval_file: str | None = None
    source_ids: tuple[str, ...] = ()

    def allows_tool(self, tool: str) -> bool:
        return any(fnmatchcase(tool, pattern) for pattern in self.allowed_tools)

    def instructions(self) -> str:
        """The text appended to the research agent's system prompt."""
        lines = [
            f"Playbook: {self.name} ({self.practice_area}, {self.country}).",
            self.description,
            "",
            "Work through this checklist and say which items the sources did not let you complete:",
        ]
        lines.extend(f"{i}. {item}" for i, item in enumerate(self.checklist, start=1))
        if self.extraction_schema:
            lines.append("")
            lines.append("Record these facts when the sources give them, and say when they do not:")
            lines.extend(f"- {key}: {desc}" for key, desc in self.extraction_schema.items())
        lines.append("")
        lines.append(
            "Structure the answer under these headings: " + "; ".join(self.output_sections) + "."
        )
        return "\n".join(lines)

    def route_text(self) -> str:
        """What a reviewer reads to judge a routing decision: the notice and
        the checklist, which the evals test with must_include."""
        return " ".join([self.notice, *self.checklist])


def _as_tuple(value: Any, *, name: str, playbook: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise InvalidPlaybook(f"playbook {playbook}: {name} must be a list of non-empty strings")
    return tuple(v.strip() for v in value)


def parse_playbook(raw: dict[str, Any], *, origin: str = "<memory>") -> Playbook:
    pid = str(raw.get("id") or "").strip()
    if not pid or pid != pid.lower():
        raise InvalidPlaybook(f"{origin}: id must be a lower-case slug")
    taxonomy = load_taxonomy()
    practice_area = str(raw.get("practice_area") or "")
    if practice_area not in taxonomy.practice_areas:
        raise InvalidPlaybook(f"playbook {pid}: unknown practice_area {practice_area!r}")
    country = str(raw.get("country") or "")
    if country not in taxonomy.countries:
        raise InvalidPlaybook(f"playbook {pid}: unknown country {country!r}")
    jurisdictions = _as_tuple(raw.get("jurisdictions"), name="jurisdictions", playbook=pid)
    for j in jurisdictions:
        resolved = taxonomy.resolve(j)
        if resolved is None or resolved.country != country:
            raise InvalidPlaybook(f"playbook {pid}: jurisdiction {j!r} is not in {country}")
    mode = str(raw.get("mode") or "research")
    if mode not in MODES:
        raise InvalidPlaybook(f"playbook {pid}: mode must be one of {MODES}")
    triggers = _as_tuple(raw.get("triggers"), name="triggers", playbook=pid)
    if not triggers:
        raise InvalidPlaybook(f"playbook {pid}: at least one trigger is required")
    allowed = _as_tuple(raw.get("allowed_tools"), name="allowed_tools", playbook=pid)
    if not allowed:
        raise InvalidPlaybook(f"playbook {pid}: allowed_tools must name at least one tool pattern")
    checklist = _as_tuple(raw.get("checklist"), name="checklist", playbook=pid)
    if not checklist:
        raise InvalidPlaybook(f"playbook {pid}: checklist is required")
    schema = raw.get("extraction_schema") or {}
    if not isinstance(schema, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in schema.items()
    ):
        raise InvalidPlaybook(
            f"playbook {pid}: extraction_schema must map field names to descriptions"
        )
    sections = _as_tuple(raw.get("output_sections"), name="output_sections", playbook=pid)
    if not sections:
        raise InvalidPlaybook(f"playbook {pid}: output_sections is required")
    notice = str(raw.get("notice") or "").strip()
    if not notice:
        raise InvalidPlaybook(
            f"playbook {pid}: notice is required (the lawyer must see what this is)"
        )
    return Playbook(
        id=pid,
        name=str(raw.get("name") or pid),
        practice_area=practice_area,
        country=country,
        jurisdictions=tuple(taxonomy.resolve(j).id for j in jurisdictions),  # type: ignore[union-attr]
        description=str(raw.get("description") or "").strip(),
        triggers=tuple(t.lower() for t in triggers),
        mode=mode,
        allowed_tools=allowed,
        checklist=checklist,
        extraction_schema=dict(schema),
        output_sections=sections,
        notice=notice,
        attribution=str(raw.get("attribution") or "").strip(),
        eval_file=raw.get("eval_file"),
        source_ids=_as_tuple(raw.get("source_ids"), name="source_ids", playbook=pid),
    )


def _data_files() -> list[tuple[str, str]]:
    root = resources.files("policy_advisor.playbooks.data")
    found = []
    for area in root.iterdir():
        if not area.is_dir():
            continue
        for item in area.iterdir():
            if item.name.endswith(".yaml"):
                found.append((f"{area.name}/{item.name}", item.read_text(encoding="utf-8")))
    return sorted(found)


@lru_cache
def load_playbooks() -> dict[str, Playbook]:
    """Every packaged playbook by id; a bad file fails at first use, loudly."""
    playbooks: dict[str, Playbook] = {}
    for origin, text in _data_files():
        raw = yaml.safe_load(text)
        if not isinstance(raw, dict):
            raise InvalidPlaybook(f"{origin}: not a mapping")
        playbook = parse_playbook(raw, origin=origin)
        if playbook.id in playbooks:
            raise InvalidPlaybook(f"{origin}: duplicate playbook id {playbook.id}")
        playbooks[playbook.id] = playbook
    return playbooks


def get_playbook(playbook_id: str | None) -> Playbook | None:
    return load_playbooks().get(playbook_id) if playbook_id else None


__all__ = [
    "InvalidPlaybook",
    "MODES",
    "Playbook",
    "field",
    "get_playbook",
    "load_playbooks",
    "parse_playbook",
]
