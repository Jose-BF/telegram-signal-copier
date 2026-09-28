"""Translate between calculator rules (research/basket_grid.BasketRule) and engine genomes
(research/dubai_iterative/contracts.StrategyGenome), for the M2 exactness check and to send
calculator zones to the exact engine (M6). Only the fields both sides implement are mapped;
anything else raises, so nothing is silently dropped.
"""
from __future__ import annotations

from research.basket_grid import BasketRule
from research.dubai_iterative.contracts import StrategyGenome

_ENTRY_TO_GENOME = {"market": "signal_market", "pullback": "pullback", "adverse_reversal": "adverse_reversal",
                    "momentum": "momentum", "delay": "delay"}
_ENTRY_FROM_GENOME = {v: k for k, v in _ENTRY_TO_GENOME.items()}


def genome_to_rule(g: StrategyGenome) -> BasketRule:
    if g.entry_mode not in _ENTRY_FROM_GENOME:
        raise ValueError(f"entry_mode {g.entry_mode} not in calculator")
    if g.target_mode not in {"none", "per_leg_steps"}:
        raise ValueError(f"target_mode {g.target_mode} not mapped")
    if g.stop_mode not in {"none", "fixed_move", "basket_money"}:
        raise ValueError(f"stop_mode {g.stop_mode} not mapped")
    if g.be_mode not in {"none", "price"}:
        raise ValueError(f"be_mode {g.be_mode} not mapped")
    if g.provider_management_mode != "ignore":
        raise ValueError("calculator ignores provider management; genome must use 'ignore'")
    entry = _ENTRY_FROM_GENOME[g.entry_mode]
    simultaneous = g.entry_ladder_mode == "simultaneous"
    if g.entry_ladder_mode not in {"simultaneous", "adverse", "favourable"}:
        raise ValueError(f"ladder {g.entry_ladder_mode} not mapped")
    return BasketRule(
        entry=entry,
        e_x=float(g.entry_value or 0.0) if entry in {"pullback", "adverse_reversal", "momentum"} else 0.0,
        e_y=float(g.entry_confirmation_value or 0.0) if entry == "adverse_reversal" else 0.0,
        delay_min=float(g.entry_value or 0.0) if entry == "delay" else 0.0,
        expiry_min=float(g.entry_expiry_min),
        weights=tuple(g.volume_weights),
        ladder="favourable" if g.entry_ladder_mode == "favourable" else "adverse",
        step=0.0 if simultaneous else float(g.entry_ladder_step or 0.0),
        tp_steps=tuple(g.target_steps) if g.target_mode == "per_leg_steps" else (),
        sl_move=float(g.stop_value) if g.stop_mode == "fixed_move" else 0.0,
        basket_stop_eur=float(g.stop_value) if g.stop_mode == "basket_money" else 0.0,
        trail=float(g.trailing_distance or 0.0),
        be_trigger=float(g.be_trigger or 0.0) if g.be_mode == "price" else 0.0,
        hard_stop_eur_leg=float(g.hard_stop_eur_per_leg or 0.0),
        lock_arm_eur=float(g.profit_lock_arm or 0.0),
        lock_give_eur=float(g.profit_lock_giveback or 0.0) if g.profit_lock_arm else 0.0,
        time_min=float(g.time_exit_min) if g.time_exit_mode != "none" else 0.0,
        time_mode=g.time_exit_mode if g.time_exit_mode != "none" else "always",
    )


def rule_to_genome(r: BasketRule) -> StrategyGenome:
    if r.invert:
        raise ValueError("the engine has no invert flag: evaluate on the flipped universe instead")
    if r.partial_frac:
        raise ValueError("partial runner needs the own-rule extension; not mapped here")
    multi = len(r.weights) > 1
    ladder = "simultaneous" if (not multi or r.step == 0.0) else r.ladder
    stop_mode, stop_value = "none", None
    if r.sl_move and r.basket_stop_eur:
        raise ValueError("engine takes one stop mode")
    if r.sl_move:
        stop_mode, stop_value = "fixed_move", r.sl_move
    elif r.basket_stop_eur:
        stop_mode, stop_value = "basket_money", r.basket_stop_eur
    g = StrategyGenome(
        schema_version=2, entry_mode=_ENTRY_TO_GENOME[r.entry],
        entry_value=(r.e_x if r.entry in {"pullback", "adverse_reversal", "momentum"} else
                     r.delay_min if r.entry == "delay" else None),
        entry_confirmation_value=r.e_y if r.entry == "adverse_reversal" else None,
        entry_expiry_min=int(r.expiry_min), entry_ladder_mode=ladder,
        entry_ladder_step=None if ladder == "simultaneous" else r.step,
        leg_count=len(r.weights), volume_weights=tuple(r.weights),
        target_mode="per_leg_steps" if r.tp_steps else "none",
        target_steps=tuple(r.tp_steps) + (r.tp_steps[-1],) * (len(r.weights) - len(r.tp_steps)) if r.tp_steps else (),
        be_mode="price" if r.be_trigger else "none", be_trigger=r.be_trigger or None,
        stop_mode=stop_mode, stop_value=stop_value,
        trailing_distance=r.trail or None, hard_stop_eur_per_leg=r.hard_stop_eur_leg or None,
        profit_lock_arm=r.lock_arm_eur or None, profit_lock_giveback=r.lock_give_eur or None,
        time_exit_min=int(r.time_min) if r.time_min else 240,
        time_exit_mode=r.time_mode if r.time_min else "none",
        provider_management_mode="ignore",
        pending_entry_policy="until_expiry" if ladder != "simultaneous" else "none",
    )
    errors = g.validation_errors()
    if errors:
        raise ValueError(f"genome invalid: {errors}")
    return g
