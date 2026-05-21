"""Benchmark del hot path actual: regex parser, journal write, asyncio overhead.

Mide el coste REAL de cada paso para decidir qué optimizar y qué no.
NO toca MT5 (queremos una medición pura del código del bot).
"""
import sys, io, os, time, asyncio, statistics
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.getcwd())

import journal
from parser import parse_canal2
from classifier import _regex_classify_all

N = 1000  # iteraciones por benchmark

def bench(label, fn):
    times = []
    # warmup
    for _ in range(10):
        fn()
    for _ in range(N):
        t0 = time.perf_counter_ns()
        fn()
        t1 = time.perf_counter_ns()
        times.append((t1 - t0) / 1000)  # microseconds
    times.sort()
    p50 = times[N // 2]
    p95 = times[int(N * 0.95)]
    p99 = times[int(N * 0.99)]
    print(f"  {label:<50}  p50={p50:>8.1f}us  p95={p95:>8.1f}us  p99={p99:>8.1f}us")

print("\n=== BENCHMARK HOT PATH ===\n")

# 1. Regex parsing
sample = "XAU USD BUY NOW\n\n4598-4603\nTP1 4606  SL 4595"
bench("parse_canal2 (regex completo: dir+range+tps+sl)", lambda: parse_canal2(sample))

mgmt_sample = "Move SL to BE"
bench("classify (regex: 'Move SL to BE')", lambda: _regex_classify_all(mgmt_sample))

# 2. Journal write a fichero (con file lock)
def write_event():
    journal.event("benchmark_test", "test_event",
                  field1="value", field2=123, field3=4.56,
                  nested={"a": 1, "b": 2})
bench("journal.event (escribe a JSONL con lock)", write_event)

# 3. Datetime + isoformat
from datetime import datetime
def dt_now():
    return datetime.utcnow().isoformat(timespec="milliseconds")
bench("datetime.utcnow().isoformat()", dt_now)

# 4. Print a stdout (sin tee)
import io as _io
_devnull = open(os.devnull, "w")
def print_to_devnull():
    print("benchmark line with some text and a number 4567.89", file=_devnull)
bench("print() a /dev/null (referencia)", print_to_devnull)
_devnull.close()

# 5. asyncio overhead: run_in_executor con función trivial
def trivial(): return 42
async def bench_run_in_executor():
    loop = asyncio.get_event_loop()
    times = []
    for _ in range(10): await loop.run_in_executor(None, trivial)
    for _ in range(N):
        t0 = time.perf_counter_ns()
        await loop.run_in_executor(None, trivial)
        t1 = time.perf_counter_ns()
        times.append((t1-t0)/1000)
    times.sort()
    p50 = times[N//2]; p95 = times[int(N*0.95)]; p99 = times[int(N*0.99)]
    print(f"  {'asyncio.run_in_executor (thread pool dispatch)':<50}  p50={p50:>8.1f}us  p95={p95:>8.1f}us  p99={p99:>8.1f}us")
asyncio.run(bench_run_in_executor())

print()
# Limpieza: borra el evento de test del log
import json
from pathlib import Path
ev_file = Path("data/trade_events.jsonl")
if ev_file.exists():
    lines = ev_file.read_text(encoding="utf-8").splitlines()
    lines = [l for l in lines if "benchmark_test" not in l]
    ev_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  (limpieza: eliminadas líneas benchmark del log)")
