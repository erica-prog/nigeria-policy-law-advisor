"""The source registry (docs/23 slice P2-A, AC-1): seeding, sign-off, the
allow gate, and the web-search domains that come from it."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from policy_advisor import sources
from policy_advisor.db import MIGRATIONS_DIR, connect
from policy_advisor.generation import web_search
from scripts import manage_sources

PHASE1_DOMAINS = ["nass.gov.ng", "ndpc.gov.ng"]


@pytest.fixture
def registry(fresh_db):
    """An empty registry; the packaged seed is restored afterwards so later
    tests (and the real web-search call) still find the Phase 1 row."""
    with connect() as conn:
        conn.execute("TRUNCATE sources")
    yield
    with connect() as conn:
        conn.execute("TRUNCATE sources")
    sources.seed()


def _source(source_id: str = "test-src", **overrides) -> sources.Source:
    fields = dict(
        source_id=source_id,
        name="Test source",
        country="NG",
        connector="ng-sources",
        trust_kind="authority",
        licence={"may_store_text": False},
        config={"base_url": "https://example.test/"},
    )
    fields.update(overrides)
    return sources.Source(**fields)


# --- the packaged seed (no database) ---


def test_packaged_seed_is_valid_and_keeps_the_phase1_allowlist():
    seed = sources.load_seed()
    by_id = {s.source_id: s for s in seed}
    web = by_id["web-official-ng"]
    assert web.allowed is True and web.config["domains"] == PHASE1_DOMAINS
    # Everything else waits for a lawyer: nothing external is on by default.
    assert all(not s.allowed for s in seed if s.source_id != "web-official-ng")
    assert {s.country for s in seed} == {"NG", "US"}


def test_seed_file_with_an_unknown_country_is_rejected_before_any_write(tmp_path: Path):
    bad = tmp_path / "seed.json"
    bad.write_text(
        '{"sources": [{"source_id": "x", "name": "X", "country": "FR", "connector": "c"}]}'
    )
    with pytest.raises(ValueError, match="unknown country"):
        sources.load_seed(str(bad))


def test_web_search_source_needs_domains_and_the_web_trust_kind():
    with pytest.raises(ValueError, match="config.domains"):
        _source(connector="web_search", trust_kind="web", config={}).validate()
    with pytest.raises(ValueError, match="trust_kind web"):
        _source(
            connector="web_search", trust_kind="authority", config={"domains": ["a.b"]}
        ).validate()
    with pytest.raises(ValueError, match="lower-case slug"):
        _source(source_id="Bad Id").validate()


# --- the table ---


def test_migration_seeds_the_phase1_allowlist_idempotently(registry):
    sql = (MIGRATIONS_DIR / "0007_sources.sql").read_text(encoding="utf-8")
    seed_sql = sql[sql.index("-- SEED") :]
    with connect() as conn:
        conn.execute(seed_sql)
        conn.execute(seed_sql)  # re-applying after a restore must not fail or duplicate
    assert sources.web_search_domains() == PHASE1_DOMAINS
    assert [s.source_id for s in sources.list_sources()] == ["web-official-ng"]


def test_seed_inserts_then_refreshes_without_touching_a_lawyers_decision(registry):
    first = sources.seed()
    assert first.updated == [] and "us-ecfr" in first.inserted
    assert sources.get_source("us-ecfr").allowed is False

    sources.set_allowed("us-ecfr", True, by="A. Lawyer")
    sources.set_allowed("web-official-ng", False, by="A. Lawyer")

    second = sources.seed()
    assert second.inserted == [] and set(second.updated) == set(first.inserted)
    assert sources.get_source("us-ecfr").allowed is True
    assert sources.get_source("us-ecfr").allowed_by == "A. Lawyer"
    assert sources.get_source("web-official-ng").allowed is False


def test_sign_off_records_who_and_when_and_refuses_anonymity(registry):
    sources.upsert_source(_source())
    with pytest.raises(ValueError):
        sources.set_allowed("test-src", True, by="  ")
    allowed = sources.set_allowed("test-src", True, by="A. Lawyer")
    assert allowed.allowed and allowed.allowed_by == "A. Lawyer" and allowed.allowed_at is not None
    with pytest.raises(sources.UnknownSource):
        sources.set_allowed("nobody", True, by="A. Lawyer")


def test_require_allowed_refuses_unknown_and_unsigned_sources(registry):
    sources.upsert_source(_source())
    with pytest.raises(sources.UnknownSource):
        sources.require_allowed("missing")
    with pytest.raises(sources.SourceNotAllowed, match="manage_sources allow test-src"):
        sources.require_allowed("test-src")
    sources.set_allowed("test-src", True, by="A. Lawyer")
    assert sources.require_allowed("test-src").source_id == "test-src"
    assert sources.allowed_connectors() == {"ng-sources"}


def test_web_search_domains_union_allowed_web_rows_only(registry):
    sources.upsert_source(
        _source(
            "web-ng",
            connector="web_search",
            trust_kind="web",
            config={"domains": ["a.gov.ng", "b.gov.ng"]},
            allowed=True,
            allowed_by="A. Lawyer",
        )
    )
    sources.upsert_source(
        _source(
            "web-us",
            country="US",
            connector="web_search",
            trust_kind="web",
            config={"domains": ["b.gov.ng", "c.gov"]},
            allowed=True,
            allowed_by="A. Lawyer",
        )
    )
    sources.upsert_source(
        _source(
            "web-off", connector="web_search", trust_kind="web", config={"domains": ["off.example"]}
        )
    )
    sources.upsert_source(_source("not-web"))  # an authority connector contributes no domains
    assert sources.web_search_domains() == ["a.gov.ng", "b.gov.ng", "c.gov"]
    assert sources.web_search_domains(country="US") == ["b.gov.ng", "c.gov"]
    assert sources.delete_source("web-us") and sources.get_source("web-us") is None


# --- the gate in web search ---


def test_web_search_fails_closed_when_no_source_is_allowed(registry):
    client = MagicMock()
    with patch(f"{web_search.__name__}.anthropic.Anthropic", return_value=client):
        result = web_search.search_official_sources("anything")
    assert result.found is False and result.citations == []
    client.messages.create.assert_not_called()


def test_web_search_restricts_anthropic_to_the_registry_domains(registry):
    sources.seed()
    client = MagicMock()
    client.messages.create.return_value = MagicMock(content=[])
    with patch(f"{web_search.__name__}.anthropic.Anthropic", return_value=client):
        web_search.search_official_sources("anything")
    tools = client.messages.create.call_args.kwargs["tools"]
    assert tools[0]["allowed_domains"] == PHASE1_DOMAINS


# --- the command line ---


def test_manage_sources_seed_allow_and_list(registry, capsys):
    assert manage_sources.main(["seed"]) == 0
    assert "await a lawyer's sign-off" in capsys.readouterr().out
    assert manage_sources.main(["allow", "us-ecfr", "--by", "A. Lawyer"]) == 0
    assert "us-ecfr is now allowed (by A. Lawyer" in capsys.readouterr().out
    assert manage_sources.main(["list", "--allowed"]) == 0
    out = capsys.readouterr().out
    assert "us-ecfr" in out and "web-official-ng" in out and "us-govinfo" not in out
    assert manage_sources.main(["disallow", "us-ecfr", "--by", "A. Lawyer"]) == 0
    assert sources.get_source("us-ecfr").allowed is False
    assert manage_sources.main(["show", "nope"]) == 1
    assert manage_sources.main(["allow", "nope", "--by", "x"]) == 1
