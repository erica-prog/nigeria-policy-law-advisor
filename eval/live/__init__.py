"""Graded cases for the live evaluation harness (docs/40, slice P4-B).

A case file is one YAML document per kind under `eval/live/cases/`, in the
legal-ai-skills plugin's eval shape (`prompt`, `must_include`,
`must_not_include`, as the routing evals use) extended with what docs/39 D1
asked for: `graders` and `must_cite`. Each file carries its own synthetic
matters, so a lawyer can read a case and the documents it runs against in
one place, and nothing here resembles a real client document (AGENTS.md
rule 7).

The loader validates every file and refuses a malformed case by its path and
id, before any model is called.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

CASES_DIR = Path(__file__).parent / "cases"

Kind = Literal["injection", "evidence_vs_authority", "citations", "routing", "research"]
Grader = Literal["deterministic", "rubric", "judge"]
Intent = Literal["auto", "analyze", "ask", "research"]
Mode = Literal["analysis", "research", "question"]
CitationKind = Literal["evidence", "authority", "web"]

KINDS: tuple[Kind, ...] = ("injection", "evidence_vs_authority", "citations", "routing", "research")


class CaseFileError(ValueError):
    pass


class DocumentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Za-z0-9._-]+\.(docx|txt)$")
    paragraphs: list[str] = Field(min_length=1)


class MatterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^eval-live-[a-z0-9-]+$")
    jurisdiction: str | None = None
    documents: list[DocumentSpec] = Field(default_factory=list)


class Case(BaseModel):
    """One graded prompt. The deterministic fields are checked by
    `eval.live.grade`; `judge_focus` is appended to the kind's rubric when
    `judge` is among the graders."""

    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    prompt: str = Field(min_length=1)
    matter: str
    intent: Intent = "auto"
    allow_web: bool = False
    jurisdiction: str | None = None
    # Offer the fake connector (eval/live/fake_connector_server.py) to the
    # research agent for this case; it plants an instruction in a result.
    fake_connector: bool = False
    expected_mode: Mode | None = None
    # A playbook id, or "none" to require that no playbook was chosen.
    expected_playbook: str | None = None
    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    # Locators the reply must cite (matched under the locator scheme, so a
    # pinpoint inside a retrieved rule counts) and must not cite.
    must_cite: list[str] = Field(default_factory=list)
    must_not_cite: list[str] = Field(default_factory=list)
    # Uploaded documents the reply must cite, by file name. A short upload is
    # one chunk ("Section 1") until the clause parser lands (docs/39 P4-E), so
    # clause-level citations on uploads cannot be asked for yet.
    must_cite_documents: list[str] = Field(default_factory=list)
    # When set, every citation's kind must be one of these.
    citation_kinds: list[CitationKind] | None = None
    # None: the reply's own faithfulness verdict must be true when it has one.
    # An explicit value is only meaningful for question and research replies.
    expect_faithful: bool | None = None
    graders: list[Grader] = Field(default_factory=lambda: ["deterministic"], min_length=1)
    judge_focus: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> Case:
        if self.expected_mode == "analysis" and self.expect_faithful is not None:
            raise ValueError(
                "expect_faithful is set but an analysis reply carries no faithfulness verdict"
            )
        if self.fake_connector and self.intent not in ("auto", "research"):
            raise ValueError("fake_connector needs a research run (intent auto or research)")
        if "judge" in self.graders and not self.judge_focus.strip():
            raise ValueError("a judged case needs judge_focus: what the judge must look for")
        if len(set(self.graders)) != len(self.graders):
            raise ValueError("graders repeats a grader")
        return self


class CaseFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    kind: Kind
    matters: list[MatterSpec] = Field(min_length=1)
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode="after")
    def _references(self) -> CaseFile:
        matters = {m.id for m in self.matters}
        if len(matters) != len(self.matters):
            raise ValueError("a matter id is defined twice")
        ids = [c.id for c in self.cases]
        if len(set(ids)) != len(ids):
            raise ValueError("a case id is defined twice")
        for case in self.cases:
            if case.matter not in matters:
                raise ValueError(f"case {case.id}: matter {case.matter!r} is not defined")
            if case.fake_connector and self.kind != "injection":
                raise ValueError(f"case {case.id}: fake_connector belongs to injection cases")
        return self


class LoadedCase(BaseModel):
    """A case with its file, kind and matter resolved."""

    file: str
    kind: Kind
    case: Case
    matter: MatterSpec


def _explain(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'file'}: {e['msg']}" for e in exc.errors()[:6]
    )


def load_case_file(path: Path) -> CaseFile:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CaseFileError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise CaseFileError(f"{path}: expected a mapping with version, kind, matters, cases")
    try:
        return CaseFile.model_validate(raw)
    except ValidationError as exc:
        raise CaseFileError(f"{path}: {_explain(exc)}") from exc


def load_cases(directory: Path = CASES_DIR, *, kind: str | None = None) -> list[LoadedCase]:
    """Every case under `directory`, in file then case order."""
    loaded: list[LoadedCase] = []
    for path in sorted(directory.glob("*.yaml")):
        case_file = load_case_file(path)
        if kind is not None and case_file.kind != kind:
            continue
        matters = {m.id: m for m in case_file.matters}
        for case in case_file.cases:
            loaded.append(
                LoadedCase(
                    file=path.name, kind=case_file.kind, case=case, matter=matters[case.matter]
                )
            )
    return loaded


def cases_version(directory: Path = CASES_DIR) -> str:
    """The versions of the shipped files, so a verdict says which cases it
    was scored against ("injection:1,research:1")."""
    parts = []
    for path in sorted(directory.glob("*.yaml")):
        case_file = load_case_file(path)
        parts.append(f"{case_file.kind}:{case_file.version}")
    return ",".join(parts)


def sample(cases: list[LoadedCase], limit: int | None) -> list[LoadedCase]:
    """At most `limit` cases, taken round-robin over kinds so a capped run
    still exercises every kind (docs/40 L4)."""
    if limit is None or limit >= len(cases):
        return list(cases)
    by_kind: dict[str, list[LoadedCase]] = {}
    for case in cases:
        by_kind.setdefault(case.kind, []).append(case)
    chosen: list[LoadedCase] = []
    queues = [list(reversed(v)) for _, v in sorted(by_kind.items())]
    while len(chosen) < limit and any(queues):
        for queue in queues:
            if queue and len(chosen) < limit:
                chosen.append(queue.pop())
    order = {id(c): i for i, c in enumerate(cases)}
    return sorted(chosen, key=lambda c: order[id(c)])
