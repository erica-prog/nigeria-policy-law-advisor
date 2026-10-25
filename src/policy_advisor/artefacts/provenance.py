"""The provenance manifest (docs/35): every paragraph of an artefact to the
passages that support it, in the plugin's source-provenance fields.

For each source a paragraph names:
- a locator that resolves to a chunk retrieved during the run whose trust
  kind is `authority` or `firm` becomes a `verified` entry;
- a URL a tool recorded during the run becomes a `verified` entry with the
  source's registry id;
- a locator that resolves only to a matter-tier (`evidence`) chunk makes
  the paragraph `rejected`: case evidence is never authority (AGENTS.md
  rule 4), and the renderers replace the paragraph with a marker;
- anything else is `unverified`, and the paragraph is kept and marked.

A `kind: "instruction"` paragraph needs no sources and is recorded as such.
The judge's verdict per section is added afterwards (`draft.py`).
"""

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from policy_advisor.api.citations import find_chunk_for_locator
from policy_advisor.artefacts.outline import Outline

AUTHORITY_TRUST = frozenset({"authority", "firm"})


@dataclass
class ManifestEntry:
    section: int
    paragraph: int
    proposition: str
    pinpoint: str  # the locator or URL as cited
    status: str  # verified | unverified | rejected_evidence | instruction
    instrument: str = ""  # source document or source name
    issuing_body: str = ""
    identifier: str = ""  # chunk id or URL
    source_id: str = ""
    trust_kind: str = ""
    version_date: str = ""
    access_date: str = ""
    verification: str = "deterministic"
    limitations: str = ""


@dataclass
class Manifest:
    entries: list[ManifestEntry] = field(default_factory=list)
    judge: list[dict[str, Any]] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    @property
    def rejected_paragraphs(self) -> set[tuple[int, int]]:
        return {(e.section, e.paragraph) for e in self.entries if e.status == "rejected_evidence"}

    @property
    def unverified_paragraphs(self) -> set[tuple[int, int]]:
        return {(e.section, e.paragraph) for e in self.entries if e.status == "unverified"}

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in self.entries:
            out[entry.status] = out.get(entry.status, 0) + 1
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "counts": self.counts(),
            "entries": [asdict(e) for e in self.entries],
            "judge": self.judge,
        }


def build_manifest(outline: Outline, chunks: list[Any], recorded_urls: dict[str, Any]) -> Manifest:
    """`chunks` are RetrievedChunk-like (text, metadata); `recorded_urls`
    maps a URL to an object with title, source_id, trust_kind."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    manifest = Manifest()
    for s_index, section in enumerate(outline.sections):
        for p_index, paragraph in enumerate(section.paragraphs):
            proposition = paragraph.text[:200]
            if paragraph.kind == "instruction":
                manifest.entries.append(
                    ManifestEntry(
                        s_index,
                        p_index,
                        proposition,
                        "",
                        "instruction",
                        limitations="restates the lawyer's instruction; not authority",
                    )
                )
                continue
            if not paragraph.sources:
                manifest.entries.append(
                    ManifestEntry(
                        s_index,
                        p_index,
                        proposition,
                        "",
                        "unverified",
                        limitations="no source named",
                    )
                )
                continue
            for cited in paragraph.sources:
                recorded = recorded_urls.get(cited)
                if recorded is not None:
                    manifest.entries.append(
                        ManifestEntry(
                            s_index,
                            p_index,
                            proposition,
                            cited,
                            "verified",
                            instrument=str(getattr(recorded, "title", "") or cited),
                            identifier=cited,
                            source_id=str(getattr(recorded, "source_id", "") or ""),
                            trust_kind=str(getattr(recorded, "trust_kind", "") or ""),
                            access_date=today,
                        )
                    )
                    continue
                chunk = find_chunk_for_locator(cited, chunks)
                if chunk is None:
                    manifest.entries.append(
                        ManifestEntry(
                            s_index,
                            p_index,
                            proposition,
                            cited,
                            "unverified",
                            limitations="not among the passages the run retrieved",
                            access_date=today,
                        )
                    )
                    continue
                meta = chunk.metadata
                trust = str(
                    meta.get("trust_kind")
                    or (
                        "authority"
                        if meta.get("doc_type") in ("statute", "judgment")
                        else "evidence"
                    )
                )
                status = "verified" if trust in AUTHORITY_TRUST else "rejected_evidence"
                manifest.entries.append(
                    ManifestEntry(
                        s_index,
                        p_index,
                        proposition,
                        cited,
                        status,
                        instrument=str(meta.get("source_document") or ""),
                        issuing_body=str(meta.get("jurisdiction") or ""),
                        identifier=str(meta.get("chunk_id") or meta.get("locator") or ""),
                        source_id=str(meta.get("source_id") or ""),
                        trust_kind=trust,
                        version_date=str(meta.get("effective_date") or ""),
                        access_date=today,
                        limitations=""
                        if status == "verified"
                        else "cites case evidence as authority; paragraph removed",
                    )
                )
    return manifest
