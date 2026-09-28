from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tools.audit_mt5_terminal_trade_logs import (
    audit, local_to_utc_ms, main, parse_body,
)

MADRID = ZoneInfo("Europe/Madrid")
OFFSET_MS = 10_800_000


def utc_ms(day: str, clock: str) -> int:
    local = datetime.strptime(f"{day} {clock}", "%Y%m%d %H:%M:%S.%f").replace(tzinfo=MADRID)
    return int(round(local.astimezone(timezone.utc).timestamp() * 1000))


def write_log(folder: Path, day: str, rows: list[tuple[str, str, str]]) -> Path:
    path = folder / f"{day}.log"
    lines = [f"XX\t0\t{clock}\t{source}\t{message}" for clock, source, message in rows]
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-16")
    return path


def write_deals(folder: Path, deals: list[dict]) -> Path:
    path = folder / "deals.json"
    path.write_text(json.dumps({"server": "Demo", "currency": "EUR",
                                "timestamp_semantics": "native broker timestamps", "deals": deals}),
                    encoding="utf-8")
    return path


def deal(ticket, order, position, day, clock, *, entry, reason, price, volume=0.04, delay_ms=0, kind=0):
    return {"ticket": ticket, "order": order, "position_id": position, "entry": entry, "reason": reason,
            "price": price, "volume": volume, "type": kind, "symbol": "XAUUSD",
            "time_msc": utc_ms(day, clock) + OFFSET_MS - delay_ms}


LOGIN = "'24767476': "


def test_market_chain_binds_deal_and_brackets_clock(tmp_path):
    day = "20260918"
    log = write_log(tmp_path, day, [
        ("13:05:41.578", "Trades", LOGIN + "market buy 0.04 XAUUSD sl: 4351.35"),
        ("13:05:43.118", "Trades", LOGIN + "accepted market buy 0.04 XAUUSD sl: 4351.35"),
        ("13:05:43.118", "Trades", LOGIN + "deal #1643180487 buy 0.04 XAUUSD at 4381.79 done (based on order #2046955499)"),
        ("13:05:43.154", "Trades", LOGIN + "order #2046955499 buy 0.04 / 0.04 XAUUSD at 4381.79 done in 1575.670 ms"),
    ])
    # Broker executes 30 ms before the terminal writes the deal line.
    deals = write_deals(tmp_path, [deal(1643180487, 2046955499, 2046955499, day, "13:05:43.118",
                                        entry=0, reason=3, price=4381.79, delay_ms=30)])
    result = audit([log], [deals])
    assert result["market_chains"] == {"total": 1, "with_request": 1, "done": 1, "server_rejected": 0,
                                       "unanswered": 0, "done_without_request_line": 0}
    assert result["per_day"][day]["market_roundtrip_ms"]["p50"] == pytest.approx(1575.670)
    clock = result["per_day"][day]["clock"]
    assert clock["status"] == "consistent"
    assert clock["upper_ms"] == 30
    assert clock["lower_ms"] == utc_ms(day, "13:05:41.578") - (utc_ms(day, "13:05:43.118") - 30)
    assert result["deal_binding"]["bound"] == 1
    assert result["deal_binding"]["broker_deals_missing_in_logs"] == []


def test_modify_chain_pairs_answers_and_separates_local_rejections(tmp_path):
    day = "20260917"
    log = write_log(tmp_path, day, [
        ("16:18:13.847", "Trades", LOGIN + "modify #2039419968 buy 0.03 XAUUSD sl: 4338.57, tp: 0.00 -> sl: 4338.35, tp: 4367.81"),
        ("16:18:24.359", "Trades", LOGIN + "failed modify #2039419968 buy 0.03 XAUUSD sl: 4338.57, tp: 0.00 -> sl: 4338.35, tp: 4367.81 [Invalid stops]"),
        ("16:18:25.399", "Trades", LOGIN + "failed modify #2039419968 buy 0.03 XAUUSD sl: 4338.57, tp: 0.00 -> sl: 4338.35, tp: 4367.81 [Invalid stops]"),
        ("16:18:36.121", "Trades", LOGIN + "modify #2039419968 buy 0.03 XAUUSD sl: 4338.57, tp: 0.00 -> sl: 4338.35, tp: 4367.81"),
        ("16:18:36.424", "Trades", LOGIN + "accepted modify #2039419968 buy 0.03 XAUUSD sl: 4338.57, tp: 0.00 -> sl: 4338.35, tp: 4367.81"),
        ("16:18:36.425", "Trades", LOGIN + "modify #2039419968 buy 0.03 XAUUSD -> sl: 4338.35, tp: 4367.81 done in 303.660 ms"),
    ])
    result = audit([log], [])
    assert result["modify_chains"]["total"] == 2
    assert result["modify_chains"]["server_rejected"] == 1
    assert result["modify_chains"]["done"] == 1
    assert result["local_rejections"] == {"market": 0, "modify": 1}
    day_stats = result["per_day"][day]
    assert day_stats["server_rejections"] == {"modify_failed:Invalid stops": 1}
    assert day_stats["local_rejections"] == {"modify_failed:Invalid stops": 1}
    assert day_stats["modify_roundtrip_ms"]["n"] == 1
    assert day_stats["modify_accepted_to_done_ms"]["p50"] == 1


def test_local_market_rejection_burst_is_reported(tmp_path):
    day = "20260916"
    rows = [(f"16:02:51.{100 + i * 20:03d}", "Trades",
             LOGIN + "failed market buy 0.03 XAUUSD sl: 4302.43 [No money]") for i in range(3)]
    result = audit([write_log(tmp_path, day, rows)], [])
    assert result["local_rejections"]["market"] == 3
    burst = result["rejection_bursts"][0]
    assert (burst["kind"], burst["reason"], burst["count"], burst["median_gap_ms"]) == (
        "market_failed", "No money", 3, 20)


def test_close_and_missing_position_variants_are_parsed():
    close = parse_body("Trades", LOGIN + "market buy 0.03 XAUUSD, close #2046955499 sell 0.03 XAUUSD 4381.79")
    assert close["kind"] == "market_request"
    assert (close["close_position"], close["close_side"], close["close_price"]) == (2046955499, "sell", 4381.79)
    missing = parse_body("Trades", LOGIN + "failed modify #0 buy 0  sl: 0.00, tp: 0.00 -> sl: 4350.12, tp: 4380.25 [Position doesn't exist]")
    assert missing["kind"] == "modify_failed"
    assert (missing["position"], missing["symbol"], missing["reason"]) == (0, None, "Position doesn't exist")
    other = parse_body("Trades", LOGIN + "a message the parser does not know")
    assert other["kind"] == "other" and "login_sha12" in other and "24767476" not in json.dumps(other)


def test_tp_exit_uses_installed_level_and_entry_to_protection_time(tmp_path):
    day = "20260917"
    log = write_log(tmp_path, day, [
        ("15:45:58.730", "Trades", LOGIN + "market sell 0.04 XAUUSD sl: 4388.94"),
        ("15:45:59.042", "Trades", LOGIN + "accepted market sell 0.04 XAUUSD sl: 4388.94"),
        ("15:45:59.042", "Trades", LOGIN + "deal #1638407263 sell 0.04 XAUUSD at 4358.85 done (based on order #2039085783)"),
        ("15:45:59.042", "Trades", LOGIN + "order #2039085783 sell 0.04 / 0.04 XAUUSD at 4358.85 done in 312.082 ms"),
        ("15:45:59.066", "Trades", LOGIN + "modify #2039085783 sell 0.04 XAUUSD sl: 4388.94, tp: 0.00 -> sl: 4388.85, tp: 4358.35"),
        ("15:45:59.597", "Trades", LOGIN + "accepted modify #2039085783 sell 0.04 XAUUSD sl: 4388.94, tp: 0.00 -> sl: 4388.85, tp: 4358.35"),
        ("15:45:59.623", "Trades", LOGIN + "modify #2039085783 sell 0.04 XAUUSD -> sl: 4388.85, tp: 4358.35 done in 557.200 ms"),
        ("15:46:01.846", "Trades", LOGIN + "deal #1638408009 buy 0.04 XAUUSD at 4358.35 done (based on order #2039086758)"),
    ])
    deals = write_deals(tmp_path, [
        deal(1638407263, 2039085783, 2039085783, day, "15:45:59.042", entry=0, reason=3,
             price=4358.85, kind=1, delay_ms=221),
        deal(1638408009, 2039086758, 2039085783, day, "15:46:01.846", entry=1, reason=5,
             price=4358.35, kind=0, delay_ms=399),
    ])
    result = audit([log], [deals])
    assert result["tp_exits"] == {"total": 1, "level_matches_price": 1, "install_done_before_exit_line": 1}
    assert result["overall"]["first_tp_done_after_entry_ms"]["p50"] == 581
    assert result["_positions"][0]["exits"][0]["install_request_utc_ms"] == utc_ms(day, "15:45:59.066")
    assert result["market_chains"]["done_without_request_line"] == 0


def test_network_events_ping_and_episode(tmp_path):
    day = "20260918"
    log = write_log(tmp_path, day, [
        ("10:00:00.000", "Network", LOGIN + "connection to VantageMarkets-Demo lost"),
        ("10:00:05.500", "Network", LOGIN + "authorized on VantageMarkets-Demo through AS03 (ping: 115.25 ms, build 5830)"),
        ("10:00:06.000", "Network", LOGIN + "previous successful authorization performed from 1.2.3.4 on 2026.09.18 13:00:00"),
        ("10:00:07.000", "Trades", LOGIN + "use MetaTrader VPS Hosting Service to speed up the execution: 1.38 ms via 'VPS Ashburn DC2 03' instead of 114.71 ms"),
    ])
    result = audit([log], [])
    stats = result["per_day"][day]
    assert stats["network"] == {"connection_lost": 1, "authorized": 1}
    assert stats["ping_ms"]["p50"] == 115.25
    assert result["network_episodes"][0]["duration_ms"] == 5500
    assert result["vps_hints"] == [{"day": day, "vps": "VPS Ashburn DC2 03", "vps_ms": 1.38, "current_ms": 114.71}]
    assert "1.2.3.4" not in json.dumps(result["_events"])


def test_local_clock_conversion_and_dst_ambiguity():
    assert local_to_utc_ms(datetime(2026, 9, 18).date(), "12:00:00.000", MADRID) == (
        int(datetime(2026, 9, 18, 10, tzinfo=timezone.utc).timestamp() * 1000), None)
    _, flag = local_to_utc_ms(datetime(2026, 10, 25).date(), "02:30:00.000", MADRID)
    assert flag == "ambiguous_local_time"


def test_cli_writes_immutable_outputs(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    write_log(logs, "20260915", [("12:00:00.000", "Trades", LOGIN + "market buy 0.01 XAUUSD sl: 4000.00")])
    output = tmp_path / "out"
    assert main(["--log-dir", str(logs), "--output-dir", str(output)]) == 0
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "diagnostic_only"
    assert set(summary["outputs_sha256"]) == {"events.jsonl.gz", "positions.json"}
    with gzip.open(output / "events.jsonl.gz", "rt", encoding="utf-8") as handle:
        assert json.loads(handle.readline())["kind"] == "market_request"
    assert summary["market_chains"]["unanswered"] == 1
    with pytest.raises(FileExistsError):
        main(["--log-dir", str(logs), "--output-dir", str(output)])


def test_duplicate_day_is_rejected(tmp_path):
    first = write_log(tmp_path, "20260915", [("12:00:00.000", "Trades", LOGIN + "x")])
    other = tmp_path / "copy"
    other.mkdir()
    second = write_log(other, "20260915", [("12:00:00.000", "Trades", LOGIN + "x")])
    with pytest.raises(ValueError, match="duplicate terminal log day"):
        audit([first, second], [])
