"""Ordered monetary-path reduction; unknown samples remain a caller's blocker."""

from decimal import Decimal


def money_path_metrics(values, *, origin):
    """Describe known samples only, including the explicitly supplied origin."""
    if isinstance(origin, bool) or not isinstance(origin, (int, Decimal)) or (
            isinstance(origin, Decimal) and not origin.is_finite()):
        raise ValueError("finite exact monetary origin required")
    peak = minimum = origin
    drawdown = origin - origin
    count, final = 0, None
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)) or (
                isinstance(value, Decimal) and not value.is_finite()):
            raise ValueError("finite exact monetary samples required")
        peak, minimum = max(peak, value), min(minimum, value)
        drawdown = max(drawdown, peak - value)
        count += 1
        final = value
    if not count:
        return None
    return {"minimum_from_origin": minimum, "maximum_from_origin": peak,
            "max_drawdown": drawdown, "final_net": final, "known_samples": count}
