"""Pure planning for provider requests to make a basket risk free."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations
import math
from typing import Iterable


@dataclass(frozen=True)
class BasketLeg:
    ticket: int
    current_pnl: float
    stop_pnl: float | None
    target_distance: float | None = None


@dataclass(frozen=True)
class RiskFreePlan:
    status: str
    reason: str
    close_tickets: tuple[int, ...]
    keep_tickets: tuple[int, ...]
    projected_floor: float | None
    realized_pnl: float | None
    safety_buffer: float

    def as_dict(self) -> dict:
        return asdict(self)


def _finite(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _target_cost(leg: BasketLeg) -> float:
    if not _finite(leg.target_distance):
        return 1_000_000.0
    return max(0.0, float(leg.target_distance))


def _empty_plan(
    *,
    status: str,
    reason: str,
    legs: list[BasketLeg],
    realized_pnl: float | None,
    safety_buffer: float,
    projected_floor: float | None = None,
) -> RiskFreePlan:
    return RiskFreePlan(
        status=status,
        reason=reason,
        close_tickets=(),
        keep_tickets=tuple(int(leg.ticket) for leg in legs),
        projected_floor=projected_floor,
        realized_pnl=realized_pnl,
        safety_buffer=float(safety_buffer),
    )


def plan_risk_free_basket(
    legs: Iterable[BasketLeg],
    *,
    realized_pnl: float | None,
    safety_buffer: float = 1.0,
) -> RiskFreePlan:
    """Prove a partial-close plan against each remaining installed stop.

    The plan closes the fewest whole positions possible and always leaves at
    least one runner. Among equally small plans it preserves the legs with
    the most target distance. No broker I/O is performed here.
    """
    positions = list(legs)
    if not _finite(safety_buffer) or float(safety_buffer) < 0:
        raise ValueError("safety_buffer must be finite and non-negative")
    safety = float(safety_buffer)
    if realized_pnl is None or not _finite(realized_pnl):
        return _empty_plan(
            status="incomplete_evidence",
            reason="missing_realized_pnl",
            legs=positions,
            realized_pnl=None,
            safety_buffer=safety,
        )
    realized = float(realized_pnl)
    if not positions:
        return _empty_plan(
            status="no_open_positions",
            reason="no_open_positions",
            legs=positions,
            realized_pnl=realized,
            safety_buffer=safety,
            projected_floor=realized - safety,
        )
    if any(
        isinstance(leg.ticket, bool)
        or not isinstance(leg.ticket, int)
        or leg.ticket <= 0
        or not _finite(leg.current_pnl)
        for leg in positions
    ):
        return _empty_plan(
            status="incomplete_evidence",
            reason="invalid_open_position_evidence",
            legs=positions,
            realized_pnl=realized,
            safety_buffer=safety,
        )

    if all(_finite(leg.stop_pnl) for leg in positions):
        current_floor = (
            realized
            + sum(float(leg.stop_pnl) for leg in positions)
            - safety
        )
        if current_floor >= 0:
            return _empty_plan(
                status="already_secured",
                reason="installed_stops_already_protect_signal",
                legs=positions,
                realized_pnl=realized,
                safety_buffer=safety,
                projected_floor=current_floor,
            )

    evaluable = False
    for close_count in range(1, len(positions)):
        candidates = []
        for closed_indexes in combinations(range(len(positions)), close_count):
            closed_set = set(closed_indexes)
            closed = [
                leg for index, leg in enumerate(positions)
                if index in closed_set
            ]
            kept = [
                leg for index, leg in enumerate(positions)
                if index not in closed_set
            ]
            if not all(_finite(leg.stop_pnl) for leg in kept):
                continue
            evaluable = True
            floor = (
                realized
                + sum(float(leg.current_pnl) for leg in closed)
                + sum(float(leg.stop_pnl) for leg in kept)
                - safety
            )
            if floor < 0:
                continue
            candidates.append((
                sum(_target_cost(leg) for leg in closed),
                -floor,
                tuple(int(leg.ticket) for leg in closed),
                closed,
                kept,
                floor,
            ))
        if not candidates:
            continue
        _cost, _negative_floor, _tickets, closed, kept, floor = min(
            candidates,
            key=lambda candidate: candidate[:3],
        )
        ordered_closed = sorted(
            closed,
            key=lambda leg: (_target_cost(leg), int(leg.ticket)),
        )
        return RiskFreePlan(
            status="secure",
            reason="minimum_proved_partial_close",
            close_tickets=tuple(int(leg.ticket) for leg in ordered_closed),
            keep_tickets=tuple(int(leg.ticket) for leg in kept),
            projected_floor=float(floor),
            realized_pnl=realized,
            safety_buffer=safety,
        )

    return _empty_plan(
        status="infeasible" if evaluable else "incomplete_evidence",
        reason=(
            "insufficient_profit_to_secure_runner"
            if evaluable
            else "missing_stop_floor_evidence"
        ),
        legs=positions,
        realized_pnl=realized,
        safety_buffer=safety,
    )
