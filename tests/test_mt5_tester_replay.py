from __future__ import annotations

import csv
import copy
import hashlib
import json
from datetime import date, datetime, timezone
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


def _result_row(fixture_row: dict, *, policy_id: str = "observed_close") -> dict:
    return {
        "schema_version": 1,
        "policy_id": policy_id,
        "signal_id": fixture_row["signal_id"],
        "ticket": fixture_row["ticket"],
        "status": "closed",
        "direction": fixture_row["direction"],
        "volume": fixture_row["volume"],
        "entry_time_msc": fixture_row["entry_time_msc"],
        "entry_price": fixture_row["entry_price"],
        "close_time_msc": fixture_row["observed_close_time_msc"],
        "close_price": fixture_row["observed_close_price"],
        "close_reason": fixture_row["observed_close_reason"],
        "pnl_eur": fixture_row["observed_pnl_eur"],
        "touch_bid": "4072.5",
        "touch_ask": "4072.73",
        "source_sha256": fixture_row["source_sha256"],
    }


def test_certify_observed_result_requires_exact_ticket_and_cent():
    rows, manifest = replay.build_fixture(
        replay_rows=[_trade()],
        day=date(2026, 7, 27),
        observed_history=_history(),
    )

    certificate = replay.certify_result(
        fixture_rows=rows,
        fixture_manifest=manifest,
        policy_id="observed_close",
        result_rows=[_result_row(rows[0])],
    )

    assert certificate["status"] == "certified"
    assert certificate["policy_id"] == "observed_close"
    assert certificate["expected_tickets"] == 1
    assert certificate["checked_tickets"] == 1
    assert certificate["observed_pnl_eur"] == "2.03"
    assert certificate["result_pnl_eur"] == "2.03"
    assert certificate["blockers"] == []
    assert len(certificate["certificate_sha256"]) == 64


@pytest.mark.parametrize(
    ("mutation", "blocker"),
    [
        ({"ticket": 99}, "ticket_set_mismatch"),
        ({"entry_price": "4074.82"}, "entry_price_mismatch"),
        ({"pnl_eur": "2.04"}, "baseline_pnl_mismatch"),
        ({"source_sha256": "0" * 64}, "source_sha256_mismatch"),
        ({"policy_id": "all_tp2_no_be"}, "policy_id_mismatch"),
    ],
)
def test_certify_observed_result_blocks_mutations(
    mutation: dict,
    blocker: str,
):
    rows, manifest = replay.build_fixture(
        replay_rows=[_trade()],
        day=date(2026, 7, 27),
        observed_history=_history(),
    )
    result = _result_row(rows[0])
    result.update(mutation)

    certificate = replay.certify_result(
        fixture_rows=rows,
        fixture_manifest=manifest,
        policy_id="observed_close",
        result_rows=[result],
    )

    assert certificate["status"] == "blocked"
    assert blocker in certificate["blockers"]
    assert certificate["result_pnl_eur"] is None


def test_certify_result_blocks_duplicate_ticket():
    rows, manifest = replay.build_fixture(
        replay_rows=[_trade()],
        day=date(2026, 7, 27),
        observed_history=_history(),
    )
    result = _result_row(rows[0])

    certificate = replay.certify_result(
        fixture_rows=rows,
        fixture_manifest=manifest,
        policy_id="observed_close",
        result_rows=[result, copy.deepcopy(result)],
    )

    assert certificate["status"] == "blocked"
    assert "duplicate_result_ticket" in certificate["blockers"]


def test_certify_alternative_is_diagnostic_and_keeps_complete_universe():
    rows, manifest = replay.build_fixture(
        replay_rows=[_trade()],
        day=date(2026, 7, 27),
        observed_history=_history(),
    )
    result = _result_row(rows[0], policy_id="all_tp2_no_be")
    result.update({
        "close_time_msc": 1785173291021,
        "close_price": "4070.0",
        "close_reason": "tp2",
        "pnl_eur": "4.23",
    })

    certificate = replay.certify_result(
        fixture_rows=rows,
        fixture_manifest=manifest,
        policy_id="all_tp2_no_be",
        result_rows=[result],
    )

    assert certificate["status"] == "diagnostic"
    assert certificate["checked_tickets"] == 1
    assert certificate["result_pnl_eur"] == "4.23"
    assert certificate["conclusions_allowed"] is False


def test_read_result_parses_fixed_schema(tmp_path: Path):
    path = tmp_path / "result.csv"
    fieldnames = list(_result_row({
        "signal_id": "canal2_500",
        "ticket": 1658463204,
        "direction": "SELL",
        "volume": "0.01",
        "entry_time_msc": 1785172944319,
        "entry_price": "4074.81",
        "observed_close_time_msc": 1785173149167,
        "observed_close_price": "4072.5",
        "observed_close_reason": "tp",
        "observed_pnl_eur": "2.03",
        "source_sha256": "a" * 64,
    }).keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerow(_result_row({
            "signal_id": "canal2_500",
            "ticket": 1658463204,
            "direction": "SELL",
            "volume": "0.01",
            "entry_time_msc": 1785172944319,
            "entry_price": "4074.81",
            "observed_close_time_msc": 1785173149167,
            "observed_close_price": "4072.5",
            "observed_close_reason": "tp",
            "observed_pnl_eur": "2.03",
            "source_sha256": "a" * 64,
        }))

    assert replay.read_result(path)[0]["ticket"] == 1658463204


def test_prepare_cli_writes_isolated_real_tick_profiles(tmp_path: Path):
    replay_file = tmp_path / "replay_trades.jsonl"
    replay_file.write_text(
        json.dumps(_trade(), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    history_file = tmp_path / "observed_history.json"
    history_file.write_text(
        json.dumps(_history(), sort_keys=True),
        encoding="utf-8",
    )
    compiled_ea = tmp_path / "TelegramSignalReplayEA.ex5"
    compiled_ea.write_bytes(b"compiled-test-ea")
    mt5_data_dir = tmp_path / "terminal"
    common_files_dir = tmp_path / "common" / "Files"
    run_root = tmp_path / "runs"

    exit_code = replay.main([
        "prepare",
        "--date",
        "2026-07-27",
        "--replay-file",
        str(replay_file),
        "--history-file",
        str(history_file),
        "--mt5-data-dir",
        str(mt5_data_dir),
        "--common-files-dir",
        str(common_files_dir),
        "--compiled-ea",
        str(compiled_ea),
        "--run-root",
        str(run_root),
    ])

    assert exit_code == 0
    common_run = common_files_dir / "TelegramSignalReplay" / "2026-07-27"
    run_dir = run_root / "2026-07-27"
    assert (common_run / "fixture.csv").is_file()
    assert (common_run / "fixture.manifest.json").is_file()
    assert (run_dir / "fixture.csv").read_bytes() == (
        common_run / "fixture.csv"
    ).read_bytes()
    installed_ea = (
        mt5_data_dir
        / "MQL5"
        / "Experts"
        / "Research"
        / "TelegramSignalReplayEA.ex5"
    )
    assert installed_ea.read_bytes() == b"compiled-test-ea"
    assert not installed_ea.with_suffix(".mq5").exists()

    profiles = mt5_data_dir / "MQL5" / "Profiles" / "Tester"
    ini_files = sorted(profiles.glob("telegram-replay-*.ini"))
    set_files = sorted(profiles.glob("telegram-replay-*.set"))
    assert len(ini_files) == len(replay.POLICY_IDS) == 3
    assert len(set_files) == len(replay.POLICY_IDS) == 3

    observed_ini = (
        profiles / "telegram-replay-2026-07-27-observed_close.ini"
    ).read_text(encoding="utf-16")
    for required in (
        "Expert=Research\\TelegramSignalReplayEA.ex5",
        "Symbol=XAUUSD",
        "Period=M1",
        "Model=4",
        "Optimization=0",
        "Dates=1",
        "Currency=EUR",
        "Leverage=500",
        "ProfitInPips=0",
        "FromDate=2026.07.27",
        "ToDate=2026.07.28",
    ):
        assert required in observed_ini

    observed_set = (
        profiles / "telegram-replay-2026-07-27-observed_close.set"
    ).read_text(encoding="utf-16")
    manifest = json.loads(
        (run_dir / "fixture.manifest.json").read_text(encoding="utf-8")
    )
    assert (
        "InpFixtureFile="
        "TelegramSignalReplay\\2026-07-27\\fixture.csv"
    ) in observed_set
    assert (
        "InpResultFile="
        "TelegramSignalReplay\\2026-07-27\\observed_close.csv"
    ) in observed_set
    assert "InpPolicy=observed_close" in observed_set
    assert (
        f"InpFixtureSha256={manifest['fixture_sha256']}" in observed_set
    )

    run_card = json.loads(
        (run_dir / "run_card.json").read_text(encoding="utf-8")
    )
    assert run_card["day"] == "2026-07-27"
    assert run_card["signals"] == 1
    assert run_card["tickets"] == 1
    assert run_card["observed_pnl_eur"] == "2.03"
    assert set(run_card["policies"]) == replay.POLICY_IDS


def test_select_replay_rows_uses_an_explicit_utc_cutoff():
    closed = _trade()
    later = _trade(ticket=1658469999)
    later["sig_id"] = "canal2_501"
    later["open_dt_utc"] = "2026-07-27T16:54:06+00:00"
    later["status"] = "partial"
    later["tickets"][0]["is_closed"] = False

    selected, selection = replay.select_replay_rows(
        [closed, later],
        day=date(2026, 7, 27),
        cutoff_utc=datetime(
            2026,
            7,
            27,
            16,
            35,
            57,
            tzinfo=timezone.utc,
        ),
    )

    assert [row["sig_id"] for row in selected] == ["canal2_500"]
    assert selection == {
        "cutoff_utc": "2026-07-27T16:35:57+00:00",
        "day_rows_seen": 2,
        "rows_selected": 1,
        "rows_after_cutoff": 1,
    }
