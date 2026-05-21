"""
ledger_report.py — Métricas sobre el ledger reconciliado.

Lee `data/ledger.jsonl` (generado por reconcile.py) y produce el informe
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
import statistics
from collections import defaultdict
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

LEDGER_FILE = Path(__file__).parent / "data" / "ledger.jsonl"


def load_ledger(path: Path) -> list:
    if not path.exists():
        print(f"ERROR: no existe {path}. Ejecuta primero: python reconcile.py")
        sys.exit(1)
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def main():
    since = None
    for i, a in enumerate(sys.argv):
        if a == "--since" and i + 1 < len(sys.argv):
            since = sys.argv[i + 1]

    rows = load_ledger(LEDGER_FILE)
    if since:
        rows = [r for r in rows if (r.get("signal_dt_utc") or "") >= since]

    # Universo de analisis: trades cerrados y reconciliables (P&L fiable).
    closed = [r for r in rows if r["status"] == "closed"]
    fiables = [r for r in closed if r.get("pnl_mt5_complete")]
    parciales = [r for r in closed if not r.get("pnl_mt5_complete")]
    open_r = [r for r in rows if r["status"] == "open"]

    print("=" * 72)
    print("  INFORME DEL LEDGER RECONCILIADO" + (f"  (desde {since})" if since else ""))
    print("=" * 72)
    print(f"\nTrades en el ledger: {len(rows)}")
    print(f"  Cerrados reconciliables: {len(fiables)}  "
          f"(P&L MT5 verificado y completo)")
    print(f"  Cerrados formato viejo:  {len(parciales)}  "
          f"(P&L parcial — no entran a las metricas)")
    print(f"  Abiertos:                {len(open_r)}")

    if not fiables:
        print("\nSin trades reconciliables en el rango. Nada que medir.")
        return

    # ── P&L global ───────────────────────────────────────────────────────
    pnls = [r["pnl_real_mt5"] for r in fiables]
    total = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    wr = len(wins) / len(pnls) * 100 if pnls else 0
    avg_w = statistics.mean(wins) if wins else 0
    avg_l = statistics.mean(losses) if losses else 0
    print(f"\n{'─'*72}")
    print(f"P&L REAL (verificado MT5)")
    print(f"{'─'*72}")
    print(f"  Total:        ${total:+.2f}")
    print(f"  Trades:       {len(pnls)}   WR: {wr:.0f}%")
    print(f"  Avg win:      ${avg_w:+.2f}   Avg loss: ${avg_l:+.2f}")
    if avg_l != 0:
        rwl = abs(avg_w / avg_l)
        breakeven = 1 / (1 + rwl) * 100
        print(f"  Ratio W/L:    {rwl:.2f}x   Breakeven WR: {breakeven:.0f}%")

    # ── Por dia ──────────────────────────────────────────────────────────
    by_day = defaultdict(list)
    for r in fiables:
        day = (r.get("signal_dt_utc") or "")[:10]
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
    discrep = [r for r in fiables if r.get("reconciled_ok") is False]
    if discrep:
        print(f"  ⚠ {len(discrep)} trades donde el bot registro un P&L "
              f"distinto al real:")
        for r in discrep:
            print(f"    {r['sig_id']:22s} bot=${r['pnl_journal']:+.2f}  "
                  f"real=${r['pnl_real_mt5']:+.2f}  "
                  f"error=${r['pnl_discrepancy']:+.2f}")
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
    flag_counter = defaultdict(int)
    for r in rows:
        for f in r.get("flags", []):
            # Normalizar flags con numeros para agrupar
            key = f.split("_")[0] if f[0].isupper() else f
            flag_counter[key] += 1
    if flag_counter:
        print(f"\n{'─'*72}")
        print(f"FLAGS detectados en el ledger")
        print(f"{'─'*72}")
        for flag, n in sorted(flag_counter.items(), key=lambda x: -x[1]):
            print(f"  {flag:42s} {n}")

    print()


if __name__ == "__main__":
    main()
