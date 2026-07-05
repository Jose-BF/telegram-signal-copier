"""
ledger_report.py — Métricas sobre el ledger reconciliado.

Lee `data/ledger.jsonl` (generado por reconcile_mt5_ledger.py) y produce el informe
que antes requeria una auditoria manual de 2 horas:

  • P&L real por dia / canal / direccion (verificado contra MT5)
  • Win rate y ratio R
  • Discrepancias journal vs MT5 pendientes
  • Huerfanos sin registrar
  • Potencial perdido — cuanto upside dejamos (max TP tocado vs capturado)

USO
───
  python ledger_report.py                  # informe completo
  python ledger_report.py --since 2026-05-01

El ledger es la FUENTE DE VERDAD. Este report nunca toca el journal crudo.
"""

import json
import sys
import subprocess
import statistics
from collections import defaultdict
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

LEDGER_FILE = Path(__file__).parent / "data" / "ledger.jsonl"


def parse_ledger_text(text: str) -> list:
    return [json.loads(l) for l in text.splitlines() if l.strip()]


def load_ledger(path: Path) -> list:
    if not path.exists():
        print(f"ERROR: no existe {path}. Ejecuta primero: python reconcile_mt5_ledger.py")
        sys.exit(1)
    return parse_ledger_text(path.read_text(encoding="utf-8"))


def load_ledger_from_git_ref(ref: str,
                             ledger_path: str = "data/ledger.jsonl") -> list:
    proc = subprocess.run(
        ["git", "show", f"{ref}:{ledger_path}"],
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        print(f"ERROR: no pude leer {ledger_path} desde {ref}: "
              f"{proc.stderr.strip()}")
        sys.exit(proc.returncode)
    return parse_ledger_text(proc.stdout)


def metric_universe(rows: list, *, include_excluded: bool = False) -> tuple[list, list]:
    excluded = [r for r in rows if r.get("analysis_excluded")]
    if include_excluded:
        return rows, excluded
    return [r for r in rows if not r.get("analysis_excluded")], excluded


def close_day(row: dict) -> str:
    return ((row.get("close_dt_utc") or row.get("signal_dt_utc") or "")[:10]
            or "unknown")


def _in_close_range(row: dict, since: str | None, until: str | None) -> bool:
    day = close_day(row)
    if day == "unknown":
        return False
    if since and day < since:
        return False
    if until and day > until:
        return False
    return True


def _money(value) -> float:
    return float(value or 0.0)


def normalize_flag(flag: str) -> str:
    if flag.startswith("PNL_DISCREPANCY"):
        return "PNL_DISCREPANCY"
    if flag.startswith("MT5_AUTOTRADING_DISABLED"):
        return "MT5_AUTOTRADING_DISABLED"
    if flag.startswith("HUERFANO_journal_sin_signal_closed"):
        return "HUERFANO_journal_sin_signal_closed"
    return flag


def summarize_closed_pnl(rows: list, *,
                         since: str | None = None,
                         until: str | None = None,
                         include_excluded: bool = False) -> dict:
    """Summarize closed trades using MT5-reconciled P&L as source of truth."""
    scoped_rows = list(rows)
    if since or until:
        scoped_rows = [
            r for r in scoped_rows
            if _in_close_range(r, since, until)
        ]

    metric_rows, excluded_rows = metric_universe(
        scoped_rows, include_excluded=include_excluded)

    closed = [r for r in metric_rows if r.get("status") == "closed"]
    reliable = [r for r in closed if r.get("pnl_mt5_complete")]
    partial = [r for r in closed if not r.get("pnl_mt5_complete")]
    open_rows = [r for r in metric_rows if r.get("status") == "open"]

    by_day = defaultdict(lambda: {
        "n": 0,
        "mt5": 0.0,
        "journal_nonnull": 0.0,
        "journal_n": 0,
    })
    total_mt5 = 0.0
    total_journal_nonnull = 0.0
    close_days = []

    for row in reliable:
        day = close_day(row)
        close_days.append(day)
        mt5_pnl = _money(row.get("pnl_real_mt5"))
        total_mt5 += mt5_pnl
        by_day[day]["n"] += 1
        by_day[day]["mt5"] += mt5_pnl

        if row.get("pnl_journal") is not None:
            journal_pnl = _money(row.get("pnl_journal"))
            total_journal_nonnull += journal_pnl
            by_day[day]["journal_nonnull"] += journal_pnl
            by_day[day]["journal_n"] += 1

    rounded_by_day = {}
    for day, values in by_day.items():
        rounded_by_day[day] = {
            "n": values["n"],
            "mt5": round(values["mt5"], 2),
            "journal_nonnull": round(values["journal_nonnull"], 2),
            "journal_n": values["journal_n"],
        }

    discrepancies = [
        r for r in reliable
        if r.get("reconciled_ok") is False
        or not r.get("journal_has_signal_closed", True)
    ]
    orphan_rows = [
        r for r in closed
        if not r.get("journal_has_signal_closed", True)
    ]
    known_days = sorted(d for d in close_days if d != "unknown")
    flag_counts = defaultdict(int)
    for row in scoped_rows:
        for flag in row.get("flags", []):
            flag_counts[normalize_flag(str(flag))] += 1

    total_mt5 = round(total_mt5, 2)
    total_journal_nonnull = round(total_journal_nonnull, 2)
    return {
        "scoped_rows": scoped_rows,
        "metric_rows": metric_rows,
        "excluded_rows": excluded_rows,
        "closed_rows": closed,
        "reliable_rows": reliable,
        "partial_rows": partial,
        "open_rows": open_rows,
        "total_mt5": total_mt5,
        "total_journal_nonnull": total_journal_nonnull,
        "journal_gap": round(total_mt5 - total_journal_nonnull, 2),
        "by_close_day": rounded_by_day,
        "discrepancies": discrepancies,
        "orphan_rows": orphan_rows,
        "flag_counts": dict(sorted(flag_counts.items())),
        "first_close_day": known_days[0] if known_days else None,
        "last_close_day": known_days[-1] if known_days else None,
    }


def main():
    since = None
    until = None
    git_ref = None
    include_excluded = "--include-excluded" in sys.argv
    for i, a in enumerate(sys.argv):
        if a == "--since" and i + 1 < len(sys.argv):
            since = sys.argv[i + 1]
        if a == "--until" and i + 1 < len(sys.argv):
            until = sys.argv[i + 1]
        if a == "--git-ref" and i + 1 < len(sys.argv):
            git_ref = sys.argv[i + 1]

    if git_ref:
        rows = load_ledger_from_git_ref(git_ref)
        ledger_source = f"git:{git_ref}:data/ledger.jsonl"
    else:
        rows = load_ledger(LEDGER_FILE)
        ledger_source = str(LEDGER_FILE)

    summary = summarize_closed_pnl(
        rows, since=since, until=until, include_excluded=include_excluded)
    metric_rows = summary["metric_rows"]
    excluded_rows = summary["excluded_rows"]

    # Universo de analisis: trades cerrados y reconciliables (P&L fiable).
    closed = summary["closed_rows"]
    fiables = summary["reliable_rows"]
    parciales = summary["partial_rows"]
    open_r = summary["open_rows"]

    print("=" * 72)
    range_label = ""
    if since or until:
        range_label = f"  (cierre {since or 'inicio'} -> {until or 'fin'})"
    print("  INFORME DEL LEDGER RECONCILIADO" + range_label)
    print("=" * 72)
    print(f"\nLedger source: {ledger_source}")
    print(f"Trades en el ledger: {len(rows)}")
    print(f"  Cerrados reconciliables: {len(fiables)}  "
          f"(P&L MT5 verificado y completo)")
    print(f"  Cerrados formato viejo:  {len(parciales)}  "
          f"(P&L parcial — no entran a las metricas)")
    print(f"  Abiertos:                {len(open_r)}")
    if excluded_rows:
        suffix = " (incluidos por --include-excluded)" if include_excluded else ""
        print(f"  Excluidos estrategia:    {len(excluded_rows)}{suffix}")
    if summary["first_close_day"] or summary["last_close_day"]:
        print(f"  Cobertura cierres MT5:   {summary['first_close_day']} -> "
              f"{summary['last_close_day']}")

    if not fiables:
        print("\nSin trades reconciliables en el rango. Nada que medir.")
        return

    # ── P&L global ───────────────────────────────────────────────────────
    pnls = [r["pnl_real_mt5"] for r in fiables]
    total = summary["total_mt5"]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    wr = len(wins) / len(pnls) * 100 if pnls else 0
    avg_w = statistics.mean(wins) if wins else 0
    avg_l = statistics.mean(losses) if losses else 0
    print(f"\n{'─'*72}")
    print(f"P&L REAL (verificado MT5)")
    print(f"{'─'*72}")
    print(f"  Total:        ${total:+.2f}")
    print(f"  Journal ctrl: ${summary['total_journal_nonnull']:+.2f}  "
          f"gap MT5-journal: ${summary['journal_gap']:+.2f}")
    print(f"  Trades:       {len(pnls)}   WR: {wr:.0f}%")
    print(f"  Avg win:      ${avg_w:+.2f}   Avg loss: ${avg_l:+.2f}")
    if avg_l != 0:
        rwl = abs(avg_w / avg_l)
        breakeven = 1 / (1 + rwl) * 100
        print(f"  Ratio W/L:    {rwl:.2f}x   Breakeven WR: {breakeven:.0f}%")

    # ── Por dia ──────────────────────────────────────────────────────────
    by_day = defaultdict(list)
    for r in fiables:
        day = close_day(r)
        by_day[day].append(r["pnl_real_mt5"])
    print(f"\n{'─'*72}")
    print(f"P&L POR DIA")
    print(f"{'─'*72}")
    for day in sorted(by_day):
        dp = by_day[day]
        print(f"  {day}:  ${sum(dp):+8.2f}  ({len(dp)} trades, "
              f"WR {sum(1 for p in dp if p>0)/len(dp)*100:.0f}%)")

    # ── Por canal y direccion ────────────────────────────────────────────
    print(f"\n{'─'*72}")
    print(f"POR CANAL x DIRECCION")
    print(f"{'─'*72}")
    by_cd = defaultdict(list)
    for r in fiables:
        by_cd[(r["channel"], r.get("direction") or "?")].append(r["pnl_real_mt5"])
    for (ch, d), dp in sorted(by_cd.items()):
        print(f"  {ch} {d:4s}:  ${sum(dp):+8.2f}  ({len(dp)} trades, "
              f"WR {sum(1 for p in dp if p>0)/len(dp)*100:.0f}%)")

    # ── Potencial perdido (max TP tocado vs capturado) ──────────────────
    print(f"\n{'─'*72}")
    print(f"POTENCIAL — hasta donde llego el precio vs cuanto cobramos")
    print(f"{'─'*72}")
    con_tphit = [r for r in fiables if r.get("max_tp_idx_touched") is not None]
    if con_tphit:
        dist = defaultdict(int)
        for r in con_tphit:
            dist[r["max_tp_idx_touched"]] += 1
        for idx in sorted(dist):
            print(f"  precio llego hasta TP{idx+1}: {dist[idx]} trades")
        print(f"  (cruza esto con el P&L para ver cuanto upside se dejo —"
              f" un trade que toco TP5 y cerro en TP1 dejo dinero en la mesa)")
    else:
        print("  Sin eventos tp_hit en el rango.")

    # ── Discrepancias journal vs MT5 ─────────────────────────────────────
    print(f"\n{'─'*72}")
    print(f"INTEGRIDAD — discrepancias journal vs MT5")
    print(f"{'─'*72}")
    discrep = summary["discrepancies"]
    if discrep:
        print(f"  ⚠ {len(discrep)} trades donde el bot registro un P&L "
              f"distinto al real:")
        for r in discrep:
            bot_pnl = (
                "None" if r.get("pnl_journal") is None
                else f"${r['pnl_journal']:+.2f}"
            )
            error_pnl = (
                "None" if r.get("pnl_discrepancy") is None
                else f"${r['pnl_discrepancy']:+.2f}"
            )
            print(f"    {r['sig_id']:22s} bot={bot_pnl:>8s}  "
                  f"real=${r['pnl_real_mt5']:+.2f}  "
                  f"error={error_pnl:>8s}")
    else:
        print(f"  ✓ El journal del bot coincide con MT5 en todos los "
              f"trades reconciliables.")

    # ── Huerfanos ────────────────────────────────────────────────────────
    huerfanos = [r for r in closed if not r.get("journal_has_signal_closed")]
    if huerfanos:
        h_fiables = [r for r in huerfanos if r.get("pnl_mt5_complete")]
        pnl_h = sum(r["pnl_real_mt5"] for r in h_fiables)
        print(f"\n  ⚠ {len(huerfanos)} huerfanos (cerraron en MT5, el bot no lo "
              f"registro). P&L real no contabilizado por el bot: ${pnl_h:+.2f}")

    # ── Flags acumulados ─────────────────────────────────────────────────
    flag_counter = summary["flag_counts"]
    if flag_counter:
        print(f"\n{'─'*72}")
        print(f"FLAGS detectados en el ledger")
        print(f"{'─'*72}")
        for flag, n in sorted(flag_counter.items(), key=lambda x: -x[1]):
            print(f"  {flag:42s} {n}")

    print()


if __name__ == "__main__":
    main()
