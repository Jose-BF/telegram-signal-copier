from datetime import datetime, timedelta, timezone

import pytest

import broker_tick_clock


BASE = datetime(2026, 8, 27, 8, 0, tzinfo=timezone.utc)


def contract(*snapshots):
    return {
        "swap_snapshots": [
            {
                "captured_at_utc": captured.isoformat(),
                "time_evidence": {"utc_offset_seconds": offset},
            }
            for captured, offset in snapshots
        ]
    }


def test_contract_clock_uses_latest_evidence_known_at_target():
    evidence = contract(
        (BASE - timedelta(days=2), 7_200),
        (BASE - timedelta(minutes=1), 10_800),
        (BASE + timedelta(minutes=1), 14_400),
    )

    assert broker_tick_clock.contract_utc_offset_seconds(
        evidence,
        at_utc=BASE,
    ) == 10_800


def test_live_clock_normalizes_vantage_server_epoch_to_utc():
    raw_msc = int((BASE + timedelta(hours=3)).timestamp() * 1000)

    offset = broker_tick_clock.resolve_utc_offset_seconds(
        contract=contract((BASE - timedelta(minutes=1), 10_800)),
        raw_server_msc=raw_msc,
        observed_utc=BASE,
    )

    assert offset == 10_800
    assert broker_tick_clock.normalize_server_msc(raw_msc, offset) == int(
        BASE.timestamp() * 1000
    )


def test_live_clock_rejects_conflicting_durable_and_observed_evidence():
    raw_msc = int((BASE + timedelta(hours=2)).timestamp() * 1000)

    with pytest.raises(ValueError, match="evidence mismatch"):
        broker_tick_clock.resolve_utc_offset_seconds(
            contract=contract((BASE - timedelta(minutes=1), 10_800)),
            raw_server_msc=raw_msc,
            observed_utc=BASE,
        )


def test_history_query_moves_utc_window_onto_server_clock():
    utc_msc = int(BASE.timestamp() * 1000)

    assert broker_tick_clock.server_query_datetime(
        utc_msc,
        10_800,
    ) == BASE + timedelta(hours=3)
