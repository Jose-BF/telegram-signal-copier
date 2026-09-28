import pytest

from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.latency_model import LatencyModel, unit_draw


def _model(seed=0, values=(100, 200, 300, 55_000)):
    return LatencyModel(label="t", seed=seed, entry_fill_ms=tuple(values))


def test_draw_is_deterministic_per_request_identity():
    model = _model(seed=3)
    first = [model.entry_fill_delay_ms("canal2_1", leg) for leg in range(5)]
    again = [model.entry_fill_delay_ms("canal2_1", leg) for leg in range(5)]
    assert first == again
    assert all(value in model.entry_fill_ms for value in first)


def test_seed_and_identity_change_the_draw():
    draws = {unit_draw(seed, "entry_fill", "canal2_1", 0) for seed in range(20)}
    assert len(draws) == 20
    assert unit_draw(0, "entry_fill", "canal2_1", 0) != unit_draw(0, "entry_fill", "canal2_2", 0)
    assert all(0 <= value < 1 for value in draws)


def test_draws_follow_the_empirical_sample():
    values = tuple(range(1000))
    model = _model(values=values)
    drawn = sorted(model.entry_fill_delay_ms(f"s{i}", 0) for i in range(4000))
    median = drawn[len(drawn) // 2]
    assert 430 <= median <= 570


@pytest.mark.parametrize("values", [(), (3, 1), (-1, 2), (1.5, 2)])
def test_invalid_samples_are_rejected(values):
    with pytest.raises(ValueError):
        LatencyModel(label="t", seed=0, entry_fill_ms=values)


def test_from_samples_requires_contract():
    with pytest.raises(ValueError):
        LatencyModel.from_samples({"contract": "other", "label": "x", "entry_fill_ms": [1]}, 0)
    model = LatencyModel.from_samples(
        {"contract": "latency_samples_v1", "label": "C", "entry_fill_ms": [5, 7]}, 4)
    assert model.label == "C#seed4" and model.entry_fill_ms == (5, 7)


def test_execution_rejects_foreign_latency_object():
    with pytest.raises(ValueError):
        ExecutionAssumptions(latency=object())
    assert ExecutionAssumptions(latency=_model()).latency.seed == 0


def test_json_round_trip_and_oracle_refusal():
    from dataclasses import asdict

    from research.dubai_iterative.client_contract import ClientProfile
    from research.dubai_iterative.market_contract import MarketProfile
    from research.dubai_iterative.protection_contract import ProtectionProfile
    from research.execution_profile import execution_from_mapping, execution_to_scenario

    base = dict(protection=ProtectionProfile(0.01, 2, 20, 0, 100, 1, 200),
                market=MarketProfile(2, 164, 2, 0.01, 1.0, 0.01), client=ClientProfile())
    execution = ExecutionAssumptions(**base, latency=_model())
    assert execution_from_mapping(asdict(execution)) == execution
    with pytest.raises(ValueError):
        execution_to_scenario(execution)
    plain = ExecutionAssumptions(**base)
    assert execution_to_scenario(plain).client == plain.client


def test_basket_correlation_zero_matches_independent_draws():
    a, b = _model(seed=5), LatencyModel(label="t", seed=5, entry_fill_ms=(100, 200, 300, 55_000),
                                         basket_correlation=0.0)
    assert [a.entry_fill_delay_ms("s", i) for i in range(6)] == [b.entry_fill_delay_ms("s", i) for i in range(6)]


def test_basket_correlation_makes_legs_of_a_basket_alike():
    values = tuple(range(1000))
    corr = LatencyModel(label="t", seed=1, entry_fill_ms=values, basket_correlation=0.9)
    spreads = []
    for basket in range(200):
        legs = [corr.entry_fill_delay_ms(f"b{basket}", leg) for leg in range(5)]
        spreads.append(max(legs) - min(legs))
    indep = LatencyModel(label="t", seed=1, entry_fill_ms=values)
    spreads_indep = [max(indep.entry_fill_delay_ms(f"b{k}", leg) for leg in range(5))
                     - min(indep.entry_fill_delay_ms(f"b{k}", leg) for leg in range(5)) for k in range(200)]
    assert sum(spreads) < 0.6 * sum(spreads_indep)


@pytest.mark.parametrize("rho", [-0.1, 1.0, True])
def test_invalid_correlation_rejected(rho):
    with pytest.raises(ValueError):
        LatencyModel(label="t", seed=0, entry_fill_ms=(1,), basket_correlation=rho)


def _adverse_path(times_ms, asks, observed_ms):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import numpy as np
    return SimpleNamespace(
        times_ns=np.array([t * 1_000_000 for t in times_ms], dtype=np.int64),
        ask=np.array(asks, dtype=float), bid=np.array(asks, dtype=float) - 0.2,
        direction="BUY",
        signal_observed_at=datetime.fromtimestamp(observed_ms / 1000, timezone.utc))


def test_quote_view_lag_moves_the_reference_to_an_older_tick(monkeypatch):
    from types import SimpleNamespace
    from research.dubai_iterative import engine
    monkeypatch.setattr(engine, "_tick_is_usable", lambda path, i: True)
    monkeypatch.setattr(engine, "_usable_tick_mask",
                        lambda path, a, b: __import__("numpy").ones(max(b - a, 0), dtype=bool))
    monkeypatch.setattr(engine, "_entry_expiry_anchor_ns", lambda path: path.times_ns[0])
    # quote jumps +0.4 just before the signal is seen, then dips 1.1 and rebounds
    times = [1000, 1500, 1600, 1700, 2000, 3000]
    asks = [100.0, 100.0, 100.4, 100.4, 99.35, 100.9]
    path = _adverse_path(times, asks, observed_ms=1650)
    genome = SimpleNamespace(entry_mode="adverse_reversal", stop_mode="none", entry_value=1.0,
                             entry_confirmation_value=1.5, entry_expiry_min=30)
    fresh = engine._causal_entry_index(path, genome, ExecutionAssumptions())
    stale = engine._causal_entry_index(path, genome, ExecutionAssumptions(quote_view_lag_ms=400))
    # fresh anchor 100.4 arms at 99.35 and fills on the rebound; the stale
    # anchor (100.0, what the terminal still showed) never arms, as in canal2_3488
    assert fresh == 5
    assert stale is None
    assert engine._causal_entry_index(path, genome, ExecutionAssumptions(quote_view_lag_ms=0)) == 5
