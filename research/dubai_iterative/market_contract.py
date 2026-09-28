"""Data-only contract for a bounded, serialized quote-clock market model."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MarketProfile:
    """Hypothesis for a serial client, not a reproduction of server internals.

    Entry processing uses ExecutionAssumptions.entry_fill_latency_ms. The client
    cannot manage positions or request later ladder legs before the entry ack.
    Installed broker protections remain active while the client is waiting.
    Market closes process on a later quote, even for a zero processing delay.
    Rejected entries cancel remaining unrequested entries; no implicit retries.
    """

    entry_acknowledgement_delay_ms: int
    close_processing_delay_ms: int
    close_acknowledgement_delay_ms: int
    volume_min: float
    volume_max: float
    volume_step: float
    max_events: int = 100_000
    name: str = "quote_clock_market_v1"

    def __post_init__(self):
        for field in ("entry_acknowledgement_delay_ms", "close_processing_delay_ms",
                      "close_acknowledgement_delay_ms", "max_events"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if any(getattr(self, field) > 86_400_000 for field in (
            "entry_acknowledgement_delay_ms", "close_processing_delay_ms",
            "close_acknowledgement_delay_ms",
        )) or not 1 <= self.max_events <= 1_000_000 or not self.name:
            raise ValueError("invalid market profile bounds")
        for field in ("volume_min", "volume_max", "volume_step"):
            value = getattr(self, field)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{field} must be finite and positive")
        if self.volume_min > self.volume_max or self.volume_step > self.volume_max:
            raise ValueError("inconsistent market volume bounds")


@dataclass(frozen=True)
class MarketEvent:
    ticket: str
    tick_index: int
    timestamp_ns: int
    request_id: int
    kind: str
    price: float | None
    volume: float
    reason: str
