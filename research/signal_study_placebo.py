"""Placebo controls for the signal study: is there information in the direction or the timing?

  flipped      : same moment, opposite direction.
  random_time  : same direction, a random moment of the same UTC day between 06:00 and 20:00
                 (deterministic seed), i.e. same market regime, no provider timing.
If real ~= placebos, the provider's signal carries no measurable edge at these horizons.
"""
import hashlib, json, sys
from datetime import datetime, timedelta
from pathlib import Path

from research.signal_study import TickStore, study_signal


def rnd(key):
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") / 2 ** 64


def variants(sig, seed):
    flipped = {**sig, "direction": "SELL" if sig["direction"] == "BUY" else "BUY"}
    pub = datetime.fromisoformat(sig["published_utc"])
    day0 = pub.replace(hour=6, minute=0, second=0, microsecond=0)
    moved = {**sig, "published_utc": (day0 + timedelta(seconds=rnd(f"{seed}|{sig['id']}") * 14 * 3600)).isoformat()}
    return {"flipped": flipped, "random_time": moved}


if __name__ == "__main__":
    sigs = [json.loads(l) for l in open(sys.argv[1])]
    store = TickStore(Path(sys.argv[2])); out = Path(sys.argv[3]); seeds = int(sys.argv[4])
    rows = []
    for s in sorted(sigs, key=lambda s: s["published_utc"]):
        for seed in range(seeds):
            for name, v in variants(s, seed).items():
                if name == "flipped" and seed > 0:
                    continue
                r = study_signal(v, store); r["variant"] = name; r["seed"] = seed
                rows.append(r)
    out.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    print(len(rows))
