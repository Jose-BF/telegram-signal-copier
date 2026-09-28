"""Per-request execution delays drawn from a measured distribution.

A fixed delay per run cannot reproduce a 55 s market fill next to 130 ms
ones, and those tails decide drawdown. LatencyModel draws one delay per
request from an empirical sample (tools/build_latency_samples.py), keyed by
(seed, component, signal, leg) so every replay of the same request with the
same seed gets the same delay, independent of evaluation order. v1 samples
only the market-open fill delay; it is supported only in the scalar engine's
client mode (the fast engine and the oracle already refuse client mode).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from statistics import NormalDist

_NORMAL = NormalDist()

COMPONENTS = ("entry_fill",)


def unit_draw(seed: int, component: str, signal_id: str, leg_index: int) -> float:
    """Deterministic uniform in [0, 1) from the request identity."""
    key = f"{seed}|{component}|{signal_id}|{leg_index}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big") / 2 ** 64


@dataclass(frozen=True)
class LatencyModel:
    label: str
    seed: int
    entry_fill_ms: tuple[int, ...]
    # Share of delay variance common to one basket (broker slow spells).
    # 0 keeps independent draws per request (identical to v1 results).
    basket_correlation: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label:
            raise ValueError("latency model label required")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("latency seed must be a non-negative integer")
        values = self.entry_fill_ms
        if (not isinstance(values, tuple) or not values
                or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in values)
                or list(values) != sorted(values)):
            raise ValueError("entry_fill_ms must be a sorted non-empty tuple of non-negative ints")
        rho = self.basket_correlation
        if isinstance(rho, bool) or not isinstance(rho, (int, float)) or not 0 <= rho < 1:
            raise ValueError("basket_correlation must be in [0, 1)")

    def entry_fill_delay_ms(self, signal_id: str, leg_index: int) -> int:
        u = unit_draw(self.seed, "entry_fill", signal_id, leg_index)
        if self.basket_correlation:
            clip = lambda value: min(max(value, 1e-12), 1 - 1e-12)  # noqa: E731
            common = _NORMAL.inv_cdf(clip(unit_draw(self.seed, "basket", signal_id, 0)))
            own = _NORMAL.inv_cdf(clip(u))
            rho = self.basket_correlation
            u = _NORMAL.cdf(rho ** 0.5 * common + (1 - rho) ** 0.5 * own)
        return self.entry_fill_ms[min(int(u * len(self.entry_fill_ms)), len(self.entry_fill_ms) - 1)]

    @classmethod
    def from_samples(cls, report: dict, seed: int, basket_correlation: float = 0.0) -> "LatencyModel":
        if report.get("contract") != "latency_samples_v1":
            raise ValueError("latency_samples_v1 report required")
        suffix = f"#rho{basket_correlation:g}" if basket_correlation else ""
        return cls(label=f"{report['label']}#seed{seed}{suffix}", seed=seed,
                   entry_fill_ms=tuple(int(v) for v in report["entry_fill_ms"]),
                   basket_correlation=float(basket_correlation))
