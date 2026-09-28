"""Block P: how each provider behaves. Compares the market situation at the real signal moments with
random moments of the same day and direction (placebo universes s0-s2), feature by feature, and
fits a small interpretable tree "signal moment vs random moment" with cross-validation by month.

Effect size = (mean real - mean placebo) / pooled sd; plus the KS distance between distributions.
All features are causal (research/signal_features*.py, tested).
"""
from __future__ import annotations

import json
from collections import Counter

import numpy as np

from research.signal_filters import load_features

NUMERIC = ("pre_move_5", "pre_move_15", "pre_move_60", "pre_range_60", "rsi_m5", "rsi_m15", "atr_m15", "trend_m15",
           "loc_4h", "day_move", "gap_prev_min", "spread", "min_in_session", "pd_loc", "pd_break", "asia_loc",
           "sweep_fav", "sweep_adv", "dist_round50", "vol_regime", "news_min", "burst_60", "dir_streak")
LABELS = {"pre_move_5": "movimiento previo 5 min (a favor +)", "pre_move_15": "movimiento previo 15 min (a favor +)",
          "pre_move_60": "movimiento previo 60 min (a favor +)", "pre_range_60": "rango de la última hora",
          "rsi_m5": "RSI 5 min (orientado)", "rsi_m15": "RSI 15 min (orientado)", "atr_m15": "ATR 15 min",
          "trend_m15": "distancia a la EMA50 de 15 min (a favor +)", "loc_4h": "posición en el rango de 4 h (0 = extremo favorable)",
          "day_move": "movimiento del día (a favor +)", "gap_prev_min": "minutos desde la señal anterior", "spread": "spread",
          "min_in_session": "minutos desde la apertura de sesión", "pd_loc": "posición en el rango de ayer (0 = extremo favorable)",
          "pd_break": "ruptura del rango de ayer (+1 a favor)", "asia_loc": "posición en el rango asiático (0 = extremo favorable)",
          "sweep_fav": "barrido de liquidez a favor", "sweep_adv": "barrido de liquidez en contra",
          "dist_round50": "distancia a número redondo (x00/x50)", "vol_regime": "volatilidad relativa (1 = normal)",
          "news_min": "minutos a la noticia fuerte más cercana", "burst_60": "señales en la hora anterior",
          "dir_streak": "señales seguidas en la misma dirección"}


def values(feats, channel, key):
    return np.array([float(f[key]) for f in feats.values()
                     if f.get("channel") == channel and f.get("status") == "ok" and key in f and f[key] is not None
                     and not isinstance(f[key], str)])


def ks(a, b):
    grid = np.sort(np.concatenate([a, b]))
    fa = np.searchsorted(np.sort(a), grid, side="right") / len(a)
    fb = np.searchsorted(np.sort(b), grid, side="right") / len(b)
    return float(np.max(np.abs(fa - fb)))


def compare(real_path, placebo_paths, channel):
    real = load_features(real_path)
    plac = {}
    for p in placebo_paths:
        plac.update({f"{k}|{p}": v for k, v in load_features(p).items()})
    rows = []
    for key in NUMERIC:
        a, b = values(real, channel, key), values(plac, channel, key)
        if len(a) < 30 or len(b) < 30:
            continue
        sd = np.sqrt((a.var() + b.var()) / 2) or 1.0
        rows.append({"rasgo": key, "texto": LABELS.get(key, key), "media_real": round(float(a.mean()), 3),
                     "media_azar": round(float(b.mean()), 3), "mediana_real": round(float(np.median(a)), 3),
                     "mediana_azar": round(float(np.median(b)), 3), "efecto": round(float((a.mean() - b.mean()) / sd), 3),
                     "ks": round(ks(a, b), 3), "n_real": int(len(a))})
    rows.sort(key=lambda r: -abs(r["efecto"]))
    return rows


def timing(universe_path, channel):
    sigs = [json.loads(l) for l in open(universe_path, encoding="utf-8")]
    sigs = [s for s in sigs if s["channel"] == channel]
    hours = Counter(int(s["published_utc"][11:13]) for s in sigs)
    wd = Counter(__import__("datetime").date.fromisoformat(s["published_utc"][:10]).weekday() for s in sigs)
    dirs = Counter(s["direction"] for s in sigs)
    return {"n": len(sigs), "por_hora_utc": dict(sorted(hours.items())), "por_dia_semana": dict(sorted(wd.items())),
            "direcciones": dict(dirs)}
