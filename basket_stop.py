"""Broker-valued common basket stop, one native response at a time."""

import math

from mt5_read_protocol import ReadOperation, ReadRequest


def basket_stop_requirement(direction, spec, common_stop, *, last_level=None,
                            last_request=0., now, force=False):
    """Classify the delivered snapshot, not the broker's current protection."""
    if common_stop is None:
        return "unavailable"
    installed = float(spec.get("sl") or 0.)
    point = max(float(spec.get("point") or .01), 1e-8)
    protected = installed > 0 and (
        installed >= common_stop - point / 2 if direction == "BUY"
        else installed <= common_stop + point / 2)
    if protected:
        return "observed_protected"
    same_level = last_level is not None and abs(float(last_level) - common_stop) <= point / 2
    if not force and same_level and now - float(last_request) < 5.:
        return "retry_deferred"
    return "request"


def open_position_specs_reads(tickets, *, default_symbol):
    if not tickets:
        return {}
    all_open = yield ReadRequest(ReadOperation.POSITIONS, request_id="positions")
    if all_open is None:
        return None
    wanted = set(int(ticket) for ticket in tickets)
    symbol_specs, result = {}, {}
    for position in all_open:
        ticket = int(position.ticket)
        if ticket not in wanted:
            continue
        symbol = str(getattr(position, "symbol", default_symbol))
        if symbol not in symbol_specs:
            symbol_specs[symbol] = yield ReadRequest(ReadOperation.SYMBOL, {"symbol": symbol},
                                                      request_id=f"symbol:{len(symbol_specs)}")
        info = symbol_specs[symbol]
        result[ticket] = {
            "symbol": symbol, "entry": float(position.price_open), "volume": float(position.volume),
            "sl": float(getattr(position, "sl", 0.0) or 0.0),
            "tp": float(getattr(position, "tp", 0.0) or 0.0),
            "digits": int(getattr(info, "digits", 2) if info else 2),
            "point": float(getattr(info, "point", .01) if info else .01)}
    return result


def basket_stop_plan_reads(direction, tickets, loss_budget, *, default_symbol):
    specs = yield from open_position_specs_reads(tickets, default_symbol=default_symbol)
    if specs is None:
        return {"positions_complete": False, "positions": None, "stop_price": None}
    level = None
    if specs:
        level = yield from basket_loss_stop_reads(direction, list(specs.values()), loss_budget,
                                                 default_symbol=default_symbol)
    return {"positions_complete": True, "positions": specs, "stop_price": level}


def basket_loss_stop_reads(direction, positions, loss_budget, symbol=None, *,
                           default_symbol, buy_action=0, sell_action=1):
    direction = str(direction).upper()
    if direction not in {"BUY", "SELL"}:
        raise ValueError("direction must be BUY or SELL")
    loss_budget = float(loss_budget)
    if not math.isfinite(loss_budget) or loss_budget <= 0:
        raise ValueError("loss budget must be positive")
    rows = [dict(row) for row in positions]
    if not rows:
        raise ValueError("positions must not be empty")
    symbols = {str(row.get("symbol") or symbol or default_symbol) for row in rows}
    if len(symbols) != 1:
        raise ValueError("all basket positions must use the same symbol")
    basket_symbol = symbols.pop()
    if symbol is not None and basket_symbol != str(symbol):
        raise ValueError("all basket positions must use the same symbol")
    parsed = []
    for row in rows:
        volume = float(row.get("volume") or 0.0)
        entry = float(row.get("entry") or 0.0)
        if any(not math.isfinite(value) or value <= 0 for value in (volume, entry)):
            raise ValueError("basket entries and volumes must be positive")
        parsed.append((volume, entry))
    info = yield ReadRequest(ReadOperation.SYMBOL, {"symbol": basket_symbol}, request_id="symbol")
    if info is None:
        return None
    point = float(getattr(info, "point", 0.0) or 0.0)
    digits = int(getattr(info, "digits", 2) or 2)
    if not math.isfinite(point) or point <= 0:
        return None
    action = buy_action if direction == "BUY" else sell_action
    sequence = 0

    def projected(price):
        nonlocal sequence
        total = 0.0
        for volume, entry in parsed:
            sequence += 1
            value = yield ReadRequest(ReadOperation.PROFIT, {
                "action": action, "symbol": basket_symbol, "volume": volume,
                "price_open": entry, "price_close": float(price)}, request_id=f"profit:{sequence}")
            if value is None:
                return None
            value = float(value)
            if not math.isfinite(value):
                return None
            total += value
        return total

    total_volume = sum(volume for volume, _ in parsed)
    safe = sum(volume * entry for volume, entry in parsed) / total_volume
    safe_pl = yield from projected(safe)
    target = -loss_budget
    if safe_pl is None or safe_pl < target:
        return None
    step = max(1.0, point)
    adverse = safe - step if direction == "BUY" else safe + step
    adverse_pl = yield from projected(adverse)
    for _ in range(64):
        if adverse_pl is None:
            return None
        if adverse_pl <= target:
            break
        step *= 2.0
        adverse = safe - step if direction == "BUY" else safe + step
        if adverse <= point:
            adverse = point
        adverse_pl = yield from projected(adverse)
    else:
        return None
    if adverse_pl is None or adverse_pl > target:
        return None
    for _ in range(80):
        midpoint = (safe + adverse) / 2.0
        value = yield from projected(midpoint)
        if value is None:
            return None
        if value < target:
            adverse = midpoint
        else:
            safe = midpoint
    units = safe / point
    safe_units = math.ceil(units - 1e-10) if direction == "BUY" else math.floor(units + 1e-10)
    result = round(safe_units * point, digits)
    result_pl = yield from projected(result)
    if result_pl is None:
        return None
    while result_pl < target - 1e-8:
        result = round(result + point if direction == "BUY" else result - point, digits)
        result_pl = yield from projected(result)
        if result_pl is None or result <= 0:
            return None
    return result
