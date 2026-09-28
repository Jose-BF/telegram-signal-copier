"""Immutable wire contract for an explicitly hypothetical client scheduler."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ClientProfile:
    name: str = "single_basket_serial_v1"
    ladder_decision_mode: str = "snapshot_batch"
    entry_price_delay_ms: int | None = None
    max_events: int = 100_000

    def __post_init__(self):
        if self.name not in {"single_basket_serial_v1", "single_basket_terminal_v2",
                             "single_basket_terminal_guard_v1"}:
            raise ValueError("unsupported client model")
        if self.ladder_decision_mode not in {"snapshot_batch", "fresh_quote"}:
            raise ValueError("unsupported ladder decision mode")
        if self.entry_price_delay_ms is not None and (
            isinstance(self.entry_price_delay_ms, bool)
            or not isinstance(self.entry_price_delay_ms, int)
            or not 0 <= self.entry_price_delay_ms <= 86_400_000
        ):
            raise ValueError("entry_price_delay_ms must be a bounded non-negative integer")
        if isinstance(self.max_events, bool) or not isinstance(self.max_events, int) or not 1 <= self.max_events <= 1_000_000:
            raise ValueError("max_events must be between 1 and 1000000")


@dataclass(frozen=True)
class ClientEvent:
    kind: str
    operation: str
    ticket: str
    tick_index: int
    time_ns: int
    decision_index: int
    decision_ns: int
