from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


SUPPORTED_ORDER_MODES = {"market", "limit"}
SUPPORTED_EXPIRY_MODES = {"session_end", "provider_progress"}
SUPPORTED_TRIGGER_MODES = {
    "zone_touch",
    "provider_active",
    "zone_touch_or_active",
}
MAX_PLANNED_VOLUME = 0.05


@dataclass(frozen=True)
class ZoneEntryPolicy:
    policy_id: str
    depth_fractions: tuple[float, ...]
    volumes: tuple[float, ...]
    order_modes: tuple[str, ...]
    expiry_mode: str
    trigger_mode: str = "zone_touch"
    activation_latency_ms: int = 0
    market_leg_spacing_ms: int = 125

    def __post_init__(self) -> None:
        leg_count = len(self.depth_fractions)
        if not self.policy_id:
            raise ValueError("policy_id is required")
        if leg_count == 0:
            raise ValueError("at least one entry leg is required")
        if len(self.volumes) != leg_count or len(self.order_modes) != leg_count:
            raise ValueError("entry policy leg fields must have equal length")
        if any(
            not isfinite(float(depth)) or not 0.0 <= float(depth) <= 1.0
            for depth in self.depth_fractions
        ):
            raise ValueError("depth fractions must be finite values from 0 to 1")
        if any(
            not isfinite(float(volume)) or float(volume) <= 0
            for volume in self.volumes
        ):
            raise ValueError("entry volumes must be positive finite values")
        if self.total_planned_volume > MAX_PLANNED_VOLUME + 1e-9:
            raise ValueError("entry policy exceeds maximum planned volume")
        if any(mode not in SUPPORTED_ORDER_MODES for mode in self.order_modes):
            raise ValueError("unsupported entry order mode")
        if self.expiry_mode not in SUPPORTED_EXPIRY_MODES:
            raise ValueError("unsupported entry expiry mode")
        if self.trigger_mode not in SUPPORTED_TRIGGER_MODES:
            raise ValueError("unsupported entry trigger mode")
        if self.activation_latency_ms < 0 or self.market_leg_spacing_ms < 0:
            raise ValueError("entry timing values cannot be negative")

    @property
    def total_planned_volume(self) -> float:
        return round(sum(float(volume) for volume in self.volumes), 8)


def default_zone_entry_policies() -> tuple[ZoneEntryPolicy, ...]:
    return (
        ZoneEntryPolicy(
            "current_live_zone_trigger",
            (0.0, 0.0, 0.0, 0.0, 0.0),
            (0.01,) * 5,
            ("market",) * 5,
            "session_end",
            "zone_touch_or_active",
        ),
        ZoneEntryPolicy(
            "all_first_touch_causal_expiry",
            (0.0, 0.0, 0.0, 0.0, 0.0),
            (0.01,) * 5,
            ("market",) * 5,
            "provider_progress",
        ),
        ZoneEntryPolicy(
            "all_provider_active",
            (0.0, 0.0, 0.0, 0.0, 0.0),
            (0.01,) * 5,
            ("market",) * 5,
            "provider_progress",
            "provider_active",
        ),
        ZoneEntryPolicy(
            "one_first_touch",
            (0.0,),
            (0.01,),
            ("market",),
            "provider_progress",
        ),
        ZoneEntryPolicy(
            "one_provider_active",
            (0.0,),
            (0.01,),
            ("market",),
            "provider_progress",
            "provider_active",
        ),
        ZoneEntryPolicy(
            "one_plus_four_equal",
            (0.0, 0.25, 0.5, 0.75, 1.0),
            (0.01,) * 5,
            ("market", "limit", "limit", "limit", "limit"),
            "provider_progress",
        ),
        ZoneEntryPolicy(
            "five_equal_limits",
            (0.0, 0.25, 0.5, 0.75, 1.0),
            (0.01,) * 5,
            ("limit",) * 5,
            "provider_progress",
        ),
        ZoneEntryPolicy(
            "best_half_ladder",
            (0.5, 0.625, 0.75, 0.875, 1.0),
            (0.01,) * 5,
            ("limit",) * 5,
            "provider_progress",
        ),
        ZoneEntryPolicy(
            "mid_and_best",
            (0.5, 1.0),
            (0.025, 0.025),
            ("limit", "limit"),
            "provider_progress",
        ),
    )


def zone_policy_by_id(policy_id: str) -> ZoneEntryPolicy:
    for policy in default_zone_entry_policies():
        if policy.policy_id == policy_id:
            return policy
    raise KeyError(f"unknown zone entry policy: {policy_id}")


__all__ = [
    "MAX_PLANNED_VOLUME",
    "ZoneEntryPolicy",
    "default_zone_entry_policies",
    "zone_policy_by_id",
]
