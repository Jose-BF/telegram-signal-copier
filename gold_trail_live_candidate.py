"""Frozen Gold Signals NOW "trail" demo policy (research of 02-03/10/2026).

Two modes share one management:
  * ``desarrollada``: follow the provider only when the day's momentum is with
    his signal (2 or more of 4 causal pieces).
  * ``candidata``: same, plus trade the OPPOSITE of the provider when he goes
    against the momentum (0-1 pieces with him and 2 or more against him), and
    ignore signals published on a minute multiple of 5 (scheduled-looking).

Management (both modes): one market position, stop at 20 $ from the real fill,
no target; once the executable price is +5 $ from the fill the stop follows the
best executable price at 3 $ (moved only in steps of at least 0.25 $ and never
closer than the broker stops level); close at most 6 hours after the fill or
just before the daily gold break. Day rule: after a losing close, no new trade
on that side (follow / reverse) for the rest of that UTC day.

Research evidence: docs/development/objetivo-gestion-profesional.md (research
worktree), lab engine search2/lab/motor.py with trail_step=0.25.  This module
is free of Telegram and MT5 side effects: runtime code supplies prices and
fills; this module returns deterministic decisions and prices.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math

import numpy as np


CANDIDATE_ID = "gold_now_trail_v1"
COMMENT_SUFFIX = "_gtr"
SUPPORTED_MODES = ("desarrollada", "candidata")
SIDE_FOLLOW = "follow"
SIDE_REVERSE = "reverse"

MIN_MS = 60_000
HOUR_MS = 3_600_000


class GoldTrailAccountError(RuntimeError):
    """Raised when the demo-only account contract is not satisfied."""


def assert_demo_eur_account(evidence: dict | None, *, demo_trade_mode: int = 0) -> None:
    evidence = evidence or {}
    trade_mode = evidence.get("trade_mode")
    trade_mode_name = str(evidence.get("trade_mode_name") or "").lower()
    currency = str(evidence.get("currency") or "").upper()
    if trade_mode != int(demo_trade_mode) or trade_mode_name != "demo" or currency != "EUR":
        raise GoldTrailAccountError(f"{CANDIDATE_ID} requiere una cuenta MT5 demo EUR verificada")


def direction_sign(direction: str) -> int:
    normalized = str(direction).upper()
    if normalized == "BUY":
        return 1
    if normalized == "SELL":
        return -1
    raise ValueError("direction must be BUY or SELL")


def opposite(direction: str) -> str:
    return "SELL" if direction_sign(direction) > 0 else "BUY"


def _positive_price(value: float) -> float:
    price = float(value)
    if not math.isfinite(price) or price <= 0:
        raise ValueError("price must be positive and finite")
    return price


@dataclass(frozen=True)
class GoldTrailPolicy:
    schema_version: int = 1
    mode: str = "candidata"
    momentum_min: int = 2
    reverse_own_max: int = 1
    reverse_opposite_min: int = 2
    stop_distance: float = 20.0
    trail_activation: float = 5.0
    trail_distance: float = 3.0
    trail_step: float = 0.25
    life_minutes: int = 360
    session_close_margin_seconds: int = 120
    day_rule: str = "stop_side_after_first_loss"
    provider_management_mode: str = "ignore"

    # Live-only sizing; excluded from the research fingerprint (the research
    # results are per 0.01 lots and scale linearly with the volume).
    live_volume: float = 0.05

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported schema_version")
        if self.mode not in SUPPORTED_MODES:
            raise ValueError(f"unsupported Gold trail mode {self.mode!r}")
        if self.day_rule != "stop_side_after_first_loss":
            raise ValueError("Gold trail day rule contract changed")
        if self.provider_management_mode != "ignore":
            raise ValueError("Gold trail must ignore provider management")
        positive = (
            self.stop_distance, self.trail_activation, self.trail_distance, self.trail_step,
            self.life_minutes, self.session_close_margin_seconds, self.live_volume,
        )
        if any(not math.isfinite(float(v)) or float(v) <= 0 for v in positive):
            raise ValueError("Gold trail thresholds must be positive and finite")
        if self.trail_distance >= self.trail_activation:
            raise ValueError("following stop must lock profit once active")

    @property
    def reverse_enabled(self) -> bool:
        return self.mode == "candidata"

    @property
    def skip_round_minutes(self) -> bool:
        return self.mode == "candidata"

    @property
    def max_signal_volume(self) -> float:
        return round(float(self.live_volume), 8)

    def research_payload(self) -> dict:
        payload = asdict(self)
        payload.pop("live_volume")
        return payload

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(self.research_payload(), sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    # ---- prices -------------------------------------------------------------
    def initial_stop(self, direction: str, real_fill: float) -> float:
        sign = direction_sign(direction)
        return round(_positive_price(real_fill) - sign * self.stop_distance, 2)

    def following_stop(
        self,
        direction: str,
        *,
        entry: float,
        best_price: float,
        executable_price: float,
        current_stop: float | None,
        stops_level: float,
    ) -> float | None:
        """New stop for the following-stop rule, or None when it must not move.

        Mirrors search2/lab/motor.py (per-entry following stop): active once the
        best executable price since the fill is ``trail_activation`` $ beyond the
        entry; candidate = best - trail_distance (never closer to the current
        price than the broker stops level); moved only when it improves the
        current stop by at least ``trail_step``.
        """
        sign = direction_sign(direction)
        entry = _positive_price(entry)
        best = _positive_price(best_price)
        price = _positive_price(executable_price)
        level = max(0.0, float(stops_level))
        if sign * (best - entry) < self.trail_activation - 1e-9:
            return None
        candidate = best - sign * max(self.trail_distance, level)
        if sign * (price - candidate) < level:
            candidate = price - sign * level
        candidate = round(candidate, 2)
        if current_stop in (None, 0, 0.0):
            return candidate
        if sign * (candidate - float(current_stop)) >= max(0.0049, self.trail_step - 1e-9):
            return candidate
        return None

    def time_exit_at(self, fill_utc: datetime, session_close_utc: datetime | None) -> datetime:
        """Latest close: life end, or a margin before the broker daily break."""
        fill = fill_utc if fill_utc.tzinfo else fill_utc.replace(tzinfo=timezone.utc)
        deadline = fill + timedelta(minutes=int(self.life_minutes))
        if session_close_utc is not None:
            close = session_close_utc if session_close_utc.tzinfo else session_close_utc.replace(tzinfo=timezone.utc)
            deadline = min(deadline, close - timedelta(seconds=int(self.session_close_margin_seconds)))
        return deadline


# ---- causal momentum (identical to research/signal_features*.py) ------------------------------------

@dataclass(frozen=True)
class Momentum:
    status: str                       # "ok" or the reason it could not be computed
    day_move: float | None = None
    pd_loc: float | None = None
    asia_loc: float | None = None
    pre_move_15: float | None = None
    mi: int = -1                      # pieces in favour of the provider's direction
    mi_rev: int = -1                  # pieces in favour of the opposite direction

    def to_dict(self) -> dict:
        return asdict(self)


def compute_momentum(
    *,
    sign: int,
    t0_ms: int,
    tick_t_ms: np.ndarray,
    tick_mid: np.ndarray,
    day_open_mid: float | None,
    bars_t_ms: np.ndarray,
    bars_h: np.ndarray,
    bars_l: np.ndarray,
    bars_c: np.ndarray,
    broker_day_start_ms: int,
    prev_broker_day_start_ms: int | None,
) -> Momentum:
    """Momentum pieces of a signal from data strictly before t0.

    tick_* : ticks in [t0 - 5 h, t0) (UTC ms, mid prices).
    day_open_mid : mid of the first tick of the current broker day (within its
        first 3 hours and before t0), or None.
    bars_* : 1-minute bars in UTC ms (start time); only bars that ended at or
        before t0 are used. The research used mid bars; bid bars give the same
        locations because high, low and close shift together.
    broker_day_start_ms : start of the current broker day (server midnight).
    prev_broker_day_start_ms : start of the broker day of the last bar before
        today (handles weekends), or None.
    """
    tick_t = np.asarray(tick_t_ms, dtype=np.int64)
    mid = np.asarray(tick_mid, dtype=float)
    keep = tick_t < int(t0_ms)
    tick_t, mid = tick_t[keep], mid[keep]
    if len(tick_t) < 50:
        return Momentum(status="no_ticks")
    now_mid = float(mid[-1])
    pre_move_15 = None
    k = int(np.searchsorted(tick_t, int(t0_ms) - 15 * MIN_MS))
    if k < len(tick_t) - 1 and int(t0_ms) - int(tick_t[k]) <= 20 * MIN_MS:
        pre_move_15 = round(float(sign * (now_mid - float(mid[k]))), 2)
    day_move = None
    if day_open_mid is not None and math.isfinite(float(day_open_mid)):
        day_move = round(float(sign * (now_mid - float(day_open_mid))), 2)

    bt = np.asarray(bars_t_ms, dtype=np.int64)
    bh, bl, bc = (np.asarray(x, dtype=float) for x in (bars_h, bars_l, bars_c))
    n_done = int(np.searchsorted(bt, int(t0_ms) - MIN_MS, side="right"))
    pd_loc = asia_loc = None
    if n_done >= 60:
        now_bar = float(bc[n_done - 1])
        i_today = int(np.searchsorted(bt, int(broker_day_start_ms), side="left"))
        if i_today > 0 and prev_broker_day_start_ms is not None:
            i_prev = int(np.searchsorted(bt, int(prev_broker_day_start_ms), side="left"))
            if i_today > i_prev:
                hi, lo = float(bh[i_prev:i_today].max()), float(bl[i_prev:i_today].min())
                if hi > lo:
                    loc = (now_bar - lo) / (hi - lo)
                    pd_loc = round(float(loc if sign == 1 else 1 - loc), 3)
        hour = datetime.fromtimestamp(int(t0_ms) / 1000, timezone.utc).hour
        if hour >= 7:
            day0 = int(t0_ms) // 86_400_000 * 86_400_000
            a0 = int(np.searchsorted(bt, day0))
            a1 = min(int(np.searchsorted(bt, day0 + 7 * HOUR_MS)), n_done)
            if a1 - a0 >= 60:
                hi, lo = float(bh[a0:a1].max()), float(bl[a0:a1].min())
                if hi > lo:
                    loc = (now_bar - lo) / (hi - lo)
                    asia_loc = round(float(loc if sign == 1 else 1 - loc), 3)
    mi = int((day_move or 0) > 0) + int((pd_loc or 0) > 0.6) + int((asia_loc or 0) > 0.8) + int((pre_move_15 or 0) > 0)
    mi_rev = (int(-(day_move or 0) > 0) + int(pd_loc is not None and pd_loc < 0.4)
              + int(asia_loc is not None and asia_loc < 0.2) + int(-(pre_move_15 or 0) > 0))
    return Momentum(status="ok", day_move=day_move, pd_loc=pd_loc, asia_loc=asia_loc, pre_move_15=pre_move_15,
                    mi=mi, mi_rev=mi_rev)


# ---- decision ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class GoldTrailDecision:
    action: str                       # "open" | "skip"
    reason: str
    side: str | None = None           # "follow" | "reverse"
    direction: str | None = None      # direction WE trade

    def to_dict(self) -> dict:
        return asdict(self)


def is_round_minute(published_utc: datetime) -> bool:
    return published_utc.minute % 5 == 0


def decide(
    policy: GoldTrailPolicy,
    *,
    provider_direction: str,
    published_utc: datetime,
    momentum: Momentum,
    day_losses: set[tuple[date, str]],
) -> GoldTrailDecision:
    provider_direction = str(provider_direction).upper()
    direction_sign(provider_direction)
    pub = published_utc if published_utc.tzinfo else published_utc.replace(tzinfo=timezone.utc)
    pub = pub.astimezone(timezone.utc)
    if policy.skip_round_minutes and is_round_minute(pub):
        return GoldTrailDecision("skip", "round_minute")
    if momentum.status != "ok":
        return GoldTrailDecision("skip", f"momentum_{momentum.status}")
    if momentum.mi >= policy.momentum_min:
        side, direction = SIDE_FOLLOW, provider_direction
    elif (policy.reverse_enabled and momentum.mi <= policy.reverse_own_max
          and momentum.mi_rev >= policy.reverse_opposite_min):
        side, direction = SIDE_REVERSE, opposite(provider_direction)
    else:
        return GoldTrailDecision("skip", "no_momentum_side")
    if (pub.date(), side) in day_losses:
        return GoldTrailDecision("skip", "day_rule_loss_today", side=side, direction=direction)
    return GoldTrailDecision("open", "momentum", side=side, direction=direction)


def policy_for_fingerprint(fingerprint: str | None) -> GoldTrailPolicy:
    """The frozen policy of an existing signal (each mode has its own fingerprint)."""
    for mode in SUPPORTED_MODES:
        candidate = GoldTrailPolicy(mode=mode)
        if candidate.fingerprint == fingerprint:
            return candidate
    raise ValueError("Gold trail fingerprint mismatch")


def market_comment(message_id: int) -> str:
    message_id = int(message_id)
    if message_id <= 0:
        raise ValueError("message_id must be positive")
    return f"c2_{message_id}{COMMENT_SUFFIX}"
