"""Validate replay trades against MT5 accounting truth.

The first replay layer is deliberately accounting-only: it sums the MT5 net
PnL already reconciled per ticket and compares it with the trade-level MT5 PnL.
Tick-by-tick strategy replay comes later, after this layer proves that the
economic truth can be reconstructed to the cent.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable

import runtime_paths

DATA_DIR = runtime_paths.active_data_dir()
DEFAULT_REPLAY_FILE = DATA_DIR / "replay_trades.jsonl"
DEFAULT_AUDIT_FILE = DATA_DIR / "accounting_replay_audit.jsonl"
SCHEMA_VERSION = 1
CENT = Decimal("0.01")


def _money(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _as_json_money(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value.quantize(CENT, rounding=ROUND_HALF_UP))


def _dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _ticket_label(ticket: dict) -> str:
    for key in ("ticket", "position_ticket", "position_id", "deal_ticket"):
        value = ticket.get(key)
        if value is not None:
            try:
                return str(int(value))
            except (TypeError, ValueError):
                return str(value)
    return "unknown"


def _estimate_ticket_pnl(
    trade: dict,
    ticket: dict,
    *,
    contract_size: Decimal,
) -> Decimal | None:
    direction = str(trade.get("direction") or "").upper()
    open_price = _decimal(ticket.get("open_price"))
    close_price = _decimal(ticket.get("close_price"))
    volume = _decimal(ticket.get("volume"))
    if direction not in {"BUY", "SELL"}:
        return None
    if open_price is None or close_price is None or volume is None:
        return None
    points = close_price - open_price if direction == "BUY" else open_price - close_price
    return (points * volume * contract_size).quantize(CENT, rounding=ROUND_HALF_UP)


def _ticket_pnls(
    trade: dict,
    *,
    allow_price_estimates: bool,
    contract_size: Decimal,
) -> tuple[list[Decimal], list[str], list[str], bool]:
    pnls: list[Decimal] = []
    blockers: list[str] = []
    assumptions: list[str] = []
    used_estimate = False

    status = trade.get("status")
    tickets = list(trade.get("tickets") or [])
    if status != "no_position" and not tickets:
        blockers.append("missing_tickets")
        return pnls, blockers, assumptions, used_estimate

    for ticket in tickets:
        label = _ticket_label(ticket)
        if ticket.get("ticket") is None:
            blockers.append(f"missing_ticket_id:{label}")
        if ticket.get("is_closed") is False:
            blockers.append(f"ticket_still_open:{label}")
        if ticket.get("open_dt_utc") is None or ticket.get("open_price") is None:
            blockers.append(f"missing_ticket_open:{label}")
        if ticket.get("is_closed") and (
            ticket.get("close_dt_utc") is None or ticket.get("close_price") is None
        ):
            blockers.append(f"missing_ticket_close:{label}")

        pnl = _money(ticket.get("pnl_net"))
        if pnl is not None:
            pnls.append(pnl)
            continue

        if allow_price_estimates:
            estimate = _estimate_ticket_pnl(
                trade,
                ticket,
                contract_size=contract_size,
            )
            if estimate is not None:
                pnls.append(estimate)
                assumptions.append(f"price_formula_pnl_estimate:{label}")
                used_estimate = True
                continue

        blockers.append(f"missing_ticket_pnl:{label}")

    return pnls, blockers, assumptions, used_estimate


def _trade_assumptions(trade: dict) -> list[str]:
    assumptions: list[str] = []
    if trade.get("pnl_real_mt5_source") == "positions_closed_by_mt5":
        assumptions.append("mt5_closure_event_fallback")
    if trade.get("status") == "closed" and trade.get("journal_has_signal_closed") is False:
        assumptions.append("journal_missing_signal_closed")
    if trade.get("pnl_journal") is None:
        assumptions.append("journal_pnl_missing")

    discrepancy = _money(trade.get("pnl_discrepancy"))
    if discrepancy is not None and discrepancy != Decimal("0.00"):
        assumptions.append("journal_pnl_discrepancy")
    if trade.get("reconciled_ok") is False:
        assumptions.append("journal_reconcile_failed")

    health = trade.get("health")
    if health and health != "ok":
        assumptions.append(f"journal_health_{health}")

    for gap in trade.get("gaps") or []:
        if gap == "missing_signal_closed":
            continue
        assumptions.append(f"replay_gap:{gap}")

    for flag in trade.get("flags") or []:
        assumptions.append(f"flag:{flag}")

    return _dedupe(assumptions)


def _classify(
    *,
    blockers: list[str],
    assumptions: list[str],
    diff: Decimal | None,
    used_estimate: bool,
) -> tuple[str, str, str]:
    if blockers:
        return "blocked", "none", "blocked"
    if used_estimate:
        return "estimated", "low", "exploratory"
    if diff != Decimal("0.00"):
        return "mismatch", "low", "review"
    if assumptions:
        return "reconstructed", "medium", "review"
    return "exact", "high", "strict"


def validate_trade(
    trade: dict,
    *,
    allow_price_estimates: bool = False,
    contract_size: int | float | str | Decimal = 100,
) -> dict:
    """Return one simulation audit row for one replay trade."""
    real_pnl = _money(trade.get("pnl_real_mt5"))
    blockers: list[str] = []
    assumptions: list[str] = []

    if not trade.get("sig_id"):
        blockers.append("missing_sig_id")
    if real_pnl is None:
        blockers.append("missing_real_pnl_mt5")
    if trade.get("pnl_mt5_complete") is False:
        blockers.append("pnl_mt5_incomplete")

    contract_size_decimal = _decimal(contract_size) or Decimal("100")
    ticket_pnls, ticket_blockers, ticket_assumptions, used_estimate = _ticket_pnls(
        trade,
        allow_price_estimates=allow_price_estimates,
        contract_size=contract_size_decimal,
    )
    blockers.extend(ticket_blockers)
    assumptions.extend(ticket_assumptions)
    assumptions.extend(_trade_assumptions(trade))

    replayed_pnl = None
    if not ticket_blockers:
        replayed_pnl = sum(ticket_pnls, Decimal("0.00")).quantize(
            CENT,
            rounding=ROUND_HALF_UP,
        )
    if trade.get("status") == "no_position" and not trade.get("tickets"):
        replayed_pnl = Decimal("0.00")

    diff = None
    if real_pnl is not None and replayed_pnl is not None:
        diff = (real_pnl - replayed_pnl).quantize(CENT, rounding=ROUND_HALF_UP)

    blockers = _dedupe(blockers)
    assumptions = _dedupe(assumptions)
    status, confidence, optimization_bucket = _classify(
        blockers=blockers,
        assumptions=assumptions,
        diff=diff,
        used_estimate=used_estimate,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "sig_id": trade.get("sig_id"),
        "channel": trade.get("channel"),
        "direction": trade.get("direction"),
        "signal_dt_utc": trade.get("signal_dt_utc"),
        "open_dt_utc": trade.get("open_dt_utc"),
        "close_dt_utc": trade.get("close_dt_utc"),
        "stage": "accounting_replay",
        "real_pnl_mt5": _as_json_money(real_pnl),
        "replayed_pnl": _as_json_money(replayed_pnl),
        "diff": _as_json_money(diff),
        "status": status,
        "confidence": confidence,
        "optimization_bucket": optimization_bucket,
        "assumptions": assumptions,
        "blockers": blockers,
        "ticket_count": len(trade.get("tickets") or []),
    }


def validate_trades(
    trades: Iterable[dict],
    *,
    allow_price_estimates: bool = False,
    contract_size: int | float | str | Decimal = 100,
) -> list[dict]:
    return [
        validate_trade(
            trade,
            allow_price_estimates=allow_price_estimates,
            contract_size=contract_size,
        )
        for trade in trades
    ]


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def write_jsonl(rows: Iterable[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def summarize(audits: Iterable[dict]) -> dict:
    audits = list(audits)
    counts = Counter(row.get("status") for row in audits)
    return {
        "total": len(audits),
        "exact": counts.get("exact", 0),
        "reconstructed": counts.get("reconstructed", 0),
        "estimated": counts.get("estimated", 0),
        "mismatch": counts.get("mismatch", 0),
        "blocked": counts.get("blocked", 0),
    }


def _print_summary(audits: list[dict], output: Path) -> None:
    summary = summarize(audits)
    print(f"Simulation audit: {summary['total']} trades")
    for key in ("exact", "reconstructed", "estimated", "mismatch", "blocked"):
        print(f"{key}: {summary[key]}")
    print(f"Output: {output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate replay_trades.jsonl against MT5 accounting PnL"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_REPLAY_FILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_AUDIT_FILE)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument(
        "--allow-price-estimates",
        action="store_true",
        help="Estimate missing ticket PnL from open/close price and volume.",
    )
    parser.add_argument("--contract-size", default="100")
    args = parser.parse_args(argv)

    trades = load_jsonl(args.input)
    audits = validate_trades(
        trades,
        allow_price_estimates=args.allow_price_estimates,
        contract_size=args.contract_size,
    )
    write_jsonl(audits, args.output)

    if not args.quiet:
        _print_summary(audits, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
