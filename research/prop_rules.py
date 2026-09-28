"""Prop-firm challenge rules as data, each value tied to the official page it came from.

Checked on 2026-09-27. A value that could not be confirmed on the firm's own site is listed
in `unverified` and must not drive a decision until checked. `to_account_rules()` converts
to research.account_sim.Rules (the simulator used for challenge pass rates); an unlimited
time limit becomes `sim_horizon_days` so the day walk stays finite.

Fields not yet enforced by account_sim (best-day consistency, profitable-day minimum,
news window) are kept here so research/prop_sim.py (M8) can apply them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from research.account_sim import Rules

CHECKED = "2026-09-27"

RULE_FIELDS = (
    "target1_pct", "target2_pct", "daily_pct", "daily_basis", "daily_includes_floating",
    "max_pct", "max_mode", "min_days", "max_days", "best_day_max_share",
    "min_profitable_days", "profitable_day_pct", "news_window_min_challenge", "news_window_min_funded",
)


@dataclass(frozen=True)
class PropRuleset:
    firm: str
    program: str
    target1_pct: float
    target2_pct: float | None
    daily_pct: float
    daily_basis: str                 # what the daily limit is measured from
    daily_includes_floating: bool
    max_pct: float
    max_mode: str                    # "static" | "trailing"
    min_days: int                    # minimum trading days per phase
    max_days: int | None             # None = no time limit
    best_day_max_share: float | None  # best day <= share of positive days' profit (FTMO 0.5)
    min_profitable_days: int | None
    profitable_day_pct: float | None  # a day counts as profitable above this % of initial balance
    news_window_min_challenge: int | None  # +-minutes around high-impact news, None = allowed
    news_window_min_funded: int | None
    sources: Mapping[str, str] = field(default_factory=dict)
    unverified: frozenset = frozenset()

    def __post_init__(self):
        errors = self.validation_errors()
        if errors:
            raise ValueError(f"{self.firm} {self.program}: {errors}")

    def validation_errors(self) -> list[str]:
        errors = []
        for name in RULE_FIELDS:
            src = self.sources.get(name)
            if name in self.unverified:
                if src is not None:
                    errors.append(f"{name}: both sourced and unverified")
                continue
            if not src or not src.startswith("https://"):
                errors.append(f"{name}: missing official source")
        for name in self.sources:
            if name not in RULE_FIELDS:
                errors.append(f"{name}: unknown field")
        if not 0 < self.daily_pct < self.max_pct:
            errors.append("daily_pct must be positive and below max_pct")
        if self.max_mode not in {"static", "trailing"}:
            errors.append("max_mode must be static or trailing")
        return errors

    def to_account_rules(self, sim_horizon_days: int = 365) -> Rules:
        return Rules(daily_pct=self.daily_pct, max_pct=self.max_pct, target1_pct=self.target1_pct,
                     target2_pct=self.target2_pct if self.target2_pct is not None else 0.0,
                     min_days=self.min_days,
                     max_days=self.max_days if self.max_days is not None else sim_horizon_days)


_FTMO_OBJ = "https://ftmo.com/en/trading-objectives/"
_FTMO_NEWS = "https://ftmo.com/en/faq/can-i-trade-news/"
_FTMO_FAQ = "https://ftmo.com/en/faq/which-instruments-can-i-trade-and-what-strategies-am-i-allowed-to-use/"
_T5 = "https://the5ers.com/high-stakes/"
_FN = "https://fundednext.com/cfds/stellar-2-step"

FTMO_2STEP = PropRuleset(
    firm="FTMO", program="Challenge 2-step (standard)",
    target1_pct=10.0, target2_pct=5.0,
    daily_pct=5.0, daily_basis="balance at 00:00 CE(S)T minus 5% of initial capital",
    daily_includes_floating=True, max_pct=10.0, max_mode="static", min_days=4, max_days=None,
    best_day_max_share=0.5, min_profitable_days=None, profitable_day_pct=None,
    news_window_min_challenge=None, news_window_min_funded=2,
    sources={"target1_pct": _FTMO_OBJ, "target2_pct": _FTMO_OBJ, "daily_pct": _FTMO_OBJ, "daily_basis": _FTMO_OBJ,
             "daily_includes_floating": _FTMO_OBJ, "max_pct": _FTMO_OBJ, "max_mode": _FTMO_OBJ, "min_days": _FTMO_OBJ,
             "max_days": _FTMO_FAQ, "best_day_max_share": _FTMO_OBJ, "min_profitable_days": _FTMO_OBJ,
             "profitable_day_pct": _FTMO_OBJ, "news_window_min_challenge": _FTMO_NEWS,
             "news_window_min_funded": _FTMO_NEWS},
)

THE5ERS_HIGH_STAKES = PropRuleset(
    firm="The5ers", program="High Stakes 2-step",
    target1_pct=10.0, target2_pct=5.0,
    daily_pct=5.0, daily_basis="min(midnight balance, midnight equity) of the previous day",
    daily_includes_floating=True, max_pct=10.0, max_mode="static", min_days=0, max_days=None,
    best_day_max_share=None, min_profitable_days=3, profitable_day_pct=0.5,
    news_window_min_challenge=2, news_window_min_funded=2,
    sources={"target1_pct": _T5, "target2_pct": _T5, "daily_pct": _T5, "daily_basis": _T5,
             "daily_includes_floating": _T5, "max_pct": _T5, "max_mode": _T5, "max_days": _T5,
             "min_profitable_days": _T5, "profitable_day_pct": _T5, "news_window_min_challenge": _T5},
    # min_days: the page states profitable days, not a separate trading-day minimum;
    # best-day rule and the funded-stage news window were not stated on the page read.
    unverified=frozenset({"min_days", "best_day_max_share", "news_window_min_funded"}),
)

FUNDEDNEXT_STELLAR_2STEP = PropRuleset(
    firm="FundedNext", program="Stellar 2-step (CFD)",
    target1_pct=8.0, target2_pct=5.0,
    daily_pct=5.0, daily_basis="5% of initial balance per day",
    daily_includes_floating=True, max_pct=10.0, max_mode="static", min_days=5, max_days=None,
    best_day_max_share=None, min_profitable_days=None, profitable_day_pct=None,
    news_window_min_challenge=None, news_window_min_funded=None,
    sources={"target1_pct": _FN, "target2_pct": _FN, "daily_pct": _FN, "daily_basis": _FN, "max_pct": _FN,
             "max_mode": _FN, "min_days": _FN, "max_days": _FN, "news_window_min_challenge": _FN,
             "news_window_min_funded": _FN},
    # the page does not say whether floating losses count; EAs "available with add-ons".
    unverified=frozenset({"daily_includes_floating", "best_day_max_share", "min_profitable_days",
                          "profitable_day_pct"}),
)

PRESETS = MappingProxyType({
    "ftmo_2step": FTMO_2STEP,
    "the5ers_high_stakes": THE5ERS_HIGH_STAKES,
    "fundednext_stellar_2step": FUNDEDNEXT_STELLAR_2STEP,
})
