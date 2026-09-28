"""Compare retained live-control shadow checkpoints with native fills, offline."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import gzip
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from broker_money import convert_profit_amount
from strategy_shadow_contracts import ShadowSignalState, canonical_hash
from tools.audit_native_equity_snapshots import _tape
from tools.audit_native_money_anchor import cents, digest, read
from tools.audit_native_week_risk_path import OFFSET_SECONDS, native_events, source_msc


MAX_SHADOW_ROWS = 10_000
MAX_SIGNAL_ROWS = 200
MAX_MARKS = 2_000


def utc_ms(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("explicit UTC timestamp required")
    delta = parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86_400 + delta.seconds) * 1000 + delta.microseconds // 1000


def shadow_rows(slice_path, manifest_path):
    manifest = read(manifest_path)
    if manifest.get("status") != "complete" or digest(slice_path) != manifest.get("output_sha256"):
        raise ValueError("shadow slice digest/status mismatch")
    rows = []
    with gzip.open(slice_path, "rt", encoding="utf-8") as source:
        for line in source:
            rows.append(json.loads(line))
            if len(rows) > MAX_SHADOW_ROWS:
                raise ValueError("shadow row budget exceeded")
    if len(rows) != manifest["selected_lines"]:
        raise ValueError("shadow selected-line count mismatch")
    return rows


def controls_and_states(rows):
    starts = [row for row in rows if row.get("ev") == "strategy_shadow_runtime_started"]
    if not starts:
        raise ValueError("no shadow runtime manifest")
    controls = starts[0]["controls"]
    manifest = starts[0]["catalog_manifest"]
    payload = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    if canonical_hash(payload) != manifest["manifest_hash"] or any(
        row["controls"] != controls or row["catalog_manifest"] != manifest for row in starts
    ):
        raise ValueError("shadow control manifest changed")
    policies = {(p["channel"], p["candidate_id"]): p for p in manifest["policies"]}
    for channel, candidate in controls.items():
        if policies[(channel, candidate)]["role"] != "live_control":
            raise ValueError("control role mismatch")
    states = defaultdict(list)
    chain = {}
    registered = set()
    in_slice = {(row["sig"], row.get("candidate_id")) for row in rows
                if row.get("ev") == "strategy_shadow_registered"
                and controls.get(row.get("channel")) == row.get("candidate_id")}
    for row in rows:
        event = row.get("ev")
        if event not in {"strategy_shadow_registered", "strategy_shadow_transition",
                         "strategy_shadow_checkpoint", "strategy_shadow_recovered"}:
            continue
        channel = row.get("channel")
        candidate = row.get("candidate_id")
        if controls.get(channel) != candidate:
            continue
        signal = row["sig"]
        key = (signal, candidate)
        if key not in in_slice:
            continue
        state = ShadowSignalState.from_dict(row["state"])
        if (state.state_hash != row["state_hash"] or state.signal_id != signal
                or state.channel != channel or state.candidate_id != candidate
                or state.strategy_fingerprint != policies[(channel, candidate)]["strategy_fingerprint"]
                or state.execution_fingerprint != policies[(channel, candidate)]["execution_fingerprint"]):
            raise ValueError(f"shadow state or policy hash mismatch: {key}")
        previous = row.get("previous_state_hash")
        if event == "strategy_shadow_registered":
            if key in registered or previous is not None:
                raise ValueError(f"duplicate shadow registration: {key}")
            registered.add(key)
        elif key in chain and previous != chain[key]:
            raise ValueError(f"shadow state chain mismatch: {key}")
        elif key not in chain and event != "strategy_shadow_recovered":
            raise ValueError(f"shadow state without registration: {key}")
        chain[key] = state.state_hash
        states[signal].append(row)
    return controls, states, registered, manifest["manifest_hash"]


def first_virtual_fill(rows):
    for row in rows:
        if row["ev"] == "strategy_shadow_transition" and row.get("transition") == "virtual_fill":
            return row
    return None


def pair_entry_fills(transitions, positions, *, contract_size, direct_clock):
    result = {"status": "blocked_clock" if not direct_clock else "blocked_entry_count",
              "pairing_basis": "entry_ordinal_and_volume_hypothesis",
              "full_entry_identity_verified": False, "pairs": [],
              "cumulative_shadow_minus_native_entry_effect_quote": None}
    if not direct_clock:
        return result
    fills = [row for row in transitions if row["ev"] == "strategy_shadow_transition"
             and row.get("transition") == "virtual_fill"]
    native = sorted(positions, key=lambda row: (row["entry_msc"], row["position_id"]))
    if not fills or len(fills) != len(native):
        return result
    try:
        size = Decimal(str(contract_size))
        legs = {row["transition_details"]["leg_index"]: row for row in fills}
        if (size <= 0 or not size.is_finite() or len(legs) != len(fills)
                or len({p["position_id"] for p in native}) != len(native)
                or set(legs) != set(range(len(fills)))):
            raise ValueError("invalid entry ordinal or contract size")
        direction = native[0]["direction"]
        if direction not in {"BUY", "SELL"} or any(
                row["direction"] != direction for row in native) or any(
                row["state"]["direction"] != direction for row in fills):
            raise ValueError("entry direction mismatch")
        total = Decimal(0)
        pairs = []
        for index, position in enumerate(native):
            fill = legs[index]
            details = fill["transition_details"]
            volume = Decimal(str(position["volume"]))
            shadow_volume = Decimal(str(details["volume"]))
            if volume <= 0 or not volume.is_finite() or volume != shadow_volume:
                result["status"] = "blocked_leg_volume"
                return result
            native_price = Decimal(str(position["entry_price"]))
            shadow_price = Decimal(str(details["entry_price"]))
            shadow_at = fill["transition_tick_msc"]
            native_at = position["entry_msc"] - OFFSET_SECONDS * 1000
            if (not native_price.is_finite() or not shadow_price.is_finite()
                    or native_price <= 0 or shadow_price <= 0
                    or type(shadow_at) is not int or type(native_at) is not int):
                raise ValueError("invalid entry price or clock")
            effect = ((native_price - shadow_price) if direction == "BUY"
                      else (shadow_price - native_price)) * volume * size
            total += effect
            pairs.append({"leg_index": index, "native_position_id": position["position_id"],
                          "volume": str(volume), "native_entry_price": str(native_price),
                          "shadow_entry_price": str(shadow_price),
                          "native_minus_shadow_ms": native_at - shadow_at,
                          "shadow_minus_native_entry_effect_quote": str(effect)})
    except (KeyError, TypeError, ValueError, ArithmeticError):
        result["status"] = "blocked_entry_facts"
        return result
    result.update(status="diagnostic_ordinal_pairing", pairs=pairs,
                  cumulative_shadow_minus_native_entry_effect_quote=str(total))
    return result


def upgrade_pairing_with_journal_binding(pairing, binding, binding_hash):
    if (pairing["status"] != "diagnostic_ordinal_pairing"
            or binding.get("status") != "verified_live_leg_journal_binding"
            or binding.get("full_entry_identity_verified") is not True):
        raise ValueError("verified entry binding unavailable")
    paired = {row["leg_index"]: row for row in pairing["pairs"]}
    bound = {row["leg_index"]: row for row in binding["rows"]}
    if (len(paired) != len(pairing["pairs"]) or len(bound) != len(binding["rows"])
            or set(paired) != set(bound)):
        raise ValueError("verified entry leg denominator mismatch")
    for leg, row in paired.items():
        proof = bound[leg]
        if (row["native_position_id"] != proof["native_position_id"]
                or Decimal(row["native_entry_price"]) != Decimal(proof["entry_price"])
                or Decimal(row["volume"]) != Decimal(proof["volume"])):
            raise ValueError("verified entry leg identity contradicts ordinal pairing")
    return {**pairing, "status": "verified_journal_entry_binding",
            "pairing_basis": "live_fill_result_native_deal_ticket",
            "full_entry_identity_verified": True,
            "binding_report_sha256": binding_hash}


def load_binding_reports(paths, expected_sources, manifest_hash):
    reports = {}
    for path in map(Path, paths):
        report = read(path)
        signal = report.get("signal_id")
        if (report.get("contract") != "shadow_live_protection_level_diagnostic_v1"
                or report.get("status") != "diagnostic_only"
                or report.get("control_manifest_hash") != manifest_hash
                or report.get("native_clock_direct") is not True
                or report.get("live_leg_binding", {}).get("full_entry_identity_verified") is not True
                or not isinstance(signal, str) or signal in reports):
            raise ValueError("entry binding report contract or identity mismatch")
        inputs = report.get("inputs_sha256", {})
        for name, sha in inputs.items():
            if digest(Path(name)) != sha:
                raise ValueError("entry binding source hash mismatch")
        for source in expected_sources:
            matches = [sha for name, sha in inputs.items()
                       if Path(name).resolve() == source.resolve()]
            if len(matches) != 1 or matches[0] != digest(source):
                raise ValueError("entry binding shared source mismatch")
        reports[signal] = {"binding": report["live_leg_binding"],
                           "sha256": digest(path), "path": path,
                           "inputs_sha256": inputs}
    return reports


def mark_native(positions, deal_events, tick, fx, contract):
    """Mark actual fills at a shadow tick; same-ms native ordering stays unknown."""
    stamp = int(tick["time_msc"]) + OFFSET_SECONDS * 1000
    if any(source_msc(event.at) == stamp
           for events in deal_events.values() for event in events):
        return {"status": "same_millisecond_native_deal_order_unknown"}
    realized = sum((event.money for events in deal_events.values() for event in events
                    if source_msc(event.at) < stamp), Decimal(0))
    open_rows = [p for p in positions if p["entry_msc"] < stamp < p["exit_msc"]]
    volume = sum((Decimal(str(p["volume"])) for p in open_rows), Decimal(0))
    result = {"status": "compared", "native_open_volume": str(volume),
              "native_realized_eur": str(realized), "native_open_count": len(open_rows)}
    if not open_rows:
        result.update(native_floating_eur="0.00", native_total_eur=str(realized))
        return result
    fx_times, fx_bid, fx_ask = fx
    index = int(np.searchsorted(fx_times, stamp, side="right")) - 1
    if index < 0:
        result["status"] = "missing_prior_fx"
        return result
    age = stamp - int(fx_times[index])
    result["prior_fx_age_ms"] = age
    if age > contract["conversion"]["max_quote_age_ms"]:
        result["status"] = "stale_prior_fx"
        return result
    floating = Decimal(0)
    for p in open_rows:
        side = Decimal(str(tick["bid"] if p["direction"] == "BUY" else tick["ask"]))
        movement = (side - Decimal(str(p["entry_price"]))) * (1 if p["direction"] == "BUY" else -1)
        raw = movement * Decimal(str(p["volume"])) * Decimal(str(contract["instrument"]["contract_size"]))
        rate = Decimal(str(fx_ask[index] if raw >= 0 else fx_bid[index]))
        floating += convert_profit_amount(raw, rate, orientation=contract["conversion"]["orientation"],
                                          currency_digits=contract["account"]["currency_digits"])
    result.update(native_floating_eur=str(floating), native_total_eur=str(realized + floating))
    return result


def entry_price_effect_at_mark(positions, deal_events, state, tick, fx, contract, native, pairs):
    if native["status"] != "compared":
        return {"status": "blocked_native_mark"}
    if cents(state["realized_eur"]) != Decimal(native["native_realized_eur"]):
        return {"status": "blocked_realized_mismatch"}
    stamp = int(tick["time_msc"]) + OFFSET_SECONDS * 1000
    native_open = [p for p in positions if p["entry_msc"] < stamp < p["exit_msc"]]
    native_closed_ids = {p["position_id"] for p in positions
                         if p["entry_msc"] < stamp and p["exit_msc"] < stamp}
    open_ids = {p["position_id"] for p in native_open}
    shadow_open = [p for p in state["positions"] if p["status"] == "open"]
    shadow_closed = [p for p in state["positions"] if p["status"] == "closed"]
    mapped_closed = [pairs.get(p["leg_index"], {}).get("native_position_id")
                     for p in shadow_closed]
    if (len(shadow_open) + len(shadow_closed) != len(state["positions"])
            or len(mapped_closed) != len(native_closed_ids)
            or set(mapped_closed) != native_closed_ids):
        return {"status": "blocked_closed_leg_identity"}
    if (not native_open or len(open_ids) != len(native_open)
            or len(shadow_open) != len(native_open)
            or {pairs.get(p["leg_index"], {}).get("native_position_id") for p in shadow_open} != open_ids
            or any(Decimal(str(p["entry_price"])) !=
                   Decimal(str(pairs[p["leg_index"]]["shadow_entry_price"])) for p in shadow_open)):
        return {"status": "blocked_exposure"}
    virtual_prices = {pair["native_position_id"]: pair["shadow_entry_price"] for pair in pairs.values()}
    replaced = [{**p, "entry_price": virtual_prices[p["position_id"]]}
                if p["position_id"] in open_ids else p for p in positions]
    counterfactual = mark_native(replaced, deal_events, tick, fx, contract)
    if counterfactual["status"] != "compared":
        return {"status": "blocked_counterfactual_mark"}
    return {"status": "attributed",
            "entry_price_only_model_delta_eur": str(
                Decimal(counterfactual["native_floating_eur"])
                - Decimal(native["native_floating_eur"]))}


def verified_exposure_at_mark(positions, state, final_positions, pairs, stamp):
    native = {pairs[leg]["native_position_id"]: leg for leg in pairs}
    by_ticket = {p["position_id"]: p for p in positions}
    final = {p["leg_index"]: p for p in final_positions}
    current = {p["leg_index"]: p for p in state["positions"]}
    if (len(native) != len(pairs) or len(by_ticket) != len(positions)
            or len(final) != len(final_positions) or len(current) != len(state["positions"])
            or set(final) != set(pairs) or set(by_ticket) != set(native)
            or not set(current) <= set(final)):
        raise ValueError("verified exposure leg denominator mismatch")
    mismatches = []
    volume_delta = Decimal(0)
    for leg, pair in sorted(pairs.items()):
        position = by_ticket[pair["native_position_id"]]
        virtual = final[leg]
        native_entry = position["entry_msc"] - OFFSET_SECONDS * 1000
        native_exit = position["exit_msc"] - OFFSET_SECONDS * 1000
        virtual_entry = virtual["opened_tick_msc"]
        virtual_exit = virtual.get("closed_tick_msc")
        if stamp in (native_entry, native_exit):
            return {"status": "blocked_same_ms_native_event", "mismatches": [],
                    "volume_delta_from_legs": None}
        native_open = native_entry < stamp < native_exit
        virtual_open = (virtual_entry <= stamp and
                        (virtual_exit is None or stamp < virtual_exit))
        observed_open = leg in current and current[leg]["status"] == "open"
        if observed_open != virtual_open:
            raise ValueError("verified exposure state/tick chronology mismatch")
        volume = Decimal(str(position["volume"]))
        if Decimal(str(virtual["volume"])) != volume:
            raise ValueError("verified exposure leg volume mismatch")
        volume_delta += volume * (int(virtual_open) - int(native_open))
        if virtual_open == native_open:
            continue
        if virtual_open:
            reason = ("virtual_entry_precedes_native" if stamp < native_entry
                      else "native_exit_precedes_virtual")
        else:
            reason = ("native_entry_precedes_virtual" if stamp < virtual_entry
                      else "virtual_exit_precedes_native")
        mismatches.append({"leg_index": leg, "native_position_id": position["position_id"],
                           "reason": reason, "native_entry_utc_msc": native_entry,
                           "native_exit_utc_msc": native_exit,
                           "virtual_entry_tick_msc": virtual_entry,
                           "virtual_exit_tick_msc": virtual_exit})
    return {"status": "different_open_legs" if mismatches else "same_open_legs",
            "mismatches": mismatches, "volume_delta_from_legs": str(volume_delta)}


def drawdown_context(marks, realized_key, floating_key):
    if not marks:
        return None
    peak, peak_at = Decimal(0), None
    worst, context = Decimal(0), None
    for item in marks:
        total = Decimal(item[realized_key]) + Decimal(item[floating_key])
        if total > peak:
            peak, peak_at = total, item["utc_msc"]
        if peak - total > worst:
            worst = peak - total
            context = {"max_drawdown_eur": str(worst), "peak_eur": str(peak),
                       "peak_at_utc_msc": peak_at, "trough_eur": str(total),
                       "trough_at_utc_msc": item["utc_msc"],
                       "entry_price_effect_at_trough_eur":
                           item.get("entry_price_only_model_delta_eur")}
    return context or {"max_drawdown_eur": "0", "peak_eur": str(peak),
                       "peak_at_utc_msc": peak_at, "trough_eur": None,
                       "trough_at_utc_msc": None, "entry_price_effect_at_trough_eur": None}


def summarize_transition_lags(rows):
    grouped = defaultdict(list)
    for row in rows:
        lag = row["lag_ms"]
        kind = row["transition"]
        event_id = row["event_id"]
        if (type(lag) is not int or lag < 0 or not isinstance(kind, str)
                or not kind or not isinstance(event_id, str) or not event_id):
            raise ValueError("negative transition lag or invalid transition identity")
        grouped[kind].append((lag, event_id))
    return {kind: {"count": len(values), "min_lag_ms": min(lag for lag, _ in values),
                   "max_lag_ms": max(values)[0], "max_event_id": max(values)[1],
                   "over_5s_count": sum(lag > 5_000 for lag, _ in values),
                   "over_60s_count": sum(lag > 60_000 for lag, _ in values)}
            for kind, values in sorted(grouped.items())}


def compare_signal(signal, rows, positions, deal_events, market, fx, contract, clock,
                   verified_binding=None):
    transitions = [r for r in rows if r["ev"] == "strategy_shadow_transition"]
    fill = first_virtual_fill(transitions)
    first_native = min(positions, key=lambda p: p["entry_msc"])
    report = {"signal_id": signal, "channel": first_native["channel"],
              "control_candidate": rows[0]["candidate_id"],
              "control_state_events": len(rows), "control_transitions": len(transitions),
              "first_native_entry_source_msc": first_native["entry_msc"],
              "first_native_entry_price": first_native["entry_price"],
              "first_native_entry_volume": first_native["volume"],
              "native_final_net_eur": str(sum((Decimal(p["actual_net_eur"]) for p in positions), Decimal(0))),
              "clock_direct_for_all_native_event_days": all(
                  clock.get(datetime.fromtimestamp(p[t] / 1000, timezone.utc).date().isoformat(), {}).get("status")
                  == "direct_anchor_available" for p in positions for t in ("entry_msc", "exit_msc"))}
    pairing = pair_entry_fills(transitions, positions,
                               contract_size=contract["instrument"]["contract_size"],
                               direct_clock=report["clock_direct_for_all_native_event_days"])
    if verified_binding is not None:
        pairing = upgrade_pairing_with_journal_binding(
            pairing, verified_binding["binding"], verified_binding["sha256"])
    report["entry_pairing"] = pairing
    paired_legs = {row["leg_index"]: row for row in pairing["pairs"]}
    final_shadow_positions = (rows[-1]["state"]["positions"]
                              if pairing["full_entry_identity_verified"] else None)
    if fill is None:
        report["first_fill_status"] = "no_virtual_fill_in_slice"
    else:
        details = fill["transition_details"]
        emitted_msc = utc_ms(fill["ts"])
        tick_msc = int(fill["transition_tick_msc"])
        if emitted_msc < tick_msc:
            raise ValueError("shadow transition emitted before its tick")
        report.update(first_fill_status="observed", first_virtual_fill_utc_msc=fill["transition_tick_msc"],
                      first_virtual_fill_journal_utc=fill["ts"],
                      first_virtual_fill_emit_minus_tick_ms=emitted_msc - tick_msc,
                      first_native_minus_shadow_emit_ms=first_native["entry_msc"]
                      - OFFSET_SECONDS * 1000 - emitted_msc,
                      first_virtual_fill_price=details["entry_price"],
                      first_virtual_fill_volume=details["volume"],
                      first_native_minus_virtual_ms=first_native["entry_msc"]
                      - fill["transition_tick_msc"] - OFFSET_SECONDS * 1000,
                      first_native_minus_virtual_price=str(Decimal(str(first_native["entry_price"]))
                                                           - Decimal(str(details["entry_price"]))))
    last = rows[-1]["state"]
    report["last_shadow_state"] = {key: last[key] for key in
                                   ("status", "realized_eur", "floating_eur", "complete", "evidence_blockers")}
    if last["status"] == "closed" and not last["evidence_blockers"]:
        report["last_shadow_minus_native_final_eur"] = str(
            cents(last["realized_eur"]) + cents(last["floating_eur"])
            - Decimal(report["native_final_net_eur"]))
    marks = {}
    transition_lags = []
    transition_lag_rows = []
    market_times, market_bid, market_ask = market
    for row in transitions:
        tick = row.get("tick")
        if tick is None:
            continue
        stamp = int(tick["time_msc"]) + OFFSET_SECONDS * 1000
        left = int(np.searchsorted(market_times, stamp, side="left"))
        right = int(np.searchsorted(market_times, stamp, side="right"))
        if left == right or not any(
            Decimal(str(market_bid[i])) == Decimal(str(tick["bid"]))
            and Decimal(str(market_ask[i])) == Decimal(str(tick["ask"])) for i in range(left, right)
        ):
            raise ValueError(f"shadow tick not present in native market tape: {signal}/{stamp}")
        if row["transition_tick_msc"] != tick["time_msc"]:
            raise ValueError("shadow transition/tick time mismatch")
        emitted_lag = utc_ms(row["ts"]) - int(tick["time_msc"])
        if emitted_lag < 0:
            raise ValueError("shadow transition emitted before its tick")
        transition_lags.append(emitted_lag)
        transition_lag_rows.append({"transition": row["transition"],
                                    "lag_ms": emitted_lag, "event_id": row["event_id"]})
        prior = marks.get(tick["time_msc"])
        if prior is not None and prior["state_hash"] != row["state_hash"]:
            raise ValueError("different terminal states for one shadow tick")
        marks[tick["time_msc"]] = row
    if len(marks) > MAX_MARKS:
        raise ValueError("per-signal comparison mark budget exceeded")
    report["ticked_transition_count"] = len(transition_lags)
    report["ticked_transition_emission_lag_min_ms"] = min(transition_lags) if transition_lags else None
    report["ticked_transition_emission_lag_max_ms"] = max(transition_lags) if transition_lags else None
    report["ticked_transitions_emitted_over_5s_after_tick"] = sum(lag > 5_000 for lag in transition_lags)
    report["ticked_transition_emission_lags_by_type"] = summarize_transition_lags(
        transition_lag_rows)
    samples = []
    for stamp, row in sorted(marks.items()):
        state = row["state"]
        native = mark_native(positions, deal_events, row["tick"], fx, contract)
        shadow_volume = sum((Decimal(str(p["volume"])) for p in state["positions"]
                             if p["status"] == "open"), Decimal(0))
        item = {"utc_msc": stamp, "shadow_state_hash": row["state_hash"],
                "shadow_open_volume": str(shadow_volume),
                "shadow_realized_eur": str(cents(state["realized_eur"])),
                "shadow_floating_eur": str(cents(state["floating_eur"])), **native}
        if final_shadow_positions is not None:
            interval = verified_exposure_at_mark(positions, state, final_shadow_positions,
                                                 paired_legs, stamp)
            item["verified_exposure_interval"] = interval
            if (interval["volume_delta_from_legs"] is not None
                    and Decimal(interval["volume_delta_from_legs"]) !=
                    shadow_volume - Decimal(native["native_open_volume"])):
                raise ValueError("verified exposure intervals do not reproduce volume delta")
        if native["status"] == "compared":
            item["volume_delta"] = str(shadow_volume - Decimal(native["native_open_volume"]))
            item["realized_delta_eur"] = str(cents(state["realized_eur"])
                                               - Decimal(native["native_realized_eur"]))
            item["floating_delta_eur"] = str(cents(state["floating_eur"])
                                               - Decimal(native["native_floating_eur"]))
            item["total_delta_eur"] = str(cents(state["realized_eur"])
                                            + cents(state["floating_eur"])
                                            - Decimal(native["native_total_eur"]))
            item["same_exposure_and_money"] = all(Decimal(item[key]) == 0 for key in
                                                  ("volume_delta", "realized_delta_eur", "floating_delta_eur"))
            if pairing["status"] in {"diagnostic_ordinal_pairing", "verified_journal_entry_binding"}:
                effect = entry_price_effect_at_mark(positions, deal_events, state, row["tick"],
                                                    fx, contract, native, paired_legs)
                item["entry_effect_status"] = effect["status"]
                if effect["status"] == "attributed":
                    item["entry_price_only_model_delta_eur"] = effect["entry_price_only_model_delta_eur"]
                    item["remaining_total_delta_after_entry_price_eur"] = str(
                        Decimal(item["total_delta_eur"])
                        - Decimal(effect["entry_price_only_model_delta_eur"]))
        samples.append(item)
    report["mark_count"] = len(samples)
    report["mark_statuses"] = dict(Counter(item["status"] for item in samples))
    if final_shadow_positions is not None:
        report["verified_exposure_interval_statuses"] = dict(Counter(
            item["verified_exposure_interval"]["status"] for item in samples))
        report["verified_exposure_reason_counts"] = dict(Counter(
            mismatch["reason"] for item in samples
            for mismatch in item["verified_exposure_interval"]["mismatches"]))
    report["same_exposure_and_money_marks"] = sum(item.get("same_exposure_and_money") is True for item in samples)
    report["native_open_marks"] = sum(Decimal(item.get("native_open_volume", "0")) > 0 for item in samples)
    report["shadow_open_marks"] = sum(Decimal(item["shadow_open_volume"]) > 0 for item in samples)
    report["both_open_marks"] = sum(Decimal(item.get("native_open_volume", "0")) > 0
                                    and Decimal(item["shadow_open_volume"]) > 0 for item in samples)
    report["first_common_tick_utc_msc"] = samples[0]["utc_msc"] if samples else None
    report["last_common_tick_utc_msc"] = samples[-1]["utc_msc"] if samples else None
    report["last_native_exit_source_msc"] = max(p["exit_msc"] for p in positions)
    report["first_divergence"] = next((item for item in samples if item.get("same_exposure_and_money") is False), None)
    report["first_exposure_divergence"] = next((item for item in samples
        if "volume_delta" in item and Decimal(item["volume_delta"]) != 0), None)
    report["first_total_delta_over_one_eur"] = next((item for item in samples
        if "total_delta_eur" in item and abs(Decimal(item["total_delta_eur"])) > 1), None)
    report["first_blocked_mark"] = next((item for item in samples if item["status"] != "compared"), None)
    report["max_abs_total_delta_eur"] = str(max(
        (abs(Decimal(item["total_delta_eur"])) for item in samples if "total_delta_eur" in item), default=Decimal(0)))
    attributed = [item for item in samples if item.get("entry_effect_status") == "attributed"]
    report["entry_price_attributed_mark_count"] = len(attributed)
    report["max_abs_remaining_delta_after_entry_price_eur"] = (
        str(max(abs(Decimal(item["remaining_total_delta_after_entry_price_eur"]))
                for item in attributed)) if attributed else None)
    report["first_attributed_total_delta_over_one_eur"] = next(
        (item for item in attributed if abs(Decimal(item["total_delta_eur"])) > 1), None)
    comparable = [item for item in samples if item["status"] == "compared"]
    for prefix, realized_key, floating_key in (
        ("shadow", "shadow_realized_eur", "shadow_floating_eur"),
        ("native", "native_realized_eur", "native_floating_eur"),
    ):
        peak = Decimal(0)
        drawdown = Decimal(0)
        minimum = Decimal(0)
        for item in comparable:
            total = Decimal(item[realized_key]) + Decimal(item[floating_key])
            peak = max(peak, total)
            drawdown = max(drawdown, peak - total)
            minimum = min(minimum, total)
        report[f"{prefix}_common_mark_max_drawdown_eur"] = str(drawdown)
        report[f"{prefix}_common_mark_minimum_eur"] = str(minimum)
        report[f"{prefix}_common_mark_drawdown_context"] = drawdown_context(
            comparable, realized_key, floating_key)
    report["sample_stream"] = samples
    return report


def audit(shadow_slice, shadow_manifest, native_deals, native_baskets, money_path,
          anchor_path, contract_path, raw_dir, entry_binding_reports=()):
    paths = list(map(Path, (shadow_slice, shadow_manifest, native_deals, native_baskets,
                            money_path, anchor_path, contract_path)))
    shadow_slice, shadow_manifest, native_deals, native_baskets, money_path, anchor_path, contract_path = paths
    rows = shadow_rows(shadow_slice, shadow_manifest)
    controls, states, registered, manifest_hash = controls_and_states(rows)
    verified_bindings = load_binding_reports(
        entry_binding_reports, (shadow_slice, shadow_manifest, native_deals,
                                money_path, anchor_path), manifest_hash)
    native, baskets, money, anchor, contract = map(read, (native_deals, native_baskets,
                                                        money_path, anchor_path, contract_path))
    if (baskets["source_sha256"] != digest(native_deals)
            or money["contract"] != "native_closed_money_anchor_v2"
            or money["account_currency"] != contract["account"]["currency"]
            or anchor["contract"] != "native_tick_anchor_diagnostic_v1"
            or contract["account"]["server"] != anchor["scope"]["server"]):
        raise ValueError("native source/contract identity mismatch")
    for path in (native_deals, native_baskets, contract_path, anchor_path):
        matching = [sha for name, sha in money["inputs_sha256"].items()
                    if Path(name).resolve() == path.resolve()]
        if len(matching) != 1 or matching[0] != digest(path):
            raise ValueError(f"money audit source mismatch: {path}")
    code_paths = (Path(__file__), Path(sys.modules[ShadowSignalState.__module__].__file__),
                  Path(sys.modules[convert_profit_amount.__module__].__file__),
                  Path(sys.modules[native_events.__module__].__file__),
                  Path(sys.modules[_tape.__module__].__file__))
    watched = {str(p): digest(p) for p in (*paths, *code_paths)}
    watched.update({str(item["path"]): item["sha256"] for item in verified_bindings.values()})
    for binding in verified_bindings.values():
        watched.update(binding["inputs_sha256"])
    start = datetime.fromisoformat(anchor["scope"]["source_epoch_start"])
    end = datetime.fromisoformat(anchor["scope"]["source_epoch_end_exclusive"])
    market = _tape(Path(raw_dir), anchor, "XAUUSD", start, end, watched)
    fx = _tape(Path(raw_dir), anchor, "EURUSD", start, end, watched)
    day_start = start.date().isoformat()
    day_end = end.date().isoformat()
    received_all = {row["sig"] for row in rows if row.get("ev") == "signal_received"}
    received = {row["sig"] for row in rows if row.get("ev") == "signal_received"
                and day_start <= row["ts"][:10] < day_end}
    native_by_signal = {row["signal_id"]: row for row in baskets["baskets"]}
    by_signal = defaultdict(list)
    for position in money["positions"]:
        by_signal[position["signal_id"]].append(position)
    if (len(received) > MAX_SIGNAL_ROWS or len(native_by_signal) != len(baskets["baskets"])
            or not set(native_by_signal) <= received or set(native_by_signal) != set(by_signal)
            or not set(states) <= received):
        raise ValueError("weekly signal denominator or identity mismatch")
    events = native_events(native["deals"], money["positions"])
    clock = anchor["independent_clock_evidence"]["days"]
    comparisons = []
    coverage = []
    for signal in sorted(received):
        has_native = signal in native_by_signal
        has_control = any(key[0] == signal for key in registered)
        coverage.append({"signal_id": signal, "native_basket_in_extract": has_native,
                         "live_control_registered_in_slice": has_control,
                         "control_state_events": len(states.get(signal, []))})
        if has_native and has_control:
            positions = by_signal[signal]
            comparisons.append(compare_signal(signal, states[signal], positions,
                {p["position_id"]: events[p["position_id"]] for p in positions},
                market, fx, contract, clock, verified_bindings.get(signal)))
    if set(verified_bindings) - {row["signal_id"] for row in comparisons}:
        raise ValueError("entry binding report has no weekly control comparison")
    for path, sha in watched.items():
        if digest(path) != sha:
            raise ValueError(f"source changed during audit: {path}")
    return {"contract": "week_shadow_live_control_path_diagnostic_v1", "status": "diagnostic_only",
            "source_clock_offset_seconds_hypothesis": OFFSET_SECONDS,
            "control_manifest_hash": manifest_hash, "controls": controls,
            "received_signal_count": len(received),
            "received_outside_week_in_slice": sorted(received_all - received),
            "native_basket_count": len(native_by_signal),
            "registered_control_count": len(registered), "compared_control_count": len(comparisons),
            "verified_entry_identity_signal_count": sum(
                row["entry_pairing"]["full_entry_identity_verified"] for row in comparisons),
            "entry_price_attributed_marks_with_verified_identity": sum(
                row["entry_price_attributed_mark_count"] for row in comparisons
                if row["entry_pairing"]["full_entry_identity_verified"]),
            "coverage": coverage, "comparisons": comparisons, "inputs_sha256": watched,
            "policy_decision_parity_verified": False, "simulator_certified": False,
            "limitations": ["Selected journal suffix only; missing controls or live requests are unknown, not no-trade.",
                            "Native broker epoch to UTC is a hypothesis except directly anchored days.",
                            "Shadow terminal state per tick is not intermediate per-transition state.",
                            "Journal ts is transition emission time, not the historical tick time or an MT5 order timestamp.",
                            "Prior FX older than the causal limit blocks monetary mark comparison.",
                            "Common-mark drawdown samples only shadow transition ticks; zero native-open marks means no native risk-path coverage.",
                            "Unbound controls retain ordinal/volume leg pairing; all entry-price effects condition on native fills and a shared quote/FX.",
                            "Verified ticket binding does not establish broker-equivalent counterfactual fill timing or continuous account equity.",
                            "A matching sampled path is not continuous native equity or decision/request parity."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shadow-slice", type=Path, required=True)
    parser.add_argument("--shadow-manifest", type=Path, required=True)
    parser.add_argument("--native-deals", type=Path, required=True)
    parser.add_argument("--native-baskets", type=Path, required=True)
    parser.add_argument("--money", type=Path, required=True)
    parser.add_argument("--anchor", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--entry-binding-report", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.shadow_slice, args.shadow_manifest, args.native_deals, args.native_baskets,
                   args.money, args.anchor, args.contract, args.raw_dir,
                   args.entry_binding_report)
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "received_signal_count", "native_basket_count",
                                                 "registered_control_count", "compared_control_count")}, indent=2))


if __name__ == "__main__":
    main()
