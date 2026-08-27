from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gold_555_entry_watch import EntryWatch


NOW = datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc)


def test_buy_waits_for_adverse_move_then_reversal() -> None:
    watch = EntryWatch.new("BUY", reference=4300.0, observed_at=NOW)

    assert watch.on_quote(
        bid=4299.2, ask=4299.4, now=NOW, tick_msc=1
    ).action == "wait"
    assert watch.on_quote(
        bid=4298.7, ask=4298.9, now=NOW, tick_msc=2
    ).action == "armed"
    assert watch.on_quote(
        bid=4299.9, ask=4300.4, now=NOW, tick_msc=3
    ).action == "confirm"
    assert watch.status == "confirmed"
    assert watch.confirmed_quote == 4300.4


def test_sell_tracks_new_adverse_extreme_before_confirming() -> None:
    watch = EntryWatch.new("SELL", reference=4300.0, observed_at=NOW)

    assert watch.on_quote(
        bid=4301.2, ask=4301.4, now=NOW, tick_msc=1
    ).action == "armed"
    assert watch.on_quote(
        bid=4302.0, ask=4302.2, now=NOW, tick_msc=2
    ).action == "track"
    result = watch.on_quote(
        bid=4300.5, ask=4300.7, now=NOW, tick_msc=3
    )

    assert result.action == "confirm"
    assert watch.adverse_extreme == 4302.0


def test_buy_tracks_lower_extreme_and_uses_it_for_reversal() -> None:
    watch = EntryWatch.new("BUY", reference=4300.0, observed_at=NOW)
    watch.on_quote(bid=4298.7, ask=4298.9, now=NOW, tick_msc=1)
    watch.on_quote(bid=4297.8, ask=4298.0, now=NOW, tick_msc=2)

    assert watch.on_quote(
        bid=4298.9, ask=4299.4, now=NOW, tick_msc=3
    ).action == "wait"
    assert watch.on_quote(
        bid=4299.0, ask=4299.5, now=NOW, tick_msc=4
    ).action == "confirm"


def test_expiry_uses_original_telegram_observation_time() -> None:
    watch = EntryWatch.new("BUY", reference=4300.0, observed_at=NOW)

    result = watch.on_quote(
        bid=4298.0,
        ask=4298.2,
        now=NOW + timedelta(minutes=30),
        tick_msc=1,
    )

    assert watch.expires_at == NOW + timedelta(minutes=30)
    assert result.action == "expire"
    assert watch.status == "expired"


def test_repeated_or_older_tick_does_not_advance_state_twice() -> None:
    watch = EntryWatch.new("SELL", reference=4300.0, observed_at=NOW)
    watch.on_quote(bid=4301.2, ask=4301.4, now=NOW, tick_msc=100)

    duplicate = watch.on_quote(
        bid=4299.0, ask=4299.2, now=NOW, tick_msc=100
    )

    assert duplicate.action == "duplicate_tick"
    assert watch.status == "waiting"


def test_confirmed_watch_cannot_confirm_twice() -> None:
    watch = EntryWatch.new("SELL", reference=4300.0, observed_at=NOW)
    watch.on_quote(bid=4301.2, ask=4301.4, now=NOW, tick_msc=1)
    watch.on_quote(bid=4299.7, ask=4299.9, now=NOW, tick_msc=2)

    repeated = watch.on_quote(
        bid=4298.0, ask=4298.2, now=NOW, tick_msc=3
    )

    assert repeated.action == "terminal"
    assert watch.status == "confirmed"


def test_json_round_trip_preserves_exact_state() -> None:
    watch = EntryWatch.new("SELL", reference=4300.0, observed_at=NOW)
    watch.on_quote(
        bid=4302.0,
        ask=4302.2,
        now=NOW + timedelta(seconds=5),
        tick_msc=123456,
    )

    restored = EntryWatch.from_dict(watch.to_dict())

    assert restored == watch


def test_naive_datetimes_are_normalized_to_utc() -> None:
    naive = datetime(2026, 8, 27, 9, 0)
    watch = EntryWatch.new("BUY", reference=4300.0, observed_at=naive)

    assert watch.observed_at.tzinfo == timezone.utc
    assert watch.expires_at.tzinfo == timezone.utc
