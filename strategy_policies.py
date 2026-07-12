"""Declarative management policies shared by both Telegram channels."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class StrategyPolicy:
    policy_id: str
    close_legs: int = 0
    be_legs: int = 0
    runner_legs: int = 5
    base_leg_count: int = 5
    mode: str = "risk_free_allocation"
    trigger_action: str = "MOVE_SL_TO_BE"
    leg_order: str = "nearest_tp_first"
    entry_policy: str = "actual_mt5"
    tp_policy: str = "provider_per_leg"
    original_sl_policy: str = "keep"
    horizon_policy: str = "eod_close"
    risk_model: str = "actual_volume"
    assumptions: tuple[str, ...] = (
        "management_trigger_uses_provider_message_time",
        "close_and_be_apply_to_nearest_targets_first",
        "unprotected_farthest_targets_are_runners",
    )

    def __post_init__(self) -> None:
        counts = (self.close_legs, self.be_legs, self.runner_legs)
        if any(value < 0 for value in counts):
            raise ValueError("leg allocations cannot be negative")
        if sum(counts) != self.base_leg_count:
            raise ValueError("leg allocations must sum to base_leg_count")
        if self.mode not in ("follow_actual", "risk_free_allocation"):
            raise ValueError(f"unsupported policy mode: {self.mode}")

    def allocation_for(self, leg_count: int) -> dict[str, int]:
        if leg_count < 0:
            raise ValueError("leg_count cannot be negative")
        if self.mode == "follow_actual":
            return {"close_now": 0, "move_to_be": 0, "runner": leg_count}

        counts = {
            "close_now": self.close_legs,
            "move_to_be": self.be_legs,
            "runner": self.runner_legs,
        }
        original = dict(counts)
        while sum(counts.values()) > leg_count:
            reducible = [
                key
                for key, value in counts.items()
                if value > 1 and original[key] > 0
            ]
            if reducible:
                key = max(
                    reducible,
                    key=lambda name: (
                        counts[name],
                        {"move_to_be": 2, "close_now": 1, "runner": 0}[name],
                    ),
                )
            else:
                key = next(
                    name
                    for name in ("move_to_be", "close_now", "runner")
                    if counts[name] > 0
                )
            counts[key] -= 1
        if sum(counts.values()) < leg_count:
            counts["runner"] += leg_count - sum(counts.values())
        return counts

    def to_dict(self) -> dict:
        result = asdict(self)
        result["assumptions"] = list(self.assumptions)
        return result


def default_policy_catalog(base_leg_count: int = 5) -> list[StrategyPolicy]:
    if base_leg_count <= 0:
        raise ValueError("base_leg_count must be positive")
    policies = [
        StrategyPolicy(
            policy_id="follow_actual",
            close_legs=0,
            be_legs=0,
            runner_legs=base_leg_count,
            base_leg_count=base_leg_count,
            mode="follow_actual",
            assumptions=("uses_observed_mt5_result_without_counterfactual_changes",),
        )
    ]
    for close_legs in range(base_leg_count + 1):
        for be_legs in range(base_leg_count - close_legs + 1):
            runner_legs = base_leg_count - close_legs - be_legs
            if close_legs == 0 and be_legs == 0:
                policy_id = "no_be"
            else:
                policy_id = (
                    f"close_{close_legs}_be_{be_legs}_runner_{runner_legs}"
                )
            policies.append(StrategyPolicy(
                policy_id=policy_id,
                close_legs=close_legs,
                be_legs=be_legs,
                runner_legs=runner_legs,
                base_leg_count=base_leg_count,
            ))
    return policies


def policy_by_id(policy_id: str, base_leg_count: int = 5) -> StrategyPolicy:
    for policy in default_policy_catalog(base_leg_count):
        if policy.policy_id == policy_id:
            return policy
    raise KeyError(policy_id)
