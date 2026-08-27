"""Pure data contracts for prospective strategy shadows.

This module deliberately has no Telegram, MT5 or live-execution dependency.
All values crossing the boundary are primitive, serializable observations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from typing import Any, Mapping


def canonical_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _positive_finite(value: float, label: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{label} must be positive and finite")
    return normalized


def normalize_direction(value: str) -> str:
    direction = str(value or "").upper()
    if direction not in {"BUY", "SELL"}:
        raise ValueError("direction must be BUY or SELL")
    return direction


@dataclass(frozen=True)
class ShadowTick:
    time_msc: int
    bid: float
    ask: float
    observed_at_utc: str
    positive_eur_per_move_lot: float | None
    negative_eur_per_move_lot: float | None
    money_evidence_id: str | None
    buy_positive_eur_per_move_lot: float | None = None
    buy_negative_eur_per_move_lot: float | None = None
    sell_positive_eur_per_move_lot: float | None = None
    sell_negative_eur_per_move_lot: float | None = None
    last: float = 0.0
    flags: int = 0
    volume_real: float = 0.0

    def __post_init__(self) -> None:
        if int(self.time_msc) < 0:
            raise ValueError("time_msc must be non-negative")
        bid = _positive_finite(self.bid, "bid")
        ask = _positive_finite(self.ask, "ask")
        if ask < bid:
            raise ValueError("ask cannot be below bid")
        for label, value in (
            ("positive_eur_per_move_lot", self.positive_eur_per_move_lot),
            ("negative_eur_per_move_lot", self.negative_eur_per_move_lot),
            ("buy_positive_eur_per_move_lot", self.buy_positive_eur_per_move_lot),
            ("buy_negative_eur_per_move_lot", self.buy_negative_eur_per_move_lot),
            ("sell_positive_eur_per_move_lot", self.sell_positive_eur_per_move_lot),
            ("sell_negative_eur_per_move_lot", self.sell_negative_eur_per_move_lot),
        ):
            if value is not None:
                _positive_finite(value, label)

    @property
    def identity(self) -> tuple[int, float, float, float, int, float]:
        return (
            int(self.time_msc),
            float(self.bid),
            float(self.ask),
            float(self.last),
            int(self.flags),
            float(self.volume_real),
        )

    def executable_price(self, direction: str, *, entry: bool) -> float:
        normalized = normalize_direction(direction)
        if entry:
            return float(self.ask if normalized == "BUY" else self.bid)
        return float(self.bid if normalized == "BUY" else self.ask)

    def money_factor(self, direction: str, *, favourable: bool) -> float | None:
        normalized = normalize_direction(direction)
        specific = {
            ("BUY", True): self.buy_positive_eur_per_move_lot,
            ("BUY", False): self.buy_negative_eur_per_move_lot,
            ("SELL", True): self.sell_positive_eur_per_move_lot,
            ("SELL", False): self.sell_negative_eur_per_move_lot,
        }[(normalized, bool(favourable))]
        if specific is not None:
            return float(specific)
        fallback = (
            self.positive_eur_per_move_lot
            if favourable else self.negative_eur_per_move_lot
        )
        return None if fallback is None else float(fallback)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ShadowTick":
        return cls(**dict(payload))


@dataclass(frozen=True)
class ShadowManagementEvent:
    event_id: str
    signal_id: str
    action: str
    observed_at_utc: str
    observed_tick_msc: int | None = None
    price: float | None = None
    raw_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id is required")
        if not self.signal_id:
            raise ValueError("signal_id is required")
        if self.price is not None:
            _positive_finite(self.price, "management price")


@dataclass(frozen=True)
class ShadowPosition:
    leg_index: int
    volume: float
    entry_price: float
    opened_tick_msc: int
    opened_at_utc: str | None = None
    target_price: float | None = None
    stop_price: float | None = None
    break_even_applied: bool = False
    status: str = "open"
    close_price: float | None = None
    closed_tick_msc: int | None = None
    close_reason: str | None = None
    realized_eur: float = 0.0

    def __post_init__(self) -> None:
        if int(self.leg_index) < 0:
            raise ValueError("leg_index must be non-negative")
        _positive_finite(self.volume, "position volume")
        _positive_finite(self.entry_price, "position entry price")
        if self.status not in {"open", "closed"}:
            raise ValueError("position status must be open or closed")
        if self.target_price is not None:
            _positive_finite(self.target_price, "target price")
        if self.stop_price is not None:
            _positive_finite(self.stop_price, "stop price")
        if self.close_price is not None:
            _positive_finite(self.close_price, "close price")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ShadowPosition":
        return cls(**dict(payload))


@dataclass(frozen=True)
class ShadowPolicy:
    candidate_id: str
    channel: str
    role: str
    strategy_fingerprint: str
    entry_mode: str
    entry_volumes: tuple[float, ...]
    ladder_step: float | None = None
    ladder_expiry_minutes: int | None = None
    entry_adverse: float | None = None
    entry_reversal: float | None = None
    target_steps: tuple[float, ...] = ()
    trailing_distance: float | None = None
    hard_stop_eur_per_leg: float | None = None
    break_even_trigger_xau: float | None = None
    basket_stop_eur: float | None = None
    profit_arm_eur: float | None = None
    profit_giveback_eur: float | None = None
    time_exit_minutes: int | None = None
    time_exit_mode: str = "none"
    provider_management_mode: str = "explicit_close_only"
    provider_protection_mode: str = "none"
    schema_version: int = 1
    fill_rule: str = "first_subsequent_tick"
    money_rounding: str = "leg_cent_then_sum"

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("candidate_id is required")
        if self.channel not in {"canal1", "canal2"}:
            raise ValueError("channel must be canal1 or canal2")
        if self.role not in {"live_control", "candidate"}:
            raise ValueError("role must be live_control or candidate")
        if len(self.strategy_fingerprint) != 64:
            raise ValueError("strategy_fingerprint must be SHA-256")
        if self.entry_mode not in {
            "market_ladder", "adverse_reversal", "immediate_multi",
        }:
            raise ValueError("unsupported entry_mode")
        if not self.entry_volumes or any(
            not math.isfinite(float(value)) or float(value) <= 0.0
            for value in self.entry_volumes
        ):
            raise ValueError("entry volumes must be positive and finite")
        if self.target_steps and len(self.target_steps) != len(self.entry_volumes):
            raise ValueError("target steps must align with entry volumes")
        for label, value in (
            ("ladder_step", self.ladder_step),
            ("entry_adverse", self.entry_adverse),
            ("entry_reversal", self.entry_reversal),
            ("trailing_distance", self.trailing_distance),
            ("hard_stop_eur_per_leg", self.hard_stop_eur_per_leg),
            ("break_even_trigger_xau", self.break_even_trigger_xau),
            ("basket_stop_eur", self.basket_stop_eur),
            ("profit_arm_eur", self.profit_arm_eur),
            ("profit_giveback_eur", self.profit_giveback_eur),
        ):
            if value is not None:
                _positive_finite(value, label)
        for label, value in (
            ("ladder_expiry_minutes", self.ladder_expiry_minutes),
            ("time_exit_minutes", self.time_exit_minutes),
        ):
            if value is not None and int(value) <= 0:
                raise ValueError(f"{label} must be positive")
        if self.time_exit_mode not in {
            "none", "loss_only", "profit_only", "non_negative",
        }:
            raise ValueError("unsupported time_exit_mode")
        if self.provider_management_mode not in {
            "exact", "explicit_close_only", "ignore",
        }:
            raise ValueError("unsupported provider_management_mode")
        if self.provider_protection_mode not in {"none", "exact"}:
            raise ValueError("unsupported provider_protection_mode")
        if (
            self.profit_giveback_eur is not None
            and self.profit_arm_eur is None
        ):
            raise ValueError("profit giveback requires a profit arm")

    @property
    def max_signal_volume(self) -> float:
        return round(sum(self.entry_volumes), 8)

    def execution_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("role")
        payload.pop("strategy_fingerprint")
        payload["entry_volumes"] = list(self.entry_volumes)
        payload["target_steps"] = list(self.target_steps)
        payload["entry_quote"] = "ask_buy_bid_sell"
        payload["exit_quote"] = "bid_buy_ask_sell"
        payload["money_factor_model"] = "directional_order_calc_profit_v1"
        return payload

    @property
    def execution_fingerprint(self) -> str:
        return canonical_hash(self.execution_payload())


@dataclass(frozen=True)
class ShadowSignalState:
    signal_id: str
    source_message_id: int
    candidate_id: str
    channel: str
    direction: str
    registered_at_utc: str
    registered_tick_msc: int | None
    strategy_fingerprint: str = ""
    execution_fingerprint: str = ""
    reference_price: float | None = None
    status: str = "waiting"
    positions: tuple[ShadowPosition, ...] = ()
    realized_eur: float = 0.0
    floating_eur: float = 0.0
    max_favourable_eur: float = 0.0
    max_adverse_eur: float = 0.0
    profit_lock_armed: bool = False
    peak_total_eur: float | None = None
    adverse_armed: bool = False
    adverse_extreme: float | None = None
    pending_provider_close: bool = False
    exit_reason: str | None = None
    last_tick_identity: tuple[int, float, float, float, int, float] | None = None
    processed_management_ids: tuple[str, ...] = ()
    evidence_blockers: tuple[str, ...] = ()
    complete: bool = True

    def __post_init__(self) -> None:
        normalize_direction(self.direction)
        if self.channel not in {"canal1", "canal2"}:
            raise ValueError("channel must be canal1 or canal2")
        if self.status not in {"waiting", "open", "closed", "cancelled", "incomplete"}:
            raise ValueError("unsupported shadow state status")

    @classmethod
    def new(
        cls,
        *,
        signal_id: str,
        source_message_id: int,
        candidate_id: str,
        channel: str,
        direction: str,
        registered_at_utc: str,
        registered_tick_msc: int | None,
        strategy_fingerprint: str = "",
        execution_fingerprint: str = "",
        reference_price: float | None = None,
    ) -> "ShadowSignalState":
        return cls(
            signal_id=str(signal_id),
            source_message_id=int(source_message_id),
            candidate_id=str(candidate_id),
            channel=str(channel),
            direction=normalize_direction(direction),
            registered_at_utc=str(registered_at_utc),
            registered_tick_msc=(
                None if registered_tick_msc is None else int(registered_tick_msc)
            ),
            strategy_fingerprint=str(strategy_fingerprint),
            execution_fingerprint=str(execution_fingerprint),
            reference_price=(
                None if reference_price is None else _positive_finite(
                    reference_price, "reference_price"
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["positions"] = [item.to_dict() for item in self.positions]
        if self.last_tick_identity is not None:
            payload["last_tick_identity"] = list(self.last_tick_identity)
        payload["processed_management_ids"] = list(self.processed_management_ids)
        payload["evidence_blockers"] = list(self.evidence_blockers)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ShadowSignalState":
        values = dict(payload)
        values["positions"] = tuple(
            ShadowPosition.from_dict(item)
            for item in values.get("positions", ())
        )
        identity = values.get("last_tick_identity")
        values["last_tick_identity"] = (
            None if identity is None else tuple(identity)
        )
        values["processed_management_ids"] = tuple(
            values.get("processed_management_ids", ())
        )
        values["evidence_blockers"] = tuple(values.get("evidence_blockers", ()))
        return cls(**values)

    @property
    def state_hash(self) -> str:
        return canonical_hash(self.to_dict())


@dataclass(frozen=True)
class ShadowTransition:
    event: str
    reason: str | None
    tick_msc: int | None
    state_hash: str
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ShadowAdvance:
    state: ShadowSignalState
    transitions: tuple[ShadowTransition, ...] = ()
