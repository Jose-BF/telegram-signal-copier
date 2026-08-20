from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import numpy as np

from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.dataset import LevelEvent
from research.dubai_iterative.engine import EntryRecord, ExitRecord
from research.dubai_iterative.risk import (
    assess_capital_risk,
    build_capital_risk_context,
)
from tools.run_dubai_capital_search import (
    _candidate_pool,
    _load_parents,
    _parser as capital_search_parser,
)


def test_capital_search_evaluates_imported_parent_and_its_neighborhood():
    parent = StrategyGenome.baseline().with_change(
        leg_count=2,
        volume_weights=(0.01, 0.02),
        time_exit_min=3,
    )

    candidates, local_count, crossover_count = _candidate_pool(
        search_space=SimpleNamespace(
            validation_errors=lambda _genome: (),
        ),
        parents=(parent,),
        scout_count=0,
        seed=7,
        seed_factory=lambda *_args, **_kwargs: (),
        scout_factory=lambda *_args, **_kwargs: (),
        neighborhood_factory=lambda *_args, **_kwargs: (
            parent.with_change(time_exit_min=2),
        ),
        crossover_factory=lambda *_args, **_kwargs: (),
    )

    assert [item.fingerprint for item in candidates] == [
        parent.fingerprint,
        parent.with_change(time_exit_min=2).fingerprint,
    ]
    assert local_count == 1
    assert crossover_count == 0


def _path(signal_id="signal_1"):
    return SimpleNamespace(
        signal_id=signal_id,
        direction="BUY",
        contract_size=100.0,
        conversion_orientation="identity",
        currency_digits=2,
        fx_bid=np.asarray([1.0]),
        fx_ask=np.asarray([1.0]),
        fx_valid=np.asarray([True]),
        legs=(),
    )


def test_fixed_move_risk_uses_planned_volume_not_historical_luck():
    genome = StrategyGenome.baseline().with_change(
        leg_count=2,
        volume_weights=(0.10, 0.10),
        stop_mode="fixed_move",
        stop_value=30.0,
    )

    report = assess_capital_risk(
        (_path(),),
        (),
        genome,
        initial_capital_eur=Decimal("500"),
        maximum_loss_fraction=Decimal("0.25"),
    )

    assert report.loss_basis == "configured_continuous_stop"
    assert report.planned_volume == 0.20
    assert report.worst_loss_eur == Decimal("600.00")
    assert report.risk_limit_eur == Decimal("125.00")
    assert report.risk_eligible is False


def test_basket_money_stop_is_compared_with_account_budget():
    genome = StrategyGenome.baseline().with_change(
        stop_mode="basket_money",
        stop_value=15.0,
    )

    report = assess_capital_risk(
        (_path(),),
        (),
        genome,
        initial_capital_eur=Decimal("500"),
        maximum_loss_fraction=Decimal("0.25"),
    )

    assert report.worst_loss_eur == Decimal("15.00")
    assert report.worst_loss_fraction == Decimal("0.0300")
    assert report.risk_eligible is True
    assert report.blockers == ()


def test_capital_gate_accounts_for_two_simultaneous_signals():
    genome = StrategyGenome.baseline().with_change(
        leg_count=3,
        volume_weights=(0.05, 0.05, 0.05),
        stop_mode="fixed_move",
        stop_value=8.0,
    )

    report = assess_capital_risk(
        (_path(),),
        (),
        genome,
        initial_capital_eur=Decimal("500"),
        maximum_loss_fraction=Decimal("0.25"),
        maximum_concurrent_signals=2,
    )

    assert report.planned_volume == 0.15
    assert report.aggregate_planned_volume == 0.30
    assert report.maximum_concurrent_signals == 2
    assert report.single_signal_worst_loss_eur == Decimal("120.00")
    assert report.worst_loss_eur == Decimal("240.00")
    assert report.risk_eligible is False


def test_capital_gate_never_understates_observed_signal_concurrency():
    opened = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    closed = datetime(2026, 8, 17, 9, 30, tzinfo=timezone.utc)
    results = tuple(
        SimpleNamespace(
            signal_id=f"signal_{index}",
            entries=(EntryRecord(
                ticket=f"entry_{index}",
                tick_index=0,
                opened_at=opened,
                entry_price=100.0,
                volume=0.01,
                source="test",
            ),),
            exits=(ExitRecord(
                ticket=f"entry_{index}",
                tick_index=1,
                closed_at=closed,
                entry_price=100.0,
                exit_price=101.0,
                volume=0.01,
                pnl_eur=Decimal("1.00"),
                reason="test",
            ),),
            blockers=(),
        )
        for index in range(3)
    )
    genome = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.01,),
        stop_mode="fixed_move",
        stop_value=30.0,
    )

    report = assess_capital_risk(
        (_path(),),
        results,
        genome,
        initial_capital_eur=Decimal("500"),
        maximum_loss_fraction=Decimal("0.25"),
        maximum_concurrent_signals=2,
    )

    assert report.configured_maximum_concurrent_signals == 2
    assert report.observed_maximum_concurrent_signals == 3
    assert report.maximum_concurrent_signals == 3
    assert report.aggregate_planned_volume == 0.03
    assert report.single_signal_worst_loss_eur == Decimal("30.00")
    assert report.worst_loss_eur == Decimal("90.00")


def test_capital_gate_fails_closed_when_an_opened_position_has_no_exit():
    opened = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    result = SimpleNamespace(
        signal_id="signal_1",
        entries=(EntryRecord(
            ticket="entry_1",
            tick_index=0,
            opened_at=opened,
            entry_price=100.0,
            volume=0.01,
            source="test",
        ),),
        exits=(),
        blockers=(),
        pnl_eur=Decimal("0.00"),
    )
    genome = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.01,),
        stop_mode="fixed_move",
        stop_value=10.0,
    )

    report = assess_capital_risk(
        (_path(),),
        (result,),
        genome,
        initial_capital_eur=Decimal("500"),
        maximum_loss_fraction=Decimal("0.25"),
    )

    assert report.risk_eligible is False
    assert report.worst_loss_eur is None
    assert report.blockers == (
        "incomplete_risk_lifecycle:signal_1:entry_1",
    )


def test_basket_stop_budget_scales_with_concurrent_signals():
    genome = StrategyGenome.baseline().with_change(
        stop_mode="basket_money",
        stop_value=50.0,
    )

    report = assess_capital_risk(
        (_path(),),
        (),
        genome,
        initial_capital_eur=Decimal("500"),
        maximum_loss_fraction=Decimal("0.25"),
        maximum_concurrent_signals=2,
    )

    assert report.single_signal_worst_loss_eur == Decimal("50.00")
    assert report.worst_loss_eur == Decimal("100.00")
    assert report.risk_eligible is True


def test_strategy_without_stop_is_explicitly_unbounded():
    genome = StrategyGenome.baseline().with_change(stop_mode="none")

    report = assess_capital_risk(
        (_path(),),
        (),
        genome,
        initial_capital_eur=Decimal("500"),
        maximum_loss_fraction=Decimal("0.25"),
    )

    assert report.worst_loss_eur is None
    assert report.risk_eligible is False
    assert report.blockers == ("unbounded_strategy_stop",)


def test_capital_risk_rejects_invalid_account_envelope():
    genome = StrategyGenome.baseline()

    for capital, fraction in ((Decimal("0"), Decimal("0.25")),
                              (Decimal("500"), Decimal("1.01"))):
        try:
            assess_capital_risk(
                (_path(),), (), genome,
                initial_capital_eur=capital,
                maximum_loss_fraction=fraction,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid capital envelope was accepted")


def test_provider_stop_risk_scales_worst_observed_risk_to_planned_volume():
    opened = datetime(2026, 8, 17, 9, 1, tzinfo=timezone.utc)
    stop = LevelEvent(
        observed_at=datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc),
        level=90.0,
        status="confirmed",
        source="telegram_signal",
    )
    path = _path()
    path.legs = (SimpleNamespace(sl_events=(stop,)),)
    result = SimpleNamespace(
        signal_id="signal_1",
        blockers=(),
        entries=(EntryRecord(
            ticket="sim_1",
            tick_index=0,
            opened_at=opened,
            entry_price=100.0,
            volume=0.01,
            source="test",
        ),),
    )
    genome = StrategyGenome.baseline().with_change(
        leg_count=2,
        volume_weights=(0.05, 0.05),
        stop_mode="provider",
    )

    report = assess_capital_risk(
        (path,),
        (result,),
        genome,
        initial_capital_eur=Decimal("500"),
        maximum_loss_fraction=Decimal("0.25"),
    )

    assert report.loss_basis == "observed_provider_stop_envelope"
    assert report.worst_loss_eur == Decimal("100.00")
    assert report.risk_eligible is True


def test_provider_stop_risk_fails_closed_when_stop_was_not_known_at_entry():
    opened = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    late_stop = LevelEvent(
        observed_at=datetime(2026, 8, 17, 9, 1, tzinfo=timezone.utc),
        level=90.0,
        status="confirmed",
        source="telegram_signal",
    )
    path = _path()
    path.legs = (SimpleNamespace(sl_events=(late_stop,)),)
    result = SimpleNamespace(
        signal_id="signal_1",
        blockers=(),
        entries=(EntryRecord(
            ticket="sim_1",
            tick_index=0,
            opened_at=opened,
            entry_price=100.0,
            volume=0.01,
            source="test",
        ),),
    )

    report = assess_capital_risk(
        (path,),
        (result,),
        StrategyGenome.baseline(),
        initial_capital_eur=Decimal("500"),
        maximum_loss_fraction=Decimal("0.25"),
    )

    assert report.worst_loss_eur is None
    assert report.risk_eligible is False
    assert report.blockers == ("provider_stop_unavailable_at_entry:signal_1:sim_1",)


def test_provider_stop_risk_respects_execution_latency():
    observed = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)
    stop = LevelEvent(
        observed_at=observed,
        level=90.0,
        status="confirmed",
        source="telegram_signal",
    )
    path = _path()
    path.legs = (SimpleNamespace(sl_events=(stop,)),)
    result = SimpleNamespace(
        signal_id="signal_1",
        blockers=(),
        entries=(EntryRecord(
            ticket="sim_1",
            tick_index=0,
            opened_at=observed + timedelta(seconds=1),
            entry_price=100.0,
            volume=0.01,
            source="test",
        ),),
    )

    report = assess_capital_risk(
        (path,),
        (result,),
        StrategyGenome.baseline(),
        initial_capital_eur=Decimal("500"),
        maximum_loss_fraction=Decimal("0.25"),
        observation_latency_ms=2_000,
    )

    assert report.risk_eligible is False
    assert report.worst_loss_eur is None
    assert report.blockers == (
        "provider_stop_unavailable_at_entry:signal_1:sim_1",
    )


def test_risk_context_reuses_a_verified_conversion_envelope():
    path = _path()
    path.conversion_orientation = "account_base_profit_quote"
    path.fx_bid = np.asarray([1.20, 1.10])
    path.fx_ask = np.asarray([1.21, 1.11])
    path.fx_valid = np.asarray([True, True])
    genome = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.10,),
        stop_mode="fixed_move",
        stop_value=10.0,
    )

    context = build_capital_risk_context((path,))
    report = assess_capital_risk(
        (path,),
        (),
        genome,
        initial_capital_eur=Decimal("500"),
        maximum_loss_fraction=Decimal("0.25"),
        risk_context=context,
    )

    assert context.loss_conversion_factor == Decimal("0.9090909090909090909090909091")
    assert report.worst_loss_eur == Decimal("90.91")


def test_capital_search_defaults_to_three_concurrent_dubai_signals():
    args = capital_search_parser().parse_args([])

    assert args.max_concurrent_signals == 3


def test_recursive_capital_search_prefers_verified_eligible_parents(tmp_path):
    eligible = StrategyGenome.baseline().with_change(time_exit_min=30)
    rejected = StrategyGenome.baseline().with_change(time_exit_min=60)
    path = tmp_path / "parents.parquet"
    import pandas as pd
    import json

    pd.DataFrame((
        {
            "genome_json": json.dumps(eligible.to_dict()),
            "positive_challenges": 4,
            "worst_net_eur": 10.0,
            "eligible": True,
        },
        {
            "genome_json": json.dumps(rejected.to_dict()),
            "positive_challenges": 99,
            "worst_net_eur": 999.0,
            "eligible": False,
        },
    )).to_parquet(path, index=False)

    parents = _load_parents(path, 10)

    assert parents == (eligible,)


def test_recursive_search_can_refine_good_rules_before_resizing_for_capital(tmp_path):
    good_rule_large_size = StrategyGenome.baseline().with_change(
        leg_count=1,
        volume_weights=(0.50,),
        time_exit_min=30,
    )
    failed_rule = StrategyGenome.baseline().with_change(time_exit_min=60)
    path = tmp_path / "parents.parquet"
    import pandas as pd
    import json

    pd.DataFrame((
        {
            "genome_json": json.dumps(good_rule_large_size.to_dict()),
            "positive_challenges": 4,
            "worst_net_eur": 100.0,
            "rule_eligible": True,
            "capital_eligible": False,
            "eligible": False,
        },
        {
            "genome_json": json.dumps(failed_rule.to_dict()),
            "positive_challenges": 4,
            "worst_net_eur": 100.0,
            "rule_eligible": False,
            "capital_eligible": True,
            "eligible": False,
        },
    )).to_parquet(path, index=False)

    parents = _load_parents(path, 10)

    assert parents == (good_rule_large_size,)
