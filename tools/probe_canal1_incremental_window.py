"""Bounded offline diagnostic of a frozen Telegram/MT5 window."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from decimal import Decimal
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.causal_canal1_stream import compile_canal1_stream
from research.causal_lifecycle import LifecycleTiming
from research.causal_replay import compile_signals, time_ns, utc
from research.causal_shared_stream import run_incremental_shared_canal1_stream
from research.dubai_iterative.client import ClientProfile
from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.latency_model import LatencyModel
from research.dubai_iterative.market_contract import MarketProfile
from research.dubai_iterative.protection_contract import ProtectionProfile
from research.dubai_iterative.shared_replay import SharedReplayProfile
from tools.run_causal_controls import STICKERS
from tools.run_week_causal_controls import policies


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            value.update(chunk)
    return value.hexdigest()


def native_start_snapshot(reconciled_path, deals_path, at, *, offset_seconds):
    """Check a necessary initial-flat control without feeding native fills to replay."""
    at = utc(at)
    if type(offset_seconds) is not int or not -86_400 <= offset_seconds <= 86_400:
        raise ValueError("invalid native start clock offset")
    reconciled_path, deals_path = Path(reconciled_path), Path(deals_path)
    source = json.loads(reconciled_path.read_text(encoding="utf-8-sig"))
    deals_sha = digest(deals_path)
    if source.get("source_sha256") != deals_sha:
        raise ValueError("native reconciled source mismatch")
    positions = source.get("positions")
    if not isinstance(positions, list):
        raise ValueError("native reconciled positions missing")
    start_source_msc = time_ns(at) // 1_000_000 + offset_seconds * 1000
    open_rows, seen = [], set()
    for row in positions:
        if not isinstance(row, dict):
            raise ValueError("native position shape invalid")
        position_id = row.get("position_id")
        first, last = row.get("first_native_msc"), row.get("last_native_msc")
        signal_id = row.get("signal_id")
        try:
            volume = Decimal(str(row.get("entry_volume")))
        except (TypeError, ValueError):
            raise ValueError("native position volume invalid") from None
        if (type(position_id) is not int or position_id in seen
                or type(first) is not int or type(last) is not int or first >= last
                or not isinstance(signal_id, str) or not signal_id
                or not volume.is_finite() or volume <= 0):
            raise ValueError("native position identity or interval invalid")
        seen.add(position_id)
        if first <= start_source_msc <= last:
            open_rows.append({"signal_id": signal_id, "position_id": position_id,
                              "volume": str(volume)})
    return {"at_utc": at.isoformat(), "source_clock_msc": start_source_msc,
            "reconciled_sha256": digest(reconciled_path), "deals_sha256": deals_sha,
            "reconciled_position_count": len(positions),
            "open_position_count": len(open_rows),
            "open_signal_ids": sorted({row["signal_id"] for row in open_rows}),
            "open_positions": open_rows,
            "does_not_prove_absence_of_pending_or_unclosed_positions": True}


def load_day(root, symbol, day, *, start_ns, cutoff_ns, offset_seconds,
             initial_padding_ns=0):
    stem = root / symbol / day
    meta_path, data_path = stem.with_suffix(".json"), stem.with_suffix(".parquet")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if (meta.get("status") != "raw_reads_consistent"
            or meta.get("symbol") != symbol
            or meta.get("source_epoch_day") != day
            or meta.get("rows", 0) < 1
            or meta.get("sha256") != digest(data_path)
            or meta.get("bytes") != data_path.stat().st_size):
        raise ValueError(f"frozen {symbol} tape source is not verified")
    frame = pd.read_parquet(data_path, columns=["time_msc", "bid", "ask"])
    if len(frame) != meta["rows"]:
        raise ValueError(f"frozen {symbol} tape row count changed")
    stamps = (frame.time_msc.to_numpy(dtype=np.int64) - offset_seconds * 1000) * 1_000_000
    bids = frame.bid.to_numpy(dtype=float)
    asks = frame.ask.to_numpy(dtype=float)
    if (np.any(np.diff(stamps) < 0) or not np.isfinite(bids).all()
            or not np.isfinite(asks).all() or np.any(bids <= 0)
            or np.any(asks < bids)):
        raise ValueError(f"invalid frozen {symbol} quote tape")
    selected = (stamps >= start_ns - initial_padding_ns) & (stamps < cutoff_ns)
    if not selected.any():
        raise ValueError(f"empty frozen {symbol} quote window")
    return ((stamps[selected], bids[selected], asks[selected]),
            {"metadata_sha256": digest(meta_path), "parquet_sha256": meta["sha256"],
             "day_rows": meta["rows"], "selected_rows": int(selected.sum()),
             "clock_admitted_by_source": meta.get("clock_admitted") is True})


def risk_summary(shared):
    peak = drawdown = 0
    known = unknown = 0
    for frame in shared.risk_grid:
        amounts = [point.equity_minor if point is not None else None
                   for _, _, point in frame.states]
        if frame.blockers or any(value is None for value in amounts):
            unknown += 1
            continue
        total = sum(amounts)
        known += 1
        peak = max(peak, total)
        drawdown = max(drawdown, peak - total)
    return {"known_frames": known, "unknown_frames": unknown,
            "max_drawdown_minor_model": (
                drawdown if known and not unknown and not shared.blockers else None),
            "not_observed_account_equity": True}


def basket_risk_summary(shared):
    """Reduce each settled model scope without mixing it with other baskets."""
    scopes = [(channel, signal_id) for channel, signal_id, _ in shared.baskets]
    if not scopes or len(set(scopes)) != len(scopes):
        raise ValueError("unique shared basket scopes required")
    known_ids = {signal_id for _, signal_id in scopes}
    states = {}
    for channel, signal_id, result in shared.baskets:
        states[signal_id] = {
            "channel": channel, "signal_id": signal_id,
            "known_frames": 0, "unknown_frames": 0,
            "peak": 0, "peak_at": None, "minimum": 0,
            "drawdown": 0, "drawdown_peak_at": None,
            "drawdown_trough_at": None, "final_total": None,
            "first_exposed_at": None, "last_exposed_at": None,
            "max_volume": Decimal(0), "exposed_frames": 0,
            "min_floating_exposed": None, "max_floating_exposed": None,
            "last_positions": None,
            "hash": hashlib.sha256(),
            "blockers": [issue for issue in shared.blockers
                         if issue.split(":", 1)[0] not in known_ids
                         or issue.startswith(f"{signal_id}:")]
            + (["basket_incomplete"] if result is None else list(result.blockers)),
        }
    expected = getattr(shared, "expected_quote_count", len(shared.risk_grid))
    if len(shared.risk_grid) != expected:
        for state in states.values():
            state["blockers"].append("incomplete_post_event_grid")
    previous_index, previous_time = -1, None
    for frame in shared.risk_grid:
        if (frame.tick_index <= previous_index
                or previous_time is not None and frame.time_ns < previous_time
                or [(channel, signal_id) for channel, signal_id, _ in frame.states]
                != scopes):
            raise ValueError("shared basket grid identity or chronology mismatch")
        if frame.tick_index != previous_index + 1:
            for state in states.values():
                state["blockers"].append("incomplete_post_event_grid")
        previous_index, previous_time = frame.tick_index, frame.time_ns
        for channel, signal_id, point in frame.states:
            state = states[signal_id]
            state["blockers"].extend(
                issue for issue in frame.blockers
                if not issue.startswith("unresolved_scope:")
                or issue == f"unresolved_scope:{signal_id}")
            if point is None or point.equity_minor is None:
                state["unknown_frames"] += 1
                state["hash"].update(json.dumps(
                    [frame.time_ns, None], separators=(",", ":")).encode() + b"\n")
                continue
            if (point.channel, point.signal_id) != (channel, signal_id):
                raise ValueError("shared basket point identity mismatch")
            total = point.equity_minor
            volume = sum((Decimal(str(row[1])) for row in point.positions), Decimal(0))
            if volume < 0:
                raise ValueError("negative basket exposure")
            state["known_frames"] += 1
            state["final_total"] = total
            state["last_positions"] = point.positions
            if total > state["peak"]:
                state["peak"], state["peak_at"] = total, frame.time_ns
            state["minimum"] = min(state["minimum"], total)
            if state["peak"] - total > state["drawdown"]:
                state["drawdown"] = state["peak"] - total
                state["drawdown_peak_at"] = state["peak_at"]
                state["drawdown_trough_at"] = frame.time_ns
            if volume:
                state["first_exposed_at"] = (state["first_exposed_at"]
                                             if state["first_exposed_at"] is not None
                                             else frame.time_ns)
                state["last_exposed_at"] = frame.time_ns
                state["exposed_frames"] += 1
                state["max_volume"] = max(state["max_volume"], volume)
                floating = point.floating_minor
                state["min_floating_exposed"] = (floating if state["min_floating_exposed"]
                                                 is None else min(state["min_floating_exposed"], floating))
                state["max_floating_exposed"] = (floating if state["max_floating_exposed"]
                                                 is None else max(state["max_floating_exposed"], floating))
            state["hash"].update(json.dumps(
                [frame.time_ns, point.realized_minor, point.floating_minor,
                 point.positions], separators=(",", ":")).encode() + b"\n")
    result = {}
    for signal_id, state in states.items():
        blockers = list(dict.fromkeys(state["blockers"]))
        if not state["known_frames"] or state["unknown_frames"]:
            blockers.append("incomplete_basket_risk_path")
        if state["last_positions"]:
            blockers.append("basket_still_open_at_cutoff")
        complete = not blockers
        result[signal_id] = {
            "channel": state["channel"], "known_frames": state["known_frames"],
            "unknown_frames": state["unknown_frames"],
            "max_drawdown_minor_model": state["drawdown"] if complete else None,
            "drawdown_peak_time_ns": state["drawdown_peak_at"] if complete else None,
            "drawdown_trough_time_ns": state["drawdown_trough_at"] if complete else None,
            "minimum_from_origin_minor_model": state["minimum"] if complete else None,
            "maximum_from_origin_minor_model": state["peak"] if complete else None,
            "final_total_minor_model": state["final_total"] if complete else None,
            "max_gross_volume_model": str(state["max_volume"]),
            "first_exposed_time_ns": state["first_exposed_at"],
            "last_exposed_time_ns": state["last_exposed_at"],
            "exposed_frames": state["exposed_frames"],
            "minimum_floating_minor_while_exposed": state["min_floating_exposed"],
            "maximum_floating_minor_while_exposed": state["max_floating_exposed"],
            "path_sha256": state["hash"].hexdigest(),
            "blockers": blockers, "not_observed_account_equity": True,
        }
    return result


def compact_risk_grid(shared):
    """Retain the modeled post-event state on every supplied market quote."""
    scopes = [[channel, signal_id] for channel, signal_id, _ in shared.baskets]
    results = {signal_id: result for _, signal_id, result in shared.baskets}
    if not scopes or len(scopes) != len({tuple(scope) for scope in scopes}):
        raise ValueError("unique risk grid scopes required")
    expected = shared.expected_quote_count
    if (type(expected) is not int or expected < len(shared.risk_grid)
            or expected * len(scopes) > 750_000):
        raise ValueError("risk grid export budget or count invalid")
    rows = []
    for frame in shared.risk_grid:
        if [[channel, signal_id] for channel, signal_id, _ in frame.states] != scopes:
            raise ValueError("risk grid scope order changed")
        states = []
        for channel, signal_id, point in frame.states:
            if point is None:
                states.append(None)
                continue
            same_quote = (point.tick_index == frame.tick_index
                          and point.time_ns == frame.time_ns)
            finished = results[signal_id]
            carried_flat = (point.tick_index < frame.tick_index
                            and point.time_ns <= frame.time_ns
                            and finished is not None and not finished.blockers
                            and not point.positions and point.floating_minor == 0
                            and point.realized_minor is not None
                            and finished.pnl_eur is not None
                            and Decimal(str(finished.pnl_eur))
                            * 10 ** getattr(shared, "currency_digits", 2)
                            == point.realized_minor)
            if (point.channel != channel or point.signal_id != signal_id
                    or point.phase != "settled" or not (same_quote or carried_flat)):
                raise ValueError("risk grid point is not post-event settled")
            volume = sum((Decimal(str(item[1])) for item in point.positions), Decimal(0))
            if volume < 0 or not volume.is_finite():
                raise ValueError("risk grid exposure invalid")
            states.append([point.realized_minor, point.floating_minor,
                           str(volume), len(point.positions)])
        rows.append([frame.tick_index, frame.time_ns, states, list(frame.blockers)])
    return {"contract": "post_event_quote_grid_compact_v1",
            "scopes": scopes, "expected_quote_count": expected, "rows": rows}


def implementation_hashes():
    root = Path(__file__).resolve().parents[1]
    names = (
        "research/causal_canal1_stream.py",
        "research/causal_lifecycle.py",
        "research/causal_management_semantics.py",
        "research/causal_replay.py",
        "research/causal_shared_stream.py",
        "research/causal_text_admission.py",
        "research/dubai_iterative/client_contract.py",
        "research/dubai_iterative/engine.py",
        "research/dubai_iterative/market.py",
        "research/dubai_iterative/shared_replay.py",
        "tools/run_week_causal_controls.py",
    )
    return {name: digest(root / name) for name in names}


def run(root, *, start, cutoff, offset_seconds, assume_initial_complete,
        optional_close_choice="block", native_reconciled=None, native_deals=None,
        include_risk_grid=False, execution_override=None, raw_name="raw_week_20260914_19_bg_v1.json",
        max_fx_age_ms=5000, scope_tick_budget=750_000, same_ms_message_after_quote=False,
        latency_model=None, ignore_unknown_stickers=False, canal1_latency_model=None,
        idle_unresolved_ignored=False):
    start, cutoff = utc(start), utc(cutoff)
    if (not start < cutoff or start.date() != cutoff.date()
            or type(offset_seconds) is not int or not -86_400 <= offset_seconds <= 86_400
            or type(assume_initial_complete) is not bool
            or type(include_risk_grid) is not bool
            or optional_close_choice not in {"block", "hold", "close"}):
        raise ValueError("invalid bounded diagnostic window")
    if (native_reconciled is None) != (native_deals is None):
        raise ValueError("native start control requires both sources")
    if assume_initial_complete and native_reconciled is None:
        raise ValueError("native start control required for assumed complete opening")
    native_start = (native_start_snapshot(native_reconciled, native_deals, start,
                                          offset_seconds=offset_seconds)
                    if native_reconciled is not None else None)
    if native_start is not None and native_start["open_position_count"]:
        raise ValueError("observed positions open at replay start")
    day = start.date().isoformat()
    raw_path = root / raw_name
    raw = json.loads(raw_path.read_text(encoding="utf-8-sig"))
    if (raw.get("contract") != "frozen_raw_telegram_slice_v1"
            or raw.get("causal_input_only") is not True
            or raw.get("complete_week_claim") is not False
            or raw.get("raw_row_count") != len(raw.get("rows", []))
            or not utc(raw["start_utc"]) <= start < cutoff <= utc(raw["end_utc"])):
        raise ValueError("frozen raw message slice contract failed")
    start_ns, cutoff_ns = time_ns(start), time_ns(cutoff)
    market, gold_proof = load_day(
        root, "XAUUSD", day, start_ns=start_ns, cutoff_ns=cutoff_ns,
        offset_seconds=offset_seconds)
    conversion, fx_proof = load_day(
        root, "EURUSD", day, start_ns=start_ns, cutoff_ns=cutoff_ns,
        offset_seconds=offset_seconds, initial_padding_ns=5_000_000_000)
    rows, same_ms_shifted, ignored_stickers = raw["rows"], [], []
    if ignore_unknown_stickers:
        # The live bot only acts on its two configured canal1 stickers and
        # ignores any other sticker; the causal compiler would instead block
        # the whole canal1 universe on it. Every dropped row is reported.
        kept = []
        for row in rows:
            if (row.get("sticker_id") is not None and str(row["sticker_id"]) not in STICKERS
                    and not (row.get("text") or "").strip()):
                ignored_stickers.append({"channel": row["channel"], "message_id": row["message_id"],
                                         "sticker_id": str(row["sticker_id"]), "ts": row["ts"]})
                continue
            kept.append(row)
        rows = kept
    if same_ms_message_after_quote:
        # A message logged in the same millisecond as a quote is ordered 1 us
        # after that quote (the causal stream refuses equal clocks). Input
        # rows are copied, never rewritten on disk; every shift is reported.
        quote_ms = set((market[0] // 1_000_000).tolist())
        shifted_rows = []
        for row in rows:
            stamp = utc(row["ts"])
            if time_ns(stamp) // 1_000_000 in quote_ms and time_ns(stamp) % 1_000_000 == 0:
                row = {**row, "ts": (stamp + timedelta(microseconds=1)).isoformat()}
                same_ms_shifted.append({"channel": row["channel"], "message_id": row["message_id"],
                                        "ts": row["ts"]})
            shifted_rows.append(row)
        rows = shifted_rows
    stream = compile_canal1_stream(
        rows, start=start, cutoff=cutoff,
        sticker_directions=STICKERS, max_entry_age_s=120,
        optional_close_choice=optional_close_choice)
    other, _ = compile_signals(
        rows, start=start, cutoff=cutoff,
        sticker_directions=STICKERS, max_entry_age_s=120)
    other = tuple(signal for signal in other if signal.channel == "canal2")
    scope_count = len(other) + len(stream.stickers) + len(stream.texts)
    if (type(max_fx_age_ms) is not int or not 0 < max_fx_age_ms <= 60_000
            or type(scope_tick_budget) is not int or scope_tick_budget < 1):
        raise ValueError("invalid FX age or budget override")
    if scope_count < 1 or len(market[0]) > 1_000_000 or scope_count * len(market[0]) > scope_tick_budget:
        raise ValueError("diagnostic risk/memory budget exceeded")

    delays = {"entry_fill_latency_ms": 0, "entry_ack_ms": 2000, "close_processing_ms": 1000,
              "close_ack_ms": 1000, "protection_processing_ms": 1000,
              "protection_ack_ms": 1000, "protection_retry_ms": 1000,
              # optional: age of the quote the bot holds when it sees a signal
              "quote_view_lag_ms": 0}
    profile_name = "explicit_uncalibrated_guard_client_v1"
    if execution_override is not None:
        unknown = set(execution_override["delays_ms"]) - set(delays)
        if unknown or execution_override.get("contract") != "execution_calibration_v1":
            raise ValueError("invalid execution calibration override")
        delays.update({k: int(v) for k, v in execution_override["delays_ms"].items()})
        profile_name = "calibrated:" + execution_override["label"]
    if latency_model is not None:
        profile_name += "+sampled_entry_fill:" + latency_model.label
    if canal1_latency_model is not None:
        profile_name += "+canal1:" + canal1_latency_model.label
    dubai_execution = ExecutionAssumptions(
        entry_fill_latency_ms=delays["entry_fill_latency_ms"],
        protection=ProtectionProfile(
            0.01, 2, 20, 0, delays["protection_processing_ms"], delays["protection_ack_ms"],
            delays["protection_retry_ms"],
            policy_extension="basket_guard_v1",
            request_quote_binding="timestamp_and_ordinal"),
        market=MarketProfile(delays["entry_ack_ms"], delays["close_processing_ms"],
                             delays["close_ack_ms"], 0.01, 1.0, 0.01),
        client=ClientProfile(name="single_basket_terminal_guard_v1"),
        latency=canal1_latency_model if canal1_latency_model is not None else latency_model,
        quote_view_lag_ms=delays["quote_view_lag_ms"])
    gold_execution = ExecutionAssumptions(
        entry_fill_latency_ms=delays["entry_fill_latency_ms"],
        protection=ProtectionProfile(0.01, 2, 20, 0, delays["protection_processing_ms"],
                                     delays["protection_ack_ms"], delays["protection_retry_ms"]),
        market=MarketProfile(delays["entry_ack_ms"], delays["close_processing_ms"],
                             delays["close_ack_ms"], 0.01, 1.0, 0.01),
        client=ClientProfile(), latency=latency_model,
        quote_view_lag_ms=delays["quote_view_lag_ms"])
    scenario = run_incremental_shared_canal1_stream(
        stream, genomes=policies(),
        executions={"canal1": dubai_execution, "canal2": gold_execution},
        profile=SharedReplayProfile(account_currency="EUR", max_events=1_000_000),
        timing=LifecycleTiming(finalization_delay_s=5),
        market=market, conversion=conversion, start=start, cutoff=cutoff,
        contract_size=100.0, currency_digits=2, max_fx_age_ms=max_fx_age_ms,
        market_sha256=gold_proof["parquet_sha256"],
        conversion_sha256=fx_proof["parquet_sha256"],
        other_signals=other, initial_universe_complete=assume_initial_complete,
        idle_unresolved_ignored=idle_unresolved_ignored)
    shared = scenario["shared"]
    report = {
        "contract": "canal1_incremental_window_diagnostic_v1",
        "status": scenario["status"],
        "full_live_parity_verified": False,
        "start_utc": start.isoformat(), "cutoff_utc": cutoff.isoformat(),
        "clock_offset_seconds_hypothesis": offset_seconds,
        "initial_universe_complete_assumed": assume_initial_complete,
        "native_start_control": native_start,
        "optional_close_choice": optional_close_choice,
        "execution_profile": profile_name,
        "execution_delays_ms": delays,
        "max_fx_age_ms": max_fx_age_ms, "scope_tick_budget": scope_tick_budget,
        "same_ms_message_after_quote": same_ms_message_after_quote,
        "same_ms_shifted_messages": same_ms_shifted,
        "ignore_unknown_stickers": ignore_unknown_stickers,
        "idle_unresolved_ignored": idle_unresolved_ignored,
        "ignored_unknown_sticker_rows": ignored_stickers,
        "execution_assumptions": {"canal1": asdict(dubai_execution),
                                  "canal2": asdict(gold_execution)},
        "policy_fingerprints": {name: genome.fingerprint
                                for name, genome in policies().items()},
        "sources": {"raw_sha256": digest(raw_path), "XAUUSD": gold_proof,
                    "EURUSD": fx_proof, "runner_sha256": digest(__file__),
                    "implementation_sha256": implementation_hashes()},
        "timeline": [{"kind": row.kind, "message_id": row.message_id,
                      "observed_at": row.observed_at.isoformat()}
                     for row in stream.timeline],
        "canal1_diagnostics": stream.diagnostics,
        "other_signal_ids": [signal.signal_id for signal in other],
        "decisions": scenario["decisions"],
        "admissions": shared.dynamic_basket_admissions,
        "delivered_provider_events": [
            {"signal_id": signal_id, "actions": [event.action for event in events]}
            for signal_id, events in shared.dynamic_provider_events],
        "basket_rows": [{"channel": channel, "signal_id": signal_id,
                         "entries": [] if result is None else [asdict(row) for row in result.entries],
                         "exits": [] if result is None else [asdict(row) for row in result.exits],
                         "market_events": [] if result is None else [asdict(row) for row in result.market_events],
                         "client_events": [] if result is None else [asdict(row) for row in result.client_events],
                         "protection_events": [] if result is None else [asdict(row) for row in result.protection_events],
                         "blockers": ["basket_incomplete"] if result is None else list(result.blockers)}
                        for channel, signal_id, result in shared.baskets],
        "shared_blockers": shared.blockers,
        "risk_point_count": shared.risk_point_count,
        "risk_grid_count": shared.risk_grid_count,
        "risk": risk_summary(shared),
        "basket_risk": basket_risk_summary(shared),
        "transport": shared.transport,
        "limitations": [*shared.limitations,
                        "Clock offset and initial flatness are hypotheses, not admitted historical facts.",
                        "Reconciled closed deals do not prove absence of pending or unclosed positions.",
                        ("Execution profile is uncalibrated; no observed broker path comparison is made."
                         if execution_override is None else
                         ("Execution delays are fixed measured quantiles per run, not a per-request distribution."
                          if latency_model is None else
                          "Entry fill delay drawn per request from the measured sample (seeded, independent draws); other delays fixed."))],
    }
    if include_risk_grid:
        report["risk_grid_samples"] = compact_risk_grid(shared)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--clock-offset-seconds", type=int, required=True)
    parser.add_argument("--assume-initial-complete", action="store_true")
    parser.add_argument("--include-risk-grid", action="store_true")
    parser.add_argument("--optional-close-choice", choices=("block", "hold", "close"),
                        default="block")
    parser.add_argument("--native-reconciled", type=Path)
    parser.add_argument("--native-deals", type=Path)
    parser.add_argument("--execution-calibration", type=Path,
                        help="optional execution_calibration_v1 JSON; default keeps the uncalibrated profile")
    parser.add_argument("--raw-name", default="raw_week_20260914_19_bg_v1.json",
                        help="frozen_raw_telegram_slice_v1 file inside --root")
    parser.add_argument("--max-fx-age-ms", type=int, default=5000,
                        help="EURUSD staleness guard for EUR conversion (default 5000)")
    parser.add_argument("--scope-tick-budget", type=int, default=750_000)
    parser.add_argument("--same-ms-message-after-quote", action="store_true")
    parser.add_argument("--latency-samples", type=Path,
                        help="latency_samples_v1 report: draw the entry fill delay per request")
    parser.add_argument("--latency-seed", type=int, default=0)
    parser.add_argument("--ignore-unknown-stickers", action="store_true")
    parser.add_argument("--idle-unresolved-ignored", action="store_true",
                        help="ignore unresolved canal1 texts while no canal1 signal is open")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    override = (json.loads(args.execution_calibration.read_text(encoding="utf-8"))
                if args.execution_calibration else None)
    report = run(args.root, start=args.start, cutoff=args.cutoff,
                 offset_seconds=args.clock_offset_seconds,
                 assume_initial_complete=args.assume_initial_complete,
                 optional_close_choice=args.optional_close_choice,
                 native_reconciled=args.native_reconciled,
                 native_deals=args.native_deals,
                 include_risk_grid=args.include_risk_grid,
                 execution_override=override, raw_name=args.raw_name,
                 max_fx_age_ms=args.max_fx_age_ms,
                 scope_tick_budget=args.scope_tick_budget,
                 same_ms_message_after_quote=args.same_ms_message_after_quote,
                 latency_model=(None if args.latency_samples is None else LatencyModel.from_samples(
                     json.loads(args.latency_samples.read_text(encoding="utf-8")), args.latency_seed)),
                 ignore_unknown_stickers=args.ignore_unknown_stickers,
                 idle_unresolved_ignored=args.idle_unresolved_ignored)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(report, target, sort_keys=True, default=str, allow_nan=False,
                  **({"separators": (",", ":")} if args.include_risk_grid else {"indent": 2}))
        target.write("\n")
    print(json.dumps({"status": report["status"], "decisions": report["decisions"],
                      "shared_blockers": report["shared_blockers"],
                      "risk": report["risk"]}, default=str))


if __name__ == "__main__":
    main()
