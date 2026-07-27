from __future__ import annotations

import csv
import copy
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

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
        "mt5_time_offset_s": 10800,
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
                "mt5_time_offset_s": 10800,
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


def _universe_proof(*tickets: int) -> dict:
    return replay.build_ticket_universe_proof(
        day=date(2026, 7, 27),
        expected_tickets=tickets,
        observed_tickets=tickets,
        mt5_time_offset_s=10800,
        source="test_full_history",
        stable_snapshots=2,
    )


def _deal(
    *,
    ticket: int,
    deal_ticket: int,
    entry: int,
    deal_type: int,
    time_msc: int,
    price: float,
    profit: float = 0.0,
    magic: int = 20260422,
) -> SimpleNamespace:
    return SimpleNamespace(
        ticket=deal_ticket,
        position_id=ticket,
        order=ticket,
        entry=entry,
        type=deal_type,
        time_msc=time_msc,
        price=price,
        volume=0.01,
        profit=profit,
        swap=0.0,
        commission=0.0,
        fee=0.0,
        magic=magic,
        symbol="XAUUSD",
        comment="c2_500" if entry == 0 else "[tp 4072.5]",
    )


def _mt5_deals(*, include_extra: bool = False) -> list[SimpleNamespace]:
    rows = [
        _deal(
            ticket=1658463204,
            deal_ticket=100,
            entry=0,
            deal_type=1,
            time_msc=1785172944319,
            price=4074.81,
        ),
        _deal(
            ticket=1658463204,
            deal_ticket=101,
            entry=1,
            deal_type=0,
            time_msc=1785173149167,
            price=4072.5,
            profit=2.03,
        ),
    ]
    if include_extra:
        rows.extend([
            _deal(
                ticket=1658469999,
                deal_ticket=102,
                entry=0,
                deal_type=0,
                time_msc=1785173000000,
                price=4073.0,
            ),
            _deal(
                ticket=1658469999,
                deal_ticket=103,
                entry=1,
                deal_type=1,
                time_msc=1785173200000,
                price=4074.0,
                profit=-1.0,
            ),
        ])
    return rows


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
        "touch_bid": "4069.77",
        "touch_ask": "4070.0",
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


@pytest.mark.parametrize(
    ("mutation", "blocker"),
    [
        ({"schema_version": 999}, "unsupported_result_schema"),
        ({"close_reason": "invented_close"}, "invalid_alternative_close_reason"),
        ({"close_price": "1"}, "tp2_close_price_mismatch"),
        (
            {"touch_bid": "0", "touch_ask": "0"},
            "invalid_touch_quote",
        ),
        (
            {"touch_bid": "4070.1", "touch_ask": "4070.0"},
            "crossed_touch_quote",
        ),
        (
            {"close_time_msc": 1785172944318},
            "result_close_before_entry",
        ),
        ({"pnl_eur": "999999.99"}, "alternative_pnl_mismatch"),
    ],
)
def test_certify_alternative_blocks_invented_result_values(
    mutation: dict,
    blocker: str,
):
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
        "touch_bid": "4069.77",
        "touch_ask": "4070.0",
    })
    result.update(mutation)

    certificate = replay.certify_result(
        fixture_rows=rows,
        fixture_manifest=manifest,
        policy_id="all_tp2_no_be",
        result_rows=[result],
        expected_alternative_rows={
            rows[0]["ticket"]: {
                "close_time_msc": 1785173291021,
                "close_price": "4070.0",
                "close_reason": "tp2",
                "pnl_eur": "4.23",
                "touch_bid": "4069.77",
                "touch_ask": "4070.0",
            }
        },
    )

    assert certificate["status"] == "blocked"
    assert blocker in certificate["blockers"]
    assert certificate["result_pnl_eur"] is None


def test_certify_alternative_blocks_money_after_broker_rollover():
    rows, manifest = replay.build_fixture(
        replay_rows=[_trade()],
        day=date(2026, 7, 27),
        observed_history=_history(),
    )
    result = _result_row(rows[0], policy_id="all_tp2_no_be")
    result.update({
        "close_time_msc": rows[0]["entry_time_msc"] + 86_400_000,
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

    assert certificate["status"] == "blocked"
    assert certificate["result_pnl_eur"] is None
    assert "overnight_cost_model_unverified" in certificate["blockers"]
    assert certificate["overnight_tickets"] == [rows[0]["ticket"]]


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
    universe_file = tmp_path / "ticket_universe.json"
    universe_file.write_text(
        json.dumps(_universe_proof(1658463204), sort_keys=True),
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
        "--universe-proof-file",
        str(universe_file),
        "--mt5-data-dir",
        str(mt5_data_dir),
        "--common-files-dir",
        str(common_files_dir),
        "--compiled-ea",
        str(compiled_ea),
        "--run-root",
        str(run_root),
        "--tester-until",
        "2026-07-31",
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
        "ToDate=2026.07.31",
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
    assert run_card["tester_window"] == {
        "from_date": "2026-07-27",
        "until_exclusive": "2026-07-31",
    }
    assert set(run_card["policies"]) == replay.POLICY_IDS


def _prepare_certification_run(
    tmp_path: Path,
    *,
    write_observed_result: bool,
) -> tuple[Path, dict]:
    compiled_ea = tmp_path / "TelegramSignalReplayEA.ex5"
    compiled_ea.write_bytes(b"compiled-test-ea")
    run_root = tmp_path / "runs"
    run_card = replay.prepare_run(
        day=date(2026, 7, 27),
        replay_rows=[_trade()],
        observed_history=_history(),
        run_root=run_root,
        mt5_data_dir=tmp_path / "terminal",
        common_files_dir=tmp_path / "common" / "Files",
        compiled_ea=compiled_ea,
        universe_proof=_universe_proof(1658463204),
    )
    run_dir = run_root / "2026-07-27"
    if write_observed_result:
        fixture_row = replay.read_fixture(run_dir / "fixture.csv")[0]
        result_path = Path(
            run_card["policies"]["observed_close"]["result_path"]
        )
        result_path.parent.mkdir(parents=True, exist_ok=True)
        with result_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=replay.RESULT_COLUMNS,
                delimiter=";",
            )
            writer.writeheader()
            writer.writerow(_result_row(fixture_row))
    return run_dir, run_card


def test_certify_run_reports_every_missing_policy_result(tmp_path: Path):
    run_dir, _run_card = _prepare_certification_run(
        tmp_path,
        write_observed_result=False,
    )

    summary = replay.certify_run(run_dir)

    assert set(summary["certificates"]) == replay.POLICY_IDS
    for certificate in summary["certificates"].values():
        assert certificate["status"] == "blocked"
        assert certificate["blockers"] == ["result_file_missing"]
        assert certificate["result_pnl_eur"] is None
        path = run_dir / f"{certificate['policy_id']}.certificate.json"
        assert json.loads(path.read_text(encoding="utf-8")) == certificate


def test_certify_run_reports_an_invalid_result_file(tmp_path: Path):
    run_dir, run_card = _prepare_certification_run(
        tmp_path,
        write_observed_result=True,
    )
    result_path = Path(
        run_card["policies"]["observed_close"]["result_path"]
    )
    result_path.write_text("wrong;columns\n", encoding="utf-8")

    summary = replay.certify_run(run_dir)
    certificate = summary["certificates"]["observed_close"]

    assert certificate["status"] == "blocked"
    assert certificate["blockers"] == ["result_file_invalid"]
    assert certificate["checked_tickets"] == 0
    assert certificate["result_pnl_eur"] is None


def test_certify_run_reports_a_missing_policy_definition(tmp_path: Path):
    run_dir, _run_card = _prepare_certification_run(
        tmp_path,
        write_observed_result=False,
    )
    run_card_path = run_dir / "run_card.json"
    run_card = json.loads(run_card_path.read_text(encoding="utf-8"))
    del run_card["policies"]["all_tp2_no_be"]
    run_card_path.write_text(
        json.dumps(run_card, sort_keys=True),
        encoding="utf-8",
    )

    summary = replay.certify_run(run_dir)
    certificate = summary["certificates"]["all_tp2_no_be"]

    assert set(summary["certificates"]) == replay.POLICY_IDS
    assert certificate["status"] == "blocked"
    assert "run_card_policy_set_mismatch" in certificate["blockers"]
    assert "policy_run_card_missing" in certificate["blockers"]


@pytest.mark.parametrize(
    ("target", "action", "blocker"),
    [
        ("fixture.csv", "delete", "fixture_file_missing"),
        ("fixture.csv", "corrupt", "fixture_file_invalid"),
        (
            "fixture.manifest.json",
            "delete",
            "fixture_manifest_missing",
        ),
        (
            "fixture.manifest.json",
            "corrupt",
            "fixture_manifest_invalid",
        ),
    ],
)
def test_certify_run_reports_unavailable_fixture_evidence(
    tmp_path: Path,
    target: str,
    action: str,
    blocker: str,
):
    run_dir, _run_card = _prepare_certification_run(
        tmp_path,
        write_observed_result=True,
    )
    path = run_dir / target
    if action == "delete":
        path.unlink()
    else:
        path.write_text("corrupt", encoding="utf-8")

    summary = replay.certify_run(run_dir)

    assert set(summary["certificates"]) == replay.POLICY_IDS
    for certificate in summary["certificates"].values():
        assert certificate["status"] == "blocked"
        assert blocker in certificate["blockers"]
        assert certificate["result_pnl_eur"] is None


@pytest.mark.parametrize(
    ("target", "blocker"),
    [
        ("compiled_ea_path", "compiled_ea_sha256_mismatch"),
        ("ini_path", "ini_sha256_mismatch"),
        ("set_path", "set_sha256_mismatch"),
        ("fixture_path", "fixture_csv_sha256_mismatch"),
        (
            "common_fixture_path",
            "common_fixture_csv_sha256_mismatch",
        ),
    ],
)
def test_certify_run_blocks_tampered_inputs(
    tmp_path: Path,
    target: str,
    blocker: str,
):
    run_dir, run_card = _prepare_certification_run(
        tmp_path,
        write_observed_result=True,
    )
    if target in {"ini_path", "set_path"}:
        path = Path(run_card["policies"]["observed_close"][target])
    else:
        path = Path(run_card[target])
    path.write_bytes(path.read_bytes() + b"\n")

    summary = replay.certify_run(run_dir)
    certificate = summary["certificates"]["observed_close"]

    assert certificate["status"] == "blocked"
    assert blocker in certificate["blockers"]
    assert certificate["result_pnl_eur"] is None
    assert certificate["certificate_sha256"] is None


def test_default_tester_horizon_is_the_next_calendar_day():
    profile = replay._ini_text(
        day=date(2026, 7, 27),
        policy_id="observed_close",
    )

    assert "FromDate=2026.07.27" in profile
    assert "ToDate=2026.07.28" in profile


@pytest.mark.parametrize(
    "tester_until",
    [date(2026, 7, 27), date(2026, 7, 26)],
)
def test_tester_horizon_must_be_after_the_signal_day(tester_until):
    with pytest.raises(
        replay.FixtureBlockedError,
        match="invalid_tester_until",
    ):
        replay._ini_text(
            day=date(2026, 7, 27),
            policy_id="observed_close",
            tester_until=tester_until,
        )


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
        "rows_opened_after_cutoff": 1,
        "rows_not_closed_by_cutoff": 0,
    }


def test_select_replay_rows_excludes_a_trade_closed_after_the_cutoff():
    future_close = _trade()
    future_close["close_dt_utc"] = "2026-07-27T16:40:00+00:00"
    future_close["tickets"][0]["close_dt_utc"] = (
        "2026-07-27T16:40:00+00:00"
    )

    selected, selection = replay.select_replay_rows(
        [future_close],
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

    assert selected == []
    assert selection["rows_selected"] == 0
    assert selection["rows_after_cutoff"] == 1
    assert selection["rows_opened_after_cutoff"] == 0
    assert selection["rows_not_closed_by_cutoff"] == 1


def test_certify_run_blocks_alternatives_without_certified_baseline(
    tmp_path: Path,
):
    run_dir, run_card = _prepare_certification_run(
        tmp_path,
        write_observed_result=False,
    )
    fixture_row = replay.read_fixture(run_dir / "fixture.csv")[0]
    alternative = _result_row(
        fixture_row,
        policy_id="all_tp2_no_be",
    )
    alternative.update({
        "close_time_msc": 1785173291021,
        "close_price": "4070.0",
        "close_reason": "tp2",
        "pnl_eur": "4.23",
        "touch_bid": "4069.77",
        "touch_ask": "4070.0",
    })
    result_path = Path(
        run_card["policies"]["all_tp2_no_be"]["result_path"]
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    with result_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=replay.RESULT_COLUMNS,
            delimiter=";",
        )
        writer.writeheader()
        writer.writerow(alternative)

    summary = replay.certify_run(run_dir)
    certificate = summary["certificates"]["all_tp2_no_be"]

    assert certificate["status"] == "blocked"
    assert "observed_baseline_not_certified" in certificate["blockers"]
    assert certificate["result_pnl_eur"] is None
    persisted = json.loads(
        (run_dir / "all_tp2_no_be.certificate.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted == certificate


def test_mt5_full_day_history_proves_the_complete_ticket_universe():
    history, proof = replay.build_mt5_history_bundle(
        replay_rows=[_trade()],
        day=date(2026, 7, 27),
        deals=_mt5_deals(),
        stable_snapshots=2,
    )

    assert history == _history()
    assert proof["status"] == "verified"
    assert proof["expected_tickets"] == [1658463204]
    assert proof["observed_tickets"] == [1658463204]
    assert proof["stable_snapshots"] == 2
    assert len(proof["evidence_sha256"]) == 64


def test_mt5_full_day_history_blocks_a_ticket_omitted_from_replay():
    with pytest.raises(
        replay.FixtureBlockedError,
        match="mt5_ticket_universe_mismatch",
    ):
        replay.build_mt5_history_bundle(
            replay_rows=[_trade()],
            day=date(2026, 7, 27),
            deals=_mt5_deals(include_extra=True),
            stable_snapshots=2,
        )


def test_prepare_run_requires_a_verified_ticket_universe(tmp_path: Path):
    compiled_ea = tmp_path / "TelegramSignalReplayEA.ex5"
    compiled_ea.write_bytes(b"compiled-test-ea")

    with pytest.raises(
        replay.FixtureBlockedError,
        match="ticket_universe_proof_missing",
    ):
        replay.prepare_run(
            day=date(2026, 7, 27),
            replay_rows=[_trade()],
            observed_history=_history(),
            run_root=tmp_path / "runs",
            mt5_data_dir=tmp_path / "terminal",
            common_files_dir=tmp_path / "common" / "Files",
            compiled_ea=compiled_ea,
        )
