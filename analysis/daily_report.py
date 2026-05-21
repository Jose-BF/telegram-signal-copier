"""
Reporte diario del bot de señales.

Uso:
    python analysis/daily_report.py              # hoy
    python analysis/daily_report.py 2026-04-28   # fecha concreta

Lee data/trade_events.jsonl y muestra un resumen limpio de cada señal
con timestamps de Telegram, latencias, niveles y resultado final.
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

# ── Config ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
EVENTS_FILE = ROOT / "data" / "trade_events.jsonl"

# Fecha a analizar (argumento o hoy)
if len(sys.argv) > 1:
    DATE_FILTER = sys.argv[1]          # "2026-04-28"
else:
    DATE_FILTER = datetime.now().strftime("%Y-%m-%d")

print(f"\n{'='*65}")
print(f"  REPORTE DIARIO — {DATE_FILTER}")
print(f"{'='*65}\n")

# ── Cargar eventos del día ────────────────────────────────────────────────────
# Estrategia two-pass para evitar el bug del cruce de día: si una señal empieza
# a las 23:58 y el signal_closed llega a las 00:10 del día siguiente, el filtro
# naïve ts.startswith(DATE_FILTER) perdería el evento de cierre.
#
# Pass 1: leer todo el fichero y encontrar los signal_id que EMPEZARON en DATE_FILTER
#         (identificados por su evento signal_received).
# Pass 2: recoger TODOS los eventos de esos signal_ids, sin importar el día del ts.
signals: dict[str, list[dict]] = defaultdict(list)
heartbeats = []

# Paso 1: cargar todos los eventos crudos y detectar señales del día
all_raw: list[dict] = []
with open(EVENTS_FILE, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        all_raw.append(ev)

# Señales cuyo signal_received ocurrió en DATE_FILTER
target_sigs: set[str] = set()
for ev in all_raw:
    if ev.get("ev") == "signal_received" and ev.get("ts", "").startswith(DATE_FILTER):
        target_sigs.add(ev.get("sig", ""))

# Paso 2: agrupar eventos por señal (todos, aunque crucen el día) + heartbeats del día
for ev in all_raw:
    sig = ev.get("sig", "")
    ts = ev.get("ts", "")
    if sig == "bot":
        if ts.startswith(DATE_FILTER):
            heartbeats.append(ev)
    elif sig in target_sigs:
        signals[sig].append(ev)

if not signals:
    print(f"  Sin señales para {DATE_FILTER}.")
    sys.exit(0)

# ── Helpers ───────────────────────────────────────────────────────────────────
def fmt_ts(ts: str | None) -> str:
    if not ts:
        return "—"
    return ts[11:19]   # HH:MM:SS

def lag(bot_ts: str | None, tg_ts: str | None) -> str:
    if not bot_ts or not tg_ts:
        return ""
    try:
        b = datetime.fromisoformat(bot_ts.replace("Z", "+00:00"))
        t = datetime.fromisoformat(tg_ts.replace("Z", "+00:00"))
        ms = int((b - t).total_seconds() * 1000)
        return f"(lag {ms}ms)"
    except Exception:
        return ""

def find(evs: list[dict], ev_name: str) -> dict | None:
    for e in evs:
        if e.get("ev") == ev_name:
            return e
    return None

def find_all(evs: list[dict], ev_name: str) -> list[dict]:
    return [e for e in evs if e.get("ev") == ev_name]

# ── Resumen por señal ─────────────────────────────────────────────────────────
total_pl = 0.0
wins = losses = breakevens = others = 0

for sig_id in sorted(signals.keys()):
    evs = signals[sig_id]
    is_test = any(e.get("test") for e in evs)
    prefix = "[TEST]" if is_test else "     "

    recv   = find(evs, "signal_received")
    fill   = find(evs, "market_filled")
    rng    = find(evs, "range_arrived")
    tps_ev = find(evs, "tps_arrived") or find(evs, "predictor_levels")
    closed = find(evs, "signal_closed")
    mgmts  = find_all(evs, "mgmt_msg")

    direction  = recv.get("direction", "?") if recv else "?"
    channel    = recv.get("channel", sig_id.split("_")[0]) if recv else "?"
    fill_price = fill.get("price") if fill else None
    fill_lat   = fill.get("latency_ms") if fill else None
    tg_recv    = recv.get("tg_ts") if recv else None
    bot_recv   = recv.get("ts") if recv else None

    print(f"{prefix} {sig_id}  {direction}  [{channel}]")
    print(f"         Señal Telegram : {fmt_ts(tg_recv)}  →  bot: {fmt_ts(bot_recv)} {lag(bot_recv, tg_recv)}")

    if fill_price:
        print(f"         Fill MT5        : {fmt_ts(fill.get('ts'))}  @{fill_price:.2f}  (latencia {fill_lat}ms)")

    if rng:
        delay = rng.get("delay_sec", "?")
        tg_rng = rng.get("tg_ts")
        print(f"         Rango           : {fmt_ts(rng.get('ts'))} {lag(rng.get('ts'), tg_rng)}  "
              f"[{rng.get('range_low')}-{rng.get('range_high')}]  delay {delay}s desde señal")

    if tps_ev:
        tps = tps_ev.get("tps", [])
        sl  = tps_ev.get("sl")
        src = "predictor" if tps_ev.get("ev") == "predictor_levels" else "canal"
        tg_tp = tps_ev.get("tg_ts")
        print(f"         TPs ({src:9}): {fmt_ts(tps_ev.get('ts'))} {lag(tps_ev.get('ts'), tg_tp)}  "
              f"TPs={[round(t,2) for t in tps[:3]]}{'...' if len(tps)>3 else ''}  SL={sl}")

    # Acciones de gestión recibidas
    for m in mgmts:
        action = m.get("action", "?")
        if action == "INFORMATIONAL":
            continue
        price  = m.get("price")
        tg_m   = m.get("tg_ts")
        applied = m.get("will_apply", True)
        tag = "" if applied else " [ignorada]"
        price_str = f" → {price}" if price else ""
        print(f"         Gestión         : {fmt_ts(m.get('ts'))} {lag(m.get('ts'), tg_m)}  "
              f"{action}{price_str}{tag}")

    # Resultado final
    if closed:
        tag_val = closed.get("tag", "?")
        pl      = closed.get("total_pl")
        pl_str  = f"  P&L={pl:+.2f}" if pl is not None else "  P&L=?"
        print(f"         Cierre          : {fmt_ts(closed.get('ts'))}  {tag_val}{pl_str}")
        if pl is not None:
            total_pl += pl
            if tag_val in ("TP1","TP2","TP3","TP4","TP5","WIN"):
                wins += 1
            elif tag_val in ("SL","LOSS","LOSS_CLEAN"):
                losses += 1
            elif "BE" in tag_val:
                breakevens += 1
            else:
                others += 1
    else:
        print(f"         Cierre          : — (señal aún abierta o sin datos)")

    print()

# ── Heartbeat check ───────────────────────────────────────────────────────────
if heartbeats:
    last_hb = heartbeats[-1]
    last_hb_ts = fmt_ts(last_hb.get("ts"))
    gap_min = round((len(heartbeats) * 5))
    print(f"  Heartbeats: {len(heartbeats)} registros  |  último: {last_hb_ts}  "
          f"|  cobertura ~{gap_min} min")
else:
    print("  Heartbeats: ninguno (bot antiguo sin heartbeat, o primer día)")

# ── Resumen global ────────────────────────────────────────────────────────────
total = wins + losses + breakevens + others
print(f"\n{'─'*65}")
print(f"  Total señales cerradas : {total}")
if total:
    print(f"  Wins / Losses / BE     : {wins} / {losses} / {breakevens}")
    wr = wins / total * 100 if total else 0
    print(f"  Win rate               : {wr:.0f}%")
print(f"  P&L total              : {total_pl:+.2f} USD")
print(f"{'='*65}\n")
