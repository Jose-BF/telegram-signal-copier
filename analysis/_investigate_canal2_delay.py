"""Investigación del delay específico canal 2 reportado por usuario."""
import io, json, sys, statistics
from collections import defaultdict, Counter
from datetime import datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

events = []
with open("data/trade_events.jsonl", encoding="utf-8") as f:
    for line in f:
        if line.strip():
            events.append(json.loads(line))

# Solo signal_received events para comparar latencias
signals_recv = [e for e in events if e.get("ev") == "signal_received"]
print(f"\nTotal signal_received: {len(signals_recv)}")

by_ch = defaultdict(list)
for e in signals_recv:
    by_ch[e.get("channel", "?")].append(e)

print("\n=== TG_TO_BOT_MS (delay Telegram -> bot recibe) ===\n")
for ch in sorted(by_ch):
    delays = [e.get("tg_to_bot_ms") for e in by_ch[ch] if e.get("tg_to_bot_ms") is not None]
    if not delays:
        print(f"  {ch}:  sin datos tg_to_bot_ms")
        continue
    delays_sorted = sorted(delays)
    print(f"  {ch}:  n={len(delays)}")
    print(f"    min={min(delays):>7}ms  med={statistics.median(delays):>7.0f}ms  "
          f"avg={statistics.mean(delays):>7.0f}ms  max={max(delays):>7}ms")
    # Histograma
    buckets = [(0,500), (500,1000), (1000,2000), (2000,5000), (5000,10000), (10000,30000), (30000,99999999)]
    print(f"    Distribución:")
    for lo, hi in buckets:
        n = sum(1 for d in delays if lo <= d < hi)
        if n > 0:
            bar = "#" * min(40, n*2)
            label = f"{lo/1000:.0f}-{hi/1000:.0f}s" if hi < 99999999 else f">{lo/1000:.0f}s"
            print(f"      {label:<10} {n:>3}  {bar}")
    # Top 5 worst
    worst = sorted(by_ch[ch], key=lambda e: -(e.get("tg_to_bot_ms") or 0))[:5]
    print(f"    Top 5 peores:")
    for e in worst:
        print(f"      {e.get('ts','')[:19]}  {e.get('tg_to_bot_ms')}ms  sig={e.get('sig')} dir={e.get('direction')}")

# Fill latency (signal_received -> market_filled)
print("\n=== FILL LATENCY (signal_received -> market_filled) ===\n")

# Construir map sig -> latency
fills = {}
for e in events:
    if e.get("ev") == "market_filled":
        fills[e.get("sig")] = e.get("latency_ms")

for ch in sorted(by_ch):
    lats = []
    for s in by_ch[ch]:
        sig = s.get("sig")
        if sig in fills and fills[sig] is not None:
            lats.append(fills[sig])
    if not lats:
        print(f"  {ch}:  sin datos")
        continue
    print(f"  {ch}:  n={len(lats)}  min={min(lats)}ms  med={statistics.median(lats):.0f}ms  max={max(lats)}ms")

# Telegram connection events
print("\n=== EVENTOS TELEGRAM CONNECTION ===\n")
conn_events = [e for e in events if e.get("ev","").startswith("telegram_connection")]
disconnects = [e for e in conn_events if e.get("ev") == "telegram_connection_change" and e.get("connected") == False]
reconnects = [e for e in conn_events if e.get("ev") == "telegram_connection_change" and e.get("connected") == True]
print(f"  Total connection_change events: {len(disconnects) + len(reconnects)}")
print(f"    Desconexiones: {len(disconnects)}")
print(f"    Reconexiones:  {len(reconnects)}")
if disconnects:
    print(f"  Lista desconexiones:")
    for d in disconnects[:20]:
        prev_dur = d.get("previous_state_duration_sec", "?")
        print(f"    {d.get('ts','')[:19]}  duró conectado {prev_dur}s antes")

# Ranges arrival lag
print("\n=== RANGE ARRIVAL DELAY (señal recibida -> rango llega) ===\n")
ranges = {e.get("sig"): e.get("delay_sec") for e in events if e.get("ev")=="range_arrived"}
for ch in sorted(by_ch):
    delays = []
    for s in by_ch[ch]:
        sig = s.get("sig")
        if sig in ranges and ranges[sig] is not None:
            delays.append(ranges[sig])
    if not delays:
        continue
    print(f"  {ch}:  n={len(delays)}  min={min(delays):.1f}s  med={statistics.median(delays):.1f}s  max={max(delays):.1f}s")
