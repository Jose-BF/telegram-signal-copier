"""Search 2 level-1 calculator, part 2 (milestone M2): basket rules on 1 s paths with numba.

Covers the families of the catalogue that need the path, not just first-passage tables:
entry at market / pullback / adverse-reversal (555) / momentum / delay, ladders against or in
favour with per-leg lots, per-leg targets (steps or fixed), partial + runner, fixed stop,
trailing, break-even, hard stop in EUR per leg, basket loss cap, profit lock, time exits.

Approximations (quantified against the exact engine by search2/check_grid_vs_engine.py):
- 1 s buckets. Inside a second the stop is checked before the target (worst case); trailing
  and break-even use the previous second; client-side closes (cap, lock, time) fill at their
  threshold or at the last quote of the second. No broker latency besides the entry latency
  already in the path; no rejections; no margin.
- Prices are relative to the BUY market entry (ask0 = 0). The SELL market entry is -spread0.

Rule layout: a float64 vector of N_PARAMS (see PARAM, `encode`). Output per signal:
pnl EUR, lowest basket equity (realized + floating at the adverse extreme) EUR, filled legs,
seconds until the basket closed, censored flag (still open at the cutoff: marked to market).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numba as nb
import numpy as np

from research.signal_paths import iter_paths

MAX_LEGS = 8
PARAM = {name: i for i, name in enumerate((
    "invert", "entry_mode", "e_x", "e_y", "expiry_s", "legs", "ladder_mode", "step",
    *[f"w{i}" for i in range(MAX_LEGS)], "tp_mode", *[f"tp{i}" for i in range(MAX_LEGS)],
    "sl_move", "trail", "be_trigger", "basket_stop_eur", "lock_arm_eur", "lock_give_eur",
    "time_s", "time_mode", "hard_stop_eur_leg", "partial_frac", "partial_tp", "runner_tp"))}
N_PARAMS = len(PARAM)
ENTRY = {"market": 0, "pullback": 1, "adverse_reversal": 2, "momentum": 3, "delay": 4}
TIME_MODE = {"always": 0, "loss_only": 1, "profit_only": 2, "non_negative": 3}


@dataclass(frozen=True)
class BasketRule:
    invert: bool = False
    entry: str = "market"
    e_x: float = 0.0            # pullback/momentum distance, adverse move for adverse_reversal ($)
    e_y: float = 0.0            # rebound for adverse_reversal ($)
    expiry_min: float = 15.0    # waiting time for the first entry and for ladder legs (from the signal)
    delay_min: float = 0.0
    weights: tuple = (0.01,)
    ladder: str = "adverse"     # adverse | favourable
    step: float = 0.0
    tp_steps: tuple = ()        # per-leg target distances from each leg's entry ($); () = none
    sl_move: float = 0.0        # per-leg stop distance ($); 0 = none
    trail: float = 0.0
    be_trigger: float = 0.0
    basket_stop_eur: float = 0.0
    lock_arm_eur: float = 0.0
    lock_give_eur: float = 0.0
    time_min: float = 0.0
    time_mode: str = "always"
    hard_stop_eur_leg: float = 0.0
    partial_frac: float = 0.0
    partial_tp: float = 0.0
    runner_tp: float = 0.0
    tags: tuple = field(default=(), compare=False)

    def encode(self) -> np.ndarray:
        if not 1 <= len(self.weights) <= MAX_LEGS:
            raise ValueError("1..8 legs")
        v = np.zeros(N_PARAMS)
        v[PARAM["invert"]] = float(self.invert)
        v[PARAM["entry_mode"]] = ENTRY[self.entry]
        v[PARAM["e_x"]] = self.delay_min * 60 if self.entry == "delay" else self.e_x
        v[PARAM["e_y"]] = self.e_y
        v[PARAM["expiry_s"]] = self.expiry_min * 60
        v[PARAM["legs"]] = len(self.weights)
        v[PARAM["ladder_mode"]] = 1.0 if self.ladder == "favourable" else 0.0
        v[PARAM["step"]] = self.step
        for i, w in enumerate(self.weights):
            v[PARAM[f"w{i}"]] = w
        if self.tp_steps:
            v[PARAM["tp_mode"]] = 1
            steps = tuple(self.tp_steps) + (self.tp_steps[-1],) * (len(self.weights) - len(self.tp_steps))
            for i, s in enumerate(steps[:len(self.weights)]):
                v[PARAM[f"tp{i}"]] = s
        for name in ("sl_move", "trail", "be_trigger", "basket_stop_eur", "lock_arm_eur", "lock_give_eur",
                     "hard_stop_eur_leg", "partial_frac", "partial_tp", "runner_tp"):
            v[PARAM[name]] = getattr(self, name)
        v[PARAM["time_s"]] = self.time_min * 60
        v[PARAM["time_mode"]] = TIME_MODE[self.time_mode]
        return v

    @property
    def worst_case_eur_per_fx(self) -> float | None:
        """Maximum designed loss per basket in USD-per-oz x lots (divide by fx for EUR); None if unbounded."""
        caps = []
        if self.basket_stop_eur:
            return None  # money cap: handled in EUR by the caller (loss <= basket_stop_eur)
        if self.sl_move:
            caps.append(sum(w * 100 * (self.sl_move + (i * self.step if self.ladder == "adverse" else 0.0))
                            for i, w in enumerate(self.weights)))
        return min(caps) if caps else None


@dataclass
class Paths:
    """All 1 s paths of a universe concatenated (relative to ask0; A_* = ask - ask0)."""
    ids: np.ndarray
    channel: np.ndarray
    direction: np.ndarray   # +1 BUY / -1 SELL (signal)
    observed: np.ndarray
    spread0: np.ndarray
    fx: np.ndarray
    offsets: np.ndarray     # int64, len n+1
    Bhi: np.ndarray
    Blo: np.ndarray
    Blast: np.ndarray
    Ahi: np.ndarray
    Alo: np.ndarray
    Alast: np.ndarray


def build_paths(tick_root, universe, days=None, seed=0, latency_ms=789) -> Paths:
    ids, ch, dirs, obs, sp, fxs, parts = [], [], [], [], [], [], []
    for trig, sig, p, fx, reason in iter_paths(tick_root, universe, days, seed, latency_ms):
        if reason:
            continue
        s0 = p.ask0 - p.bid0
        ids.append(trig.trigger_id); ch.append(trig.channel); dirs.append(1 if trig.direction == "BUY" else -1)
        obs.append(sig.observed_at.isoformat()); sp.append(s0); fxs.append(fx)
        # p.ask_* are relative to bid0; convert to ask0-relative: ask - ask0 = (ask - bid0) - s0
        parts.append((p.bid_hi, p.bid_lo, p.bid_last, p.ask_hi - s0, p.ask_lo - s0, p.ask_last - s0))
    order = np.argsort(np.array(obs), kind="stable")
    parts = [parts[i] for i in order]
    lens = np.array([len(x[0]) for x in parts], dtype=np.int64)
    offsets = np.concatenate([[0], np.cumsum(lens)])
    cat = [np.concatenate([x[j] for x in parts]).astype(np.float32) for j in range(6)]
    pick = lambda a, dt=None: np.array([a[i] for i in order], dtype=dt)
    return Paths(pick(ids), pick(ch), pick(dirs, np.int8), pick(obs), pick(sp), pick(fxs), offsets, *cat)


@nb.njit(cache=True)
def _one(r, dirn, s0, fx, Bhi, Blo, Blast, Ahi, Alo, Alast):
    n = len(Blast)
    legs = int(r[5])
    entry_mode = int(r[1])
    e_x, e_y, expiry = r[2], r[3], r[4]
    step, ladder_fav = r[7], r[6] > 0.5
    tp_mode = int(r[16])
    sl_move, trail, be_trig = r[25], r[26], r[27]
    bstop, lock_arm, lock_give = r[28], r[29], r[30]
    time_s, time_mode, hard = r[31], int(r[32]), r[33]
    pfrac, ptp, rtp = r[34], r[35], r[36]
    NAN = np.nan
    entry_px = np.full(MAX_LEGS, NAN)
    vol = np.zeros(MAX_LEGS)         # open volume per leg
    part_done = np.zeros(MAX_LEGS, dtype=np.bool_)
    trail_sl = np.full(MAX_LEGS, NAN)
    be_on = np.zeros(MAX_LEGS, dtype=np.bool_)
    realized = 0.0
    k_first = -1
    filled = 0
    armed = False
    ext = 0.0
    max_total = -1e18
    lock_armed = False
    min_total = 0.0
    p0 = 0.0 if dirn > 0 else -s0            # market entry price (ask0 or bid0)
    closed_k = -1
    censored = False
    fill_next = False
    for k in range(-1, n):
        # --- quotes of this second (k = -1 is the entry tick itself)
        if k == -1:
            if dirn > 0:
                ent_lo = ent_hi = ent_last = 0.0
                ex_fav = ex_adv = ex_last = -s0
            else:
                ent_lo = ent_hi = ent_last = -s0
                ex_fav = ex_adv = ex_last = 0.0
        else:
            if dirn > 0:
                ent_lo, ent_hi, ent_last = Alo[k], Ahi[k], Alast[k]
                ex_fav, ex_adv, ex_last = Bhi[k], Blo[k], Blast[k]
            else:
                ent_lo, ent_hi, ent_last = Blo[k], Bhi[k], Blast[k]
                ex_fav, ex_adv, ex_last = Alo[k], Ahi[k], Alast[k]
            if np.isnan(ex_fav):          # no quote in this second
                ex_fav = ex_adv = ex_last
                ent_lo = ent_hi = ent_last
        t = k + 1                          # seconds since the entry tick
        # --- first entry
        if filled == 0:
            if t > expiry and entry_mode != 0:
                break
            px = NAN
            if fill_next:
                # conditional entries go to market: the bot sees the condition on a ~0.5 s old quote
                # and the order fills ~0.8 s later -> fill at this (next) second's last quote
                px = ent_last
            elif entry_mode == 0 and k == -1:
                px = p0
            elif entry_mode == 4:
                if t >= e_x:
                    px = ent_last
            elif entry_mode == 1 and k >= 0:
                lvl = p0 - dirn * e_x
                if (dirn > 0 and ent_lo <= lvl) or (dirn < 0 and ent_hi >= lvl):
                    px = lvl
            elif entry_mode == 3 and k >= 0:
                lvl = p0 + dirn * e_x
                if (dirn > 0 and ent_hi >= lvl) or (dirn < 0 and ent_lo <= lvl):
                    px = lvl
            elif entry_mode == 2 and k >= 0:
                if armed:
                    lvl = ext + dirn * e_y
                    if (dirn > 0 and ent_hi >= lvl) or (dirn < 0 and ent_lo <= lvl):
                        px = lvl
                if np.isnan(px):
                    adv = ent_lo if dirn > 0 else ent_hi
                    if not armed:
                        if dirn * (adv - p0) <= -e_x:
                            armed = True
                            ext = adv
                    elif dirn * (adv - ext) < 0:
                        ext = adv
            if np.isnan(px):
                continue
            if not fill_next and (entry_mode == 2 or entry_mode == 3 or entry_mode == 4):
                fill_next = True
                continue
            entry_px[0] = px
            vol[0] = r[8]
            filled = 1
            k_first = k
            if trail > 0:
                trail_sl[0] = ex_last - dirn * trail
            if step == 0.0:                 # simultaneous: every leg at the same price at once
                while filled < legs:
                    entry_px[filled] = px
                    vol[filled] = r[8 + filled]
                    if trail > 0:
                        trail_sl[filled] = ex_last - dirn * trail
                    filled += 1
            if k < 0:
                continue
        # --- ladder legs (not in the same second as the first entry)
        elif filled < legs and t <= expiry:
            while filled < legs:
                i = filled
                lvl = entry_px[0] + (dirn * step * i if ladder_fav else -dirn * step * i)
                hit = False
                if ladder_fav:
                    hit = (dirn > 0 and ent_hi >= lvl) or (dirn < 0 and ent_lo <= lvl)
                else:
                    hit = (dirn > 0 and ent_lo <= lvl) or (dirn < 0 and ent_hi >= lvl)
                if not hit:
                    break
                entry_px[i] = lvl
                vol[i] = r[8 + i]
                if trail > 0:
                    trail_sl[i] = ex_last - dirn * trail
                filled += 1
        if filled == 0:
            continue
        # --- per-leg exits: stop first (worst case), then target
        any_open = False
        for i in range(filled):
            if vol[i] <= 0:
                continue
            e = entry_px[i]
            sl = NAN
            if sl_move > 0:
                sl = e - dirn * sl_move
            if hard > 0:
                h = e - dirn * hard * fx / (r[8 + i] * 100.0)
                if np.isnan(sl) or dirn * (h - sl) > 0:
                    sl = h
            if be_on[i]:
                if np.isnan(sl) or dirn * (e - sl) > 0:
                    sl = e
            if trail > 0 and not np.isnan(trail_sl[i]):
                if np.isnan(sl) or dirn * (trail_sl[i] - sl) > 0:
                    sl = trail_sl[i]
            if not np.isnan(sl) and dirn * (ex_adv - sl) <= 0:
                realized += vol[i] * 100.0 * dirn * (sl - e) / fx
                vol[i] = 0.0
                continue
            if pfrac > 0:
                if not part_done[i] and dirn * (ex_fav - (e + dirn * ptp)) >= 0:
                    closed = r[8 + i] * pfrac
                    realized += closed * 100.0 * ptp / fx
                    vol[i] -= closed
                    part_done[i] = True
                if rtp > 0 and dirn * (ex_fav - (e + dirn * rtp)) >= 0:
                    realized += vol[i] * 100.0 * rtp / fx
                    vol[i] = 0.0
                    continue
            elif tp_mode == 1:
                tpd = r[17 + i]
                if dirn * (ex_fav - (e + dirn * tpd)) >= 0:
                    realized += vol[i] * 100.0 * tpd / fx
                    vol[i] = 0.0
                    continue
            any_open = True
            # updates that act from the next second on
            if be_trig > 0 and not be_on[i] and dirn * (ex_fav - e) >= be_trig:
                be_on[i] = True
            if trail > 0:
                cand = ex_last - dirn * trail
                if np.isnan(trail_sl[i]) or dirn * (cand - trail_sl[i]) > 0:
                    trail_sl[i] = cand
        # --- basket level
        fl_worst = 0.0
        fl_last = 0.0
        fl_best = 0.0
        for i in range(filled):
            if vol[i] > 0:
                fl_worst += vol[i] * 100.0 * dirn * (ex_adv - entry_px[i]) / fx
                fl_last += vol[i] * 100.0 * dirn * (ex_last - entry_px[i]) / fx
                fl_best += vol[i] * 100.0 * dirn * (ex_fav - entry_px[i]) / fx
        tot_worst = realized + fl_worst
        tot_last = realized + fl_last
        tot_best = realized + fl_best
        if tot_worst < min_total:
            min_total = tot_worst
        if not any_open:          # every filled leg closed: the basket is done, pending legs cancelled
            closed_k = k
            break
        if bstop > 0 and tot_worst <= -bstop:
            realized = -bstop
            for i in range(filled):
                vol[i] = 0.0
            closed_k = k
            break
        if lock_arm > 0:
            # trigger against the peak seen up to the previous second, then update the peak with
            # this second's best (the engine tracks the peak tick by tick)
            if lock_armed and tot_worst <= max_total - lock_give:
                realized = max_total - lock_give
                for i in range(filled):
                    vol[i] = 0.0
                closed_k = k
                break
            if tot_best > max_total:
                max_total = tot_best
            if max_total >= lock_arm:
                lock_armed = True
        if time_s > 0 and t - (k_first + 1) >= time_s:
            ok = (time_mode == 0 or (time_mode == 1 and tot_last <= 0) or (time_mode == 2 and tot_last > 0)
                  or (time_mode == 3 and tot_last >= 0))
            if ok:
                realized = tot_last
                for i in range(filled):
                    vol[i] = 0.0
                closed_k = k
                break
    if filled == 0:
        return 0.0, 0.0, 0, 0, False, -2
    open_left = False
    for i in range(filled):
        if vol[i] > 0:
            open_left = True
    if open_left:                                   # cutoff: mark to market at the last quote
        last = Blast[n - 1] if dirn > 0 else Alast[n - 1]
        for i in range(filled):
            if vol[i] > 0:
                realized += vol[i] * 100.0 * dirn * (last - entry_px[i]) / fx
        censored = True
        closed_k = n - 1
    return realized, min(min_total, realized), filled, closed_k - k_first, censored, k_first


@nb.njit(parallel=True, cache=True)
def _batch(R, dirs, s0, fx, offsets, Bhi, Blo, Blast, Ahi, Alo, Alast, pnl, mint, legs, dur, cens, first):
    n = len(dirs)
    for j in range(R.shape[0]):
        r = R[j]
        for s in nb.prange(n):
            a, b = offsets[s], offsets[s + 1]
            d = -dirs[s] if r[0] > 0.5 else dirs[s]
            x = _one(r, d, s0[s], fx[s], Bhi[a:b], Blo[a:b], Blast[a:b], Ahi[a:b], Alo[a:b], Alast[a:b])
            pnl[s, j] = x[0]; mint[s, j] = x[1]; legs[s, j] = x[2]; dur[s, j] = x[3]; cens[s, j] = x[4]
            first[s, j] = x[5]


def evaluate(paths: Paths, rules, mask=None):
    """Run rules over the paths (optionally a boolean subset of signals).
    Returns dict of [signal, rule] arrays: pnl, min_equity (EUR), legs, seconds, censored,
    entry_second (second of the first fill; -1 = the entry tick, -2 = not filled)."""
    sel = np.flatnonzero(mask) if mask is not None else np.arange(len(paths.ids))
    R = np.stack([r.encode() if isinstance(r, BasketRule) else np.asarray(r, float) for r in rules])
    n = len(sel)
    offs = np.concatenate([[0], np.cumsum(paths.offsets[sel + 1] - paths.offsets[sel])])
    idx = np.concatenate([np.arange(paths.offsets[s], paths.offsets[s + 1]) for s in sel]) if n else np.zeros(0, np.int64)
    arrs = [getattr(paths, k)[idx] for k in ("Bhi", "Blo", "Blast", "Ahi", "Alo", "Alast")] if len(sel) != len(paths.ids) \
        else [getattr(paths, k) for k in ("Bhi", "Blo", "Blast", "Ahi", "Alo", "Alast")]
    out = {k: np.zeros((n, len(R)), dt) for k, dt in (("pnl", np.float64), ("min_equity", np.float64),
                                                       ("legs", np.int8), ("seconds", np.int32), ("censored", np.bool_),
                                                       ("entry_second", np.int32))}
    _batch(R, paths.direction[sel].astype(np.int64), paths.spread0[sel].astype(np.float64), paths.fx[sel].astype(np.float64),
           offs.astype(np.int64), *arrs, out["pnl"], out["min_equity"], out["legs"], out["seconds"], out["censored"],
           out["entry_second"])
    return out
