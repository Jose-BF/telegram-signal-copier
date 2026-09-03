"""Small, side-effect-free helpers for passive execution telemetry."""

from __future__ import annotations

from collections import defaultdict
import math
from statistics import fmean


def _non_negative_number(value, *, integer: bool = False):
    try:
        parsed = int(value) if integer else float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(value, bool) or not math.isfinite(float(parsed)) or parsed < 0:
        return None
    return parsed


def terminal_network_fields(info) -> dict:
    """Return network diagnostics already exposed by ``terminal_info``.

    The MT5 connection monitor calls ``terminal_info`` independently from the
    order path.  Reading fields from that existing snapshot therefore adds no
    broker request, ping, disk write, or wait to order execution.
    """

    ping_us = _non_negative_number(
        getattr(info, "ping_last", None),
        integer=True,
    )
    retransmission = _non_negative_number(
        getattr(info, "retransmission", None),
    )
    return {
        "terminal_ping_us": ping_us,
        "terminal_ping_ms": (
            round(float(ping_us) / 1_000.0, 3)
            if ping_us is not None else None
        ),
        "terminal_retransmission_pct": retransmission,
    }


def _channel(event: dict) -> str:
    channel = str(event.get("channel") or "").lower()
    if channel in {"canal1", "canal2"}:
        return channel
    signal_id = str(event.get("sig") or "").lower()
    if signal_id.startswith("canal1_"):
        return "canal1"
    if signal_id.startswith("canal2_"):
        return "canal2"
    return "other"


def _finite_number(value):
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(value, bool) or not math.isfinite(parsed):
        return None
    return parsed


def _duration_ms(value):
    parsed = _finite_number(value)
    if parsed is None or parsed < 0:
        return None
    return parsed / 1_000_000.0


def _is_non_negative_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_versioned_timing(event: dict) -> bool:
    if event.get("timing_schema_version") != 1:
        return event.get("timing_schema_version") is None
    attempt_started = event.get("attempt_started_monotonic_ns")
    attempt_finished = event.get("attempt_finished_monotonic_ns")
    if not (
        _is_non_negative_int(attempt_started)
        and _is_non_negative_int(attempt_finished)
        and attempt_started <= attempt_finished
    ):
        return False
    sent = event.get("broker_request_sent")
    if not isinstance(sent, bool):
        return False
    broker_fields = (
        "broker_request_started_monotonic_ns",
        "broker_response_received_monotonic_ns",
        "pre_broker_duration_ns",
        "broker_roundtrip_ns",
        "post_broker_duration_ns",
    )
    if not sent:
        return all(event.get(field) is None for field in broker_fields)
    values = [event.get(field) for field in broker_fields]
    if not all(_is_non_negative_int(value) for value in values):
        return False
    broker_started, broker_finished, pre, roundtrip, post = values
    return bool(
        attempt_started <= broker_started <= broker_finished <= attempt_finished
        and pre == broker_started - attempt_started
        and roundtrip == broker_finished - broker_started
        and post == attempt_finished - broker_finished
    )


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _stats(values: list[float]) -> dict | None:
    clean = [
        float(value)
        for value in values
        if value is not None and math.isfinite(float(value))
    ]
    if not clean:
        return None

    def rounded(value: float) -> float:
        return round(float(value), 3)

    return {
        "count": len(clean),
        "mean": rounded(fmean(clean)),
        "p50": rounded(_percentile(clean, 0.50)),
        "p90": rounded(_percentile(clean, 0.90)),
        "p95": rounded(_percentile(clean, 0.95)),
        "p99": rounded(_percentile(clean, 0.99)),
        "max": rounded(max(clean)),
    }


def _metric_report(samples: list[dict], field: str) -> dict:
    overall = [row[field] for row in samples if row.get(field) is not None]
    by_channel: defaultdict[str, list[float]] = defaultdict(list)
    by_operation: defaultdict[str, list[float]] = defaultdict(list)
    for row in samples:
        value = row.get(field)
        if value is None:
            continue
        by_channel[row["channel"]].append(value)
        by_operation[row["operation"]].append(value)
    return {
        "overall": _stats(overall),
        "by_channel": {
            key: _stats(values)
            for key, values in sorted(by_channel.items())
        },
        "by_operation": {
            key: _stats(values)
            for key, values in sorted(by_operation.items())
        },
    }


def summarize_events(events) -> dict:
    """Summarize passive latency evidence without mixing process clocks.

    Cross-event durations use ``monotonic_ns`` only when both records belong
    to the same process session.  The broker round trip is the local MT5
    ``order_send`` call duration; it includes terminal IPC and broker/network
    time and is deliberately not labelled as pure network latency.
    """

    materialized = (
        events if isinstance(events, (list, tuple)) else list(events)
    )
    decision_starts: dict[tuple[str, str], int] = {}
    for event in materialized:
        if event.get("ev") not in {
            "telegram_decision_started",
            "bot_internal_decision_started",
        }:
            continue
        session_id = event.get("session_id")
        decision_id = event.get("decision_id")
        monotonic_ns = event.get("monotonic_ns")
        if not session_id or not decision_id or not isinstance(monotonic_ns, int):
            continue
        key = (str(session_id), str(decision_id))
        previous = decision_starts.get(key)
        if previous is None or monotonic_ns < previous:
            decision_starts[key] = monotonic_ns

    samples = []
    successful_market_latencies = []
    invalid_timing_samples = 0
    for event in materialized:
        if event.get("ev") != "mt5_action_attempt":
            continue
        if not _valid_versioned_timing(event):
            invalid_timing_samples += 1
            continue
        broker_response_ns = event.get(
            "broker_response_received_monotonic_ns"
        )
        decision_to_response_ms = None
        session_id = event.get("session_id")
        decision_id = event.get("decision_id")
        if (
            session_id
            and decision_id
            and isinstance(broker_response_ns, int)
        ):
            decision_start_ns = decision_starts.get(
                (str(session_id), str(decision_id))
            )
            if (
                decision_start_ns is not None
                and broker_response_ns >= decision_start_ns
            ):
                decision_to_response_ms = (
                    broker_response_ns - decision_start_ns
                ) / 1_000_000.0

        row = {
            "channel": _channel(event),
            "operation": str(event.get("operation") or "unknown"),
            "broker_roundtrip_ms": _duration_ms(
                event.get("broker_roundtrip_ns")
            ),
            "pre_broker_ms": _duration_ms(
                event.get("pre_broker_duration_ns")
            ),
            "post_broker_ms": _duration_ms(
                event.get("post_broker_duration_ns")
            ),
            "decision_to_broker_response_ms": decision_to_response_ms,
            "adverse_slippage_xau": _finite_number(
                event.get("adverse_slippage_xau")
            ),
        }
        samples.append(row)
        result = event.get("result") or {}
        try:
            retcode = int(result.get("retcode"))
        except (AttributeError, TypeError, ValueError):
            retcode = None
        if (
            row["operation"] == "OPEN_MARKET"
            and retcode == 10009
            and decision_to_response_ms is not None
        ):
            successful_market_latencies.append(decision_to_response_ms)

    transport_samples = []
    ping_samples = []
    for event in materialized:
        if event.get("ev") == "handler_entry":
            kind = str(event.get("kind") or "").lower()
            if kind and kind not in {"new", "poll_new"}:
                continue
            value = _finite_number(
                event.get("telegram_to_handler_ms", event.get("delay_ms"))
            )
            if value is not None and value >= 0:
                transport_samples.append({
                    "channel": _channel(event),
                    "operation": "TELEGRAM_DELIVERY",
                    "value": value,
                })
        elif event.get("ev") in {
            "mt5_connection_change",
            "mt5_connection_beat",
        }:
            value = _finite_number(event.get("terminal_ping_ms"))
            if value is not None and value >= 0:
                ping_samples.append({
                    "channel": "other",
                    "operation": "TERMINAL_INFO",
                    "value": value,
                })

    scenario_stats = _stats(successful_market_latencies)
    scenario_values = []
    if scenario_stats is not None:
        scenario_values = sorted({
            int(round(scenario_stats[key]))
            for key in ("p50", "p90", "p99")
        })
    enough = len(successful_market_latencies) >= 30
    scenarios = {
        "status": "ready" if enough else "diagnostic_only",
        "reason": (
            "at_least_30_successful_market_samples"
            if enough else "fewer_than_30_successful_market_samples"
        ),
        "sample_count": len(successful_market_latencies),
        "basis": "decision_to_broker_response_ms",
        "p50_ms": (
            int(round(scenario_stats["p50"])) if scenario_stats else None
        ),
        "p90_ms": (
            int(round(scenario_stats["p90"])) if scenario_stats else None
        ),
        "p99_ms": (
            int(round(scenario_stats["p99"])) if scenario_stats else None
        ),
        "scenarios_ms": scenario_values,
    }

    return {
        "schema_version": 1,
        "attempt_samples": len(samples),
        "invalid_timing_samples": invalid_timing_samples,
        "broker_roundtrip_ms": _metric_report(
            samples, "broker_roundtrip_ms"
        ),
        "pre_broker_ms": _metric_report(samples, "pre_broker_ms"),
        "post_broker_ms": _metric_report(samples, "post_broker_ms"),
        "decision_to_broker_response_ms": _metric_report(
            samples,
            "decision_to_broker_response_ms",
        ),
        "telegram_transport_ms": _metric_report(
            transport_samples,
            "value",
        ),
        "terminal_ping_ms": _metric_report(ping_samples, "value"),
        "adverse_slippage_xau": _metric_report(
            samples,
            "adverse_slippage_xau",
        ),
        "simulation_latency_scenarios": scenarios,
    }
