from __future__ import annotations

from datetime import datetime, timedelta, timezone

from research.gold_iterative.entry_watch_parity import certify_entry_watch_parity


BASE = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _start(*, signal_id: str = "canal2_100") -> dict:
    return {
        "sig": signal_id,
        "ev": "gold_555_entry_watch_started",
        "ts": _iso(BASE),
        "reference_price": 100.0,
        "reference_bid": 99.8,
        "reference_ask": 100.0,
        "reference_tick_time_msc": 1_000,
        "watch": {
            "direction": "BUY",
            "reference": 100.0,
            "observed_at": _iso(BASE),
            "expires_at": _iso(BASE + timedelta(minutes=30)),
            "adverse_extreme": None,
            "armed": False,
            "status": "waiting",
            "last_tick_msc": None,
            "confirmed_quote": None,
            "confirmed_at": None,
        },
    }


def _state(
    action: str,
    *,
    at: datetime,
    tick_msc: int,
    bid: float,
    ask: float,
    adverse: float,
    confirmed: bool = False,
) -> dict:
    return {
        "sig": "canal2_100",
        "ev": "gold_555_entry_watch_state",
        "ts": _iso(at),
        "action": action,
        "bid": bid,
        "ask": ask,
        "tick_time_msc": tick_msc,
        "watch": {
            "direction": "BUY",
            "reference": 100.0,
            "observed_at": _iso(BASE),
            "expires_at": _iso(BASE + timedelta(minutes=30)),
            "adverse_extreme": adverse,
            "armed": True,
            "status": "confirmed" if confirmed else "waiting",
            "last_tick_msc": tick_msc,
            "confirmed_quote": ask if confirmed else None,
            "confirmed_at": _iso(at) if confirmed else None,
        },
    }


def _terminal(*, at: datetime, quote: float) -> dict:
    return {
        "sig": "canal2_100",
        "ev": "gold_555_entry_watch_confirmed",
        "ts": _iso(at),
        "watch": {
            "direction": "BUY",
            "reference": 100.0,
            "observed_at": _iso(BASE),
            "expires_at": _iso(BASE + timedelta(minutes=30)),
            "adverse_extreme": 99.0,
            "armed": True,
            "status": "confirmed",
            "last_tick_msc": 3_000,
            "confirmed_quote": quote,
            "confirmed_at": _iso(at),
        },
    }


def _events() -> tuple[dict, ...]:
    armed_at = BASE + timedelta(seconds=1)
    confirmed_at = BASE + timedelta(seconds=2)
    return (
        _start(),
        _state(
            "armed",
            at=armed_at,
            tick_msc=2_000,
            bid=98.8,
            ask=99.0,
            adverse=99.0,
        ),
        _state(
            "confirm",
            at=confirmed_at,
            tick_msc=3_000,
            bid=100.3,
            ask=100.5,
            adverse=99.0,
            confirmed=True,
        ),
        _terminal(at=confirmed_at, quote=100.5),
        {
            "sig": "canal2_100",
            "ev": "gold_555_first_leg_filled",
            "ts": _iso(confirmed_at + timedelta(milliseconds=100)),
            "fill_price": 100.55,
            "ticket": 123,
        },
    )


def _ticks(_day: str, _reference: int, _expires_at: datetime):
    return (
        {
            "bid": 98.8,
            "ask": 99.0,
            "source_time_msc": 2_000,
            "time_utc": BASE + timedelta(seconds=1),
        },
        {
            "bid": 100.3,
            "ask": 100.5,
            "source_time_msc": 3_000,
            "time_utc": BASE + timedelta(seconds=2),
        },
    )


def test_entry_watch_trace_and_full_ticks_are_reported_separately() -> None:
    report = certify_entry_watch_parity(_events(), tick_loader=_ticks)

    assert report["attempts"] == 1
    assert report["logged_sample_replay"]["status"] == "exact"
    assert report["logged_sample_replay"]["exact_attempts"] == 1
    assert report["full_tick_replay"]["status"] == "exact"
    assert report["full_tick_replay"]["outcome_matches"] == 1
    assert report["full_tick_replay"]["trigger_tick_matches"] == 1
    assert report["full_tick_replay"]["quote_matches"] == 1
    assert report["prospective_entry_outcome_allowed"] is True
    assert report["prospective_entry_trigger_allowed"] is True
    assert report["broker_execution"] == {
        "status": "observed_variance",
        "confirmed_attempts": 1,
        "filled_attempts": 1,
        "failed_attempts": 0,
        "missing_outcomes": 0,
        "exact_quote_fills": 0,
        "median_result_event_delay_ms": 100,
        "max_result_event_delay_ms": 100,
        "median_unfavourable_slippage": "0.05",
        "min_unfavourable_slippage": "0.05",
        "max_unfavourable_slippage": "0.05",
    }
    assert report["prospective_fill_model_allowed"] is False
    assert report["end_to_end_historical_extension_allowed"] is False


def test_a_logged_state_mismatch_blocks_state_machine_parity() -> None:
    events = list(_events())
    events[1] = dict(events[1], action="track")

    report = certify_entry_watch_parity(events, tick_loader=_ticks)

    assert report["logged_sample_replay"]["status"] == "mismatch"
    assert report["logged_sample_replay"]["exact_attempts"] == 0
    assert "logged_action_mismatch" in report["rows"][0]["blockers"]


def test_full_tick_outcome_mismatch_blocks_prospective_entry_gate() -> None:
    def never_confirms(_day: str, _reference: int, _expires_at: datetime):
        return ({
            "bid": 98.8,
            "ask": 99.0,
            "source_time_msc": 2_000,
            "time_utc": BASE + timedelta(seconds=1),
        },)

    report = certify_entry_watch_parity(_events(), tick_loader=never_confirms)

    assert report["logged_sample_replay"]["status"] == "exact"
    assert report["full_tick_replay"]["status"] == "mismatch"
    assert report["prospective_entry_outcome_allowed"] is False
    assert "full_tick_outcome_mismatch" in report["rows"][0]["blockers"]


def test_quote_and_scheduler_delta_are_measured_without_becoming_a_range() -> None:
    def slightly_earlier(_day: str, _reference: int, _expires_at: datetime):
        return (
            {
                "bid": 98.8,
                "ask": 99.0,
                "source_time_msc": 2_000,
                "time_utc": BASE + timedelta(seconds=1),
            },
            {
                "bid": 98.7,
                "ask": 98.9,
                "source_time_msc": 2_100,
                "time_utc": BASE + timedelta(seconds=1, milliseconds=100),
            },
            {
                "bid": 100.2,
                "ask": 100.4,
                "source_time_msc": 2_900,
                "time_utc": BASE + timedelta(seconds=1, milliseconds=900),
            },
        )

    report = certify_entry_watch_parity(_events(), tick_loader=slightly_earlier)

    row = report["rows"][0]
    assert report["full_tick_replay"]["status"] == "outcome_only"
    assert report["full_tick_replay"]["quote_matches"] == 0
    assert report["full_tick_replay"]["trigger_tick_matches"] == 0
    assert report["prospective_entry_outcome_allowed"] is True
    assert report["prospective_entry_trigger_allowed"] is False
    assert row["confirmation_wall_clock_delta_ms"] == -100
    assert row["actual_confirmation_tick_msc"] == 3_000
    assert row["full_tick_confirmation_tick_msc"] == 2_900
    assert row["confirmation_tick_match"] is False
    assert row["confirmation_quote_delta"] == "-0.10"
    assert row["broker_result_event_at"] is not None
    assert "broker_fill_at" not in row


def test_broker_rejection_is_attached_to_the_confirmed_attempt() -> None:
    events = list(_events())
    events[-1] = {
        "sig": "canal2_100",
        "ev": "market_fill_failed",
        "ts": _iso(BASE + timedelta(seconds=2, milliseconds=300)),
        "reason": "order_send returned no fill",
    }

    report = certify_entry_watch_parity(events, tick_loader=_ticks)

    assert report["broker_execution"]["filled_attempts"] == 0
    assert report["broker_execution"]["failed_attempts"] == 1
    assert report["rows"][0]["broker_order_outcome"] == "failed"
    assert report["rows"][0]["broker_failure_reason"] == (
        "order_send returned no fill"
    )
