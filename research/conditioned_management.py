"""Management-only diagnostic with confirmed entries, never a prediction.

Exit times, prices, money and installed levels are deliberately not inputs.
The caller must bind the entry facts to native evidence independently.
"""

from dataclasses import asdict, replace
from decimal import Decimal

from research.causal_comparison import SequenceEvent
from research.causal_replay import time_ns, utc
from research.dubai_iterative.dataset import SignalLeg
from research.dubai_iterative.engine import simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import oracle_simulate
from research.gold_iterative.live_parity import _actual_fill_genome


def condition_entries(path, genome, entries):
    entries = tuple(entries)
    if not entries or len(entries) > genome.leg_count:
        raise ValueError("nonempty observed prefix within declared leg count required")
    if any(not isinstance(row, SequenceEvent) or row.kind != "entry" for row in entries):
        raise ValueError("conditioned inputs must be entries only")
    if [row.slot for row in entries] != list(range(1, len(entries) + 1)):
        raise ValueError("contiguous observed logical prefix required")
    if any(row.direction != path.direction for row in entries):
        raise ValueError("observed entry direction differs from signal")
    if any(row.volume != Decimal(str(genome.volume_weights[i])) for i, row in enumerate(entries)):
        raise ValueError("observed entry volume differs from frozen policy")
    if any(row.money != 0 for row in entries):
        raise ValueError("nonzero opening costs unsupported by conditioned control")
    if (not len(path.times_ns) or any(time_ns(row.at) < time_ns(path.signal_observed_at)
                                    or time_ns(row.at) > int(path.times_ns[-1]) for row in entries)):
        raise ValueError("observed entry outside causal replay window")
    if any(left.at > right.at for left, right in zip(entries, entries[1:])):
        raise ValueError("observed entry chronology contradicts logical slots")
    if genome.entry_mode == "actual_mt5" or genome.validation_errors():
        raise ValueError("valid independent policy contract required before conditioning")
    legs = tuple(SignalLeg(f"native_slot_{row.slot}", "market" if row.slot == 1 else "dca",
                           float(row.volume), row.at, float(row.price), None, None, None,
                           Decimal(0), (), ()) for row in entries)
    conditioned = replace(path, legs=legs, actual_pnl_eur=None, opened_at=entries[0].at,
                          entry_evidence_kind="actual_mt5")
    mirror = _actual_fill_genome(genome, conditioned)
    if mirror is None or mirror.validation_errors():
        raise ValueError("policy cannot represent conditioned entries")
    return conditioned, mirror


def replay_management(path, genome, entries):
    """Return three-engine evidence and replay events, or explicit blockers.

    Exact entry validation is a gate, not substitution of missing simulated
    entries. A prior simulated basket exit can prevent a later observed entry;
    that counterexample remains blocked rather than receiving an invented fill.
    """
    entries = tuple(entries)
    conditioned, mirror = condition_entries(path, genome, entries)
    results = {
        "scalar": asdict(simulate(conditioned, mirror)),
        "fast": asdict(FastEvaluator()(conditioned, mirror)),
        "oracle": asdict(oracle_simulate(conditioned, mirror)),
    }
    oracle = results["oracle"]
    mismatches = {name: [key for key in oracle if result[key] != oracle[key]]
                  for name, result in results.items() if name != "oracle"}
    blockers = sorted({str(reason) for result in results.values() for reason in result["blockers"]})
    if any(mismatches.values()):
        blockers.append("conditioned_engines_disagree")
    expected = {f"native_slot_{entry.slot}": entry for entry in entries}
    seen, events = set(), []
    for row in results["scalar"]["entries"]:
        entry = expected.get(row["ticket"])
        if (entry is None or row["ticket"] in seen or row["source"] != "observed_mt5_fill"
                or utc(row["opened_at"]) != entry.at
                or Decimal(str(row["entry_price"])) != entry.price
                or Decimal(str(row["volume"])) != entry.volume):
            blockers.append("conditioned_entry_facts_changed")
            continue
        seen.add(row["ticket"])
        events.append(entry)
    if seen != expected.keys():
        blockers.append("conditioned_entry_missing")
    for row in (() if blockers else results["scalar"]["exits"]):
        entry = expected.get(row["ticket"])
        if entry is None:
            blockers.append("conditioned_exit_identity_unbound")
            continue
        events.append(SequenceEvent(entry.slot, "exit", utc(row["closed_at"]), path.direction,
                                    row["exit_price"], row["volume"], row["pnl_eur"], row["reason"]))
    report = {
        "status": "blocked" if blockers else "evaluated_conditioned_management",
        "evidence_role": "management_hypothesis_conditioned_on_confirmed_entries",
        "full_live_parity_verified": False, "entry_decisions_verified": False,
        "client_observation_replay_verified": False, "native_policy_identity_verified": False,
        "blockers": sorted(set(blockers)), "engine_mismatches": mismatches,
        "declared_policy": asdict(genome), "conditioned_policy": asdict(mirror),
        "results": results,
    }
    return report, None if blockers else events
