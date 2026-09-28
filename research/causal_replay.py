"""Raw-message, execution-independent controls for the existing tick engines.

This diagnostic scope covers Gold NOW and predeclared Dubai stickers, not all
provider grammar. No broker result, live state or actual entry is an input.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import math
import re
from typing import Mapping

import numpy as np
import pandas as pd

from parser import is_canal2_entry, parse_canal2
from provider_signal_catalog import _deterministic_management_semantics
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.dataset import ProviderEvent, SignalLeg, SignalPath, _align_conversion


RAW_FIELDS = (
    "ev", "channel", "message_id", "message_revision_id", "date_utc", "ts",
    "text", "is_edit", "edit_date_utc", "reply_to_msg_id", "sticker_id",
)
WEEKLY_ENTRY_MAX_AGE_S = 120.0
_COMMAND = re.compile(r"\b(?:close|exit|take\s+(?:the\s+)?money|secure|break\s*even)\b", re.I)


def utc(value) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def time_ns(value: datetime) -> int:
    delta = utc(value) - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000


def raw_message(row: Mapping) -> dict:
    if row.get("ev") != "telegram_raw":
        raise ValueError("only telegram_raw messages may cross the input boundary")
    return {name: row.get(name) for name in RAW_FIELDS}


@dataclass(frozen=True)
class CausalSignal:
    signal_id: str
    channel: str
    direction: str
    observed_at: datetime
    published_at: datetime
    message_revision_id: str
    provider_events: tuple[ProviderEvent, ...] = ()


def compile_signals(
    messages, *, start: datetime, cutoff: datetime,
    sticker_directions: Mapping[str, str],
    max_entry_age_s: float | None = None,
    management_semantics=None,
) -> tuple[tuple[CausalSignal, ...], tuple[dict, ...]]:
    start, cutoff = utc(start), utc(cutoff)
    if cutoff < start:
        raise ValueError("cutoff precedes start")
    if any(value not in ("BUY", "SELL") for value in sticker_directions.values()):
        raise ValueError("invalid sticker direction contract")
    if max_entry_age_s is not None and (not math.isfinite(max_entry_age_s)
                                        or max_entry_age_s <= 0):
        raise ValueError("entry age limit must be positive and finite")
    if management_semantics is None:
        management_semantics = _deterministic_management_semantics
    elif not callable(management_semantics):
        raise ValueError("management semantics must be callable")
    rows = [raw_message(row) for row in messages]
    rows.sort(key=lambda row: utc(row["ts"]))
    signals, links, latest, seen = {}, {}, {}, {}
    diagnostics = []

    def issue(row, reason):
        diagnostics.append({"channel": row["channel"], "message_id": row["message_id"],
                            "message_revision_id": row["message_revision_id"],
                            "observed_at": row["ts"], "reason": reason})

    for row in rows:
        observed = utc(row["ts"])
        if observed > cutoff:
            break
        revision = row["message_revision_id"]
        if not isinstance(revision, str) or not revision:
            raise ValueError("raw message revision identity missing")
        if revision in seen:
            # A poll can redeliver an edit as a new-message transport callback.
            if any(row[key] != seen[revision][key] for key in RAW_FIELDS if key not in {"ts", "is_edit"}):
                raise ValueError(f"revision identity conflict: {revision}")
            continue
        seen[revision] = row
        channel, message_id = row["channel"], row["message_id"]
        if channel not in ("canal1", "canal2"):
            issue(row, "unsupported_channel")
            continue
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("invalid message identity")
        key = (channel, message_id)
        published = utc(row["date_utc"])
        revision_time = utc(row["edit_date_utc"]) if row["edit_date_utc"] else published
        if published > observed or revision_time > observed:
            issue(row, "publication_clock_after_receipt")
            links[key] = None
            latest.pop(channel, None)
            continue
        text = str(row["text"] or "")
        direction = None
        if channel == "canal1" and row["sticker_id"] is not None:
            direction = sticker_directions.get(str(row["sticker_id"]))
            if direction is None:
                issue(row, "unknown_sticker_direction")
                links[key] = None
                latest.pop(channel, None)
        elif channel == "canal2" and is_canal2_entry(text):
            direction = parse_canal2(text).get("direction")
        if direction in ("BUY", "SELL") and key not in signals:
            if start <= observed and start <= published:
                if (max_entry_age_s is not None
                        and (observed - published).total_seconds() > max_entry_age_s):
                    issue(row, "stale_entry_candidate")
                    links[key] = None
                    continue
                signals[key] = CausalSignal(f"{channel}_{message_id}", channel, direction,
                                            observed, published, revision)
                links[key] = key
                latest[channel] = key
            else:
                issue(row, "trigger_outside_control_window")
                links[key] = None
                latest.pop(channel, None)
        elif direction and key in signals and direction != signals[key].direction:
            issue(row, "direction_edit_after_trigger")
        if key not in links:
            reply = row["reply_to_msg_id"]
            links[key] = links.get((channel, reply)) if reply is not None else latest.get(channel)
        root = links.get(key)
        semantic = management_semantics(text) if text else None
        if semantic is not None and semantic.get("modality") not in {"direct", "informational"}:
            issue(row, "conditional_management_unsupported")
            continue
        executable = semantic is not None and semantic.get("modality") == "direct"
        if not executable:
            if _COMMAND.search(text) and not semantic:
                issue(row, "unresolved_management_semantics")
            continue
        if root not in signals:
            issue(row, "unresolved_management_root")
            continue
        event = ProviderEvent(observed, semantic["action"], dict(semantic))
        signal = signals[root]
        signals[root] = replace(signal, provider_events=signal.provider_events + (event,))
    return tuple(signals.values()), tuple(diagnostics)


def _tape(values, label):
    if len(values) != 3:
        raise ValueError(f"{label} tape requires time, Bid and Ask")
    original = np.asarray(values[0])
    if original.dtype.kind not in "iu" or original.ndim != 1:
        raise ValueError(f"{label} timestamps must be integer nanoseconds")
    times, bid, ask = original.astype(np.int64), np.asarray(values[1], dtype=float), np.asarray(values[2], dtype=float)
    if not len(times) or bid.shape != times.shape or ask.shape != times.shape:
        raise ValueError(f"{label} tape is empty or has inconsistent shapes")
    if (np.diff(times) < 0).any() or not (np.isfinite(bid).all() and np.isfinite(ask).all()):
        raise ValueError(f"{label} tape is unsorted or nonfinite")
    if (bid <= 0).any() or (ask < bid).any():
        raise ValueError(f"{label} tape contains invalid Bid/Ask")
    return times, bid, ask


def _readonly(values):
    array = np.array(values, copy=True)
    array.setflags(write=False)
    return array


def _validate_causal_signal_policy(signal: CausalSignal, genome: StrategyGenome):
    if genome.entry_mode == "actual_mt5":
        raise ValueError("independent replay cannot use observed entries")
    if genome.validation_errors():
        raise ValueError(f"invalid control genome: {genome.validation_errors()}")
    if (genome.target_mode in {"provider_per_leg", "provider_target_all"}
            or genome.stop_mode == "provider" or genome.be_mode == "provider"):
        raise ValueError("provider level histories require a separate causal template contract")
    if signal.direction not in ("BUY", "SELL") or signal.channel not in ("canal1", "canal2"):
        raise ValueError("unsupported signal scope")


def _causal_template(signal: CausalSignal, genome: StrategyGenome):
    return SignalLeg("causal-template", "market", genome.volume_weights[0],
                     signal.observed_at, 1.0, None, None, None, Decimal(0), (), ())


def fx_validation_contract(max_fx_age_ms: int, max_fx_interval_ms: int | None = None) -> dict:
    if type(max_fx_age_ms) is not int or max_fx_age_ms < 0:
        raise ValueError("invalid FX freshness contract")
    if max_fx_interval_ms is None:
        max_fx_interval_ms = max_fx_age_ms
    elif (type(max_fx_interval_ms) is not int
          or not max_fx_age_ms <= max_fx_interval_ms <= 60_000):
        raise ValueError("max_fx_interval_ms must be an integer between max_fx_age_ms and 60000")
    bracketed = max_fx_interval_ms > max_fx_age_ms
    if bracketed and max_fx_age_ms == 0:
        raise ValueError("historical FX interval requires a positive max_fx_age_ms")
    return {"max_fx_age_ms": max_fx_age_ms, "max_fx_interval_ms": max_fx_interval_ms,
            "mode": "historical_bracketed_prior_quote" if bracketed else "strict_prior_quote_age",
            "status": "declared_hypothesis", "future_quote_price_used": False,
            "automatic_tape_admission": False, "money_contract_verified": False}


def align_conversion_quotes(times, conversion, *, max_fx_age_ms: int,
                            max_fx_interval_ms: int | None = None):
    """Share M7 coverage/path semantics; the historical option never reads a future price."""
    contract = fx_validation_contract(max_fx_age_ms, max_fx_interval_ms)
    fx_times, fx_bid, fx_ask = _tape(conversion, "conversion")
    if contract["mode"] == "historical_bracketed_prior_quote":
        frame = pd.DataFrame({"time_utc": pd.to_datetime(fx_times, unit="ns", utc=True),
                              "bid": fx_bid, "ask": fx_ask})
        aligned = _align_conversion(times, frame, max_age_ms=max_fx_age_ms,
                                    max_interval_ms=contract["max_fx_interval_ms"])
        if aligned is None:
            raise ValueError("existing FX interval contract rejected conversion")
        return aligned
    # Preserve legacy arrays, including negative ages when there is no prior quote.
    indices = np.searchsorted(fx_times, times, side="right") - 1
    known, safe = indices >= 0, np.maximum(indices, 0)
    ages = (times - fx_times[safe]) / 1_000_000
    valid = known & (ages >= 0) & (ages <= max_fx_age_ms)
    return np.where(known, fx_bid[safe], np.nan), np.where(known, fx_ask[safe], np.nan), ages, valid


def make_path(
    signal: CausalSignal, genome: StrategyGenome, *, market, conversion,
    cutoff: datetime, contract_size: float, currency_digits: int,
    max_fx_age_ms: int, market_sha256: str, conversion_sha256: str,
    max_fx_interval_ms: int | None = None,
    tape_start: datetime | None = None,
) -> SignalPath:
    _validate_causal_signal_policy(signal, genome)
    if not math.isfinite(contract_size) or contract_size <= 0 or type(currency_digits) is not int or currency_digits != 2:
        raise ValueError("this diagnostic requires a positive contract and EUR two-digit money")
    fx_contract = fx_validation_contract(max_fx_age_ms, max_fx_interval_ms)
    first_at, cutoff = utc(tape_start or signal.observed_at), utc(cutoff)
    if not first_at <= utc(signal.observed_at) <= cutoff:
        raise ValueError("signal outside common tape window")
    times, bid, ask = _tape(market, "market")
    start = int(np.searchsorted(times, time_ns(first_at), side="left"))
    end = int(np.searchsorted(times, time_ns(cutoff), side="right"))
    times, bid, ask = times[start:end], bid[start:end], ask[start:end]
    if not len(times) or time_ns(signal.observed_at) > int(times[-1]):
        raise ValueError("market tape has no quote in the control window")
    aligned_bid, aligned_ask, ages, valid = align_conversion_quotes(
        times, conversion, max_fx_age_ms=max_fx_age_ms, max_fx_interval_ms=max_fx_interval_ms)
    template = _causal_template(signal, genome)
    return SignalPath(
        signal_id=signal.signal_id, day=signal.observed_at.date().isoformat(), direction=signal.direction,
        signal_observed_at=signal.observed_at, opened_at=signal.observed_at, actual_pnl_eur=None,
        legs=(template,), provider_events=signal.provider_events,
        times_ns=_readonly(times), bid=_readonly(bid), ask=_readonly(ask),
        exit_quotes=_readonly(bid if signal.direction == "BUY" else ask),
        fx_bid=_readonly(aligned_bid), fx_ask=_readonly(aligned_ask),
        fx_age_ms=_readonly(ages), fx_valid=_readonly(valid),
        contract_size=contract_size, currency_digits=currency_digits,
        conversion_orientation="account_base_profit_quote",
        market_evidence=({"sha256": market_sha256, "diagnostic_only": True},),
        conversion_evidence=({"sha256": conversion_sha256, "diagnostic_only": True,
                              "fx_validation": fx_contract},),
        entry_evidence_kind="provider_signal",
        entry_expiry_anchor_at=signal.published_at if signal.channel == "canal2" else signal.observed_at,
    )


def make_shared_paths(
    signals, genomes: Mapping[str, StrategyGenome], *, start: datetime,
    market, conversion, cutoff: datetime, contract_size: float,
    currency_digits: int, max_fx_age_ms: int, market_sha256: str,
    conversion_sha256: str, max_fx_interval_ms: int | None = None,
) -> tuple[SignalPath, ...]:
    """Build independent signals on one immutable, causally aligned quote tape."""
    signals = tuple(signals)
    if (not 1 <= len(signals) <= 128
            or any(not isinstance(signal, CausalSignal) for signal in signals)
            or len({signal.signal_id for signal in signals}) != len(signals)
            or any(signal.channel not in genomes for signal in signals)):
        raise ValueError("invalid or duplicate shared causal signal universe")
    start, cutoff = utc(start), utc(cutoff)
    if not start < cutoff:
        raise ValueError("invalid shared causal tape window")
    for signal in signals:
        _validate_causal_signal_policy(signal, genomes[signal.channel])
        if not start <= utc(signal.observed_at) <= cutoff:
            raise ValueError("signal outside common tape window")
    first = signals[0]
    base = make_path(
        first, genomes[first.channel], market=market, conversion=conversion,
        cutoff=cutoff, contract_size=contract_size, currency_digits=currency_digits,
        max_fx_age_ms=max_fx_age_ms, market_sha256=market_sha256,
        conversion_sha256=conversion_sha256,
        max_fx_interval_ms=max_fx_interval_ms, tape_start=start)
    paths = []
    for signal in signals:
        if time_ns(signal.observed_at) > int(base.times_ns[-1]):
            raise ValueError("signal outside common tape window")
        paths.append(replace(
            base, signal_id=signal.signal_id, day=signal.observed_at.date().isoformat(),
            direction=signal.direction, signal_observed_at=signal.observed_at,
            opened_at=signal.observed_at, legs=(_causal_template(signal, genomes[signal.channel]),),
            provider_events=signal.provider_events,
            exit_quotes=base.bid if signal.direction == "BUY" else base.ask,
            entry_expiry_anchor_at=(signal.published_at if signal.channel == "canal2"
                                    else signal.observed_at)))
    return tuple(paths)
