from dataclasses import fields

import pytest

from research import dubai_family_catalog as families
from research.dubai_iterative.contracts import StrategyGenome


def reference():
    return StrategyGenome(schema_version=2, entry_mode="signal_market", entry_expiry_min=3,
        leg_count=1, volume_weights=(.01,), stop_mode="fixed_move", stop_value=10.,
        target_mode="per_leg_steps", target_steps=(5.,), time_exit_mode="always", time_exit_min=15,
        be_mode="none", provider_management_mode="ignore").to_dict()


def test_every_genome_field_is_classified_exactly_once():
    names = [name for group in families.FIELD_GROUPS.values() for name in group]
    assert len(names) == len(set(names))
    assert set(names) == {f.name for f in fields(StrategyGenome)}


def test_controls_are_unique_own_rules_with_same_initial_reference_risk():
    rows = families.fixed_controls(reference())
    assert 12 <= len(rows) <= 18
    genomes = [StrategyGenome.from_dict(row["strategy"]) for row in rows]
    assert len({g.fingerprint for g in genomes}) == len(genomes)
    for genome in genomes:
        assert not genome.validation_errors()
        assert genome.provider_management_mode == "ignore"
        assert genome.entry_mode != "actual_mt5"
        assert genome.stop_mode == "fixed_move" and genome.stop_value == 10
        assert sum(genome.volume_weights) == .02
        assert genome.time_exit_min <= 15 and genome.entry_expiry_min == 3
        assert genome.context_filter_mode != "max_volatility"


def test_catalog_distinguishes_controls_uncovered_capabilities_and_data_gates():
    result = families.catalog(reference())
    assert not result["exhaustive_search_claimed"]
    assert not result["automatic_selection_allowed"]
    assert result["search_candidates"] == 0
    options = result["categorical_options"]
    assert options["entry_mode"]["actual_mt5"]["status"] == "outside_own_rule_scope"
    assert options["context_filter_mode"]["max_volatility"]["status"] == "blocked_pretrigger_context_contract"
    assert "shared_close_ack_release" in result["extensions"]
    assert "overnight" in result["extensions"]
    assert result["extensions"]["opposite_direction"]["status"] == "requires_new_rule_contract"
    assert options["entry_mode"]["published_range"]["status"] == "requires_causal_absolute_level_contract"
    assert options["entry_mode"]["published_range"]["control_families"] == []


def test_new_unclassified_field_fails_catalog_not_silent_search(monkeypatch):
    monkeypatch.setattr(families, "FIELD_GROUPS", {k: v for k, v in families.FIELD_GROUPS.items() if k != "entry"})
    with pytest.raises(ValueError, match="unclassified"):
        families.catalog(reference())
