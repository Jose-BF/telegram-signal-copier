"""Explicit own-rule coverage inventory, not a claim of exhaustive discovery."""

import ast
from dataclasses import fields
import inspect
import textwrap

from research.dubai_iterative.contracts import StrategyGenome


FIELD_GROUPS = {
    "entry": ("entry_mode", "entry_value", "entry_confirmation_value", "entry_expiry_min"),
    "construction": ("entry_ladder_mode", "entry_ladder_step", "leg_count", "volume_weights", "pending_entry_policy"),
    "targets": ("target_mode", "target_value", "target_steps", "partial_fraction", "runner_target"),
    "protection": ("stop_mode", "stop_value", "be_mode", "be_trigger", "trailing_distance", "hard_stop_eur_per_leg"),
    "profit_lock": ("profit_lock_arm", "profit_lock_giveback"),
    "time_exit": ("time_exit_min", "time_exit_mode"),
    "context": ("context_filter_mode", "context_filter_value"),
    "provider": ("provider_management_mode",),
    "provenance": ("schema_version", "source_strategy_fingerprint", "parent_fingerprints", "mutation_reason", "lineage_depth"),
}

OPTIONS = {
    "entry_mode": {"actual_mt5", "delay", "pullback", "momentum", "signal_market", "adverse_reversal", "no_entry", "published_range", "published_limit"},
    "entry_ladder_mode": {"simultaneous", "adverse", "favourable", "range_levels"},
    "target_mode": {"provider_per_leg", "provider_target_all", "fixed_basket", "fixed_move", "partial_runner", "per_leg_steps", "none", "per_leg_levels"},
    "be_mode": {"provider", "none", "price", "delayed", "partial"},
    "stop_mode": {"provider", "fixed_move", "basket_money", "none", "fixed_level"},
    "provider_management_mode": {"exact", "close_only", "explicit_close_only", "ignore"},
    "context_filter_mode": {"none", "max_spread", "time_window", "max_volatility", "min_reward_risk"},
    "time_exit_mode": {"none", "always", "loss_only", "profit_only", "non_negative"},
    "pending_entry_policy": {"none", "until_expiry"},
}


def _check_grammar():
    classified = [name for names in FIELD_GROUPS.values() for name in names]
    if len(classified) != len(set(classified)) or set(classified) != {f.name for f in fields(StrategyGenome)}:
        raise ValueError("unclassified or duplicate strategy field")
    tree = ast.parse(textwrap.dedent(inspect.getsource(StrategyGenome.validation_errors)))
    actual = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "allowed" for t in node.targets):
            actual.update(ast.literal_eval(node.value))
        if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute)
                and isinstance(node.left.value, ast.Name) and node.left.value.id == "self"
                and len(node.ops) == 1 and isinstance(node.ops[0], ast.NotIn)
                and len(node.comparators) == 1 and isinstance(node.comparators[0], ast.Set)):
            choices = ast.literal_eval(node.comparators[0])
            if all(isinstance(v, str) for v in choices):
                actual[node.left.attr] = choices
    if actual != OPTIONS:
        raise ValueError("unclassified strategy grammar option; review the catalog")


def fixed_controls(reference):
    base = StrategyGenome.from_dict(reference).with_change(volume_weights=(.02,))
    changes = {
        "market": {},
        "delay": {"entry_mode": "delay", "entry_value": 30.0},
        "pullback": {"entry_mode": "pullback", "entry_value": 2.0},
        "momentum": {"entry_mode": "momentum", "entry_value": 2.0},
        "adverse_reversal": {"entry_mode": "adverse_reversal", "entry_value": 2.0, "entry_confirmation_value": 1.0},
        "split_targets": {"leg_count": 2, "volume_weights": (.01, .01), "target_steps": (3., 8.)},
        "adverse_ladder": {"leg_count": 2, "volume_weights": (.01, .01), "target_steps": (5., 8.),
                           "entry_ladder_mode": "adverse", "entry_ladder_step": 2.0},
        "favourable_ladder": {"leg_count": 2, "volume_weights": (.01, .01), "target_steps": (5., 8.),
                              "entry_ladder_mode": "favourable", "entry_ladder_step": 2.0},
        "break_even": {"be_mode": "price", "be_trigger": 3.0},
        "trailing": {"target_mode": "none", "target_steps": (), "trailing_distance": 3.0},
        "profit_lock": {"profit_lock_arm": 4.0, "profit_lock_giveback": 2.0},
        "time_exit": {"target_mode": "none", "target_steps": (), "time_exit_min": 5},
        "partial_runner": {"target_mode": "partial_runner", "target_steps": (), "target_value": 3.0,
                           "partial_fraction": .5, "runner_target": 8.0},
        "spread_filter": {"context_filter_mode": "max_spread", "context_filter_value": .6},
        "utc_cutoff": {"context_filter_mode": "time_window", "context_filter_value": 12.0},
        "no_entry": {"entry_mode": "no_entry"},
    }
    rows = []
    for name, values in changes.items():
        genome = base.with_change(**values)
        if (genome.validation_errors() or genome.schema_version != 2 or genome.provider_management_mode != "ignore"
                or genome.stop_mode != "fixed_move" or genome.stop_value != 10 or sum(genome.volume_weights) != .02
                or genome.entry_expiry_min != 3 or genome.time_exit_min > 15):
            raise ValueError("reference does not support the frozen own-rule controls")
        rows.append({"family": name, "strategy": genome.to_dict(), "fingerprint": genome.fingerprint,
                     "initial_configured_loss_usd_before_gaps_and_costs": 20,
                     "parameter_selection": "fixed_integration_control_not_optimized"})
    if len({r["fingerprint"] for r in rows}) != len(rows):
        raise ValueError("duplicate control strategy")
    return rows


def catalog(reference):
    _check_grammar()
    controls = fixed_controls(reference)
    options = {}
    for field, choices in OPTIONS.items():
        options[field] = {}
        for choice in sorted(choices):
            represented = [r["family"] for r in controls if r["strategy"][field] == choice]
            status = "represented_by_fixed_control" if represented else "grammar_present_not_covered_by_control"
            if choice in {"provider", "provider_per_leg", "provider_target_all", "actual_mt5", "min_reward_risk"} or (
                    field == "provider_management_mode" and choice != "ignore"):
                status = "outside_own_rule_scope"
            if field == "context_filter_mode" and choice == "max_volatility":
                status = "blocked_pretrigger_context_contract"
            if field == "stop_mode" and choice in {"none", "basket_money"}:
                status = "requires_separate_risk_contract"
            if choice in {"published_range", "published_limit", "range_levels", "per_leg_levels", "fixed_level"}:
                status = "requires_causal_absolute_level_contract"
            options[field][choice] = {"status": status, "control_families": represented,
                                     "all_combinations_validated": False}
    extensions = {
        "opposite_direction": ("requires_new_rule_contract", "Explicit direction transform preserving original signal and scenario identities."),
        "shared_close_ack_release": ("requires_new_account_contract", "Release reservations from causally available close acknowledgements, not future exits."),
        "shared_account_controls": ("requires_new_account_contract", "Opposite-signal replacement, netting/hedging, daily stops, cooldown and capacity priority."),
        "reentries": ("requires_new_rule_contract", "New bounded attempts after completion, distinct from unfilled ladder slots."),
        "pretrigger_context": ("blocked_pretrigger_context_contract", "Retained pre-entry Bid/Ask, warmup coverage and causal indicator availability."),
        "adaptive_volatility": ("requires_new_rule_contract", "Stops, targets and size based on validated past-only context."),
        "trend_and_regime": ("requires_new_rule_contract", "Past-only regime classification and frozen switch rule; never label regimes from future returns."),
        "news_and_external_features": ("requires_new_timestamped_data", "Event-time and publication-time sources with revisions and frozen release availability."),
        "overnight": ("requires_new_data_and_money_contract", "Longer quote horizons, swaps, calendar, rollover and historical metadata."),
        "capital_margin_and_stopout": ("requires_capital_and_broker_contract", "Capital, loss tolerance, margin tiers, netting/hedging and stop-out semantics."),
        "partial_broker_fills": ("requires_broker_execution_contract", "Liquidity, fill sizes, partial confirmations and residual orders."),
        "multi_channel_or_asset": ("outside_current_universe", "Separate source identities, simultaneous exposure and currency rules."),
        "unbounded_recovery_or_martingale": ("excluded_unbounded_risk", "Only explicitly bounded alternatives may enter a new reviewed contract."),
    }
    return {"schema_version": "dubai_family_catalog_v1", "field_groups": FIELD_GROUPS,
        "categorical_options": options, "fixed_controls": controls,
        "units": {"entry_value": "seconds for delay; XAUUSD price units for pullback/momentum/reversal; absolute price for published_range/published_limit",
            "entry_confirmation_value": "XAUUSD price units", "entry_expiry_min": "minutes",
            "entry_ladder_step": "XAUUSD price units", "volume_weights": "lots per leg, not percentages",
            "target_steps": "XAUUSD price units", "target_value": "EUR basket equity for fixed_basket/partial_runner; XAUUSD price units for fixed_move",
            "partial_fraction": "fraction of position", "runner_target": "EUR total basket equity for partial_runner",
            "stop_value": "XAUUSD price units for fixed_move; EUR for basket_money; absolute price for fixed_level",
            "be_trigger": "price units for price/partial; minutes for delayed", "trailing_distance": "XAUUSD price units",
            "hard_stop_eur_per_leg": "EUR", "profit_lock_arm": "EUR", "profit_lock_giveback": "EUR",
            "time_exit_min": "minutes", "context_filter_value": "max_spread price units; time_window UTC hour cutoff, not a session range"},
        "future_coarse_domains_not_executed": {"delay_seconds": [1, 5, 15, 30, 60, 120],
            "entry_distance_price": [.5, 1, 2, 5, 10], "stop_distance_price": [2, 5, 10, 20, 40],
            "target_distance_price": [1, 2, 5, 10, 20, 40], "holding_minutes": [1, 5, 15, 30, 60, 120, 180],
            "rules": ["Review and freeze domains before search; no implicit Cartesian product.",
                "Maintain equal configured risk when resizing; respect broker lot steps and total exposure.",
                "Longer entry/holding/tail must fit a validated horizon; four hours is not four hours of holding plus entry.",
                "Report boundary hits, observational equivalence and untested interactions.",
                "Reserve exploratory budget for distant families; log every attempt and reused validation block."]},
        "extensions": {name: {"status": status, "requirement": requirement} for name, (status, requirement) in extensions.items()},
        "search_candidates": 0, "exhaustive_search_claimed": False, "automatic_selection_allowed": False,
        "money_contract_verified": False,
        "limitations": ["Grammar support does not certify broker execution or all parameter combinations.",
            "Fixed controls test integration, not profitability or the full family space.",
            "No prior provider outcomes, observed fills or future information are features."]}
