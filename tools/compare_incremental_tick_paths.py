"""Compare native-deal and shared-model basket paths on identical XAU ticks."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

from research.causal_replay import time_ns, utc
from research.risk_trajectory import RiskSpec, reconstruct_risk
from tools.audit_native_week_risk_path import native_events, quote_grid, summarize_path
from tools.compare_canal1_incremental_risk import compare_rows, verify_sources
from tools.probe_canal1_incremental_window import digest, load_day


def _minor(value):
    if value is None:
        raise ValueError("money sample unavailable")
    amount = Decimal(str(value)) * 100
    if not amount.is_finite() or amount != amount.to_integral_value():
        raise ValueError("money sample is not exact euro cents")
    return int(amount)


def compare_aligned_samples(grid_rows, native_samples, *, scope_index):
    """Compare post-event model states with reconstructed native quote marks."""
    quotes = [row for row in native_samples if row["quote_at"] == row["at"]]
    if not grid_rows or len(quotes) != len(grid_rows):
        raise ValueError("quote grid mismatch")
    counts = Counter()
    first = None
    model_peak = native_peak = model_dd = native_dd = 0
    max_total_delta = max_exposed_total_delta = 0
    max_exposure_delta = Decimal(0)
    for expected_index, (frame, native) in enumerate(zip(grid_rows, quotes, strict=True)):
        if (len(frame) != 4 or frame[0] != expected_index
                or frame[1] != time_ns(native["at"])):
            raise ValueError("quote grid mismatch")
        if frame[3] or len(frame[2]) <= scope_index or frame[2][scope_index] is None:
            raise ValueError("model risk state unavailable")
        state = frame[2][scope_index]
        if (len(state) != 4 or type(state[0]) is not int
                or type(state[1]) is not int or type(state[3]) is not int):
            raise ValueError("model risk money or exposure unavailable")
        model_realized, model_floating, volume_text, model_count = state
        model_volume = Decimal(volume_text)
        native_volume = native["long_volume"] + native["short_volume"]
        native_realized = _minor(native["realized"])
        native_floating = _minor(native["floating"])
        native_total = _minor(native["total"])
        model_total = model_realized + model_floating
        if (model_volume < 0 or not model_volume.is_finite()
                or native_total != native_realized + native_floating):
            raise ValueError("risk sample accounting inconsistent")
        fields = [name for name, different in (
            ("total", model_total != native_total),
            ("floating", model_floating != native_floating),
            ("realized", model_realized != native_realized),
            ("exposure", model_volume != native_volume),
            ("open_count", model_count != native["open_count"]),
        ) if different]
        for name in fields:
            counts[name] += 1
        if fields and first is None:
            first = {"tick_index": expected_index, "time_ns": frame[1],
                     "utc": native["at"].isoformat(), "fields": fields,
                     "model_total_minor": model_total,
                     "native_total_minor": native_total,
                     "model_gross_volume": str(model_volume),
                     "native_gross_volume": str(native_volume)}
        model_peak = max(model_peak, model_total)
        native_peak = max(native_peak, native_total)
        model_dd = max(model_dd, model_peak - model_total)
        native_dd = max(native_dd, native_peak - native_total)
        max_total_delta = max(max_total_delta, abs(model_total - native_total))
        if model_volume or native_volume:
            counts["union_exposed"] += 1
            if model_total != native_total:
                counts["total_exposed"] += 1
            if model_floating != native_floating:
                counts["floating_exposed"] += 1
            max_exposed_total_delta = max(
                max_exposed_total_delta, abs(model_total - native_total))
        max_exposure_delta = max(max_exposure_delta, abs(model_volume - native_volume))
    return {"sample_count": len(grid_rows),
            "first_divergence": first,
            "different_total_ticks": counts["total"],
            "different_floating_ticks": counts["floating"],
            "different_realized_ticks": counts["realized"],
            "different_exposure_ticks": counts["exposure"],
            "different_open_count_ticks": counts["open_count"],
            "union_exposed_ticks": counts["union_exposed"],
            "different_total_exposed_ticks": counts["total_exposed"],
            "different_floating_exposed_ticks": counts["floating_exposed"],
            "max_abs_total_delta_minor": max_total_delta,
            "max_abs_total_delta_exposed_minor": max_exposed_total_delta,
            "max_abs_exposure_delta": str(max_exposure_delta),
            "model_max_drawdown_minor": model_dd,
            "native_max_drawdown_minor": native_dd,
            "same_grid_fields_equal": first is None}


def aggregate_model_grid(grid_rows):
    """Sum settled basket states without treating an unknown as zero."""
    aggregate = []
    for frame in grid_rows:
        if len(frame) != 4 or frame[3] or not frame[2]:
            raise ValueError("model account state unavailable")
        realized = floating = count = 0
        volume = Decimal(0)
        for state in frame[2]:
            if (state is None or len(state) != 4
                    or type(state[0]) is not int or type(state[1]) is not int
                    or type(state[3]) is not int):
                raise ValueError("model account state unavailable")
            realized += state[0]
            floating += state[1]
            volume += Decimal(state[2])
            count += state[3]
        if volume < 0 or not volume.is_finite() or count < 0:
            raise ValueError("model account exposure invalid")
        aggregate.append([frame[0], frame[1],
                          [[realized, floating, str(volume), count]], []])
    return aggregate


def admit_native_window_positions(positions, *, start_source_msc,
                                  cutoff_source_msc, admitted_ids):
    """Require every known native position overlapping the window to be whole."""
    if (type(start_source_msc) is not int or type(cutoff_source_msc) is not int
            or start_source_msc >= cutoff_source_msc):
        raise ValueError("invalid native account window")
    selected = set()
    for position in positions:
        first, last = position["first_native_msc"], position["last_native_msc"]
        if type(first) is not int or type(last) is not int or first >= last:
            raise ValueError("invalid native position interval")
        if first >= cutoff_source_msc or last <= start_source_msc:
            continue
        if first <= start_source_msc or last >= cutoff_source_msc:
            raise ValueError("native position crosses window boundary")
        if position["signal_id"] not in admitted_ids:
            raise ValueError("native position outside admitted universe")
        position_id = position["position_id"]
        if position_id in selected:
            raise ValueError("duplicate native position in account window")
        selected.add(position_id)
    if not selected:
        raise ValueError("native account window has no observed positions")
    return selected


def _bound_native_input(native, path):
    matches = [sha for name, sha in native["inputs_sha256"].items()
               if Path(name).name == Path(path).name]
    if matches != [digest(path)]:
        raise ValueError(f"native source changed: {Path(path).name}")


def require_recomputed_scalar(scalar, rows, counts):
    if scalar.get("rows") != rows or scalar.get("counts") != counts:
        raise ValueError("scalar comparison differs from recomputed controls")


def first_entry_evidence(model_entries, native_positions, *, offset_seconds,
                         scalar_entry_deltas):
    """Bind the first model request to the first observed fill by ordinal."""
    if not model_entries or not native_positions or not scalar_entry_deltas:
        raise ValueError("first entry comparison unavailable")
    model_entry = min(model_entries, key=lambda row: utc(row["opened_at"]))
    native_entry = min(native_positions, key=lambda row: row["entry_msc"])
    model_at = utc(model_entry["opened_at"])
    if time_ns(model_at) != model_entry["requested_ns"]:
        raise ValueError("model first entry clock inconsistent")
    native_at = datetime.fromtimestamp(
        native_entry["entry_msc"] / 1000 - offset_seconds, timezone.utc)
    delta_ms = int((model_at - native_at).total_seconds() * 1000)
    if delta_ms != scalar_entry_deltas[0]:
        raise ValueError("first entry differs from scalar timing control")
    model_price = Decimal(str(model_entry["entry_price"]))
    native_price = Decimal(str(native_entry["entry_price"]))
    model_volume = Decimal(str(model_entry["volume"]))
    native_volume = Decimal(str(native_entry["volume"]))
    if any(not value.is_finite() or value <= 0 for value in (
            model_price, native_price, model_volume, native_volume)):
        raise ValueError("first entry price or volume invalid")
    return {"ordinal_pairing_not_ticket_identity": True,
            "model_requested_utc": model_at.isoformat(),
            "native_fill_utc": native_at.isoformat(),
            "model_minus_native_ms": delta_ms,
            "model_entry_price": str(model_price),
            "native_entry_price": str(native_price),
            "model_minus_native_price": str(model_price - native_price),
            "same_entry_volume": model_volume == native_volume,
            "model_entry_volume": str(model_volume),
            "native_entry_volume": str(native_volume)}


def _datetime_ns(value):
    seconds, remaining = divmod(value, 1_000_000_000)
    return (datetime.fromtimestamp(seconds, timezone.utc)
            + timedelta(microseconds=remaining // 1_000))


def compare_window(model, native, scalar, reconciled, money, deals, broker,
                   anchor, root, *, model_path, native_path, scalar_path,
                   reconciled_path, money_path, deals_path, broker_path,
                   anchor_path):
    root = Path(root)
    sources = verify_sources(model, native, model_path=model_path,
                             native_path=native_path, frozen_root=root)
    if (scalar["sources"]["model_sha256"] != digest(model_path)
            or scalar["sources"]["native_sha256"] != digest(native_path)
            or scalar["start_utc"] != model["start_utc"]
            or scalar["cutoff_utc"] != model["cutoff_utc"]):
        raise ValueError("scalar comparison is not bound to this window")
    for path in (reconciled_path, money_path, deals_path, broker_path, anchor_path):
        _bound_native_input(native, path)
    if (money.get("account_currency") != "EUR"
            or model["native_start_control"]["reconciled_sha256"] != digest(reconciled_path)
            or model["native_start_control"]["deals_sha256"] != digest(deals_path)
            or reconciled["source_sha256"] != digest(deals_path)
            or model["clock_offset_seconds_hypothesis"] != 10_800):
        raise ValueError("native money, ledger or clock hypothesis changed")
    scalar_rows, scalar_counts = compare_rows(model, native, reconciled["positions"])
    require_recomputed_scalar(scalar, scalar_rows, scalar_counts)
    day = utc(model["start_utc"]).date().isoformat()
    clock = anchor["independent_clock_evidence"]["days"].get(day, {})
    if (clock.get("status") != "direct_anchor_available"
            or clock.get("offset_seconds") != 10_800):
        raise ValueError("direct matching clock anchor required")
    grid = model.get("risk_grid_samples", {})
    scopes = [[row["channel"], row["signal_id"]] for row in model["basket_rows"]]
    if (grid.get("contract") != "post_event_quote_grid_compact_v1"
            or grid.get("scopes") != scopes
            or len(scopes) != len({tuple(scope) for scope in scopes})):
        raise ValueError("modeled risk grid identity unavailable")
    market, _ = load_day(root, "XAUUSD", day,
                         start_ns=time_ns(utc(model["start_utc"])),
                         cutoff_ns=time_ns(utc(model["cutoff_utc"])),
                         offset_seconds=10_800)
    conversion, _ = load_day(root, "EURUSD", day,
                             start_ns=time_ns(utc(model["start_utc"])),
                             cutoff_ns=time_ns(utc(model["cutoff_utc"])),
                             offset_seconds=10_800, initial_padding_ns=5_000_000_000)
    grid_rows = grid["rows"]
    if (len(grid_rows) != grid["expected_quote_count"]
            or len(grid_rows) != len(market[0]) or len(grid_rows) > 100_000
            or any(row[0] != index or row[1] != int(market[0][index])
                   for index, row in enumerate(grid_rows))):
        raise ValueError("modeled risk grid is incomplete or shifted")
    source_market = (market[0] // 1_000_000 + 10_800_000, *market[1:])
    source_fx = (conversion[0] // 1_000_000 + 10_800_000, *conversion[1:])
    spec = RiskSpec("EUR", broker["account"]["currency_digits"],
                    Decimal(str(broker["instrument"]["contract_size"])),
                    broker["conversion"]["orientation"],
                    broker["conversion"]["max_quote_age_ms"], 5_000)
    if spec.orientation != "account_base_profit_quote":
        raise ValueError("unexpected native conversion contract")
    all_events = native_events(deals["deals"], money["positions"])
    money_by_signal = defaultdict(list)
    for position in money["positions"]:
        money_by_signal[position["signal_id"]].append(position)
    native_by_id = {row["signal_id"]: row for row in native["baskets"]}
    model_by_id = {row["signal_id"]: row for row in model["basket_rows"]}
    if len(native_by_id) != len(native["baskets"]):
        raise ValueError("duplicate native basket identity")
    if len(model_by_id) != len(model["basket_rows"]):
        raise ValueError("duplicate model basket identity")
    watched = {Path(path): digest(path) for path in (
        model_path, native_path, scalar_path, reconciled_path, money_path,
        deals_path, broker_path, anchor_path,
        root / "XAUUSD" / f"{day}.parquet",
        root / "EURUSD" / f"{day}.parquet")}
    rows = []
    for scalar_row in scalar_rows:
        signal_id = scalar_row["signal_id"]
        row = {"channel": scalar_row["channel"], "signal_id": signal_id,
               "scalar_status": scalar_row["status"]}
        if not scalar_row["status"].startswith("matched_"):
            row["status"] = "blocked_scalar_gate" if scalar_row["status"] == "blocked_comparison" else scalar_row["status"]
            row["blockers"] = scalar_row.get("model_blockers", [])
            rows.append(row)
            continue
        try:
            native_row = native_by_id[signal_id]
            positions = money_by_signal[signal_id]
            if (len(positions) != native_row["position_count"]
                    or len({item["position_id"] for item in positions}) != len(positions)):
                raise ValueError("native position identity incomplete")
            reconciled_by_id = {item["position_id"]: item
                                for item in reconciled["positions"]}
            if any(reconciled_by_id[position["position_id"]]["first_native_msc"]
                   != position["entry_msc"] for position in positions):
                raise ValueError("money and ledger first fills disagree")
            first_entry = first_entry_evidence(
                model_by_id[signal_id]["entries"], positions,
                offset_seconds=10_800,
                scalar_entry_deltas=scalar_row["entry_delta_ms_by_ordinal"])
            events = [event for position in positions
                      for event in all_events[position["position_id"]]]
            reconstructed_native = reconstruct_risk(
                events, quote_grid(source_market, source_fx, events), spec=spec,
                retrospective_fx_interval_ms=broker["conversion"]["max_quote_interval_ms"])
            original = summarize_path(reconstructed_native)
            archived = native_row["path"]
            if (original["sample_count"] != archived["sample_count"]
                    or original["sample_stream_sha256"] != archived["sample_stream_sha256"]
                    or original["metrics"] != archived["metrics"]):
                raise ValueError("native risk path does not reproduce archived samples")
            first_at = _datetime_ns(grid_rows[0][1])
            last_at = _datetime_ns(grid_rows[-1][1])
            bounds = [SimpleNamespace(at=first_at), SimpleNamespace(at=last_at)]
            common_native = reconstruct_risk(
                events, quote_grid(source_market, source_fx, [*events, *bounds]),
                spec=spec)
            scope_index = scopes.index([row["channel"], signal_id])
            comparison = compare_aligned_samples(
                grid_rows, common_native["samples"], scope_index=scope_index)
            if (common_native["blockers"]
                    or comparison["model_max_drawdown_minor"]
                    != model["basket_risk"][signal_id]["max_drawdown_minor_model"]):
                raise ValueError("common-grid money coverage or model reduction differs")
            row.update(status=("same_grid_fields_equal" if comparison["same_grid_fields_equal"]
                               else "path_discrepant"),
                       first_entry_evidence=first_entry,
                       native_sample_sha256=original["sample_stream_sha256"],
                       native_sample_hash_verified=True, comparison=comparison)
        except (KeyError, TypeError, ValueError) as exc:
            row.update(status="blocked_tick_comparison", blockers=[str(exc)])
        rows.append(row)
    account = {"observed_account_equity_compared": False}
    try:
        admitted_ids = {row["signal_id"] for row in rows if row["status"] in {
            "path_discrepant", "same_grid_fields_equal"}}
        start_source = time_ns(utc(model["start_utc"])) // 1_000_000 + 10_800_000
        cutoff_source = time_ns(utc(model["cutoff_utc"])) // 1_000_000 + 10_800_000
        native_ids = admit_native_window_positions(
            reconciled["positions"], start_source_msc=start_source,
            cutoff_source_msc=cutoff_source, admitted_ids=admitted_ids)
        selected_money = [position for position in money["positions"]
                          if position["signal_id"] in admitted_ids]
        if {position["position_id"] for position in selected_money} != native_ids:
            raise ValueError("native account basket extends beyond complete window")
        events = [event for position in selected_money
                  for event in all_events[position["position_id"]]]
        bounds = [SimpleNamespace(at=_datetime_ns(grid_rows[index][1]))
                  for index in (0, -1)]
        native_portfolio = reconstruct_risk(
            events, quote_grid(source_market, source_fx, [*events, *bounds]),
            spec=spec)
        model_portfolio = aggregate_model_grid(grid_rows)
        comparison = compare_aligned_samples(
            model_portfolio, native_portfolio["samples"], scope_index=0)
        expected_net = sum((Decimal(str(position["actual_net_eur"]))
                            for position in selected_money), Decimal(0))
        if (native_portfolio["blockers"] or native_portfolio["metrics"] is None
                or native_portfolio["metrics"]["final_net"] != expected_net
                or comparison["model_max_drawdown_minor"]
                != model["risk"]["max_drawdown_minor_model"]):
            raise ValueError("native-deal portfolio coverage or model reduction differs")
        account.update(
            status=("same_grid_fields_equal" if comparison["same_grid_fields_equal"]
                    else "native_deal_path_discrepant"),
            observed_signal_ids=sorted(admitted_ids),
            native_position_count=len(native_ids),
            native_booked_net_eur=str(expected_net), comparison=comparison)
    except (KeyError, TypeError, ValueError) as exc:
        account.update(status="blocked_native_deal_portfolio", blockers=[str(exc)])
    if any(digest(path) != sha for path, sha in watched.items()):
        raise ValueError("tick-path sources changed during comparison")
    return {"contract": "incremental_common_tick_path_comparison_v1",
            "status": "diagnostic_only", "full_live_parity_verified": False,
            "account_path_parity_verified": False,
            "start_utc": model["start_utc"], "cutoff_utc": model["cutoff_utc"],
            "counts": dict(Counter(row["status"] for row in rows)),
            "rows": rows,
            "native_deal_portfolio": account,
            "sources": {**sources, "scalar_sha256": digest(scalar_path),
                        "reconciled_sha256": digest(reconciled_path),
                        "money_sha256": digest(money_path),
                        "deals_sha256": digest(deals_path),
                        "broker_sha256": digest(broker_path),
                        "anchor_sha256": digest(anchor_path),
                        "comparator_sha256": digest(__file__)},
            "limitations": [
                "Native path is reconstructed from actual fills, not an independent account-equity trace.",
                "The model uses an uncalibrated execution profile and assumed initial completeness.",
                "Exact quote-grid parity does not prove between-tick or broker-server behavior.",
                "Native-deal portfolio is reconstructed, not independently observed MT5 account equity.",
                "Unclosed or unattributed positions, balance, credit and margin remain unverified.",
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("model", "native", "scalar", "reconciled", "money", "deals", "broker",
                 "anchor", "frozen_root", "output"):
        parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
    args = parser.parse_args()
    inputs = {name: json.loads(getattr(args, name).read_text(encoding="utf-8"))
              for name in ("model", "native", "scalar", "reconciled", "money", "deals", "broker", "anchor")}
    report = compare_window(**inputs, root=args.frozen_root,
                            model_path=args.model, native_path=args.native,
                            scalar_path=args.scalar, money_path=args.money,
                            reconciled_path=args.reconciled,
                            deals_path=args.deals, broker_path=args.broker,
                            anchor_path=args.anchor)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2, sort_keys=True, allow_nan=False)
        target.write("\n")
    print(json.dumps({"status": report["status"], "counts": report["counts"]}))


if __name__ == "__main__":
    main()
