from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from research.causal_replay import compile_signals, make_path, make_shared_paths, raw_message
from research.dubai_iterative.client import ClientProfile
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.engine import ExecutionAssumptions, simulate
from research.dubai_iterative.shared_replay import (
    BasketReplaySpec, SharedReplayProfile, simulate_shared,
)
from tests.test_iterative_market import market as market_profile
from tests.test_iterative_protection import profile as protection_profile


BASE = datetime(2026, 9, 8, 10, tzinfo=timezone.utc)
BASE_NS = 1_788_861_600_000_000_000


def message(index=1, text="BUY GOLD NOW", **changes):
    return {
        "ev": "telegram_raw", "channel": "canal2", "message_id": index,
        "message_revision_id": f"revision-{index}", "date_utc": BASE.isoformat(),
        "ts": BASE.isoformat(), "text": text, "is_edit": False,
        "reply_to_msg_id": None, "sticker_id": None,
    } | changes


def compile_rows(rows):
    return compile_signals(rows, start=BASE, cutoff=BASE + timedelta(hours=1),
                           sticker_directions={"known-buy": "BUY"})


def test_only_raw_message_fields_cross_the_simulation_boundary():
    value = raw_message(message(entry_price=9999, positions=[123], result={"deal": 999}))
    assert "entry_price" not in value and "positions" not in value and "result" not in value
    with pytest.raises(ValueError, match="telegram_raw"):
        raw_message(message(ev="market_filled"))


@pytest.mark.parametrize("text,direction", [
    ("Sell zone Now", "SELL"), ("Sell zone again now", "SELL"),
    ("Buy XAU USD now", "BUY"), ("GOLD: buy again now", "BUY"),
])
def test_raw_control_uses_existing_immediate_entry_grammar(text, direction):
    signals, _ = compile_rows([message(text=text)])
    assert len(signals) == 1
    assert signals[0].direction == direction


@pytest.mark.parametrize("text", [
    "If price breaks 2500 buy gold now", "Do not sell gold now",
    "Possible buy gold now", "Buy zone at 2500", "Sell gold later",
])
def test_raw_control_does_not_turn_conditional_or_zone_plans_into_entries(text):
    assert compile_rows([message(text=text)])[0] == ()


def test_distinct_immediate_messages_are_not_deduplicated_by_price_or_time():
    second = message(2, ts=(BASE + timedelta(seconds=3, milliseconds=338)).isoformat())
    signals, _ = compile_rows([message(), second, second])
    assert [row.signal_id for row in signals] == ["canal2_1", "canal2_2"]


def test_future_edit_does_not_rewrite_original_direction_or_anchor():
    later = message(text="SELL GOLD NOW", is_edit=True, message_revision_id="edited",
                    ts=(BASE + timedelta(minutes=5)).isoformat())
    original, _ = compile_rows([message()])
    extended, diagnostics = compile_rows([message(), later])
    assert asdict(original[0]) == asdict(extended[0])
    assert any(row["reason"] == "direction_edit_after_trigger" for row in diagnostics)


def test_arrival_order_not_publication_controls_entry_and_management():
    close = message(2, "CLOSE ALL", reply_to_msg_id=1,
                    ts=(BASE + timedelta(seconds=2)).isoformat())
    signals, _ = compile_rows([close, message()])
    assert signals[0].observed_at == BASE
    assert len(signals[0].provider_events) == 1
    assert signals[0].provider_events[0].observed_at == BASE + timedelta(seconds=2)


def test_reply_to_unknown_root_does_not_close_latest_signal():
    close = message(2, "CLOSE ALL", reply_to_msg_id=999,
                    ts=(BASE + timedelta(seconds=2)).isoformat())
    signals, diagnostics = compile_rows([message(), close])
    assert signals[0].provider_events == ()
    assert any(row["reason"] == "unresolved_management_root" for row in diagnostics)


def test_unknown_sticker_is_retained_as_unsupported_without_guessing_direction():
    signals, diagnostics = compile_rows([message(channel="canal1", text="", sticker_id="new")])
    assert signals == ()
    assert diagnostics[0]["reason"] == "unknown_sticker_direction"


@pytest.mark.parametrize("reply", [None, 2])
def test_unknown_sticker_is_a_boundary_not_an_alias_of_previous_signal(reply):
    first = message(channel="canal1", text="", sticker_id="known-buy")
    unknown = message(2, "", channel="canal1", sticker_id="unknown",
                      ts=(BASE + timedelta(seconds=1)).isoformat())
    close = message(3, "CLOSE ALL", channel="canal1", reply_to_msg_id=reply,
                    ts=(BASE + timedelta(seconds=2)).isoformat())
    signals, diagnostics = compile_rows([first, unknown, close])
    assert signals[0].provider_events == ()
    assert any(row["reason"] == "unresolved_management_root" for row in diagnostics)


def test_conditional_command_is_retained_but_never_executed_unconditionally():
    conditional = message(2, "IF PRICE REACHES 105 CLOSE ALL", reply_to_msg_id=1,
                          ts=(BASE + timedelta(seconds=2)).isoformat())
    signals, diagnostics = compile_rows([message(), conditional])
    assert signals[0].provider_events == ()
    assert any(row["reason"] == "conditional_management_unsupported" for row in diagnostics)


def test_returning_to_previous_management_value_is_a_new_causal_revision():
    changes = [message(2, f"MOVE SL TO {price}", reply_to_msg_id=1,
               message_revision_id=f"revision-{index}",
               ts=(BASE + timedelta(seconds=index)).isoformat())
               for index, price in enumerate([100, 105, 100], start=2)]
    signals, _ = compile_rows([message(), *changes])
    assert len(signals[0].provider_events) == 3


def test_publication_after_receipt_is_not_an_executable_signal():
    signals, diagnostics = compile_rows([message(date_utc=(BASE + timedelta(hours=2)).isoformat())])
    assert signals == ()
    assert diagnostics[0]["reason"] == "publication_clock_after_receipt"


def test_same_revision_with_different_content_is_rejected():
    with pytest.raises(ValueError, match="revision.*conflict"):
        compile_rows([message(), message(text="SELL GOLD NOW")])


def test_duplicate_delivery_does_not_duplicate_signals_or_management():
    close = message(2, "CLOSE ALL", reply_to_msg_id=1,
                    ts=(BASE + timedelta(seconds=2)).isoformat())
    signals, _ = compile_rows([message(), message(), close, close])
    assert len(signals) == 1 and len(signals[0].provider_events) == 1


def test_polling_redelivery_of_an_edit_keeps_first_causal_availability():
    first = message(is_edit=True, edit_date_utc=BASE.isoformat())
    polled = first | {"is_edit": False, "ts": (BASE + timedelta(seconds=1)).isoformat()}
    signals, _ = compile_rows([first, polled])
    assert len(signals) == 1 and signals[0].observed_at == BASE


def test_unavailable_future_rows_are_not_read_to_resolve_a_root():
    late = message(2, ts=(BASE + timedelta(hours=2)).isoformat())
    signals, _ = compile_rows([late, message()])
    assert [row.signal_id for row in signals] == ["canal2_1"]


def test_naive_clock_is_rejected():
    with pytest.raises(ValueError, match="timezone"):
        compile_rows([message(ts="2026-09-08T10:00:00")])


def path_inputs():
    times = np.array([BASE_NS, BASE_NS + 1_000_000_000, BASE_NS + 2_000_000_000])
    return dict(market=(times, np.array([100., 101., 99.]), np.array([100.2, 101.2, 99.2])),
                conversion=(times, np.ones(3), np.ones(3)), cutoff=BASE + timedelta(seconds=2),
                contract_size=100., currency_digits=2, max_fx_age_ms=5000,
                market_sha256="a" * 64, conversion_sha256="b" * 64)


def genome():
    return StrategyGenome(schema_version=2, entry_mode="signal_market", leg_count=1,
                          volume_weights=(0.01,), target_mode="none", stop_mode="none",
                          be_mode="none", time_exit_min=180, provider_management_mode="exact")


def test_continuous_replay_owns_its_entries_and_cannot_consume_actual_fills():
    close = message(2, "CLOSE ALL", reply_to_msg_id=1,
                    ts=(BASE + timedelta(seconds=2)).isoformat())
    signal = compile_rows([message(), close])[0][0]
    path = make_path(signal, genome(), **path_inputs())
    result = simulate(path, genome())
    assert path.actual_pnl_eur is None and path.entry_evidence_kind == "provider_signal"
    assert all(leg.closed_at is None for leg in path.legs)
    assert result.entries[0].entry_price == 100.2
    assert result.entries[0].opened_at == BASE
    assert result.exits[0].closed_at == BASE + timedelta(seconds=2)
    assert result.exit_reason == "provider_close"
    assert path.times_ns.flags.writeable is False
    with pytest.raises(ValueError, match="observed entries"):
        make_path(signal, genome().with_change(entry_mode="actual_mt5"), **path_inputs())


def test_shared_causal_paths_reuse_one_tape_and_wait_for_each_signal_receipt():
    later = message(2, "SELL GOLD NOW", date_utc=(BASE + timedelta(seconds=1)).isoformat(),
                    ts=(BASE + timedelta(seconds=1)).isoformat())
    signals, _ = compile_rows([message(), later])
    paths = make_shared_paths(
        signals, {"canal2": genome()}, start=BASE, **path_inputs())

    assert len(paths) == 2
    assert paths[0].times_ns is paths[1].times_ns
    assert paths[0].bid is paths[1].bid
    assert paths[0].fx_bid is paths[1].fx_bid
    assert paths[0].exit_quotes is paths[0].bid
    assert paths[1].exit_quotes is paths[1].ask
    assert paths[0].times_ns.flags.writeable is False
    execution = ExecutionAssumptions(protection=protection_profile(),
                                     market=market_profile(), client=ClientProfile())
    specs = [BasketReplaySpec("canal2", path, genome(), execution) for path in paths]
    before_future = simulate_shared(
        specs[:1], profile=SharedReplayProfile(account_currency="EUR"))
    report = simulate_shared(
        specs,
        profile=SharedReplayProfile(account_currency="EUR"))
    entries = {signal_id: result.entries for _, signal_id, result in report.baskets}
    assert entries["canal2_1"][0].opened_at >= BASE
    assert entries["canal2_2"][0].opened_at >= BASE + timedelta(seconds=1)
    assert report.expected_quote_count == 3
    assert any(sum(bool(point.positions) for _, _, point in frame.states if point) == 2
               for frame in report.risk_grid)
    future_at_ns = paths[1].times_ns[1]
    prior_points = [(point.time_ns, point.realized_minor, point.floating_minor,
                     point.positions, point.phase)
                    for point in before_future.risk
                    if point.time_ns < future_at_ns]
    shared_prior_points = [(point.time_ns, point.realized_minor,
                            point.floating_minor, point.positions, point.phase)
                           for point in report.risk
                           if point.signal_id == "canal2_1" and point.time_ns < future_at_ns]
    assert shared_prior_points == prior_points


def test_shared_causal_tape_rejects_identity_or_window_leakage():
    signal = compile_rows([message()])[0][0]
    with pytest.raises(ValueError, match="duplicate"):
        make_shared_paths((signal, signal), {"canal2": genome()},
                          start=BASE, **path_inputs())
    with pytest.raises(ValueError, match="outside common tape window"):
        make_shared_paths((signal,), {"canal2": genome()},
                          start=BASE + timedelta(seconds=1), **path_inputs())


def test_conversion_never_uses_future_tick_when_history_is_missing():
    signal = compile_rows([message()])[0][0]
    inputs = path_inputs()
    inputs["conversion"] = tuple(values[1:] for values in inputs["conversion"])
    path = make_path(signal, genome(), **inputs)
    assert path.fx_valid.tolist() == [False, True, True]
    assert np.isnan(path.fx_bid[0])


@pytest.mark.parametrize("defect", ["unsorted", "crossed", "nonfinite"])
def test_broken_market_tape_is_not_silently_filtered(defect):
    signal = compile_rows([message()])[0][0]
    inputs = path_inputs()
    times, bid, ask = inputs["market"]
    if defect == "unsorted":
        times[2] = times[0] - 1
    elif defect == "crossed":
        ask[1] = bid[1] - 1
    else:
        bid[1] = np.nan
    with pytest.raises(ValueError, match="market"):
        make_path(signal, genome(), **inputs)
