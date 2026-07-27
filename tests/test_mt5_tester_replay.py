from __future__ import annotations

import csv
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

import mt5_tester_replay as replay


def _trade(
    *,
    ticket: int = 1658463204,
    pnl: float = 2.03,
    tps: list[float] | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "sig_id": "canal2_500",
        "channel": "canal2",
        "direction": "SELL",
        "open_dt_utc": "2026-07-27T14:22:24+00:00",
        "close_dt_utc": "2026-07-27T14:25:49+00:00",
        "pnl_real_mt5": pnl,
        "status": "closed",
        "levels": {
            "provider_tps": tps or [4072.5, 4070.0, 4067.0, 4060.0],
            "provider_sl": 4085.0,
        },
        "tickets": [
            {
                "ticket": ticket,
                "volume": 0.01,
                "open_price": 4074.81,
                "open_dt_utc": "2026-07-27T14:22:24+00:00",
                "close_price": 4072.5,
                "close_dt_utc": "2026-07-27T14:25:49+00:00",
                "close_reason": "tp",
                "pnl_net": pnl,
                "is_closed": True,
                "open_deal": {
                    "time_msc": 1785172944319,
                    "price": 4074.81,
                    "volume": 0.01,
                },
                "close_deal": {
                    "time_msc": 1785173149167,
                    "price": 4072.5,
                    "profit": pnl,
                    "commission": 0.0,
                    "swap": 0.0,
                    "fee": 0.0,
                },
            }
        ],
    }


def _history(*, ticket: int = 1658463204, pnl: float = 2.03) -> list[dict]:
    return [
        {
            "ticket": ticket,
            "direction": "SELL",
            "volume": 0.01,
            "open_time_msc": 1785172944319,
            "open_price": 4074.81,
            "close_time_msc": 1785173149167,
            "close_price": 4072.5,
            "close_reason": "tp",
            "pnl_net": pnl,
        }
    ]


def test_build_fixture_preserves_observed_entry_and_provider_tp2():
    rows, manifest = replay.build_fixture(
        replay_rows=[_trade()],
        day=date(2026, 7, 27),
        observed_history=_history(),
    )

    assert rows == [
        {
            "schema_version": 1,
            "signal_id": "canal2_500",
            "provider": "Gold Signals",
            "ticket": 1658463204,
            "direction": "SELL",
            "volume": "0.01",
            "entry_time_msc": 1785172944319,
            "entry_price": "4074.81",
            "observed_close_time_msc": 1785173149167,
            "observed_close_price": "4072.5",
            "observed_close_reason": "tp",
            "observed_pnl_eur": "2.03",
            "provider_sl": "4085.0",
            "provider_tp1": "4072.5",
            "provider_tp2": "4070.0",
            "source_sha256": rows[0]["source_sha256"],
        }
    ]
    assert len(rows[0]["source_sha256"]) == 64
    assert manifest["day"] == "2026-07-27"
    assert manifest["signals"] == 1
    assert manifest["tickets"] == 1
    assert manifest["observed_pnl_eur"] == "2.03"
    assert manifest["fixture_sha256"] == replay.fixture_sha256(rows)


def test_build_fixture_sorts_by_entry_signal_and_ticket():
    later = _trade(ticket=20)
    later["tickets"][0]["open_deal"]["time_msc"] += 100
    earlier = _trade(ticket=10)

    rows, _ = replay.build_fixture(
        replay_rows=[later, earlier],
        day=date(2026, 7, 27),
        observed_history=[
            _history(ticket=20)[0] | {"open_time_msc": 1785172944419},
            _history(ticket=10)[0],
        ],
    )

    assert [row["ticket"] for row in rows] == [10, 20]


@pytest.mark.parametrize(
    ("trade", "history", "blocker"),
    [
        (_trade(tps=[4072.5]), _history(), "missing_provider_tp2"),
        (
            _trade(),
            _history(pnl=2.04),
            "history_pnl_mismatch",
        ),
    ],
)
def test_build_fixture_blocks_incomplete_or_conflicting_evidence(
    trade: dict,
    history: list[dict],
    blocker: str,
):
    with pytest.raises(replay.FixtureBlockedError, match=blocker):
        replay.build_fixture(
            replay_rows=[trade],
            day=date(2026, 7, 27),
            observed_history=history,
        )


def test_build_fixture_blocks_duplicate_ticket():
    with pytest.raises(replay.FixtureBlockedError, match="duplicate_ticket"):
        replay.build_fixture(
            replay_rows=[_trade(), _trade()],
            day=date(2026, 7, 27),
            observed_history=_history(),
        )


def test_write_fixture_is_deterministic_and_binds_csv(tmp_path: Path):
    rows, manifest = replay.build_fixture(
        replay_rows=[_trade()],
        day=date(2026, 7, 27),
        observed_history=_history(),
    )

    csv_path, manifest_path = replay.write_fixture(
        rows,
        manifest,
        output_dir=tmp_path,
        stem="2026-07-27",
    )

    payload = csv_path.read_bytes()
    written_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with csv_path.open(newline="", encoding="utf-8") as handle:
        written_rows = list(csv.DictReader(handle, delimiter=";"))

    assert written_rows[0]["ticket"] == "1658463204"
    assert written_manifest["csv_sha256"] == hashlib.sha256(payload).hexdigest()
    assert written_manifest["fixture_sha256"] == manifest["fixture_sha256"]

