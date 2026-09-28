"""Data-only contract for offline, quote-clock broker-protection hypotheses."""

from dataclasses import dataclass
import math

from .passive_fill_contract import PassiveFillScenario


@dataclass(frozen=True)
class InitialProtection:
    ticket: str
    sl: float | None
    tp: float | None
    source: str

    def __post_init__(self):
        if not self.ticket or not self.source:
            raise ValueError("initial protection requires ticket and source")
        for name in ("sl", "tp"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not math.isfinite(value) or value <= 0):
                raise ValueError(f"initial {name} must be positive or None")


@dataclass(frozen=True)
class ProtectionProfile:
    """Explicit hypothesis, not a claim to reproduce undocumented server rules.

    Processing occurs on the next available quote at/after the delay, never on
    the requesting quote. Existing protection wins before modifications. By
    default TP fills retain the limit-level convention; an explicit passive
    fill scenario separates terminal touch from hypothetical fill. SL fills
    at the quote.
    Nonzero freeze levels are deliberately unsupported until independently
    specified. Observation/market-close latency is not represented by this model.
    """

    point: float
    digits: int
    stops_level_points: int
    freeze_level_points: int
    processing_delay_ms: int
    acknowledgement_delay_ms: int
    retry_delay_ms: int
    initial_protections: tuple[InitialProtection, ...] = ()
    max_events: int = 100_000
    name: str = "quote_clock_protection_v1"
    policy_extension: str = "none"
    request_quote_binding: str = "timestamp_only"
    passive_fill_scenario: PassiveFillScenario | None = None

    def __post_init__(self):
        if self.policy_extension not in {"none", "own_rule_be_partial_v1", "absolute_levels_v1", "absolute_levels_be_v1", "basket_guard_v1"}:
            raise ValueError("unsupported protection policy extension")
        if self.request_quote_binding not in {"timestamp_only", "timestamp_and_ordinal"}:
            raise ValueError("unsupported request quote binding")
        if (self.passive_fill_scenario is not None
                and not isinstance(self.passive_fill_scenario, PassiveFillScenario)):
            raise ValueError("passive_fill_scenario must be a PassiveFillScenario")
        if isinstance(self.point, bool) or not math.isfinite(self.point) or self.point <= 0:
            raise ValueError("point must be finite and positive")
        for name in ("digits", "stops_level_points", "freeze_level_points", "processing_delay_ms",
                     "acknowledgement_delay_ms", "retry_delay_ms", "max_events"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.digits > 8 or not 1 <= self.max_events <= 1_000_000 or not self.name:
            raise ValueError("invalid protection profile bounds")
        if any(getattr(self, field) > 86_400_000 for field in (
            "processing_delay_ms", "acknowledgement_delay_ms", "retry_delay_ms"
        )):
            raise ValueError("protection delays cannot exceed one day")
        if not math.isclose(self.point, 10 ** -self.digits, rel_tol=0, abs_tol=1e-12):
            raise ValueError("point must match digits")
        if not isinstance(self.initial_protections, tuple) or not all(
            isinstance(row, InitialProtection) for row in self.initial_protections
        ):
            raise ValueError("initial_protections must be a tuple of InitialProtection")
        tickets = [row.ticket for row in self.initial_protections]
        if len(tickets) != len(set(tickets)):
            raise ValueError("duplicate initial protection ticket")


@dataclass(frozen=True)
class ProtectionEvent:
    ticket: str
    tick_index: int
    timestamp_ns: int
    request_id: int
    kind: str
    sl: float | None
    tp: float | None
    reason: str
