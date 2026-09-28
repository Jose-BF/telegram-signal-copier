"""Independent synthetic regressions for the M7-to-search input boundary."""

import pytest

from research import strategy_study as fixed
from research import strategy_study_dataset as bridge
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.engine import simulate
from research.dubai_iterative.portfolio import build_portfolio_tape, reconstruct_portfolio
from research.execution_profile import execution_from_mapping
from tests.test_strategy_study import case


def _portfolio(bundle, results, execution):
    tape = build_portfolio_tape(
        bundle.dataset.paths,
        market_tick_source=bundle.market_tick_source,
        conversion_tick_source=bundle.conversion_tick_source,
        max_conversion_age_ms=bundle.max_fx_age_ms,
        max_conversion_interval_ms=bundle.max_fx_age_ms,
    )
    return reconstruct_portfolio(
        bundle.dataset.paths, results, execution=execution, portfolio_tape=tape,
    )


def test_canonical_source_mutation_cannot_change_portfolio_under_same_identity(case):
    config, _, _ = case()
    bundle = bridge.load_study_dataset(config)
    execution = execution_from_mapping(bundle.config["execution"])
    genome = StrategyGenome.from_dict(bundle.config["strategy"])
    results = tuple(simulate(path, genome, execution=execution) for path in bundle.dataset.paths)
    before = _portfolio(bundle, results, execution)
    assert not before.blockers
    identity = fixed._sha(bundle.identity)

    # This changes only an intermediate mark, not the stored entry or exit.
    try:
        bundle.market_tick_source.frame.loc[1, ["bid", "ask"]] = [90.0, 90.2]
    except (AttributeError, TypeError, ValueError):
        return
    try:
        bundle.verify_sources()
        after = _portfolio(bundle, results, execution)
    except ValueError:
        return
    assert fixed._sha(bundle.identity) == identity
    assert after == before, (
        "A changed canonical source passed verify_sources under the same identity: "
        f"before={before}; after={after}"
    )


def test_bridge_rejects_m7_code_changed_before_bridge_import(case, monkeypatch):
    config, _, _ = case()
    # Emulate a dependency imported by M7 before its source changed on disk.
    # The bridge's later snapshot matches the current bytes, not that old import.
    monkeypatch.setitem(fixed._IMPORTED_SOURCES, "research/causal_replay.py", "0" * 64)
    with pytest.raises(ValueError, match="loaded implementation changed"):
        fixed._check_current(fixed.current_identity())
    with pytest.raises(ValueError, match="implementation|interpreter|changed"):
        bridge.load_study_dataset(config)
