"""
analyze_profitability.py — Estimación realista de rentabilidad por canal.

Objetivo:
  Tomar el CSV generado por analyze_deep.py y calcular, escenario por escenario,
  cuánto ganaríamos REALMENTE replicando las señales con el bot.

Modelos de rentabilidad:
  1. LITERAL OPTIMISTA: cierre en max_tp_hit confirmado, sin costes
  2. LITERAL REALISTA: cierre en max_tp_hit, descuenta spread + slippage
  3. ESCALONADO (lo que hace el bot): N posiciones cierran cada una en su TP_i
  4. ESCALONADO + DCA: añade posiciones extra dentro del rango
  5. PESIMISTA: asume que las señales sin reply son SL hit silencioso
  6. CON COSTES VANTAGE: spread real ~0.30, slippage 0.50, comisión 0

Tiene en cuenta:
  - Tamaño de lote (€500 capital, lot 0.01 = 1 oz = 1$/pip)
  - 30% de señales sin confirmar → varios escenarios de imputación
  - Sesgo de selección (canal solo escribe TP, no SL)
"""

import sys
import io
import csv
from collections import defaultdict
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


COST_PER_TRADE_OZ = 0.80  # spread (0.30) + slippage promedio (0.50)
LOT_SIZE = 0.01            # → 1 oz por trade → 1$ por dolar de movimiento


def load_csv(path: str) -> list[dict]:
    rows = []
    # El CSV puede tener emojis en raw_text → leemos UTF-8
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows


def to_f(s: str):
    if s in (None, "", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_i(s: str):
    if s in (None, "", "None"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


# ─── Modelos ──────────────────────────────────────────────────────────────────

def pl_literal(row, optimistic: bool, with_costs: bool) -> tuple[float, str]:
    """
    Modelo LITERAL: cierre en el max_tp_hit confirmado.
    optimistic=True  → señales sin reply se descartan
    optimistic=False → señales sin reply se asumen SL hit
    """
    direction   = row["direction"]
    sl          = to_f(row["sl"])
    tp1         = to_f(row["tp1"])
    tps_str     = row["tps"]
    max_tp_hit  = to_i(row["max_tp_hit"])
    sl_hit      = row["sl_hit"] == "True"
    tp1_dist    = to_f(row["tp1_dist"])
    tp_last_dist= to_f(row["tp_last_dist"])
    sl_dist     = to_f(row["sl_dist"])
    range_size  = to_f(row["range_size"])

    tps = []
    if tps_str:
        for t in tps_str.split("|"):
            v = to_f(t)
            if v is not None:
                tps.append(v)

    cost = COST_PER_TRADE_OZ if with_costs else 0.0

    if sl_hit:
        # SL hit: asumimos pérdida = sl_dist + (rango/2) si entra en medio
        # Conservador: pérdida = (rango + sl_dist)
        loss = (range_size or 0) + (sl_dist or 4)
        return -loss - cost, "SL_HIT"

    if max_tp_hit and max_tp_hit >= 1:
        # Calcula la distancia del TP alcanzado
        # Asumimos entrada en el edge (BUY=high, SELL=low) y TPs ordenados
        if max_tp_hit == 1:
            dist = tp1_dist or 0
        elif max_tp_hit >= len(tps):
            dist = tp_last_dist or 0
        else:
            # Distancia al TP_n
            entry = tps[0] - (tp1_dist or 0) * (1 if direction == "BUY" else -1)
            tp_n = tps[max_tp_hit - 1]
            dist = abs(tp_n - entry)
        return dist - cost, f"TP{max_tp_hit}_HIT"

    # Sin reply / sin confirmación
    if optimistic:
        return None, "UNKNOWN"  # se descarta del cómputo
    else:
        loss = (range_size or 0) + (sl_dist or 4)
        return -loss - cost, "ASSUMED_SL"


def pl_escalonado(row, with_costs: bool, n_positions: int = 4) -> tuple[float, str]:
    """
    Modelo ESCALONADO (lo que hace el bot):
      - Abre n_positions posiciones (1 entry + DCA si aplica)
      - Cada posición i cierra en TP_i (o el último TP si i > num_tps)
      - Si SL hit, todas pierden lo mismo

    Distancias relativas: usamos los TPs del canal y la entry asumida en edge.
    """
    direction   = row["direction"]
    tps_str     = row["tps"]
    max_tp_hit  = to_i(row["max_tp_hit"])
    sl_hit      = row["sl_hit"] == "True"
    tp1_dist    = to_f(row["tp1_dist"])
    tp_last_dist= to_f(row["tp_last_dist"])
    sl_dist     = to_f(row["sl_dist"])
    range_size  = to_f(row["range_size"])

    tps = []
    if tps_str:
        for t in tps_str.split("|"):
            v = to_f(t)
            if v is not None:
                tps.append(v)

    if not tps or tp1_dist is None:
        return None, "NO_DATA"

    # Entry asumida (edge del rango)
    entry = tps[0] - tp1_dist * (1 if direction == "BUY" else -1)

    # Distancias para cada TP
    tp_distances = [abs(tp - entry) for tp in tps]

    cost = COST_PER_TRADE_OZ if with_costs else 0.0

    if sl_hit:
        loss = ((range_size or 0) + (sl_dist or 4)) * n_positions + cost * n_positions
        return -loss, "SL_HIT_ALL"

    if not max_tp_hit or max_tp_hit < 1:
        return None, "UNKNOWN"

    # Posición i cierra en TP[min(i, max_tp_hit, len(tps)-1)]
    # Como no sabemos exactamente cuándo paró, asumimos: si max_tp_hit = K,
    # entonces posiciones 1..K cerraron en sus TPs, y posiciones K+1..n
    # se quedaron abiertas → nadie las cierra → asumimos break-even
    pl = 0.0
    for i in range(n_positions):
        tp_idx = min(i, len(tps) - 1)
        if tp_idx < max_tp_hit:
            pl += tp_distances[tp_idx] - cost
        else:
            # Posición no llegó a su TP → asumimos cerrada en BE (sin ganancia ni pérdida)
            pl += 0 - cost
    return pl, f"TP{max_tp_hit}_PARTIAL"


# ─── Pipeline ─────────────────────────────────────────────────────────────────

def evaluate_channel(rows: list[dict], channel: str):
    chan_rows = [r for r in rows if r["channel"] == channel]
    n = len(chan_rows)

    print(f"\n{'='*70}")
    print(f"  RENTABILIDAD — {channel.upper()}  (n={n} señales)")
    print(f"{'='*70}\n")

    scenarios = [
        ("LITERAL optimista, SIN costes",   lambda r: pl_literal(r, True,  False)),
        ("LITERAL optimista, CON costes",   lambda r: pl_literal(r, True,  True)),
        ("LITERAL pesimista, CON costes",   lambda r: pl_literal(r, False, True)),
        ("ESCALONADO 4-pos, SIN costes",    lambda r: pl_escalonado(r, False, 4)),
        ("ESCALONADO 4-pos, CON costes",    lambda r: pl_escalonado(r, True,  4)),
        ("ESCALONADO 1-pos (no DCA)",       lambda r: pl_escalonado(r, True,  1)),
    ]

    for name, fn in scenarios:
        total_pl = 0.0
        wins = 0
        losses = 0
        unknowns = 0
        breakdown = defaultdict(int)
        for r in chan_rows:
            pl, tag = fn(r)
            breakdown[tag] += 1
            if pl is None:
                unknowns += 1
                continue
            total_pl += pl
            if pl > 0:
                wins += 1
            else:
                losses += 1

        evaluated = wins + losses
        avg = total_pl / evaluated if evaluated else 0
        wr = (wins / evaluated * 100) if evaluated else 0

        print(f"  {name}")
        print(f"    Evaluables: {evaluated}/{n}  (sin datos: {unknowns})")
        print(f"    Wins: {wins}  Losses: {losses}  WR: {wr:.0f}%")
        print(f"    P/L total: ${total_pl:+.0f}  |  P/L medio: ${avg:+.2f}/trade")
        print(f"    Tags: {dict(breakdown)}")
        print()


def monthly_estimate(rows: list[dict]):
    """Calcula señales/mes y P/L mensual estimado por canal."""
    print(f"\n{'='*70}")
    print(f"  ESTIMACIÓN MENSUAL  (capital €500, lot 0.01 → $1/pip)")
    print(f"{'='*70}\n")

    for channel in ("canal1", "canal2"):
        chan_rows = [r for r in rows if r["channel"] == channel]
        if not chan_rows:
            continue

        # Días distintos en el dataset
        dates = set()
        for r in chan_rows:
            d = r["date"][:10]  # YYYY-MM-DD
            dates.add(d)

        days = len(dates)
        weeks = days / 5  # solo lunes-viernes
        months = days / 21  # ~21 días hábiles/mes

        sigs_per_month = len(chan_rows) / months if months else 0

        # P/L medio CON costes, escalonado 4 pos
        total_pl = 0.0
        evaluated = 0
        for r in chan_rows:
            pl, _ = pl_escalonado(r, True, 4)
            if pl is not None:
                total_pl += pl
                evaluated += 1

        avg_pl = total_pl / evaluated if evaluated else 0
        # Proyección: señales/mes × P/L medio (asumiendo sample representativo)
        monthly = sigs_per_month * avg_pl

        # Convertir a € (lot 0.01 = $1/pip → en EUR ~= 0.92$ pero ignoramos forex)
        print(f"  {channel.upper()}:")
        print(f"    Días con señales: {days}  (~{months:.1f} meses)")
        print(f"    Señales/mes: {sigs_per_month:.0f}")
        print(f"    P/L medio (escalonado 4 pos, con costes): ${avg_pl:+.2f}/señal")
        print(f"    P/L mensual estimado (lot 0.01): ${monthly:+.0f}")
        print(f"    Sobre capital €500 → {monthly/500*100:+.1f}%/mes")
        print()


def detect_outliers(rows: list[dict]):
    """Detecta señales con TPs muy alejados (>50$) que parecen 'long term'."""
    print(f"\n{'='*70}")
    print(f"  SEÑALES OUTLIER (TP último > 50$)")
    print(f"{'='*70}\n")

    for channel in ("canal1", "canal2"):
        chan_rows = [r for r in rows if r["channel"] == channel]
        outliers = []
        for r in chan_rows:
            d = to_f(r["tp_last_dist"])
            if d is not None and d > 50:
                outliers.append((d, to_i(r["max_tp_hit"]), r["direction"], r["date"][:10]))

        outliers.sort(reverse=True)
        print(f"  {channel.upper()}: {len(outliers)} outliers de {len(chan_rows)}")
        for dist, max_tp, direction, date in outliers[:15]:
            print(f"    {date}  {direction:4}  tp_last_dist=${dist:.0f}  max_tp_hit={max_tp}")
        if len(outliers) > 15:
            print(f"    ... y {len(outliers)-15} más")
        print()


def hourly_pl(rows: list[dict]):
    """P/L por hora UTC para detectar mejores ventanas."""
    print(f"\n{'='*70}")
    print(f"  P/L MEDIO POR HORA UTC  (escalonado 4-pos, con costes)")
    print(f"{'='*70}\n")

    for channel in ("canal1", "canal2"):
        chan_rows = [r for r in rows if r["channel"] == channel]
        by_hour = defaultdict(list)
        for r in chan_rows:
            pl, _ = pl_escalonado(r, True, 4)
            if pl is not None:
                h = to_i(r["hour_utc"])
                if h is not None:
                    by_hour[h].append(pl)

        if not by_hour:
            continue

        print(f"  {channel.upper()}:")
        for h in sorted(by_hour.keys()):
            pls = by_hour[h]
            avg = sum(pls) / len(pls)
            n = len(pls)
            bar = "█" * int(min(abs(avg), 30))
            sign = "+" if avg >= 0 else "-"
            print(f"    {h:02d}h  n={n:4}  avg=${avg:+7.2f}/sig  {sign}{bar}")
        print()


def weekday_pl(rows: list[dict]):
    """P/L por día de la semana."""
    print(f"\n{'='*70}")
    print(f"  P/L MEDIO POR DÍA DE LA SEMANA  (escalonado 4-pos, con costes)")
    print(f"{'='*70}\n")

    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for channel in ("canal1", "canal2"):
        chan_rows = [r for r in rows if r["channel"] == channel]
        by_day = defaultdict(list)
        for r in chan_rows:
            pl, _ = pl_escalonado(r, True, 4)
            if pl is not None:
                wd = r["weekday"]
                by_day[wd].append(pl)

        if not by_day:
            continue

        print(f"  {channel.upper()}:")
        for d in days:
            if d in by_day:
                pls = by_day[d]
                avg = sum(pls) / len(pls)
                n = len(pls)
                print(f"    {d}  n={n:4}  avg=${avg:+7.2f}/sig  total=${sum(pls):+7.0f}")
        print()


def main():
    csv_path = Path(__file__).parent / "signals_detail.csv"
    if not csv_path.exists():
        print(f"❌ No existe {csv_path}. Corre primero analyze_deep.py")
        sys.exit(1)

    rows = load_csv(str(csv_path))
    print(f"Cargadas {len(rows)} filas")

    evaluate_channel(rows, "canal1")
    evaluate_channel(rows, "canal2")
    monthly_estimate(rows)
    detect_outliers(rows)
    hourly_pl(rows)
    weekday_pl(rows)


if __name__ == "__main__":
    main()
