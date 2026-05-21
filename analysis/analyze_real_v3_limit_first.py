"""
analyze_real_v3_limit_first.py — Estrategia LIMIT-FIRST.

Hipótesis: la operativa correcta de estos canales NO es market + limits,
sino LIMITS PUROS. Es decir:

  - Si precio actual está dentro del rango → market + limit (igual que antes)
  - Si precio actual está FUERA del rango → solo limits (esperar pull-back)

Esto coincide con cómo opera el trader humano:
  - Si la señal llega cuando precio ya pasó del rango, no entra a market
    (sería tarde y mal precio), pero deja limits puestos por si vuelve.
  - Si precio nunca vuelve, no opera (P/L = 0). Si vuelve, captura el setup.

Vs v2 (que descartaba la señal entera): v3 deja al menos los limits activos.
Mide si esto recupera P/L vs no operar nada.
"""

import sys, statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

PARQUET_M1 = Path(__file__).parent / "data" / "xauusd_m1_2026.parquet"
PARQUET_C1 = Path(__file__).parent / "data" / "canal1_signals_2026.parquet"
PARQUET_C2 = Path(__file__).parent / "data" / "canal2_signals_2026.parquet"

SPREAD_USD = 0.30
SLIPPAGE_USD = 0.50
IS_END = datetime(2026, 3, 1, tzinfo=timezone.utc)


def load_m1():
    df = pd.read_parquet(PARQUET_M1)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


def load_signals(path):
    df = pd.read_parquet(path)
    df["dt"] = pd.to_datetime(df["dt"], utc=True)
    sigs = []
    for _, row in df.iterrows():
        tps = [row[f"tp{i}"] for i in range(1, 6) if pd.notna(row[f"tp{i}"])]
        rng = (row["range_low"], row["range_high"]) if pd.notna(row["range_low"]) else None
        sigs.append({
            "id": row["id"], "dt": row["dt"].to_pydatetime(),
            "direction": row["direction"],
            "entry": row["entry"] if pd.notna(row["entry"]) else None,
            "range": rng, "tps": tps, "sl": row["sl"],
        })
    return sigs


@dataclass
class StrategyConfig:
    entry_mode: str = "extremes"
    n_entries: int = 2
    target_tp_index: int = 2
    be_at_tp_index: int = -1
    time_stop_min: int = 60


def get_market_price(sig, m1_df):
    sig_dt = sig["dt"]
    if sig_dt.tzinfo is None:
        sig_dt = sig_dt.replace(tzinfo=timezone.utc)
    bar_dt = sig_dt.replace(second=0, microsecond=0)
    bars = m1_df[m1_df["time"] >= bar_dt].head(1)
    if len(bars) == 0: return None
    return float(bars.iloc[0]["open"])


def simulate_v3(sig, cfg, m1_df):
    """v3: si market está fuera de rango, NO opera market pero sí coloca limits."""
    direction = sig["direction"]
    rng = sig.get("range")
    entry_unique = sig.get("entry")

    real_market_price = get_market_price(sig, m1_df)
    if real_market_price is None:
        return None, 0, "NO_BARS", 0

    slip = SLIPPAGE_USD if direction == "BUY" else -SLIPPAGE_USD
    market_entry = real_market_price + slip

    # Comprobar si market está dentro del rango
    market_inside = True if rng is None else (rng[0] <= market_entry <= rng[1])

    tps = sig["tps"]
    target_tp = tps[min(cfg.target_tp_index, len(tps) - 1)]
    be_tp = tps[min(cfg.be_at_tp_index, len(tps) - 1)] if cfg.be_at_tp_index >= 0 else None

    positions = []

    # Market: solo si dentro del rango (o si no hay rango)
    if market_inside:
        positions.append({
            "kind": "market", "open": True, "filled": True,
            "entry": market_entry, "sl": sig["sl"], "tp": target_tp,
            "limit_price": None, "closed_at": None, "reason": "",
        })

    # Limits: SIEMPRE se colocan si la config los tiene (esperan pull-back)
    if cfg.entry_mode == "extremes" and rng is not None:
        far = rng[0] if direction == "BUY" else rng[1]
        positions.append({
            "kind": "limit", "open": False, "filled": False,
            "entry": None, "sl": sig["sl"], "tp": target_tp,
            "limit_price": far, "closed_at": None, "reason": "",
        })

    elif cfg.entry_mode == "intra_dca" and rng is not None:
        n = max(2, cfg.n_entries)
        step = (rng[1] - rng[0]) / (n - 1)
        prices = [rng[1] - i*step if direction == "BUY" else rng[0] + i*step
                  for i in range(n)]
        for p in prices[1:] if market_inside else prices:
            # Si market no operó, todos los precios son candidatos a limit
            positions.append({
                "kind": "limit", "open": False, "filled": False,
                "entry": None, "sl": sig["sl"], "tp": target_tp,
                "limit_price": p, "closed_at": None, "reason": "",
            })

    if not positions:
        # Sin rango y market fuera (no debería pasar)
        return 0.0, 0, "NO_POSITIONS", 0

    # Iterar
    sig_dt = sig["dt"]
    if sig_dt.tzinfo is None:
        sig_dt = sig_dt.replace(tzinfo=timezone.utc)
    bars = m1_df[m1_df["time"] >= sig_dt.replace(second=0, microsecond=0)].head(240)

    be_active = False
    minutes_elapsed = 0

    for _, bar in bars.iterrows():
        minutes_elapsed += 1
        bh, bl, bo, bc = bar["high"], bar["low"], bar["open"], bar["close"]

        for p in positions:
            if not p["filled"] and bl <= p["limit_price"] <= bh:
                p["entry"] = p["limit_price"]
                p["filled"] = True
                p["open"] = True

        if not be_active and be_tp is not None:
            tp_touched = ((direction == "BUY" and bh >= be_tp) or
                          (direction == "SELL" and bl <= be_tp))
            if tp_touched:
                be_active = True
                for p in positions:
                    if p["filled"] and p["open"]:
                        p["sl"] = p["entry"]

        for p in positions:
            if not p["open"] or not p["filled"]: continue
            sl_hit = ((direction == "BUY" and bl <= p["sl"]) or
                      (direction == "SELL" and bh >= p["sl"]))
            tp_hit = ((direction == "BUY" and bh >= p["tp"]) or
                      (direction == "SELL" and bl <= p["tp"]))
            if sl_hit and tp_hit:
                if be_active and abs(p["sl"] - p["entry"]) < 0.5:
                    p["closed_at"] = p["sl"]; p["reason"] = "BE"
                else:
                    dist_sl = abs(bo - p["sl"]); dist_tp = abs(bo - p["tp"])
                    if dist_sl < dist_tp:
                        p["closed_at"] = p["sl"]; p["reason"] = "SL"
                    else:
                        p["closed_at"] = p["tp"]; p["reason"] = "TP"
                p["open"] = False
            elif sl_hit:
                p["closed_at"] = p["sl"]
                p["reason"] = "BE" if (be_active and abs(p["sl"] - p["entry"]) < 0.5) else "SL"
                p["open"] = False
            elif tp_hit:
                p["closed_at"] = p["tp"]; p["reason"] = "TP"
                p["open"] = False

        if cfg.time_stop_min and minutes_elapsed >= cfg.time_stop_min and not be_active:
            for p in positions:
                if p["open"] and p["filled"]:
                    p["closed_at"] = bc; p["reason"] = "TIME"
                    p["open"] = False
            break

        if all(not p["open"] or not p["filled"] for p in positions):
            for p in positions:
                if not p["filled"]:
                    p["open"] = False
            break

    if len(bars):
        last_close = bars.iloc[-1]["close"]
        for p in positions:
            if p["open"] and p["filled"]:
                p["closed_at"] = last_close; p["reason"] = "EOH"
                p["open"] = False

    pl = 0.0
    n_filled = 0
    for p in positions:
        if not p["filled"]: continue
        n_filled += 1
        d = (p["closed_at"] - p["entry"]) if direction == "BUY" else (p["entry"] - p["closed_at"])
        d -= SPREAD_USD
        pl += d

    status = "OPERATED" if n_filled > 0 else "NO_FILLS"
    return pl, n_filled, status, (1 if not market_inside else 0)


def evaluate(sigs, cfg, m1):
    pls = []
    no_market = 0
    no_fills = 0
    for s in sigs:
        pl, n, status, no_mkt = simulate_v3(s, cfg, m1)
        if status == "NO_BARS": continue
        no_market += no_mkt
        if status == "NO_FILLS":
            no_fills += 1
            pls.append(0.0)  # señal sin fills cuenta como 0 (no opera pero existe)
            continue
        pls.append(pl)
    if not pls: return None
    eq, peak, max_dd = 0, 0, 0
    for p in pls:
        eq += p
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
    operated = [p for p in pls if p != 0.0]
    avg = sum(pls) / len(pls)
    sd = statistics.stdev(pls) if len(pls) > 1 else 0
    sharpe = avg / sd if sd > 0 else 0
    wr = sum(1 for p in operated if p > 0) / len(operated) * 100 if operated else 0
    return {"total": sum(pls), "avg": avg, "wr": wr, "max_dd": max_dd,
            "sharpe": sharpe, "n": len(pls), "no_market": no_market,
            "no_fills": no_fills, "operated": len(operated)}


def fmt(m):
    if m is None: return "n/a"
    return (f"${m['total']:+8.0f} avg${m['avg']:+5.2f} WR{m['wr']:3.0f}% "
            f"DD${m['max_dd']:5.0f} S{m['sharpe']:+.3f} n={m['n']} "
            f"(op:{m['operated']} nofill:{m['no_fills']} nomkt:{m['no_market']})")


def main():
    print("Cargando M1 + señales...")
    m1 = load_m1()
    sigs1 = load_signals(PARQUET_C1)
    sigs2 = load_signals(PARQUET_C2)

    is1 = [s for s in sigs1 if s["dt"] < IS_END]
    oos1 = [s for s in sigs1 if s["dt"] >= IS_END]
    is2 = [s for s in sigs2 if s["dt"] < IS_END]
    oos2 = [s for s in sigs2 if s["dt"] >= IS_END]

    print(f"Canal 1: {len(sigs1)} | IS={len(is1)} OOS={len(oos1)}")
    print(f"Canal 2: {len(sigs2)} | IS={len(is2)} OOS={len(oos2)}")
    print("\nLEYENDA: nomkt = señales con market FUERA del rango (limits-only)")
    print("         nofill = señales que ningún limit se llenó (P/L=0)")
    print("         op = señales con al menos 1 fill\n")

    print("="*100)
    print("  CANAL 1 — LIMIT-FIRST (si market fuera, solo limits)")
    print("="*100)
    c1_configs = [
        ("market_only TP3+TS60",         StrategyConfig("market_only", 1, 2, -1, 60)),
        ("extremes TP3+TS60 ⭐",          StrategyConfig("extremes",    2, 2, -1, 60)),
        ("extremes TP2+TS60",            StrategyConfig("extremes",    2, 1, -1, 60)),
        ("extremes TP3 (NO TS)",         StrategyConfig("extremes",    2, 2, -1, 0)),
        ("extremes TP3+TS120",           StrategyConfig("extremes",    2, 2, -1, 120)),
    ]
    for name, cfg in c1_configs:
        is_m  = evaluate(is1, cfg, m1)
        oos_m = evaluate(oos1, cfg, m1)
        print(f"  {name:<28} IS: {fmt(is_m)}")
        print(f"  {' '*28} OOS:{fmt(oos_m)}\n")

    print("="*100)
    print("  CANAL 2 — LIMIT-FIRST")
    print("="*100)
    c2_configs = [
        ("intra_dca/4p TP4+BE@TP1 ⭐",    StrategyConfig("intra_dca",   4, 3, 0, 0)),
        ("intra_dca/4p TP4 (NO BE)",     StrategyConfig("intra_dca",   4, 3, -1, 0)),
        ("intra_dca/3p TP4+BE@TP1",      StrategyConfig("intra_dca",   3, 3, 0, 0)),
        ("intra_dca/4p TP3+BE@TP1",      StrategyConfig("intra_dca",   4, 2, 0, 0)),
        ("intra_dca/4p TP4 NO BE TS60",  StrategyConfig("intra_dca",   4, 3, -1, 60)),
        ("intra_dca/4p TP4 NO BE TS120", StrategyConfig("intra_dca",   4, 3, -1, 120)),
    ]
    for name, cfg in c2_configs:
        is_m  = evaluate(is2, cfg, m1)
        oos_m = evaluate(oos2, cfg, m1)
        print(f"  {name:<28} IS: {fmt(is_m)}")
        print(f"  {' '*28} OOS:{fmt(oos_m)}\n")


if __name__ == "__main__":
    main()
