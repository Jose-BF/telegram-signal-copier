"""Prospective account-risk gates for offline Dubai strategy research.

Historical drawdown only describes paths that already happened.  This module
also measures the configured loss implied by a strategy before it can be
called a finalist.  The estimate assumes continuous executable prices; gap and
margin evidence remain separate deployment gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

import numpy as np

from .contracts import StrategyGenome


@dataclass(frozen=True)
class CapitalRiskAssessment:
    initial_capital_eur: Decimal
    risk_limit_eur: Decimal
    planned_volume: float
    aggregate_planned_volume: float
    configured_maximum_concurrent_signals: int
    observed_maximum_concurrent_signals: int
    maximum_concurrent_signals: int
    loss_basis: str
    single_signal_worst_loss_eur: Decimal | None
    worst_loss_eur: Decimal | None
    worst_loss_fraction: Decimal | None
    continuous_market_only: bool
    risk_eligible: bool
    blockers: tuple[str, ...]

    @property
    def evidence_complete(self) -> bool:
        return self.worst_loss_eur is not None and not self.blockers


@dataclass(frozen=True)
class CapitalRiskContext:
    contract_size: Decimal
    conversion_orientation: str
    currency_digits: int
    loss_conversion_factor: Decimal
    signal_ids: tuple[str, ...]


def build_capital_risk_context(paths: Sequence[object]) -> CapitalRiskContext:
    """Precompute the conservative conversion envelope once per dataset."""

    paths = tuple(paths)
    blockers: list[str] = []
    contract = _consistent_money_contract(paths, blockers)
    if contract is None:
        raise ValueError(",".join(blockers))
    contract_size, orientation, digits = contract
    if orientation == "identity":
        factor = Decimal("1")
    elif orientation == "account_base_profit_quote":
        quotes = _valid_quotes(paths, "fx_bid")
        if not quotes:
            raise ValueError("missing_risk_conversion_quote")
        factor = Decimal("1") / Decimal(str(min(quotes)))
    elif orientation == "profit_base_account_quote":
        quotes = _valid_quotes(paths, "fx_ask")
        if not quotes:
            raise ValueError("missing_risk_conversion_quote")
        factor = Decimal(str(max(quotes)))
    else:
        raise ValueError("unsupported_risk_conversion_orientation")
    return CapitalRiskContext(
        contract_size=Decimal(str(contract_size)),
        conversion_orientation=orientation,
        currency_digits=digits,
        loss_conversion_factor=factor,
        signal_ids=tuple(sorted(str(path.signal_id) for path in paths)),
    )


def assess_capital_risk(
    paths: Sequence[object],
    results: Sequence[object],
    genome: StrategyGenome,
    *,
    initial_capital_eur: Decimal,
    maximum_loss_fraction: Decimal,
    maximum_concurrent_signals: int = 1,
    risk_context: CapitalRiskContext | None = None,
    observation_latency_ms: int = 0,
) -> CapitalRiskAssessment:
    """Compare a strategy's configured continuous-market loss with capital."""

    capital = Decimal(str(initial_capital_eur))
    fraction = Decimal(str(maximum_loss_fraction))
    if not capital.is_finite() or capital <= 0:
        raise ValueError("initial_capital_eur must be positive and finite")
    if not fraction.is_finite() or not Decimal("0") < fraction <= Decimal("1"):
        raise ValueError("maximum_loss_fraction must be in (0, 1]")
    if (
        isinstance(maximum_concurrent_signals, bool)
        or not isinstance(maximum_concurrent_signals, int)
        or maximum_concurrent_signals <= 0
    ):
        raise ValueError("maximum_concurrent_signals must be a positive integer")
    if (
        isinstance(observation_latency_ms, bool)
        or not isinstance(observation_latency_ms, int)
        or observation_latency_ms < 0
    ):
        raise ValueError("observation_latency_ms must be a non-negative integer")
    risk_limit = _money(capital * fraction, 2)
    planned_volume = round(sum(genome.volume_weights), 10)
    observed_concurrency, lifecycle_blockers = _observed_signal_concurrency(
        results
    )
    effective_concurrency = max(
        maximum_concurrent_signals,
        observed_concurrency,
    )
    blockers = list(genome.validation_errors())
    blockers.extend(lifecycle_blockers)
    for result in results:
        signal_id = str(getattr(result, "signal_id", "unknown"))
        blockers.extend(
            f"risk_simulation_blocked:{signal_id}:{item}"
            for item in tuple(getattr(result, "blockers", ()) or ())
        )

    if genome.stop_mode == "none":
        blockers.append("unbounded_strategy_stop")
        return _blocked(
            capital,
            risk_limit,
            planned_volume,
            maximum_concurrent_signals,
            observed_concurrency,
            effective_concurrency,
            "unbounded",
            blockers,
        )

    if genome.stop_mode == "basket_money":
        single_loss = _money(Decimal(str(genome.stop_value)), 2)
        basis = "configured_basket_trigger"
    elif genome.stop_mode == "fixed_move":
        single_loss = _fixed_move_loss(
            tuple(paths),
            genome,
            blockers,
            risk_context=risk_context,
        )
        basis = "configured_continuous_stop"
    else:
        single_loss = _provider_stop_loss(
            tuple(paths),
            tuple(results),
            genome,
            blockers,
            observation_latency_ms=observation_latency_ms,
        )
        basis = "observed_provider_stop_envelope"

    if single_loss is None or blockers:
        return _blocked(
            capital,
            risk_limit,
            planned_volume,
            maximum_concurrent_signals,
            observed_concurrency,
            effective_concurrency,
            basis,
            blockers,
        )
    loss = _money(
        single_loss * Decimal(effective_concurrency),
        2,
    )
    loss_fraction = (loss / capital).quantize(
        Decimal("0.0001"),
        rounding=ROUND_HALF_UP,
    )
    return CapitalRiskAssessment(
        initial_capital_eur=capital,
        risk_limit_eur=risk_limit,
        planned_volume=planned_volume,
        aggregate_planned_volume=round(
            planned_volume * effective_concurrency,
            10,
        ),
        configured_maximum_concurrent_signals=(
            maximum_concurrent_signals
        ),
        observed_maximum_concurrent_signals=observed_concurrency,
        maximum_concurrent_signals=effective_concurrency,
        loss_basis=basis,
        single_signal_worst_loss_eur=single_loss,
        worst_loss_eur=loss,
        worst_loss_fraction=loss_fraction,
        continuous_market_only=True,
        risk_eligible=loss <= risk_limit,
        blockers=(),
    )


def _fixed_move_loss(paths, genome, blockers, *, risk_context=None):
    if risk_context is None:
        try:
            risk_context = build_capital_risk_context(paths)
        except ValueError as exc:
            blockers.extend(str(exc).split(","))
            return None
    else:
        contract = _consistent_money_contract(paths, blockers)
        if contract is None:
            return None
        contract_size, orientation, digits = contract
        expected = (
            Decimal(str(contract_size)),
            orientation,
            digits,
            tuple(sorted(str(path.signal_id) for path in paths)),
        )
        actual = (
            risk_context.contract_size,
            risk_context.conversion_orientation,
            risk_context.currency_digits,
            risk_context.signal_ids,
        )
        if actual != expected:
            blockers.append("risk_context_mismatch")
            return None
    quote_loss = (
        Decimal(str(sum(genome.volume_weights)))
        * Decimal(str(genome.stop_value))
        * risk_context.contract_size
    )
    account_loss = quote_loss * risk_context.loss_conversion_factor
    return _money(account_loss, risk_context.currency_digits)


def _provider_stop_loss(
    paths,
    results,
    genome,
    blockers,
    *,
    observation_latency_ms=0,
):
    if not results:
        blockers.append("missing_provider_stop_results")
        return None
    indexed_paths = _index_signals(paths, "path", blockers)
    indexed_results = _index_signals(results, "result", blockers)
    for signal_id in sorted(set(indexed_results) - set(indexed_paths)):
        blockers.append(f"missing_risk_path:{signal_id}")
    per_lot_losses: list[Decimal] = []
    for signal_id, result in indexed_results.items():
        path = indexed_paths.get(signal_id)
        if path is None:
            continue
        for item in tuple(getattr(result, "blockers", ()) or ()):
            blockers.append(f"risk_simulation_blocked:{signal_id}:{item}")
        entries = tuple(getattr(result, "entries", ()) or ())
        for position_index, entry in enumerate(entries):
            ticket = str(getattr(entry, "ticket", "") or position_index)
            if not path.legs:
                blockers.append(f"missing_provider_stop_leg:{signal_id}:{ticket}")
                continue
            template = path.legs[min(position_index, len(path.legs) - 1)]
            stop = _latest_provider_stop(
                tuple(getattr(template, "sl_events", ()) or ()),
                entry.opened_at,
                observation_latency_ms=observation_latency_ms,
            )
            if stop is None:
                blockers.append(
                    f"provider_stop_unavailable_at_entry:{signal_id}:{ticket}"
                )
                continue
            loss = _provider_loss_per_lot(path, entry, stop, blockers)
            if loss is not None:
                per_lot_losses.append(loss)
    if blockers:
        return None
    if not per_lot_losses:
        blockers.append("no_filled_provider_stop_evidence")
        return None
    planned = Decimal(str(sum(genome.volume_weights)))
    return _money(max(per_lot_losses) * planned, 2)


def _provider_loss_per_lot(path, entry, stop, blockers):
    signal_id = str(path.signal_id)
    ticket = str(getattr(entry, "ticket", "") or "unknown")
    entry_price = Decimal(str(entry.entry_price))
    stop_price = Decimal(str(stop))
    direction = Decimal("1") if path.direction == "BUY" else Decimal("-1")
    raw = direction * (stop_price - entry_price) * Decimal(str(path.contract_size))
    if raw >= 0:
        blockers.append(f"provider_stop_not_adverse:{signal_id}:{ticket}")
        return None
    index = int(entry.tick_index)
    if index < 0 or index >= len(path.fx_valid) or not bool(path.fx_valid[index]):
        blockers.append(f"risk_conversion_unavailable:{signal_id}:{ticket}")
        return None
    loss = -raw
    if path.conversion_orientation == "account_base_profit_quote":
        quote = Decimal(str(path.fx_bid[index]))
        if quote <= 0:
            blockers.append(f"invalid_risk_conversion:{signal_id}:{ticket}")
            return None
        loss /= quote
    elif path.conversion_orientation == "profit_base_account_quote":
        quote = Decimal(str(path.fx_ask[index]))
        if quote <= 0:
            blockers.append(f"invalid_risk_conversion:{signal_id}:{ticket}")
            return None
        loss *= quote
    elif path.conversion_orientation != "identity":
        blockers.append(f"unsupported_risk_conversion:{signal_id}:{ticket}")
        return None
    return loss


def _latest_provider_stop(
    events,
    opened_at,
    *,
    observation_latency_ms=0,
):
    latest = None
    for event in events:
        if event.status not in {"confirmed", "snapshot"}:
            continue
        source = str(event.source or "").upper()
        if "BE" in source or "BREAK EVEN" in source or "BREAKEVEN" in source:
            continue
        effective_at = event.observed_at + timedelta(
            milliseconds=observation_latency_ms
        )
        if effective_at <= opened_at:
            latest = float(event.level)
        else:
            break
    return latest


def _index_signals(rows, kind, blockers):
    indexed = {}
    for row in rows:
        signal_id = str(getattr(row, "signal_id", "") or "")
        if not signal_id:
            blockers.append(f"risk_{kind}_without_signal_id")
        elif signal_id in indexed:
            blockers.append(f"duplicate_risk_{kind}:{signal_id}")
        else:
            indexed[signal_id] = row
    return indexed


def _consistent_money_contract(paths, blockers):
    if not paths:
        blockers.append("empty_risk_paths")
        return None
    contracts = {
        (
            float(path.contract_size),
            str(path.conversion_orientation),
            int(path.currency_digits),
        )
        for path in paths
    }
    if len(contracts) != 1:
        blockers.append("mixed_risk_money_contract")
        return None
    contract_size, orientation, digits = contracts.pop()
    if contract_size <= 0 or digits < 0:
        blockers.append("invalid_risk_money_contract")
        return None
    return contract_size, orientation, digits


def _valid_quotes(paths, field):
    values: list[float] = []
    for path in paths:
        quotes = np.asarray(getattr(path, field), dtype=float)
        valid = np.asarray(path.fx_valid, dtype=bool)
        usable = quotes[valid & np.isfinite(quotes) & (quotes > 0)]
        values.extend(float(value) for value in usable)
    return values


def _observed_signal_concurrency(results) -> tuple[int, tuple[str, ...]]:
    events: list[tuple[object, int, int]] = []
    blockers: list[str] = []
    for result in results:
        signal_id = str(getattr(result, "signal_id", "unknown"))
        entries = tuple(getattr(result, "entries", ()) or ())
        if not entries:
            continue
        starts = [getattr(entry, "opened_at", None) for entry in entries]
        if any(item is None for item in starts):
            blockers.append(f"missing_risk_open_time:{signal_id}")
            continue
        if not hasattr(result, "exits"):
            start = min(starts)
            events.append((start, 0, 1))
            events.append((start, 1, -1))
            continue
        exits = tuple(getattr(result, "exits", ()) or ())
        entries_by_ticket: dict[str, object] = {}
        for index, entry in enumerate(entries):
            ticket = str(getattr(entry, "ticket", "") or index)
            if ticket in entries_by_ticket:
                blockers.append(f"duplicate_risk_entry:{signal_id}:{ticket}")
            entries_by_ticket[ticket] = entry
        exits_by_ticket: dict[str, list[object]] = {}
        for item in exits:
            ticket = str(getattr(item, "ticket", "") or "unknown")
            if ticket not in entries_by_ticket:
                blockers.append(f"orphan_risk_exit:{signal_id}:{ticket}")
            exits_by_ticket.setdefault(ticket, []).append(item)
        ends = []
        for ticket, entry in entries_by_ticket.items():
            position_exits = exits_by_ticket.get(ticket, ())
            closed_volume = sum(
                float(getattr(item, "volume", 0.0) or 0.0)
                for item in position_exits
            )
            entry_volume = float(getattr(entry, "volume", 0.0) or 0.0)
            close_times = [
                getattr(item, "closed_at", None) for item in position_exits
            ]
            if (
                not position_exits
                or any(moment is None for moment in close_times)
                or abs(closed_volume - entry_volume) > 1e-9
            ):
                blockers.append(
                    f"incomplete_risk_lifecycle:{signal_id}:{ticket}"
                )
                continue
            ends.extend(close_times)
        start = min(starts)
        if not ends:
            continue
        end = max(ends)
        events.append((start, 0, 1))
        events.append((end, 1, -1))
    active = 0
    maximum = 0
    for _moment, _order, delta in sorted(events):
        active += delta
        maximum = max(maximum, active)
    return maximum, tuple(dict.fromkeys(blockers))


def _money(value: Decimal, digits: int) -> Decimal:
    quantum = Decimal(1).scaleb(-digits)
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _blocked(
    capital,
    risk_limit,
    volume,
    configured_maximum_concurrent_signals,
    observed_maximum_concurrent_signals,
    maximum_concurrent_signals,
    basis,
    blockers,
):
    return CapitalRiskAssessment(
        initial_capital_eur=capital,
        risk_limit_eur=risk_limit,
        planned_volume=volume,
        aggregate_planned_volume=round(
            volume * maximum_concurrent_signals,
            10,
        ),
        configured_maximum_concurrent_signals=(
            configured_maximum_concurrent_signals
        ),
        observed_maximum_concurrent_signals=(
            observed_maximum_concurrent_signals
        ),
        maximum_concurrent_signals=maximum_concurrent_signals,
        loss_basis=basis,
        single_signal_worst_loss_eur=None,
        worst_loss_eur=None,
        worst_loss_fraction=None,
        continuous_market_only=True,
        risk_eligible=False,
        blockers=tuple(dict.fromkeys(blockers)),
    )
