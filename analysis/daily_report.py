"""Honest daily accounting report for the Telegram signal copier.

The report keeps two different questions separate:

* signal cohort: P&L from signals first observed on the requested UTC day;
* MT5 server calendar: P&L from positions whose raw broker deal timestamp
  belongs to that server-calendar day.

These totals can legitimately differ when an older signal closes after the
broker rolls its trading day. Neither value is a provider pip summary.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import runtime_paths


DATA_DIR = runtime_paths.active_data_dir(ROOT)
DEFAULT_LEDGER = DATA_DIR / "ledger.jsonl"
DEFAULT_ACCOUNTING = DATA_DIR / "accounting_replay_audit.jsonl"
DEFAULT_EVENTS = DATA_DIR / "trade_events.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _date_part(value: object) -> str | None:
    text = str(value or "")
    return text[:10] if len(text) >= 10 else None


def _money(value: float) -> float:
    return round(float(value), 2)


def _currency_evidence(events: Iterable[dict], report_date: str) -> tuple[str | None, str | None]:
    candidates = [
        row for row in events
        if row.get("ev") == "mt5_account_connected"
        and row.get("currency")
        and (_date_part(row.get("ts")) or "9999-99-99") <= report_date
    ]
    if not candidates:
        return None, None
    latest = max(candidates, key=lambda row: str(row.get("ts") or ""))
    return str(latest["currency"]), "mt5_account_connected"


def _signal_cohort(
    report_date: str,
    ledger_rows: list[dict],
    accounting_by_signal: dict[str, str],
) -> dict:
    rows = [
        row for row in ledger_rows
        if _date_part(row.get("signal_dt_utc")) == report_date
    ]
    known_pnl = [
        float(row["pnl_real_mt5"])
        for row in rows
        if row.get("pnl_real_mt5") is not None
    ]
    wins = losses = breakevens = unclassified = 0
    exact = reconstructed = 0
    by_channel: dict[str, dict] = {}

    for row in rows:
        signal = str(row.get("sig_id") or "")
        accounting_status = accounting_by_signal.get(signal, "unverified")
        pnl = row.get("pnl_real_mt5")
        if accounting_status == "exact":
            exact += 1
            if pnl is None:
                unclassified += 1
            elif float(pnl) > 0:
                wins += 1
            elif float(pnl) < 0:
                losses += 1
            else:
                breakevens += 1
        else:
            if accounting_status == "reconstructed":
                reconstructed += 1
            unclassified += 1

        channel = str(row.get("channel") or "unknown")
        bucket = by_channel.setdefault(channel, {"signals": 0, "pnl": 0.0})
        bucket["signals"] += 1
        if pnl is not None:
            bucket["pnl"] += float(pnl)

    for bucket in by_channel.values():
        bucket["pnl"] = _money(bucket["pnl"])

    return {
        "signals": len(rows),
        "pnl": _money(sum(known_pnl)),
        "pnl_complete": len(known_pnl) == len(rows),
        "exact_accounting": exact,
        "reconstructed_accounting": reconstructed,
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "unclassified_outcomes": unclassified,
        "by_channel": dict(sorted(by_channel.items())),
    }


def _server_calendar(report_date: str, ledger_rows: list[dict]) -> dict:
    """Aggregate unique closed positions using raw MT5 server-epoch dates.

    Nested ``close_deal.time_utc`` in retained ledgers is the broker server
    clock encoded as an epoch. Its date component is therefore the same date
    shown by MT5 history, even though it must not be used as true UTC for tick
    replay. The field is intentionally labelled as source evidence here.
    """
    seen_positions: set[object] = set()
    closed_positions = 0
    pnl = 0.0
    contributing_signals: set[str] = set()

    for trade in ledger_rows:
        for position in trade.get("positions") or []:
            close_deal = position.get("close_deal") or {}
            if _date_part(close_deal.get("time_utc")) != report_date:
                continue
            identity = (
                position.get("position_id")
                or position.get("ticket")
                or close_deal.get("position_id")
                or close_deal.get("ticket")
            )
            if identity is None or identity in seen_positions:
                continue
            seen_positions.add(identity)
            closed_positions += 1
            contributing_signals.add(str(trade.get("sig_id") or "unknown"))
            if position.get("pnl_net") is not None:
                pnl += float(position["pnl_net"])

    return {
        "closed_positions": closed_positions,
        "contributing_signals": len(contributing_signals),
        "pnl": _money(pnl),
        "time_basis": "mt5_raw_server_epoch_date",
    }


def build_daily_report(
    report_date: str,
    ledger_rows: Iterable[dict],
    *,
    accounting_rows: Iterable[dict] = (),
    events: Iterable[dict] = (),
) -> dict:
    ledger_rows = list(ledger_rows)
    accounting_by_signal = {
        str(row.get("sig_id") or ""): str(row.get("status") or "unverified").lower()
        for row in accounting_rows
        if row.get("sig_id")
    }
    currency, currency_source = _currency_evidence(events, report_date)
    cohort = _signal_cohort(report_date, ledger_rows, accounting_by_signal)
    server = _server_calendar(report_date, ledger_rows)
    return {
        "schema_version": 1,
        "date": report_date,
        "currency": currency,
        "currency_source": currency_source,
        "currency_verified": currency is not None,
        "signal_cohort_pnl": cohort["pnl"],
        "server_calendar_pnl": server["pnl"],
        "signal_cohort": cohort,
        "server_calendar": server,
        "difference_explanation": (
            "Server-calendar P&L can include positions from signals that began "
            "on an earlier day. Signal-cohort P&L follows signal start time."
        ),
    }


def _format_money(value: float, currency: str | None) -> str:
    suffix = currency or "currency unknown"
    return f"{value:+.2f} {suffix}"


def render_report(report: dict) -> str:
    cohort = report["signal_cohort"]
    server = report["server_calendar"]
    currency = report.get("currency")
    lines = [
        f"REPORTE DIARIO - {report['date']}",
        "",
        "Senales originadas ese dia (UTC)",
        f"  Senales: {cohort['signals']}",
        f"  P&L: {_format_money(cohort['pnl'], currency)}",
        (
            "  Resultados exactos W/L/BE: "
            f"{cohort['wins']}/{cohort['losses']}/{cohort['breakevens']}"
        ),
        f"  Sin clasificar como win/loss: {cohort['unclassified_outcomes']}",
        "",
        "Cierres mostrados por el calendario servidor MT5",
        f"  Posiciones cerradas: {server['closed_positions']}",
        f"  P&L: {_format_money(server['pnl'], currency)}",
    ]
    if not report["currency_verified"]:
        lines.extend((
            "",
            "AVISO: no existe evidencia de moneda de cuenta en los logs; "
            "no se etiqueta el P&L como USD ni EUR.",
        ))
    if cohort["pnl"] != server["pnl"]:
        lines.extend(("", report["difference_explanation"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Honest daily MT5 P&L report")
    parser.add_argument(
        "date",
        nargs="?",
        default=datetime.now().strftime("%Y-%m-%d"),
    )
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--accounting", type=Path, default=DEFAULT_ACCOUNTING)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_daily_report(
        args.date,
        load_jsonl(args.ledger),
        accounting_rows=load_jsonl(args.accounting),
        events=load_jsonl(args.events),
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
