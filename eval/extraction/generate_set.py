"""Generate the labelled synthetic contract set (docs/34). Deterministic: the
same seed writes the same `contracts.json`, which is committed so the score
is reproducible. Every document is synthetic (AGENTS.md rule 7): invented
parties, invented terms, clause text written for the purpose.

Each document is a list of clauses with `Clause N` locators and a label
record in the contract schema's shape (bulk/schemas.py), derived from the
same choices that wrote the text, so the labels are exact by construction.

Usage:
  uv run python -m eval.extraction.generate_set            # writes contracts.json
  uv run python -m eval.extraction.generate_set --count 60 --seed 11
"""

import argparse
import json
import random
from pathlib import Path

OUT = Path(__file__).parent / "contracts.json"

COMPANIES = [
    ("Foxglove Trading Ltd", "Foxglove"),
    ("Acme Fabrication Ltd", "Acme"),
    ("Kola Nut Logistics Ltd", "Kola"),
    ("Harmattan Energy Plc", "Harmattan"),
    ("Blue Heron Media Ltd", "Heron"),
    ("Sable Ridge Holdings LLC", "Sable Ridge"),
    ("Obi & Daughters Ltd", "Obi"),
    ("Northwind Software Inc", "Northwind"),
    ("Lagoon Pharmaceuticals Ltd", "Lagoon"),
    ("Granite Peak Properties Ltd", "Granite Peak"),
    ("Ironwood Consulting LLC", "Ironwood"),
    ("Zuma Rock Minerals Ltd", "Zuma"),
]
KINDS = [
    ("supply", "Supply Agreement", "Supplier", "Buyer", "supply the Goods described in Schedule 1"),
    (
        "services",
        "Services Agreement",
        "Provider",
        "Client",
        "perform the Services described in Schedule 1",
    ),
    (
        "licence",
        "Software Licence Agreement",
        "Licensor",
        "Licensee",
        "grant the Licence to use the Software",
    ),
    (
        "distribution",
        "Distribution Agreement",
        "Principal",
        "Distributor",
        "supply the Products for resale in the Territory",
    ),
    (
        "lease",
        "Lease Agreement",
        "Landlord",
        "Tenant",
        "grant the Tenant exclusive possession of the Premises",
    ),
    ("loan", "Loan Agreement", "Lender", "Borrower", "advance the Loan to the Borrower"),
    (
        "nda",
        "Confidentiality Agreement",
        "Discloser",
        "Recipient",
        "disclose Confidential Information for the Purpose",
    ),
]
LAWS = [
    "the laws of the Federal Republic of Nigeria",
    "the laws of Lagos State",
    "the laws of the State of New York",
    "the laws of England and Wales",
]
BUSINESS_DAY = [
    "a day other than a Saturday, Sunday or public holiday on which banks are open for business in Lagos",
    "any day on which banks are open for business in New York",
    "a day other than a Saturday, Sunday or public holiday in England",
]
PAY_DAYS = ["thirty", "forty-five", "sixty", "fourteen", "twenty-one"]
NOTICE = ["thirty", "sixty", "ninety", "one hundred and eighty"]
TERMS = ["one (1) year", "two (2) years", "three (3) years", "five (5) years"]
RISKS = {
    "indemnity": "The {obligor} shall indemnify the {obligee} against all losses arising from any breach of this Agreement by the {obligor}.",
    "limitation of liability": "Neither party's aggregate liability under this Agreement shall exceed the total fees paid in the twelve months before the claim.",
    "change of control": "Either party may terminate this Agreement on written notice if the other party undergoes a change of control.",
    "exclusivity": "The {obligee} shall not purchase goods or services of the kind supplied under this Agreement from any person other than the {obligor} during the Term.",
    "non-compete": "The {obligee} shall not carry on any business competing with the {obligor} in the Territory for twelve months after termination.",
    "assignment": "Neither party may assign this Agreement without the prior written consent of the other party.",
    "data protection": "Each party shall process personal data under this Agreement in accordance with the Nigeria Data Protection Act 2023.",
    "dispute resolution": "Any dispute arising from this Agreement shall be referred to arbitration seated in Lagos under the Arbitration and Mediation Act 2023.",
}


def _date(rng: random.Random) -> str:
    months = [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]
    return f"{rng.randint(1, 28)} {rng.choice(months)} {rng.randint(2024, 2026)}"


def make_contract(index: int, rng: random.Random) -> dict:
    kind, title, role_a, role_b, performance = rng.choice(KINDS)
    (name_a, short_a), (name_b, short_b) = rng.sample(COMPANIES, 2)
    law = rng.choice(LAWS)
    effective = _date(rng)
    term = rng.choice(TERMS)
    pay_days = rng.choice(PAY_DAYS)
    notice_days = rng.choice(NOTICE)
    business_day = rng.choice(BUSINESS_DAY)
    risks = rng.sample(list(RISKS), rng.randint(1, 3))
    omit_deadline = rng.random() < 0.3  # a gap the extractor should report
    survival = rng.random() < 0.5

    clauses: list[dict] = []
    obligations: list[dict] = []
    key_dates: list[dict] = []
    risk_rows: list[dict] = []

    def add(locator: str, text: str) -> None:
        clauses.append({"locator": locator, "text": text})

    add(
        "Clause 1",
        f"This {title} is made on {effective} (the Effective Date) between {name_a} (the {role_a}) and {name_b} (the {role_b}).",
    )
    key_dates.append(
        {
            "label": "Effective Date",
            "date_text": effective,
            "evidence": [{"locator": "Clause 1", "quote": f"made on {effective}"}],
        }
    )
    add(
        "Clause 2",
        f"In this Agreement, Business Day means {business_day}. The Term means the period of {term} from the Effective Date.",
    )
    key_dates.append(
        {
            "label": "Term",
            "date_text": term,
            "evidence": [
                {"locator": "Clause 2", "quote": f"the period of {term} from the Effective Date"}
            ],
        }
    )
    add("Clause 3", f"The {role_a} shall {performance} throughout the Term.")
    obligations.append(
        {
            "obligor": f"the {role_a}",
            "obligee": f"the {role_b}",
            "obligation": performance,
            "trigger": "",
            "deadline": "throughout the Term",
            "conditions": "",
            "consequence": "",
            "recurring": True,
            "survives_termination": None,
            "evidence": [
                {
                    "locator": "Clause 3",
                    "quote": f"The {role_a} shall {performance} throughout the Term",
                }
            ],
        }
    )
    if omit_deadline:
        add(
            "Clause 4",
            f"The {role_b} shall pay each invoice issued by the {role_a}. Late payment attracts interest at two per cent per month.",
        )
        obligations.append(
            {
                "obligor": f"the {role_b}",
                "obligee": f"the {role_a}",
                "obligation": "pay each invoice",
                "trigger": "an invoice",
                "deadline": "",
                "conditions": "",
                "consequence": "interest at two per cent per month",
                "recurring": True,
                "survives_termination": None,
                "evidence": [
                    {
                        "locator": "Clause 4",
                        "quote": f"The {role_b} shall pay each invoice issued by the {role_a}",
                    }
                ],
            }
        )
    else:
        add(
            "Clause 4",
            f"The {role_b} shall pay each invoice within {pay_days} Business Days of receipt. Late payment attracts interest at two per cent per month.",
        )
        obligations.append(
            {
                "obligor": f"the {role_b}",
                "obligee": f"the {role_a}",
                "obligation": "pay each invoice",
                "trigger": "receipt of an invoice",
                "deadline": f"within {pay_days} Business Days of receipt",
                "conditions": "",
                "consequence": "interest at two per cent per month",
                "recurring": True,
                "survives_termination": None,
                "evidence": [
                    {
                        "locator": "Clause 4",
                        "quote": f"pay each invoice within {pay_days} Business Days of receipt",
                    }
                ],
            }
        )
        key_dates.append(
            {
                "label": "Payment period",
                "date_text": f"{pay_days} Business Days",
                "evidence": [
                    {"locator": "Clause 4", "quote": f"within {pay_days} Business Days of receipt"}
                ],
            }
        )
    add(
        "Clause 5",
        f"Either party may terminate this Agreement on {notice_days} days written notice delivered to the registered office of the other party.",
    )
    key_dates.append(
        {
            "label": "Notice period",
            "date_text": f"{notice_days} days",
            "evidence": [{"locator": "Clause 5", "quote": f"{notice_days} days written notice"}],
        }
    )
    locator_n = 6
    for risk in risks:
        text = RISKS[risk].format(obligor=role_a, obligee=role_b)
        add(f"Clause {locator_n}", text)
        risk_rows.append(
            {
                "kind": risk,
                "summary": text,
                "mutual": None,
                "evidence": [{"locator": f"Clause {locator_n}", "quote": text[:80]}],
            }
        )
        locator_n += 1
    if survival:
        add(
            f"Clause {locator_n}",
            "Clauses 4, 6 and this clause survive termination or expiry of this Agreement.",
        )
        locator_n += 1
    add(f"Clause {locator_n}", f"This Agreement is governed by {law}.")
    law_locator = f"Clause {locator_n}"

    record = {
        "classification": "complete executed agreement",
        "title": title,
        "parties": [
            {
                "name": name_a,
                "role": role_a,
                "defined_as": f"the {role_a}",
                "evidence": [{"locator": "Clause 1", "quote": f"{name_a} (the {role_a})"}],
            },
            {
                "name": name_b,
                "role": role_b,
                "defined_as": f"the {role_b}",
                "evidence": [{"locator": "Clause 1", "quote": f"{name_b} (the {role_b})"}],
            },
        ],
        "governing_law": law,
        "governing_law_evidence": [{"locator": law_locator, "quote": f"governed by {law}"}],
        "key_dates": key_dates,
        "obligations": obligations,
        "defined_terms": [
            {
                "term": "Business Day",
                "definition": business_day,
                "evidence": [
                    {"locator": "Clause 2", "quote": f"Business Day means {business_day}"}
                ],
            }
        ],
        "risk_clauses": risk_rows,
        "gaps": ["the obligation to pay invoices has no stated deadline"] if omit_deadline else [],
    }
    return {
        "name": f"{kind}-{index:03d}-{short_a.lower().replace(' ', '')}-{short_b.lower().replace(' ', '')}.txt",
        "kind": kind,
        "clauses": clauses,
        "labels": record,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=48)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    rng = random.Random(args.seed)
    documents = [make_contract(i + 1, rng) for i in range(args.count)]
    args.out.write_text(
        json.dumps(
            {"version": 1, "seed": args.seed, "documents": documents}, indent=1, ensure_ascii=False
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(documents)} synthetic contracts to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
