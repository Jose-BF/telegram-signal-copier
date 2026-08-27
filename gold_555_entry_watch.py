"""Durable pre-entry state machine for the Gold 555 policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math

from gold_555_live_candidate import Gold555Policy


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return _utc(datetime.fromisoformat(value))


def _price(value: float) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError("quote must be positive and finite")
    return parsed


@dataclass(frozen=True)
class EntryWatchResult:
    action: str
    executable_quote: float | None
    state_changed: bool


@dataclass
class EntryWatch:
    direction: str
    reference: float
    observed_at: datetime
    expires_at: datetime
    adverse_extreme: float | None = None
    armed: bool = False
    status: str = "waiting"
    last_tick_msc: int | None = None
    confirmed_quote: float | None = None
    confirmed_at: datetime | None = None

    @classmethod
    def new(
        cls,
        direction: str,
        *,
        reference: float,
        observed_at: datetime,
        policy: Gold555Policy | None = None,
    ) -> "EntryWatch":
        normalized_direction = str(direction).upper()
        if normalized_direction not in {"BUY", "SELL"}:
            raise ValueError("direction must be BUY or SELL")
        observed = _utc(observed_at)
        active_policy = policy or Gold555Policy()
        return cls(
            direction=normalized_direction,
            reference=_price(reference),
            observed_at=observed,
            expires_at=observed
            + timedelta(minutes=active_policy.entry_expiry_minutes),
        )

    def on_quote(
        self,
        *,
        bid: float,
        ask: float,
        now: datetime,
        tick_msc: int | None,
        policy: Gold555Policy | None = None,
    ) -> EntryWatchResult:
        if self.status != "waiting":
            return EntryWatchResult("terminal", None, False)

        observed_now = _utc(now)
        if observed_now >= self.expires_at:
            self.status = "expired"
            return EntryWatchResult("expire", None, True)

        parsed_tick = int(tick_msc) if tick_msc is not None else None
        if (
            parsed_tick is not None
            and self.last_tick_msc is not None
            and parsed_tick <= self.last_tick_msc
        ):
            return EntryWatchResult("duplicate_tick", None, False)

        quote = _price(ask if self.direction == "BUY" else bid)
        if parsed_tick is not None:
            self.last_tick_msc = parsed_tick
        active_policy = policy or Gold555Policy()

        if not self.armed:
            crossed = (
                quote <= self.reference - active_policy.entry_adverse
                if self.direction == "BUY"
                else quote >= self.reference + active_policy.entry_adverse
            )
            if not crossed:
                return EntryWatchResult("wait", quote, parsed_tick is not None)
            self.armed = True
            self.adverse_extreme = quote
            return EntryWatchResult("armed", quote, True)

        assert self.adverse_extreme is not None
        previous_extreme = self.adverse_extreme
        if self.direction == "BUY":
            self.adverse_extreme = min(self.adverse_extreme, quote)
            confirmed = quote >= (
                self.adverse_extreme + active_policy.entry_reversal
            )
        else:
            self.adverse_extreme = max(self.adverse_extreme, quote)
            confirmed = quote <= (
                self.adverse_extreme - active_policy.entry_reversal
            )

        if confirmed:
            self.status = "confirmed"
            self.confirmed_quote = quote
            self.confirmed_at = observed_now
            return EntryWatchResult("confirm", quote, True)
        if self.adverse_extreme != previous_extreme:
            return EntryWatchResult("track", quote, True)
        return EntryWatchResult("wait", quote, parsed_tick is not None)

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "reference": self.reference,
            "observed_at": _iso(self.observed_at),
            "expires_at": _iso(self.expires_at),
            "adverse_extreme": self.adverse_extreme,
            "armed": self.armed,
            "status": self.status,
            "last_tick_msc": self.last_tick_msc,
            "confirmed_quote": self.confirmed_quote,
            "confirmed_at": _iso(self.confirmed_at),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "EntryWatch":
        observed_at = _parse_iso(payload.get("observed_at"))
        expires_at = _parse_iso(payload.get("expires_at"))
        if observed_at is None or expires_at is None:
            raise ValueError("entry watch timestamps are required")
        direction = str(payload.get("direction") or "").upper()
        if direction not in {"BUY", "SELL"}:
            raise ValueError("direction must be BUY or SELL")
        status = str(payload.get("status") or "waiting")
        if status not in {"waiting", "confirmed", "expired", "cancelled"}:
            raise ValueError("unsupported entry watch status")
        return cls(
            direction=direction,
            reference=_price(payload.get("reference")),
            observed_at=observed_at,
            expires_at=expires_at,
            adverse_extreme=(
                None
                if payload.get("adverse_extreme") is None
                else _price(payload["adverse_extreme"])
            ),
            armed=bool(payload.get("armed", False)),
            status=status,
            last_tick_msc=(
                None
                if payload.get("last_tick_msc") is None
                else int(payload["last_tick_msc"])
            ),
            confirmed_quote=(
                None
                if payload.get("confirmed_quote") is None
                else _price(payload["confirmed_quote"])
            ),
            confirmed_at=_parse_iso(payload.get("confirmed_at")),
        )
