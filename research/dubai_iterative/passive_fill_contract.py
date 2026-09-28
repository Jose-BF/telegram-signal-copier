"""Versioned hypotheses for passive TP fill timing in offline research."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PassiveFillScenario:
    """A terminal touch commits a hypothetical fill at a later quote clock."""

    delay_ns: int
    price_mode: str
    trigger_hypothesis: str = "terminal_first_touch_committed"

    def __post_init__(self) -> None:
        if isinstance(self.delay_ns, bool) or not isinstance(self.delay_ns, int) or self.delay_ns < 0:
            raise ValueError("passive fill delay must be a non-negative integer")
        if self.price_mode not in {"installed_level", "executable_quote"}:
            raise ValueError("unsupported passive fill price_mode")
        if self.trigger_hypothesis != "terminal_first_touch_committed":
            raise ValueError("unsupported passive fill trigger_hypothesis")
