"""Taxonomy and locator schemes (docs/22 slice P1-A, AC-4 and AC-5)."""

import pytest

from policy_advisor.jurisdictions import (
    UnknownJurisdiction,
    load_taxonomy,
    normalize_jurisdiction,
    storage_values,
)
from policy_advisor.jurisdictions.locators import (
    best_support,
    is_supported,
    load_schemes,
    parse_locator,
    scheme_of,
)


def test_legacy_values_are_aliases_of_the_nigerian_ids():
    assert normalize_jurisdiction("federal") == "ng-federal"
    assert normalize_jurisdiction("lagos") == "ng-lagos"
    assert normalize_jurisdiction("ng-lagos") == "ng-lagos"
    assert normalize_jurisdiction(None) is None
    assert normalize_jurisdiction("") is None


def test_unknown_jurisdiction_is_an_error_not_a_silent_empty_filter():
    with pytest.raises(UnknownJurisdiction):
        normalize_jurisdiction("texas")


def test_storage_values_cover_the_id_and_its_aliases():
    assert set(storage_values("lagos")) == {"ng-lagos", "lagos"}
    assert set(storage_values("us-federal")) == {"us-federal"}
    assert storage_values(None) == ()


def test_every_jurisdiction_resolves_to_country_level_and_bodies():
    taxonomy = load_taxonomy()
    assert set(taxonomy.countries) == {"NG", "US"}
    for jid, j in taxonomy.jurisdictions.items():
        assert j.country in taxonomy.countries
        assert j.level in {"federal", "state"}
        assert j.bodies, jid
        for body in j.bodies:
            assert taxonomy.bodies[body].jurisdiction == jid


def test_every_scheme_matches_its_own_example():
    for scheme in load_schemes():
        parsed = parse_locator(scheme.example)
        assert parsed.scheme == scheme.id, scheme.id


@pytest.mark.parametrize(
    ("cited", "available", "expected"),
    [
        ("Order 5 Rule 3", "Order 5 Rule 3", True),
        ("Order 5 Rule 3(6)", "Order 5 Rule 3", True),
        ("Order 5 Rule 3(6)(a)", "Order 5 Rule 3", True),
        ("Order 5 Rule 30", "Order 5 Rule 3", False),  # the old prefix match accepted this
        ("Order 5 Rule 3", "Order 5 Rule 3 (Part 2)", False),
        ("Paragraph 11", "Paragraph 1", False),
        ("Section 37(1)", "Section 37", True),
        ("Section 370", "Section 37", False),
        ("8 U.S.C. § 1255(a)", "8 U.S.C. § 1255", True),
        ("8 U.S.C. § 12550", "8 U.S.C. § 1255", False),
        ("8 C.F.R. § 245.1(b)", "8 C.F.R. § 245.1", True),
        ("123 F.3d 456, 460", "123 F.3d 456", True),
        ("12 F. Supp. 3d 100, 105", "12 F. Supp. 3d 100", True),
        ("USCIS-PM Vol. 7, Pt. B, Ch. 2.A", "USCIS-PM Vol. 7, Pt. B, Ch. 2", True),
        ("Clause 12.3.1", "Clause 12.3", True),
        ("Clause 12.3", "Clause 12.31", False),
        ("ACME-0001234", "ACME-0001234", True),
        ("ACME-0001234", "ACME-0001235", False),
        ("Something odd", "Something odd", True),
        ("Something odd and more", "Something odd", False),
    ],
)
def test_scheme_aware_support(cited, available, expected):
    assert is_supported(cited, {available}) is expected


def test_scheme_detection():
    assert scheme_of("Order 1 Rule 2") == "ng_rules"
    assert scheme_of("8 U.S.C. § 1255") == "us_usc"
    assert scheme_of("Clause 4") == "contract_clause"
    assert scheme_of("a free-text heading") is None


def test_best_support_prefers_exact_then_longest_supporting_base():
    assert best_support("Order 5 Rule 3", ["Section 2", "Order 5 Rule 3"]) == "Order 5 Rule 3"
    assert best_support("Order 5 Rule 3(1)", ["Section 2", "Order 5 Rule 3"]) == "Order 5 Rule 3"
    assert best_support("Clause 12.3.1", ["Clause 12", "Clause 12.3"]) == "Clause 12.3"
    assert best_support("Order 9 Rule 9", ["Order 5 Rule 3"]) is None
