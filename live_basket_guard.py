"""Pure account-currency basket protection for live provider signals."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class GuardPolicy:
    enabled: bool
    channel: str
    loss_cap: float
    profit_arm: float
    profit_lock: float

    def __post_init__(self) -> None:
        thresholds = (self.loss_cap, self.profit_arm, self.profit_lock)
        if not all(math.isfinite(float(value)) for value in thresholds):
            raise ValueError("guard thresholds must be finite")
        if float(self.loss_cap) >= 0:
            raise ValueError("loss_cap must be negative")
        if float(self.profit_lock) < 0:
            raise ValueError("profit_lock must be non-negative")
        if float(self.profit_arm) <= float(self.profit_lock):
            raise ValueError("profit_arm must be greater than profit_lock")


@dataclass(frozen=True)
class GuardState:
    armed: bool = False
    triggered: bool = False
    peak_pl: float | None = None
    trigger_reason: str | None = None
    recovery_pending: bool = False


@dataclass(frozen=True)
class GuardDecision:
    action: str
    reason: str | None
    observed_pl: float
    state: GuardState


def evaluate_guard(
    *,
    channel: str,
    floating_pl: float,
    n_open: int,
    state: GuardState,
    policy: GuardPolicy,
    profit_evidence_complete: bool = True,
) -> GuardDecision:
    """Return one deterministic transition without performing I/O."""
    observed_pl = float(floating_pl)
    if not math.isfinite(observed_pl):
        raise ValueError("floating_pl must be finite")
    if (
        not policy.enabled
        or str(channel) != str(policy.channel)
        or int(n_open) <= 0
    ):
        return GuardDecision("none", None, observed_pl, state)

    if state.triggered:
        if state.recovery_pending:
            recovered = replace(state, recovery_pending=False)
            return GuardDecision("close", "recovery", observed_pl, recovered)
        return GuardDecision("none", None, observed_pl, state)

    if observed_pl <= float(policy.loss_cap):
        triggered = replace(
            state,
            triggered=True,
            trigger_reason="loss_cap",
        )
        return GuardDecision("close", "loss_cap", observed_pl, triggered)

    if not profit_evidence_complete:
        return GuardDecision("none", None, observed_pl, state)

    peak_pl = (
        observed_pl
        if state.peak_pl is None
        else max(float(state.peak_pl), observed_pl)
    )
    updated = replace(state, peak_pl=peak_pl)

    if not updated.armed and observed_pl >= float(policy.profit_arm):
        armed = replace(updated, armed=True)
        return GuardDecision("arm", "profit_arm", observed_pl, armed)

    if updated.armed and observed_pl <= float(policy.profit_lock):
        triggered = replace(
            updated,
            triggered=True,
            trigger_reason="profit_lock",
        )
        return GuardDecision("close", "profit_lock", observed_pl, triggered)

    return GuardDecision("none", None, observed_pl, updated)


def load_guard_states(
    path: str | Path,
    signal_ids: Iterable[str],
) -> dict[str, GuardState]:
    """Recover armed/triggered states needed to resume open MT5 baskets."""
    wanted = {str(signal_id) for signal_id in signal_ids}
    states: dict[str, GuardState] = {}
    source = Path(path)
    if not source.exists() or not wanted:
        return states

    with source.open("rb") as handle:
        for raw_line in handle:
            try:
                row = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            signal_id = str(row.get("sig") or "")
            if signal_id not in wanted:
                continue
            event = row.get("ev")
            if event not in {"basket_guard_armed", "basket_guard_triggered"}:
                continue

            previous = states.get(signal_id, GuardState())
            raw_peak = row.get("peak_pl")
            peak = previous.peak_pl
            if raw_peak is not None:
                try:
                    peak = (
                        float(raw_peak)
                        if peak is None
                        else max(float(peak), float(raw_peak))
                    )
                except (TypeError, ValueError):
                    pass
            if event == "basket_guard_armed":
                states[signal_id] = replace(
                    previous,
                    armed=True,
                    peak_pl=peak,
                )
            else:
                states[signal_id] = GuardState(
                    armed=True if previous.armed else bool(row.get("armed")),
                    triggered=True,
                    peak_pl=peak,
                    trigger_reason=str(row.get("reason") or "unknown"),
                    recovery_pending=True,
                )
    return states


def _iter_signal_events(path: str | Path, signal_ids: Iterable[str]):
    wanted = {str(signal_id) for signal_id in signal_ids}
    source = Path(path)
    if not source.exists() or not wanted:
        return
    with source.open("rb") as handle:
        for raw_line in handle:
            try:
                row = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if str(row.get("sig") or "") in wanted:
                yield row


def load_realized_ticket_cache(
    path: str | Path,
    signal_ids: Iterable[str],
) -> dict[str, dict[int, float]]:
    recovered: dict[str, dict[int, float]] = {}
    for row in _iter_signal_events(path, signal_ids) or ():
        if row.get("ev") != "basket_guard_realized_ticket_confirmed":
            continue
        try:
            signal_id = str(row["sig"])
            ticket = int(row["ticket"])
            realized_pl = float(row["realized_pl"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(realized_pl):
            continue
        recovered.setdefault(signal_id, {})[ticket] = realized_pl
    return recovered


def load_signal_ticket_ids(
    path: str | Path,
    signal_ids: Iterable[str],
) -> dict[str, list[int]]:
    fill_events = {
        "market_filled",
        "market_b_filled",
        "scale_out_leg_filled",
        "dca_filled",
    }
    recovered: dict[str, list[int]] = {}
    seen: dict[str, set[int]] = {}
    for row in _iter_signal_events(path, signal_ids) or ():
        if row.get("ev") not in fill_events:
            continue
        try:
            signal_id = str(row["sig"])
            ticket = int(row["ticket"])
        except (KeyError, TypeError, ValueError):
            continue
        signal_seen = seen.setdefault(signal_id, set())
        if ticket in signal_seen:
            continue
        signal_seen.add(ticket)
        recovered.setdefault(signal_id, []).append(ticket)
    return recovered
