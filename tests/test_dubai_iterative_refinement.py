from research.dubai_iterative.contracts import SearchSpace, StrategyGenome
from research.dubai_iterative.refinement import parameter_neighborhood


def test_parameter_neighborhood_explores_more_and_less_exposure():
    parent = StrategyGenome.baseline()
    space = SearchSpace(max_total_volume=1.0)

    children = parameter_neighborhood(parent, space)
    totals = {round(sum(item.volume_weights), 2) for item in children}

    assert any(total < 0.04 for total in totals)
    assert any(total > 0.04 for total in totals)


def test_parameter_neighborhood_probes_the_local_plateau_without_volume_gaps():
    parent = StrategyGenome.baseline().with_change(
        entry_mode="pullback",
        entry_value=4.0,
        entry_expiry_min=120,
        entry_ladder_mode="favourable",
        entry_ladder_step=0.25,
        leg_count=4,
        volume_weights=(0.01, 0.01, 0.01, 0.03),
        target_mode="fixed_basket",
        target_value=50.0,
        be_mode="partial",
        be_trigger=2.0,
        stop_mode="fixed_move",
        stop_value=6.0,
        time_exit_min=3,
    )

    children = parameter_neighborhood(
        parent,
        SearchSpace(max_total_volume=1.0),
    )
    pullbacks = {
        item.entry_value
        for item in children
        if item.entry_mode == "pullback"
        and item.mutation_reason == "entry_family"
    }
    exits = {
        item.time_exit_min
        for item in children
        if item.mutation_reason == "time_exit"
    }
    totals = {
        round(sum(item.volume_weights), 2)
        for item in children
        if item.mutation_reason == "exposure_plan"
    }

    assert {3.0, 3.5, 4.5, 5.0, 6.0} <= pullbacks
    assert {4, 6, 8} <= exits
    assert {0.05, 0.07} <= totals


def test_parameter_neighborhood_changes_every_strategy_block():
    parent = StrategyGenome.baseline()
    children = parameter_neighborhood(parent, SearchSpace(max_total_volume=1.0))

    assert any(item.entry_mode != parent.entry_mode for item in children)
    assert any(item.target_mode != parent.target_mode for item in children)
    assert any(item.be_mode != parent.be_mode for item in children)
    assert any(item.stop_mode != parent.stop_mode for item in children)
    assert any(item.time_exit_min != parent.time_exit_min for item in children)
    assert any(
        item.provider_management_mode != parent.provider_management_mode
        for item in children
    )


def test_parameter_neighborhood_is_finite_deterministic_and_valid():
    parent = StrategyGenome.baseline().with_change(
        leg_count=3,
        volume_weights=(0.01, 0.02, 0.03),
    )
    space = SearchSpace(max_total_volume=1.0)

    first = parameter_neighborhood(parent, space)
    second = parameter_neighborhood(parent, space)

    assert 1 < len(first) < 800
    assert [item.fingerprint for item in first] == [
        item.fingerprint for item in second
    ]
    assert parent.fingerprint not in {item.fingerprint for item in first}
    assert len({item.fingerprint for item in first}) == len(first)
    assert all(not item.validation_errors() for item in first)
    assert all(not space.validation_errors(item) for item in first)
