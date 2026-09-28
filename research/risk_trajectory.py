"""Post-run risk on a common retained-quote grid, never a fill predictor."""

from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal

from broker_money import SUPPORTED_ORIENTATIONS, convert_profit_amount
from research.causal_comparison import SequenceEvent
from research.causal_replay import utc
from research.risk_metrics import money_path_metrics


ZERO = Decimal(0)
MAX_QUOTES = 200_000
MAX_EVENTS = 10_000
MAX_POSITION_MARKS = 5_000_000


def _decimal(value, *, positive=False):
    if isinstance(value, bool):
        raise ValueError("boolean is not a monetary value")
    result = Decimal(str(value))
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError("invalid finite monetary value")
    return result


@dataclass(frozen=True)
class RiskSpec:
    currency: str
    currency_digits: int
    contract_size: Decimal
    orientation: str
    max_fx_age_ms: int
    max_market_gap_ms: int

    def __post_init__(self):
        if not isinstance(self.currency, str) or len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError("explicit account currency required")
        if type(self.currency_digits) is not int or not 0 <= self.currency_digits <= 8:
            raise ValueError("invalid currency precision")
        if self.orientation not in SUPPORTED_ORIENTATIONS:
            raise ValueError("unsupported conversion orientation")
        for field in ("max_fx_age_ms", "max_market_gap_ms"):
            value = getattr(self, field)
            if type(value) is not int or value <= 0:
                raise ValueError("explicit positive quote coverage limits required")
        object.__setattr__(self, "contract_size", _decimal(self.contract_size, positive=True))


@dataclass(frozen=True)
class RiskQuote:
    at: object
    bid: Decimal
    ask: Decimal
    conversion_at: object = None
    conversion_bid: Decimal | None = None
    conversion_ask: Decimal | None = None
    market_at: object = None
    conversion_next_at: object = None

    def __post_init__(self):
        object.__setattr__(self, "at", utc(self.at))
        object.__setattr__(self, "market_at", utc(self.market_at) if self.market_at is not None else self.at)
        if self.market_at > self.at:
            raise ValueError("future market quote")
        if self.conversion_at is not None:
            object.__setattr__(self, "conversion_at", utc(self.conversion_at))
        if self.conversion_next_at is not None:
            next_at = utc(self.conversion_next_at)
            if self.conversion_at is None or next_at <= self.conversion_at:
                raise ValueError("invalid retrospective conversion bracket")
            object.__setattr__(self, "conversion_next_at", next_at)
        for field in ("bid", "ask", "conversion_bid", "conversion_ask"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _decimal(value, positive=True))
        if self.bid is None or self.ask is None or self.bid > self.ask:
            raise ValueError("invalid executable market spread")
        if self.conversion_bid is not None and self.conversion_ask is not None and self.conversion_bid > self.conversion_ask:
            raise ValueError("invalid conversion spread")


def _positions(events):
    if len(events) > MAX_EVENTS or any(not isinstance(row, SequenceEvent) for row in events):
        raise ValueError("invalid or excessive sequence events")
    entries = {}
    for row in events:
        if row.kind == "entry":
            if row.slot in entries:
                raise ValueError("multiple entries for a logical slot")
            entries[row.slot] = row
    for row in events:
        entry = entries.get(row.slot)
        if entry is None or row.at < entry.at or row.direction != entry.direction:
            raise ValueError("invalid exit identity, direction or chronology")
    for slot, entry in entries.items():
        volume = sum((row.volume for row in events if row.slot == slot and row.kind == "exit"), ZERO)
        if volume > entry.volume:
            raise ValueError("exit volume exceeds opened volume")
    return entries


def _metrics(samples):
    values = [row for row in samples if row["total"] is not None]
    if not values:
        return None
    return {**money_path_metrics((row["total"] for row in values), origin=ZERO),
            "final_at": values[-1]["at"],
            "max_gross_volume": max(row["long_volume"] + row["short_volume"] for row in values)}


def reconstruct_risk(events, quotes, *, spec, grid_events=(), retrospective_fx_interval_ms=None):
    """Mark actual or simulated event sequences without changing their fills.

    Costs in SequenceEvent.money are booked at their event, not backdated.
    Accrued swap or costs not represented by events require an external blocker.
    A later FX timestamp may prove retrospective coverage, never live availability.
    Metrics describe the retained grid, not unobserved intra-quote extrema.
    """
    events, quotes, grid_events = tuple(events), tuple(quotes), tuple(grid_events)
    entries = _positions(events)
    if not isinstance(spec, RiskSpec) or len(quotes) > MAX_QUOTES or len(grid_events) > MAX_EVENTS:
        raise ValueError("invalid risk specification or bounded grid exceeded")
    if retrospective_fx_interval_ms is not None and (type(retrospective_fx_interval_ms) is not int
                                                   or retrospective_fx_interval_ms <= 0):
        raise ValueError("retrospective FX interval must be explicit and positive")
    if any(row.money != convert_profit_amount(row.money, Decimal(1), orientation="identity",
                                              currency_digits=spec.currency_digits) for row in events):
        raise ValueError("booked money exceeds account precision")
    if any(not isinstance(row, RiskQuote) for row in quotes):
        raise ValueError("typed risk quotes required")
    if any(a.at > b.at for a, b in zip(quotes, quotes[1:])):
        raise ValueError("market quotes not in source order")
    quote_times = [row.at for row in quotes]
    quote_set = set(quote_times)
    boundaries = {row.at for row in (*events, *grid_events)} - quote_set
    grid = [(row.at, index, row) for index, row in enumerate(quotes)]
    for at in boundaries:
        index = bisect_right(quote_times, at) - 1
        grid.append((at, -1, quotes[index] if index >= 0 else None))
    grid.sort(key=lambda item: (item[0], item[1]))
    if len(grid) * max(1, len(entries)) > MAX_POSITION_MARKS:
        raise ValueError("risk position-mark budget exceeded")
    blockers = set()
    if not quotes:
        blockers.add("missing_market_quotes")
    market_times = list(dict.fromkeys(row.market_at for row in quotes))
    if market_times != sorted(market_times):
        raise ValueError("market source clocks regressed")
    for left, right in zip(market_times, market_times[1:]):
        if (right - left).total_seconds() * 1000 <= spec.max_market_gap_ms:
            continue
        for slot, entry in entries.items():
            exits = [row for row in events if row.slot == slot and row.kind == "exit"]
            closed = max((row.at for row in exits), default=None)
            remaining = entry.volume - sum((row.volume for row in exits), ZERO)
            if entry.at < right and (remaining or closed is None or closed > left):
                blockers.add("market_quote_gap")
    pending = sorted(events, key=lambda row: (row.at, row.kind != "entry", row.slot))
    index, states, samples, bracketed_samples = 0, {}, [], 0
    ordinals = {}
    for at, _, quote in grid:
        while index < len(pending) and pending[index].at <= at:
            row = pending[index]
            if row.kind == "entry":
                states[row.slot] = {"direction": row.direction, "volume": row.volume, "realized": row.money}
            else:
                states[row.slot]["volume"] -= row.volume
                states[row.slot]["realized"] += row.money
            index += 1
        issues, positions, bracketed = set(), {}, False
        floating, realized, long_volume, short_volume = ZERO, ZERO, ZERO, ZERO
        for slot, state in states.items():
            entry = entries[slot]
            volume = state["volume"]
            value = ZERO
            realized += state["realized"]
            if state["direction"] == "BUY":
                long_volume += volume
            else:
                short_volume += volume
            if volume:
                if quote is None:
                    issues.add("missing_prior_market_quote")
                    value = None
                elif (at - quote.market_at).total_seconds() * 1000 > spec.max_market_gap_ms:
                    issues.add("stale_market_quote")
                    value = None
                else:
                    side, sign = (quote.bid, 1) if entry.direction == "BUY" else (quote.ask, -1)
                    amount = (side - entry.price) * sign * volume * spec.contract_size
                    fx = Decimal(1)
                    if amount and spec.orientation != "identity":
                        age = (at - quote.conversion_at).total_seconds() * 1000 if quote.conversion_at is not None else None
                        if age is None or quote.conversion_bid is None or quote.conversion_ask is None:
                            issues.add("missing_conversion_quote")
                            fx = None
                        elif age < 0:
                            issues.add("future_conversion_quote")
                            fx = None
                        elif age > spec.max_fx_age_ms:
                            next_at = quote.conversion_next_at
                            valid_bracket = (retrospective_fx_interval_ms is not None
                                             and next_at is not None and at < next_at
                                             and (next_at - quote.conversion_at).total_seconds() * 1000
                                             <= retrospective_fx_interval_ms)
                            if valid_bracket:
                                bracketed = True
                            else:
                                issues.add("stale_conversion_quote")
                                fx = None
                        if fx is not None:
                            if spec.orientation == "account_base_profit_quote":
                                fx = quote.conversion_ask if amount >= 0 else quote.conversion_bid
                            else:
                                fx = quote.conversion_bid if amount >= 0 else quote.conversion_ask
                    value = convert_profit_amount(amount, fx, orientation=spec.orientation,
                                                  currency_digits=spec.currency_digits) if fx is not None else None
            if value is not None:
                floating += value
            positions[str(slot)] = {**state, "floating": value,
                                    "total": state["realized"] + value if value is not None else None}
        ordinal = ordinals.get(at, 0)
        ordinals[at] = ordinal + 1
        samples.append({"at": at, "ordinal": ordinal, "quote_at": quote.market_at if quote else None,
                        "realized": realized, "floating": floating if not issues else None,
                        "total": realized + floating if not issues else None,
                        "long_volume": long_volume, "short_volume": short_volume,
                        "open_count": sum(row["volume"] > 0 for row in states.values()),
                        "positions": positions, "blockers": sorted(issues)})
        bracketed_samples += bracketed
        blockers.update(issues)
    known = _metrics(samples)
    return {"currency": spec.currency, "samples": samples, "blockers": sorted(blockers),
            "metrics": known if not blockers else None, "known_sample_metrics": known,
            "valuation": "realized_booked_at_events_plus_executable_floating",
            "same_time_convention": "confirmed_events_before_quote_mark",
            "fx_coverage_mode": "strict_causal" if retrospective_fx_interval_ms is None else "retrospective_bracketed",
            "retrospective_fx_bracketed_samples": bracketed_samples,
            "scope": "retained_grid_including_event_boundaries_not_continuous_market_extrema",
            "full_live_parity_verified": False}


def compare_risk(observed, simulated, quotes, *, spec):
    observed, simulated, quotes = tuple(observed), tuple(simulated), tuple(quotes)
    left = reconstruct_risk(observed, quotes, spec=spec, grid_events=simulated)
    right = reconstruct_risk(simulated, quotes, spec=spec, grid_events=observed)
    first, first_unknown, mismatches, unknown, maximum = None, None, 0, 0, ZERO
    for actual, replayed in zip(left["samples"], right["samples"], strict=True):
        assert (actual["at"], actual["ordinal"]) == (replayed["at"], replayed["ordinal"])
        if actual["total"] is None or replayed["total"] is None:
            unknown += 1
            if first_unknown is None:
                first_unknown = {"at": actual["at"], "ordinal": actual["ordinal"]}
            continue
        differences = [key for key in ("total", "floating", "realized", "long_volume", "short_volume", "open_count", "positions")
                       if actual[key] != replayed[key]]
        maximum = max(maximum, abs(replayed["total"] - actual["total"]))
        if differences:
            mismatches += 1
            if first is None:
                first = {"at": actual["at"], "ordinal": actual["ordinal"], "differences": differences,
                         "observed": actual, "simulated": replayed}
    blockers = sorted(set(left["blockers"] + right["blockers"]))
    return {"status": "blocked" if blockers else "mismatch" if mismatches else "exact_sampled_path_only",
            "observed": left, "simulated": right, "first_divergence": first,
            "first_unknown": first_unknown,
            "first_divergence_scope": "first_comparable_sample_not_proof_before_unknown_intervals",
            "mismatched_pairs": mismatches, "unknown_pairs": unknown,
            "max_abs_total_difference": maximum if len(left["samples"]) > unknown else None,
            "blockers": blockers, "full_live_parity_verified": False}
