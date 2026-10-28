"""Every eval runner that reaches the model or a connector runs as a tenant,
so its calls land in the cost ledger (docs/32) instead of being refused by
row-level security (docs/31). Found by the nightly run on 2026-10-28."""

import importlib

import pytest

from policy_advisor.tenancy import DEFAULT_TENANT, current_tenant, tenant_context

RUNNERS = [
    ("eval.run_golden_set", None),
    ("eval.run_case_reasoning_invariants", None),
    ("eval.run_case_reasoning_seed", None),
    ("eval.calibrate_relevance_floor", None),
    ("eval.run_connector_canaries", []),
]


@pytest.mark.parametrize(("module_name", "argv"), RUNNERS)
def test_eval_runner_main_runs_its_work_as_the_default_tenant(module_name, argv, monkeypatch):
    module = importlib.import_module(module_name)
    seen: list[str | None] = []

    def fake_main(*args):
        seen.append(current_tenant())
        return 0

    monkeypatch.setattr(module, "_main", fake_main)
    with tenant_context(None):  # the autouse fixture's tenant is not the runner's
        module.main() if argv is None else module.main(argv)
        assert current_tenant() is None
    assert seen == [DEFAULT_TENANT]
