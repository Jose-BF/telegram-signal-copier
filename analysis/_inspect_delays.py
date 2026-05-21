"""Análisis de delays + DCA proximity + predictor placement."""
import sys, io, json
from collections import defaultdict
from datetime import datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

def parse_iso(s):
    if not s: return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

events = []
with open("data/trade_events.jsonl", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line: continue
        e = json.loads(line)
        if e.get("ts","").startswith("2026-04-28"):
            events.append(e)

sigs = defaultdict(list)
for e in events:
    if e.get("sig","") != "bot":
        sigs[e["sig"]].append(e)

print("\n=== 1) DELAYS POR FASE ===\n")
print(f"{'sig':<18} {'tg→bot':<10} {'bot→MT5':<10} {'rng tg→bot':<12} {'tps tg→bot':<12}")
print("-"*72)
for sid in sorted(sigs):
    evs = sigs[sid]
    recv = next((e for e in evs if e["ev"]=="signal_received"), None)
    fill = next((e for e in evs if e["ev"]=="market_filled"), None)
    rng  = next((e for e in evs if e["ev"]=="range_arrived"), None)
    tps  = next((e for e in evs if e["ev"]=="tps_arrived"), None)

    # tg→bot: cuánto tarda el mensaje del canal en llegar al bot
    tg_to_bot = "?"
    if recv and recv.get("tg_ts"):
        t1 = parse_iso(recv["tg_ts"])
        t2 = parse_iso(recv["ts"])
        if t1 and t2:
            tg_to_bot = f"{int((t2-t1).total_seconds()*1000)}ms"

    # bot→MT5: cuánto tarda el bot en confirmar el fill
    bot_to_mt5 = f"{fill['latency_ms']}ms" if fill and fill.get("latency_ms") else "?"

    # rng_tg→bot
    rng_lag = "?"
    if rng and rng.get("tg_ts"):
        t1 = parse_iso(rng["tg_ts"])
        t2 = parse_iso(rng["ts"])
        if t1 and t2:
            rng_lag = f"{int((t2-t1).total_seconds()*1000)}ms"

    # tps_tg→bot
    tps_lag = "?"
    if tps and tps.get("tg_ts"):
        t1 = parse_iso(tps["tg_ts"])
        t2 = parse_iso(tps["ts"])
        if t1 and t2:
            tps_lag = f"{int((t2-t1).total_seconds()*1000)}ms"

    print(f"{sid:<18} {tg_to_bot:<10} {bot_to_mt5:<10} {rng_lag:<12} {tps_lag:<12}")

print("\n=== 2) DCA PROXIMITY (fills consecutivos con poca separación) ===\n")
print(f"{'sig':<18} {'ticket':<12} {'level':<8} {'fill':<8} {'gap_vs_prev':<14} {'time_vs_prev':<14}")
print("-"*80)
for sid in sorted(sigs):
    evs = sigs[sid]
    market = next((e for e in evs if e["ev"]=="market_filled"), None)
    dcas = [e for e in evs if e["ev"]=="dca_filled"]
    if not dcas: continue
    prev_fill = market["price"] if market else None
    prev_ts = parse_iso(market["ts"]) if market else None
    print(f"{sid}  market: fill={prev_fill}")
    for d in dcas:
        fill = d.get("fill_price")
        lvl = d.get("level")
        ts = parse_iso(d["ts"])
        gap = abs(fill - prev_fill) if (fill and prev_fill) else None
        dt = (ts - prev_ts).total_seconds() if (ts and prev_ts) else None
        gap_s = f"${gap:.2f}" if gap is not None else "?"
        warn = " ⚠ <$0.50!" if gap is not None and gap < 0.5 else ""
        dt_s = f"{dt:.2f}s" if dt is not None else "?"
        print(f"  pos={d.get('position_index')}  #{d.get('ticket')}  lvl={lvl}  fill={fill}  gap={gap_s}{warn}  dt={dt_s}")
        prev_fill = fill
        prev_ts = ts
    print()

print("\n=== 3) PREDICTOR PLACEMENT (cuándo se aplica vs cuándo se salta) ===\n")
for sid in sorted(sigs):
    evs = sigs[sid]
    has_predictor = any(e["ev"]=="predictor_levels" for e in evs)
    rng = next((e for e in evs if e["ev"]=="range_arrived"), None)
    tps_first = next((e for e in evs if e["ev"]=="tps_arrived"), None)
    sl_first  = next((e for e in evs if e["ev"]=="sl_arrived"), None)

    if has_predictor:
        print(f"  {sid}  ✓ predictor SI aplicado (rango llegó sin TPs/SL reales todavía)")
    else:
        # Por qué no
        order = []
        if rng: order.append(("range", rng["ts"]))
        if tps_first: order.append(("tps_real", tps_first["ts"]))
        if sl_first: order.append(("sl_real", sl_first["ts"]))
        order.sort(key=lambda x: x[1])
        seq = " → ".join(o[0] for o in order)
        print(f"  {sid}  ✗ predictor NO aplicado  |  orden de llegada: {seq}")
        print(f"      (cuando llegó el rango, ya teníamos TPs/SL reales → no hace falta predictor)")
