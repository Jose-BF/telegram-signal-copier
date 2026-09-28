"""Causality of the v1 signal features (research/signal_features.py) on real ticks: recomputing each
feature with a tick store that cannot see anything at or after t0 must give the same values."""
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from research.signal_features import features
from research.signal_study import MEDIAN_DELAY_S, TickStore

ROOT = Path("runtime_data/ticks_all")
UNIVERSE = Path("runtime_data/signal_universe_v1/signals.jsonl")


class ClippedStore(TickStore):
    """Same store, but every window is cut strictly before `limit_ms` (the signal's t0)."""

    def __init__(self, root, limit_ms):
        super().__init__(root)
        self.limit_ms = limit_ms

    def window(self, start_ms, end_ms):
        got = super().window(start_ms, min(end_ms, self.limit_ms))
        if got is None:
            return None
        t, b, a = got
        keep = t < self.limit_ms
        return (t[keep], b[keep], a[keep]) if keep.any() else None


@pytest.mark.skipif(not UNIVERSE.exists() or not (ROOT / "XAUUSD").exists(), reason="local data not present")
def test_v1_features_do_not_use_ticks_at_or_after_t0():
    sigs = sorted((json.loads(l) for l in UNIVERSE.open(encoding="utf-8")), key=lambda s: s["published_utc"])
    rng = np.random.default_rng(0)
    sample = [sigs[i] for i in sorted(rng.choice(len(sigs), size=40, replace=False))]
    full = TickStore(ROOT)
    checked = 0
    for s in sample:
        t0 = int((datetime.fromisoformat(s["published_utc"]).timestamp() + MEDIAN_DELAY_S[s["channel"]]) * 1000)
        a = features(s, full, None, None)
        b = features(s, ClippedStore(ROOT, t0), None, None)
        if a.get("status") != "ok":
            continue
        checked += 1
        for k, v in a.items():
            if isinstance(v, float):
                assert b.get(k) == pytest.approx(v, abs=1e-9), (s["id"], k, v, b.get(k))
            else:
                assert b.get(k) == v, (s["id"], k, v, b.get(k))
    assert checked >= 30
