"""Extraction schemas: the typed record a document becomes (docs/33).

The contract schema is seeded from the MIT `legal-ai-skills` plugin's
obligations extractor (Rohas Nagpal, commit ad5df19): obligor, obligee,
obligation, trigger, deadline, conditions, consequence, survival, plus the
parties, key dates, governing law, defined terms and risk clauses a diligence
file needs. Every item carries evidence: the clause locator and the exact
words, which `evidence.py` checks against the document. The model is told to
extract, never to assess (the plugin's own rule); assessment is the lawyer's.

Schemas are versioned; a record is stored with the version that produced
it, and a new version re-extracts rather than silently reinterpreting old rows.
"""

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class EvidenceRef(BaseModel):
    locator: str = Field(
        description="The clause or section locator exactly as marked in the document, e.g. 'Clause 12.3'"
    )
    quote: str = Field(
        description="The exact words from the document that support this item, 5 to 60 words"
    )


class Obligation(BaseModel):
    obligor: str = Field(description="Who owes the obligation, by defined term")
    obligee: str = Field(description="Who it is owed to, by defined term")
    obligation: str = Field(description="What performance discharges it, in one sentence")
    trigger: str = Field(default="", description="A date, an event or another party's act")
    deadline: str = Field(
        default="", description="The period or date as written; no calendar arithmetic"
    )
    conditions: str = Field(
        default="", description="Conditions on which the obligation arises, or empty"
    )
    consequence: str = Field(
        default="", description="What the document says happens on failure, or empty"
    )
    recurring: bool = Field(default=False)
    survives_termination: bool | None = Field(
        default=None, description="True, False, or None when the document is silent"
    )
    evidence: list[EvidenceRef] = Field(default_factory=list)


class Party(BaseModel):
    name: str
    role: str = Field(default="", description="Buyer, Supplier, Licensor, Guarantor...")
    defined_as: str = Field(
        default="", description="The defined term the document uses for this party"
    )
    evidence: list[EvidenceRef] = Field(default_factory=list)


class KeyDate(BaseModel):
    label: str = Field(description="Effective date, Term, Expiry, Notice period, Longstop...")
    date_text: str = Field(description="The date or period exactly as written")
    evidence: list[EvidenceRef] = Field(default_factory=list)


class DefinedTerm(BaseModel):
    term: str
    definition: str
    evidence: list[EvidenceRef] = Field(default_factory=list)


class RiskClause(BaseModel):
    kind: str = Field(
        description="indemnity, limitation of liability, termination, change of control, exclusivity, non-compete, assignment, governing law, dispute resolution, data protection, other"
    )
    summary: str = Field(description="What the clause does, in one sentence, without assessing it")
    mutual: bool | None = Field(default=None)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class ContractExtraction(BaseModel):
    classification: str = Field(
        description="complete executed agreement, complete draft, excerpt, single schedule, or other"
    )
    title: str = Field(default="")
    parties: list[Party] = Field(default_factory=list)
    governing_law: str = Field(default="", description="As written, with its locator in evidence")
    governing_law_evidence: list[EvidenceRef] = Field(default_factory=list)
    key_dates: list[KeyDate] = Field(default_factory=list)
    obligations: list[Obligation] = Field(default_factory=list)
    defined_terms: list[DefinedTerm] = Field(
        default_factory=list, description="Only terms a deadline, party or risk clause depends on"
    )
    risk_clauses: list[RiskClause] = Field(default_factory=list)
    gaps: list[str] = Field(
        default_factory=list,
        description="Obligations without deadlines, deadlines without consequences, undefined terms a deadline depends on, referenced documents not supplied",
    )


CONTRACT_INSTRUCTIONS = """You extract, you do not assess. Read the whole document before writing the record. Record every obligation-creating clause (shall, will, must, agrees to, undertakes, covenants) as an obligation with who owes it, to whom, what discharges it, the trigger, the deadline or period exactly as written, its conditions, the stated consequence of failure, whether it recurs, and whether it survives termination where the document says so. Record the parties with their defined terms, the governing law, every hard date and period, the defined terms a deadline or party depends on, and the risk clauses (indemnity, limitation of liability, termination, change of control, exclusivity, non-compete, assignment, dispute resolution, data protection) with a one-sentence summary of what each does.

Every item must carry evidence: the locator exactly as marked in the text ([Clause 12.3] appears as 'Clause 12.3') and the exact words that support it. Never invent clauses, quotations, defined terms, dates or facts. Do not calculate dates. If the document is an excerpt, say so in the classification and list what it refers to but does not contain under gaps."""

# Adapted from legal-ai-skills (MIT): skills/obligations-extractor/SKILL.md, method steps 1 to 11 and output 2 to 6.
ATTRIBUTION = "Schema and instructions adapted from the MIT-licensed legal-ai-skills plugin (Rohas Nagpal, commit ad5df19), skill obligations-extractor."


class ExtractionSchema(BaseModel):
    schema_id: str
    version: int
    model: type[BaseModel]
    instructions: str
    searchable_fields: tuple[str, ...]

    model_config = {"arbitrary_types_allowed": True}


SCHEMAS: dict[str, ExtractionSchema] = {
    "contract": ExtractionSchema(
        schema_id="contract",
        version=SCHEMA_VERSION,
        model=ContractExtraction,
        instructions=CONTRACT_INSTRUCTIONS,
        searchable_fields=(
            "parties",
            "obligations",
            "key_dates",
            "defined_terms",
            "risk_clauses",
            "governing_law",
            "gaps",
        ),
    )
}


def get_schema(schema_id: str) -> ExtractionSchema:
    try:
        return SCHEMAS[schema_id]
    except KeyError:
        raise KeyError(
            f"unknown extraction schema {schema_id!r}; known: {sorted(SCHEMAS)}"
        ) from None
