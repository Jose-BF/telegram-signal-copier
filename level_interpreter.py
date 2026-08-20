"""Interpretacion global de niveles de entrada.

Politica de ejecucion:
  - una entrada con direccion no se descarta por niveles incompletos o raros;
  - los niveles incoherentes se sustituyen por valores provisionales;
  - los valores oficiales posteriores siguen pasando por el validador existente.
"""

from __future__ import annotations

from copy import deepcopy

from parser import (
    correct_tp_typos,
    levels_consistent_with_direction,
    predict_levels,
    validate_range_vs_entry,
)


FALLBACK_RANGE_WIDTH_USD = 5.0
MAX_PROVIDER_RANGE_WIDTH_USD = 20.0
MAX_TP_DISTANCE_USD = 80.0
MAX_SL_DISTANCE_USD = 80.0
MIN_REBUILT_TP_STEP_USD = 2.0
MARKET_CONTEXT_SHIFT_UNIT_USD = 100.0
MARKET_CONTEXT_SHIFT_TRIGGER_USD = 80.0
MARKET_CONTEXT_SHIFT_MAX_RESIDUAL_USD = 20.0
MARKET_CONTEXT_SHIFT_MAX_UNITS = 2
MARKET_CONTEXT_SHIFT_MIN_IMPROVEMENT_USD = 40.0


def _round_price(value: float) -> float:
    return round(float(value), 2)


def expected_entry_from_range(direction: str, rng: tuple[float, float] | None):
    if not rng:
        return None
    lo, hi = rng
    return hi if direction == "BUY" else lo


def synthetic_range_from_entry(direction: str, entry: float,
                               width: float = FALLBACK_RANGE_WIDTH_USD):
    entry = _round_price(entry)
    width = float(width)
    if direction == "BUY":
        return (_round_price(entry - width), entry)
    return (entry, _round_price(entry + width))


def _shift_plan_to_market_context(direction: str, parsed: dict,
                                  reference_price: float | None):
    """Repair a coherent +/-100 XAU typo across the complete level plan."""
    if reference_price is None or not parsed.get("range"):
        return parsed, None

    try:
        raw_range = (
            _round_price(parsed["range"][0]),
            _round_price(parsed["range"][1]),
        )
        anchor = expected_entry_from_range(direction, raw_range)
        distance = float(reference_price) - float(anchor)
    except (IndexError, TypeError, ValueError):
        return parsed, None

    if abs(distance) < MARKET_CONTEXT_SHIFT_TRIGGER_USD:
        return parsed, None

    units = round(distance / MARKET_CONTEXT_SHIFT_UNIT_USD)
    if units == 0 or abs(units) > MARKET_CONTEXT_SHIFT_MAX_UNITS:
        return parsed, None
    offset = units * MARKET_CONTEXT_SHIFT_UNIT_USD
    residual = abs(distance - offset)
    if residual > MARKET_CONTEXT_SHIFT_MAX_RESIDUAL_USD:
        return parsed, None

    shifted_range = tuple(_round_price(value + offset) for value in raw_range)
    usable, _ = _range_is_usable(
        direction, shifted_range, _round_price(reference_price))
    if not usable:
        return parsed, None

    shifted = deepcopy(parsed)
    shifted["range"] = shifted_range
    shifted_fields = ["range"]

    def shift_if_closer(value):
        raw_value = _round_price(value)
        candidate = _round_price(raw_value + offset)
        improvement = (
            abs(raw_value - float(reference_price))
            - abs(candidate - float(reference_price))
        )
        if improvement >= MARKET_CONTEXT_SHIFT_MIN_IMPROVEMENT_USD:
            return candidate, True
        return raw_value, False

    if shifted.get("tps"):
        try:
            corrected_tps = []
            any_tp_shifted = False
            for value in shifted["tps"]:
                corrected, did_shift = shift_if_closer(value)
                corrected_tps.append(corrected)
                any_tp_shifted = any_tp_shifted or did_shift
            shifted["tps"] = corrected_tps
            if any_tp_shifted:
                shifted_fields.append("tps")
        except (TypeError, ValueError):
            return parsed, None
    if shifted.get("sl") is not None:
        try:
            shifted["sl"], sl_shifted = shift_if_closer(shifted["sl"])
            if sl_shifted:
                shifted_fields.append("sl")
        except (TypeError, ValueError):
            return parsed, None

    return shifted, {
        "field": "plan",
        "kind": "market_context_shift",
        "offset": offset,
        "reference_price": _round_price(reference_price),
        "original_range": list(raw_range),
        "corrected_range": list(shifted_range),
        "residual": _round_price(residual),
        "shifted_fields": shifted_fields,
    }


def align_provider_plan_to_market_context(
        direction: str, parsed: dict,
        reference_price: float | None = None) -> dict:
    """Align only provider-supplied prices; never infer missing levels."""
    normalized = deepcopy(parsed or {})
    direction = str(direction or normalized.get("direction") or "").upper()
    if direction:
        normalized["direction"] = direction

    zones = normalized.get("zones") or []
    uses_zone_shape = bool(zones) and not normalized.get("range")
    candidate = deepcopy(normalized)
    if uses_zone_shape:
        candidate["range"] = tuple(zones[0])

    shifted, correction = _shift_plan_to_market_context(
        direction,
        candidate,
        reference_price,
    )
    if correction is None:
        return {
            "parsed": normalized,
            "corrections": [],
            "provisional": False,
        }

    if uses_zone_shape:
        offset = float(correction["offset"])
        shifted["zones"] = [
            [_round_price(value + offset) for value in zone]
            for zone in zones
        ]
        shifted.pop("range", None)
        correction["shifted_fields"] = [
            "zones" if field == "range" else field
            for field in correction.get("shifted_fields") or []
        ]

    return {
        "parsed": shifted,
        "corrections": [correction],
        "provisional": True,
    }


def _range_is_usable(direction: str, rng: tuple[float, float],
                     reference_price: float | None) -> tuple[bool, str | None]:
    lo, hi = rng
    width = hi - lo
    if width <= 0:
        return False, f"invalid_range_width={width:g}"
    if width > MAX_PROVIDER_RANGE_WIDTH_USD:
        return False, f"range_width_too_wide={width:g}"
    if reference_price is not None:
        validation = validate_range_vs_entry(direction, reference_price, lo, hi)
        if not validation["ok"]:
            return False, validation["reason"]
    return True, None


def _tp_is_usable(direction: str, entry: float, tp: float) -> bool:
    if abs(tp - entry) > MAX_TP_DISTANCE_USD:
        return False
    if direction == "BUY":
        return tp > entry
    return tp < entry


def _tp_keeps_sequence(direction: str, previous: float | None,
                       tp: float) -> bool:
    if previous is None:
        return True
    if direction == "BUY":
        return tp > previous
    return tp < previous


def _repair_mixed_market_context(direction: str, parsed: dict,
                                 reference_price: float | None):
    """Repair plans where only some provider levels are off by +/-100.

    A complete-plan shift cannot repair inputs such as ``4389 - 4494`` near
    a 4394 market because one endpoint is already in the right context.  The
    repair is deliberately narrow: it runs only for an unusable range and
    accepts companion TP/SL shifts only when their original value is itself
    unusable.
    """
    if reference_price is None or not parsed.get("range"):
        return parsed, None

    try:
        raw_range = tuple(_round_price(value) for value in parsed["range"][:2])
        if len(raw_range) != 2:
            return parsed, None
    except (TypeError, ValueError):
        return parsed, None

    reference = _round_price(reference_price)
    usable, _ = _range_is_usable(direction, raw_range, reference)
    if usable:
        return parsed, None

    offsets = tuple(
        unit * MARKET_CONTEXT_SHIFT_UNIT_USD
        for unit in range(-MARKET_CONTEXT_SHIFT_MAX_UNITS,
                          MARKET_CONTEXT_SHIFT_MAX_UNITS + 1)
    )
    candidates = []
    for low_offset in offsets:
        for high_offset in offsets:
            if low_offset == high_offset:
                continue
            candidate = (
                _round_price(raw_range[0] + low_offset),
                _round_price(raw_range[1] + high_offset),
            )
            candidate_usable, _ = _range_is_usable(
                direction, candidate, reference)
            if not candidate_usable:
                continue
            anchor = expected_entry_from_range(direction, candidate)
            score = (
                abs(float(anchor) - reference),
                abs((candidate[1] - candidate[0]) - FALLBACK_RANGE_WIDTH_USD),
                int(low_offset != 0.0) + int(high_offset != 0.0),
                abs(low_offset) + abs(high_offset),
            )
            candidates.append((score, candidate, low_offset, high_offset))

    if not candidates:
        return parsed, None

    _, corrected_range, low_offset, high_offset = min(
        candidates, key=lambda item: item[0])
    repaired = deepcopy(parsed)
    repaired["range"] = corrected_range
    shifted_fields = []
    applied_offsets: dict[str, object] = {}
    if low_offset:
        shifted_fields.append("range_low")
        applied_offsets["range_low"] = low_offset
    if high_offset:
        shifted_fields.append("range_high")
        applied_offsets["range_high"] = high_offset

    raw_tps = list(repaired.get("tps") or [])
    if raw_tps:
        corrected_tps = []
        tp_offsets = []
        previous = None
        for raw_tp in raw_tps:
            tp = _round_price(raw_tp)
            applied = 0.0
            if not (
                _tp_is_usable(direction, reference, tp)
                and _tp_keeps_sequence(direction, previous, tp)
            ):
                tp_candidates = []
                for offset in offsets:
                    if not offset:
                        continue
                    candidate = _round_price(tp + offset)
                    if (
                        _tp_is_usable(direction, reference, candidate)
                        and _tp_keeps_sequence(direction, previous, candidate)
                    ):
                        tp_candidates.append((
                            (abs(offset), abs(candidate - reference)),
                            candidate,
                            offset,
                        ))
                if tp_candidates:
                    _, tp, applied = min(tp_candidates, key=lambda item: item[0])
            corrected_tps.append(tp)
            tp_offsets.append(applied)
            if _tp_is_usable(direction, reference, tp):
                previous = tp
        repaired["tps"] = corrected_tps
        if any(tp_offsets):
            shifted_fields.append("tps")
            applied_offsets["tps"] = tp_offsets

    if repaired.get("sl") is not None:
        raw_sl = _round_price(repaired["sl"])
        validation = levels_consistent_with_direction(
            direction, reference, tps=None, sl=raw_sl)
        sl_usable = (
            validation["sl_ok"]
            and abs(raw_sl - reference) <= MAX_SL_DISTANCE_USD
        )
        if not sl_usable:
            sl_candidates = []
            for offset in offsets:
                if not offset:
                    continue
                candidate = _round_price(raw_sl + offset)
                candidate_validation = levels_consistent_with_direction(
                    direction, reference, tps=None, sl=candidate)
                if (
                    candidate_validation["sl_ok"]
                    and abs(candidate - reference) <= MAX_SL_DISTANCE_USD
                ):
                    sl_candidates.append((
                        (abs(offset), abs(candidate - reference)),
                        candidate,
                        offset,
                    ))
            if sl_candidates:
                _, corrected_sl, sl_offset = min(
                    sl_candidates, key=lambda item: item[0])
                repaired["sl"] = corrected_sl
                shifted_fields.append("sl")
                applied_offsets["sl"] = sl_offset

    return repaired, {
        "field": "plan",
        "kind": "mixed_market_context_shift",
        "reference_price": reference,
        "original_range": list(raw_range),
        "corrected_range": list(corrected_range),
        "shifted_fields": shifted_fields,
        "offsets": applied_offsets,
    }


def _fallback_tp(direction: str, entry: float, index: int) -> float:
    offsets = (3, 5, 7, 9, 14, 20)
    off = offsets[index] if index < len(offsets) else offsets[-1] + 5 * (index - len(offsets) + 1)
    return _round_price(entry + off if direction == "BUY" else entry - off)


def _sequence_safe_tp(direction: str, entry: float, index: int,
                      previous: float | None,
                      predicted_tps: list[float]) -> float:
    candidates = []
    if index < len(predicted_tps):
        candidates.append(_round_price(predicted_tps[index]))
    candidates.append(_fallback_tp(direction, entry, index))

    for candidate in candidates:
        if (_tp_is_usable(direction, entry, candidate)
                and _tp_keeps_sequence(direction, previous, candidate)):
            return candidate

    anchor = previous if previous is not None else entry
    if direction == "BUY":
        return _round_price(anchor + MIN_REBUILT_TP_STEP_USD)
    return _round_price(anchor - MIN_REBUILT_TP_STEP_USD)


def interpret_entry_levels(channel: str, direction: str, parsed: dict,
                           reference_price: float | None = None) -> dict:
    """Devuelve niveles seguros para abrir/aplicar una entrada.

    `parsed` contiene lo que saco el parser del canal. `reference_price` es el
    mejor precio contextual disponible: tick pre-open, fill real, o None.
    """
    direction = (direction or parsed.get("direction") or "").upper()
    normalized = deepcopy(parsed or {})
    if direction:
        normalized["direction"] = direction

    corrections: list[dict] = []
    normalized, context_correction = _shift_plan_to_market_context(
        direction, normalized, reference_price)
    if context_correction:
        corrections.append(context_correction)
    normalized, mixed_context_correction = _repair_mixed_market_context(
        direction, normalized, reference_price)
    if mixed_context_correction:
        corrections.append(mixed_context_correction)
    raw_range = normalized.get("range")
    entry = _round_price(reference_price) if reference_price is not None else None

    usable_range = None
    if raw_range:
        raw_range = (_round_price(raw_range[0]), _round_price(raw_range[1]))
        ok, reason = _range_is_usable(direction, raw_range, entry)
        if ok:
            usable_range = raw_range
        else:
            corrections.append({
                "field": "range",
                "kind": "rebuilt_from_reference",
                "original": list(raw_range),
                "reason": reason,
            })

    if usable_range is None:
        if entry is None and raw_range:
            entry = expected_entry_from_range(direction, raw_range)
        if entry is not None:
            usable_range = synthetic_range_from_entry(direction, entry)
            normalized["range"] = usable_range
            if not any(c["field"] == "range" for c in corrections):
                corrections.append({
                    "field": "range",
                    "kind": "inferred_from_reference",
                    "original": None,
                    "corrected": list(usable_range),
                    "reason": "missing_range",
                })
            else:
                corrections[-1]["corrected"] = list(usable_range)
        elif raw_range:
            usable_range = raw_range
            normalized["range"] = usable_range
    else:
        normalized["range"] = usable_range

    if entry is None:
        entry = expected_entry_from_range(direction, usable_range)

    predicted = None
    if usable_range:
        predicted = predict_levels(direction, usable_range[0], usable_range[1])

    raw_tps = list(normalized.get("tps") or [])
    if entry is not None and raw_tps:
        corrected_tps, typo_corrections = correct_tp_typos(
            direction, entry, raw_tps, max_dist_usd=MAX_TP_DISTANCE_USD)
        if typo_corrections:
            corrections.append({
                "field": "tps",
                "kind": "typo_corrected",
                "original": raw_tps,
                "corrected": corrected_tps,
                "details": typo_corrections,
            })
        raw_tps = corrected_tps

    final_tps: list[float] = []
    predicted_tps = list((predicted or {}).get("tps") or [])
    if raw_tps and entry is not None:
        for idx, tp in enumerate(raw_tps):
            tp = _round_price(tp)
            previous_tp = final_tps[-1] if final_tps else None
            if (_tp_is_usable(direction, entry, tp)
                    and _tp_keeps_sequence(direction, previous_tp, tp)):
                final_tps.append(tp)
            else:
                fallback = _sequence_safe_tp(
                    direction, entry, idx, previous_tp, predicted_tps)
                final_tps.append(fallback)
                corrections.append({
                    "field": "tps",
                    "kind": "tp_replaced",
                    "index": idx,
                    "original": tp,
                    "corrected": fallback,
                    "reason": "tp_inconsistent_with_direction",
                })
        if len(final_tps) < len(predicted_tps):
            for idx in range(len(final_tps), len(predicted_tps)):
                previous_tp = final_tps[-1] if final_tps else None
                candidate = _round_price(predicted_tps[idx])
                if (_tp_is_usable(direction, entry, candidate)
                        and _tp_keeps_sequence(direction, previous_tp, candidate)):
                    final_tps.append(candidate)
                else:
                    final_tps.append(_sequence_safe_tp(
                        direction, entry, idx, previous_tp, predicted_tps))
    elif predicted_tps:
        final_tps = predicted_tps
        corrections.append({
            "field": "tps",
            "kind": "inferred",
            "original": None,
            "corrected": final_tps,
            "reason": "missing_tps",
        })
    if final_tps:
        normalized["tps"] = final_tps

    raw_sl = normalized.get("sl")
    final_sl = None
    if raw_sl is not None and entry is not None:
        raw_sl = _round_price(raw_sl)
        validation = levels_consistent_with_direction(
            direction, entry, tps=None, sl=raw_sl)
        sl_distance_ok = abs(raw_sl - entry) <= MAX_SL_DISTANCE_USD
        if validation["sl_ok"] and sl_distance_ok:
            final_sl = raw_sl
        else:
            final_sl = (predicted or {}).get("sl")
            if final_sl is None and entry is not None:
                fallback_range = synthetic_range_from_entry(direction, entry)
                final_sl = predict_levels(
                    direction, fallback_range[0], fallback_range[1])["sl"]
            corrections.append({
                "field": "sl",
                "kind": "sl_replaced",
                "original": raw_sl,
                "corrected": final_sl,
                "reason": (
                    validation["sl_problem"]
                    if not validation["sl_ok"]
                    else "sl_too_far_from_entry"
                ),
            })
    elif (predicted or {}).get("sl") is not None:
        final_sl = predicted["sl"]
        corrections.append({
            "field": "sl",
            "kind": "inferred",
            "original": None,
            "corrected": final_sl,
            "reason": "missing_sl",
        })
    if final_sl is not None:
        normalized["sl"] = _round_price(final_sl)

    return {
        "channel": channel,
        "direction": direction,
        "parsed": normalized,
        "corrections": corrections,
        "provisional": bool(corrections),
    }
