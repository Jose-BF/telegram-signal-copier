"""Investigación profunda de bugs detectados en sesiones recientes."""
import io, json, sys
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

events = []
with open("data/trade_events.jsonl", encoding="utf-8") as f:
    for line in f:
        if line.strip():
            events.append(json.loads(line))

by_sig = defaultdict(list)
for e in events:
    by_sig[e.get("sig")].append(e)

# ═══ 1) Ghost signals: investigar por qué no finalizan ═══
print("=" * 78)
print("1) GHOST SIGNALS — por qué auto-finalize NO disparó")
print("=" * 78)

ghosts = ["canal1_19269", "canal1_19296", "canal1_19318",
          "canal1_19356", "canal1_19373", "canal2_12101"]

for sid in ghosts:
    evs = by_sig.get(sid, [])
    if not evs:
        continue
    print(f"\n--- {sid} ---")
    last_ev = evs[-1]
    print(f"  Total events: {len(evs)}")
    print(f"  Último event: {last_ev.get('ts','')[:19]}  ev={last_ev.get('ev')}")
    has_filled = any(e.get("ev")=="market_filled" for e in evs)
    has_dca = sum(1 for e in evs if e.get("ev")=="dca_filled")
    has_closed = any(e.get("ev")=="signal_closed" for e in evs)
    has_pos_closed = any(e.get("ev")=="positions_closed_by_mt5" for e in evs)
    print(f"  market_filled: {has_filled}  dca_filled: {has_dca}  "
          f"positions_closed_by_mt5: {has_pos_closed}  signal_closed: {has_closed}")
    # Mostrar timeline corta
    for e in evs[-6:]:
        ev = e.get("ev")
        ts = e.get("ts","")[:23]
        extra = ""
        if ev == "market_filled": extra = f" #{e.get('ticket')}"
        elif ev == "mgmt_msg":
            snip = (e.get("raw_snippet","") or "")[:50].replace("\n"," | ")
            extra = f" [{e.get('action')}] {snip!r}"
        elif ev == "dca_filled": extra = f" lvl={e.get('level')}"
        print(f"    {ts}  {ev:30}{extra}")

# ═══ 2) canal1_19269 fill latency 6 segundos ═══
print("\n\n" + "=" * 78)
print("2) canal1_19269: fill latency anómalo de 6151ms")
print("=" * 78)

evs = by_sig.get("canal1_19269", [])
print(f"\nTimeline completa:")
for e in evs:
    ts = e.get("ts","")[:23]
    ev = e.get("ev")
    extra = ""
    for k in ("ticket","price","latency_ms","tg_to_bot_ms","direction","action"):
        if k in e: extra += f" {k}={e[k]}"
    print(f"  {ts}  {ev:30}{extra}")

# ═══ 3) Canal 2 delay: cuándo / patrón ═══
print("\n\n" + "=" * 78)
print("3) CANAL 2 DELAY — patrón temporal y causas")
print("=" * 78)

c2_recv = [e for e in events if e.get("ev")=="signal_received" and e.get("channel")=="canal2"]
c2_recv.sort(key=lambda e: e.get("ts",""))

# Por hora del día y por día
from collections import Counter
by_hour = defaultdict(list)
by_date = defaultdict(list)
for e in c2_recv:
    d = e.get("tg_to_bot_ms")
    if d is None: continue
    hour = int(e.get("ts","00")[11:13])
    date = e.get("ts","")[:10]
    by_hour[hour].append(d)
    by_date[date].append(d)

print("\nDelay canal 2 por DÍA:")
for d in sorted(by_date):
    delays = by_date[d]
    avg = sum(delays)/len(delays)
    mx = max(delays)
    print(f"  {d}:  n={len(delays):>2}  avg={avg:>6.0f}ms  max={mx:>6}ms")

print("\nDelay canal 2 por HORA del día (UTC):")
for h in sorted(by_hour):
    delays = by_hour[h]
    avg = sum(delays)/len(delays)
    print(f"  {h:02d}h:  n={len(delays):>2}  avg={avg:>6.0f}ms  max={max(delays):>6}ms")

# ═══ 4) Telegram connection events ═══
print("\n\n" + "=" * 78)
print("4) TELEGRAM CONNECTION EVENTS (correlacionar con delays)")
print("=" * 78)
conn = [e for e in events if "telegram_connection" in e.get("ev","")]
print(f"\nTotal eventos: {len(conn)}")
for e in conn[:30]:
    print(f"  {e.get('ts','')[:23]}  {e.get('ev'):35}  connected={e.get('connected')}  "
          f"prev_dur={e.get('previous_state_duration_sec','?')}")

# ═══ 5) Mgmt actions canal 1 con alias (post-fix) ═══
print("\n\n" + "=" * 78)
print("5) MGMT ACTIONS canal 1 después del alias fix (debería funcionar)")
print("=" * 78)
c1_mgmt = [e for e in events if e.get("ev")=="mgmt_msg" and e.get("sig","").startswith("canal1_")]
print(f"\nTotal mgmt msgs canal 1: {len(c1_mgmt)}")
applied = sum(1 for e in c1_mgmt if e.get("will_apply"))
print(f"  Aplicados: {applied}")
print(f"  Ignorados: {len(c1_mgmt) - applied}")
print(f"\nPor acción:")
acts = Counter((e.get("action"), e.get("will_apply")) for e in c1_mgmt)
for (a, applied), n in acts.most_common():
    print(f"  {a:<22}  applied={applied}  n={n}")
