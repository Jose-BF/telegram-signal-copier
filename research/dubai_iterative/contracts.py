"""Immutable contracts for bounded Dubai strategy research."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
from typing import Any, Mapping


OBSERVED_DUBAI_VOLUME = 0.04


@dataclass(frozen=True)
class SearchBudget:
    max_generations: int = 50
    max_evaluations: int = 1_000_000
    max_wall_seconds: int = 7_200
    patience_generations: int = 8
    max_lineage_depth: int = 12

    def __post_init__(self) -> None:
        for field_name in (
            "max_generations",
            "max_evaluations",
            "max_wall_seconds",
            "patience_generations",
            "max_lineage_depth",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")

    def stop_reason(
        self,
        *,
        generation: int,
        evaluations: int,
        elapsed_seconds: float,
        stale_generations: int,
        deepest_lineage: int,
    ) -> str | None:
        checks = (
            (generation >= self.max_generations, "max_generations"),
            (evaluations >= self.max_evaluations, "max_evaluations"),
            (elapsed_seconds >= self.max_wall_seconds, "max_wall_seconds"),
            (stale_generations >= self.patience_generations, "no_improvement"),
            (deepest_lineage >= self.max_lineage_depth, "max_lineage_depth"),
        )
        return next((reason for reached, reason in checks if reached), None)


@dataclass(frozen=True)
class SearchSpace:
    """Explicit, configurable envelope for one finite research run."""

    min_total_volume: float = 0.01
    max_total_volume: float = 0.20
    max_legs: int = 12
    volume_step: float = 0.01

    def __post_init__(self) -> None:
        for field_name in (
            "min_total_volume",
            "max_total_volume",
            "volume_step",
        ):
            if not _positive_finite(getattr(self, field_name)):
                raise ValueError(f"{field_name} must be positive and finite")
        if self.max_total_volume < self.min_total_volume:
            raise ValueError("max_total_volume must be at least min_total_volume")
        if (
            isinstance(self.max_legs, bool)
            or not isinstance(self.max_legs, int)
            or self.max_legs <= 0
        ):
            raise ValueError("max_legs must be a positive integer")

    def validation_errors(self, genome: "StrategyGenome") -> tuple[str, ...]:
        errors: list[str] = []
        total = sum(genome.volume_weights)
        tolerance = max(1e-12, self.volume_step * 1e-9)
        if not (
            self.min_total_volume - tolerance
            <= total
            <= self.max_total_volume + tolerance
        ):
            errors.append("outside_search_volume")
        if genome.leg_count > self.max_legs:
            errors.append("outside_search_leg_count")
        for volume in genome.volume_weights:
            steps = volume / self.volume_step
            if abs(steps - round(steps)) > tolerance:
                errors.append("outside_search_volume_step")
                break
        return tuple(errors)


@dataclass(frozen=True)
class StrategyGenome:
    schema_version: int = 1
    entry_mode: str = "actual_mt5"
    entry_value: float | None = None
    entry_expiry_min: int = 15
    leg_count: int = 4
    volume_weights: tuple[float, ...] = (0.01, 0.01, 0.01, 0.01)
    target_mode: str = "provider_per_leg"
    target_value: float | None = None
    partial_fraction: float = 0.0
    runner_target: float | None = None
    be_mode: str = "provider"
    be_trigger: float | None = None
    stop_mode: str = "provider"
    stop_value: float | None = None
    profit_lock_arm: float | None = None
    profit_lock_giveback: float | None = None
    time_exit_min: int = 240
    provider_management_mode: str = "exact"
    context_filter_mode: str = "none"
    context_filter_value: float | None = None
    parent_fingerprints: tuple[str, ...] = ()
    mutation_reason: str | None = None
    lineage_depth: int = 0

    @classmethod
    def baseline(cls) -> "StrategyGenome":
        return cls()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StrategyGenome":
        values = dict(payload)
        for name in ("volume_weights", "parent_fingerprints"):
            if name in values and not isinstance(values[name], tuple):
                values[name] = tuple(values[name] or ())
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["volume_weights"] = list(self.volume_weights)
        payload["parent_fingerprints"] = list(self.parent_fingerprints)
        return payload

    def with_change(self, **changes: Any) -> "StrategyGenome":
        tuple_fields = {"volume_weights", "parent_fingerprints"}
        normalized = {
            key: tuple(value) if key in tuple_fields and value is not None else value
            for key, value in changes.items()
        }
        return replace(self, **normalized)

    def with_lineage(
        self,
        *,
        parent_fingerprints: tuple[str, ...],
        mutation_reason: str,
        lineage_depth: int,
    ) -> "StrategyGenome":
        return replace(
            self,
            parent_fingerprints=tuple(parent_fingerprints),
            mutation_reason=str(mutation_reason),
            lineage_depth=int(lineage_depth),
        )

    @property
    def fingerprint(self) -> str:
        payload = self.to_dict()
        for field_name in (
            "parent_fingerprints",
            "mutation_reason",
            "lineage_depth",
        ):
            payload.pop(field_name, None)
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def validation_errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        allowed = {
            "entry_mode": {"actual_mt5", "delay", "pullback", "momentum"},
            "target_mode": {
                "provider_per_leg",
                "provider_target_all",
                "fixed_basket",
                "partial_runner",
                "none",
            },
            "be_mode": {"provider", "none", "price", "delayed", "partial"},
            "stop_mode": {"provider", "fixed_move", "basket_money", "none"},
            "provider_management_mode": {"exact", "close_only", "ignore"},
            "context_filter_mode": {
                "none",
                "max_spread",
                "time_window",
                "max_volatility",
                "min_reward_risk",
            },
        }
        for field_name, choices in allowed.items():
            if getattr(self, field_name) not in choices:
                errors.append(f"unsupported_{field_name}")

        if self.schema_version != 1:
            errors.append("unsupported_schema_version")
        if self.leg_count < 1:
            errors.append("invalid_leg_count")
        if len(self.volume_weights) != self.leg_count:
            errors.append("volume_weight_count_mismatch")
        if any(not _positive_finite(value) for value in self.volume_weights):
            errors.append("invalid_volume_weight")

        if self.entry_mode != "actual_mt5" and not _positive_finite(self.entry_value):
            errors.append("missing_entry_value")
        if self.entry_expiry_min <= 0:
            errors.append("invalid_entry_expiry")
        if self.target_mode in {"fixed_basket", "provider_target_all"}:
            if not _positive_finite(self.target_value):
                errors.append("missing_target_value")
        if self.target_mode == "partial_runner":
            if not 0.0 < self.partial_fraction < 1.0:
                errors.append("invalid_partial_fraction")
            if not _positive_finite(self.target_value):
                errors.append("missing_target_value")
            if not _positive_finite(self.runner_target):
                errors.append("missing_runner_target")
        if self.be_mode in {"price", "delayed", "partial"}:
            if not _positive_finite(self.be_trigger):
                errors.append("missing_be_trigger")
        if self.stop_mode in {"fixed_move", "basket_money"}:
            if not _positive_finite(self.stop_value):
                errors.append("missing_stop_value")
        if (self.profit_lock_arm is None) != (self.profit_lock_giveback is None):
            errors.append("incomplete_profit_lock")
        elif self.profit_lock_arm is not None:
            if not _positive_finite(self.profit_lock_arm):
                errors.append("invalid_profit_lock_arm")
            if not _positive_finite(self.profit_lock_giveback):
                errors.append("invalid_profit_lock_giveback")
        if self.time_exit_min <= 0:
            errors.append("invalid_time_exit")
        if self.context_filter_mode != "none" and not _positive_finite(
            self.context_filter_value
        ):
            errors.append("missing_context_filter_value")
        if self.lineage_depth < 0:
            errors.append("invalid_lineage_depth")
        return tuple(dict.fromkeys(errors))


def _positive_finite(value: object) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0.0
