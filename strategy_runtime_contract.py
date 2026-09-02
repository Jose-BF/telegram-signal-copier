"""Canonical, immutable contracts shared by live and shadow strategies.

The historical strategy fingerprint remains frozen for audit continuity.  The
execution fingerprint covers the complete broker-facing semantics and is
compiled from the same payload for both adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from strategy_shadow_contracts import ShadowPolicy


@dataclass(frozen=True)
class EntryLegContract:
    index: int
    volume: float
    adverse_offset: float
    target_step: float | None = None


@dataclass(frozen=True)
class EntryPlanContract:
    mode: str
    volumes: tuple[float, ...]
    ladder_step: float | None = None
    expiry_minutes: int | None = None
    adverse: float | None = None
    reversal: float | None = None

    @property
    def legs(self) -> tuple[EntryLegContract, ...]:
        step = float(self.ladder_step or 0.0)
        return tuple(
            EntryLegContract(
                index=index,
                volume=float(volume),
                adverse_offset=round(step * index, 8),
            )
            for index, volume in enumerate(self.volumes)
        )


@dataclass(frozen=True)
class ProtectionContract:
    target_steps: tuple[float, ...] = ()
    trailing_distance: float | None = None
    hard_stop_eur_per_leg: float | None = None
    break_even_trigger_xau: float | None = None
    basket_stop_eur: float | None = None
    profit_arm_eur: float | None = None
    profit_giveback_eur: float | None = None
    time_exit_minutes: int | None = None
    time_exit_mode: str = "none"


@dataclass(frozen=True)
class TerminalPolicy:
    pending_entry_policy: str
    automatic_flat_policy: str
    provider_management_mode: str
    provider_protection_mode: str = "none"
    require_zero_positions: bool = True

    def __post_init__(self) -> None:
        if self.pending_entry_policy not in {"none", "until_expiry"}:
            raise ValueError("unsupported pending entry policy")
        if self.automatic_flat_policy not in {
            "finalize",
            "keep_if_eligible",
        }:
            raise ValueError("unsupported automatic flat policy")


@dataclass(frozen=True)
class LiveStrategyPlan:
    strategy_id: str
    channel: str
    strategy_fingerprint: str
    execution_fingerprint: str
    execution_payload: Mapping[str, Any]
    entry: EntryPlanContract
    protection: ProtectionContract
    terminal: TerminalPolicy


@dataclass(frozen=True)
class StrategyRuntimeContract:
    strategy_id: str
    channel: str
    strategy_fingerprint: str
    entry: EntryPlanContract
    protection: ProtectionContract
    terminal: TerminalPolicy
    schema_version: int = 1
    fill_rule: str = "first_subsequent_tick"
    money_rounding: str = "leg_cent_then_sum"

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("strategy_id is required")
        if self.channel not in {"canal1", "canal2"}:
            raise ValueError("channel must be canal1 or canal2")
        fingerprint = self.strategy_fingerprint.lower()
        if len(fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in fingerprint
        ):
            raise ValueError("strategy_fingerprint must be SHA-256")
        if (
            self.terminal.pending_entry_policy == "until_expiry"
            and self.entry.expiry_minutes is None
        ):
            raise ValueError("pending entries require an expiry")

    def to_shadow_policy(self, *, role: str) -> ShadowPolicy:
        return ShadowPolicy(
            candidate_id=self.strategy_id,
            channel=self.channel,
            role=role,
            strategy_fingerprint=self.strategy_fingerprint,
            entry_mode=self.entry.mode,
            entry_volumes=self.entry.volumes,
            ladder_step=self.entry.ladder_step,
            ladder_expiry_minutes=self.entry.expiry_minutes,
            entry_adverse=self.entry.adverse,
            entry_reversal=self.entry.reversal,
            target_steps=self.protection.target_steps,
            trailing_distance=self.protection.trailing_distance,
            hard_stop_eur_per_leg=self.protection.hard_stop_eur_per_leg,
            break_even_trigger_xau=self.protection.break_even_trigger_xau,
            basket_stop_eur=self.protection.basket_stop_eur,
            profit_arm_eur=self.protection.profit_arm_eur,
            profit_giveback_eur=self.protection.profit_giveback_eur,
            time_exit_minutes=self.protection.time_exit_minutes,
            time_exit_mode=self.protection.time_exit_mode,
            provider_management_mode=(
                self.terminal.provider_management_mode
            ),
            provider_protection_mode=(
                self.terminal.provider_protection_mode
            ),
            schema_version=self.schema_version,
            fill_rule=self.fill_rule,
            money_rounding=self.money_rounding,
        )

    @property
    def execution_fingerprint(self) -> str:
        return self.to_shadow_policy(role="candidate").execution_fingerprint

    def to_live_plan(self) -> LiveStrategyPlan:
        policy = self.to_shadow_policy(role="candidate")
        return LiveStrategyPlan(
            strategy_id=self.strategy_id,
            channel=self.channel,
            strategy_fingerprint=self.strategy_fingerprint,
            execution_fingerprint=policy.execution_fingerprint,
            execution_payload=MappingProxyType(policy.execution_payload()),
            entry=self.entry,
            protection=self.protection,
            terminal=self.terminal,
        )


def _terminal(
    provider_management_mode: str,
    *,
    pending: bool,
) -> TerminalPolicy:
    return TerminalPolicy(
        pending_entry_policy="until_expiry" if pending else "none",
        automatic_flat_policy="keep_if_eligible" if pending else "finalize",
        provider_management_mode=provider_management_mode,
    )


_CONTRACTS = (
    StrategyRuntimeContract(
        strategy_id="dubai_balanced_v1",
        channel="canal1",
        strategy_fingerprint=(
            "32cb5c0fe8205ad00a0c655bacd5446c6cc219d1ad7338967212c71781860631"
        ),
        entry=EntryPlanContract(
            mode="market_ladder",
            volumes=(0.01, 0.04, 0.04),
            ladder_step=4.0,
            expiry_minutes=15,
        ),
        protection=ProtectionContract(
            basket_stop_eur=25.0,
            profit_arm_eur=10.0,
            profit_giveback_eur=2.0,
            time_exit_minutes=40,
            time_exit_mode="loss_only",
        ),
        terminal=_terminal("exact", pending=True),
    ),
    StrategyRuntimeContract(
        strategy_id="dubai_frontloaded_30m_v1",
        channel="canal1",
        strategy_fingerprint=(
            "d486f5ce418094e862fe3b58e6ccc14068a136ef7116f8a9a80c347083e6dc1c"
        ),
        entry=EntryPlanContract(
            mode="market_ladder",
            volumes=(0.01, 0.05, 0.01, 0.02, 0.01, 0.02),
            ladder_step=4.0,
            expiry_minutes=15,
        ),
        protection=ProtectionContract(
            basket_stop_eur=30.0,
            profit_arm_eur=10.0,
            profit_giveback_eur=8.0,
            time_exit_minutes=30,
            time_exit_mode="loss_only",
        ),
        terminal=_terminal("exact", pending=True),
    ),
    StrategyRuntimeContract(
        strategy_id="dubai_frontloaded_40m_v1",
        channel="canal1",
        strategy_fingerprint=(
            "cdee2bdfc53aff748d0b87e1d57301793eeb620a4287916c4494cb6681a070b0"
        ),
        entry=EntryPlanContract(
            mode="market_ladder",
            volumes=(0.01, 0.05, 0.01, 0.02, 0.01, 0.02),
            ladder_step=4.0,
            expiry_minutes=15,
        ),
        protection=ProtectionContract(
            basket_stop_eur=30.0,
            profit_arm_eur=10.0,
            profit_giveback_eur=8.0,
            time_exit_minutes=40,
            time_exit_mode="loss_only",
        ),
        terminal=_terminal("exact", pending=True),
    ),
    StrategyRuntimeContract(
        strategy_id="gold_now_555_v1",
        channel="canal2",
        strategy_fingerprint=(
            "555124a24b534aa2abda53ddaaa2ee35fd3afd07e61d05937eb14c80ad0676f0"
        ),
        entry=EntryPlanContract(
            mode="adverse_reversal",
            volumes=(0.04, 0.03, 0.03, 0.03, 0.03),
            ladder_step=1.5,
            expiry_minutes=30,
            adverse=1.0,
            reversal=1.5,
        ),
        protection=ProtectionContract(
            target_steps=(0.5, 1.0, 1.5, 2.0, 2.5),
            trailing_distance=30.0,
            profit_arm_eur=30.0,
            profit_giveback_eur=1.0,
            time_exit_minutes=180,
            time_exit_mode="non_negative",
        ),
        terminal=_terminal("explicit_close_only", pending=True),
    ),
    StrategyRuntimeContract(
        strategy_id="gold_now_b210_v1",
        channel="canal2",
        strategy_fingerprint=(
            "b210010f4122b5fc2d5e657c512c8a8e94db81647b4d8fe9b0b95228983b5f58"
        ),
        entry=EntryPlanContract(
            mode="market_ladder",
            volumes=(0.01, 0.01, 0.01, 0.01, 0.01, 0.01),
            ladder_step=1.0,
            expiry_minutes=15,
        ),
        protection=ProtectionContract(
            basket_stop_eur=60.0,
            profit_arm_eur=30.0,
            profit_giveback_eur=10.0,
            time_exit_minutes=3,
            time_exit_mode="profit_only",
        ),
        terminal=_terminal("exact", pending=True),
    ),
    StrategyRuntimeContract(
        strategy_id="gold_now_c490_v1",
        channel="canal2",
        strategy_fingerprint=(
            "c4900550abae98de1500bf5b849072956175fdecda102fad69be9f7975cbf8d6"
        ),
        entry=EntryPlanContract(
            mode="immediate_multi",
            volumes=(0.01, 0.01, 0.01, 0.01, 0.01),
        ),
        protection=ProtectionContract(
            hard_stop_eur_per_leg=20.0,
            break_even_trigger_xau=12.0,
            basket_stop_eur=100.0,
            profit_arm_eur=10.0,
            profit_giveback_eur=8.0,
            time_exit_minutes=40,
            time_exit_mode="loss_only",
        ),
        terminal=_terminal("ignore", pending=False),
    ),
)

_BY_ID = MappingProxyType({item.strategy_id: item for item in _CONTRACTS})
if len(_BY_ID) != len(_CONTRACTS):
    raise RuntimeError("duplicate strategy contract ID")


def all_strategy_contracts() -> tuple[StrategyRuntimeContract, ...]:
    return _CONTRACTS


def strategy_contract_by_id(strategy_id: str) -> StrategyRuntimeContract:
    try:
        return _BY_ID[str(strategy_id)]
    except KeyError as exc:
        raise KeyError(f"unknown strategy contract: {strategy_id}") from exc
