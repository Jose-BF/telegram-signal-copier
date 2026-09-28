"""Sequential basket reads shared by the monitor and causal offline drivers.

Each yield is a real protocol operation. No clock, transport, broker access or
policy decision is hidden here; the caller supplies one completed response.
"""

from mt5_read_protocol import ReadOperation, ReadRequest


def drive_reads(flow, read):
    response = None
    try:
        while True:
            try:
                request = flow.send(response)
            except StopIteration as complete:
                return complete.value
            response = read(request)
    finally:
        flow.close()


def floating_summary_reads(symbol, direction, filled_tickets):
    summary = {"pl": 0.0, "n_open": 0, "avg_entry": None, "current_price": None,
               "lots_total": 0.0, "open_tickets": [], "positions_complete": True}
    tick = yield ReadRequest(ReadOperation.TICK, {"symbol": symbol}, request_id="tick")
    if tick:
        summary["current_price"] = tick.bid if direction == "BUY" else tick.ask
    positions = yield ReadRequest(ReadOperation.POSITIONS, request_id="positions")
    if positions is None:
        summary["positions_complete"] = False
        return summary
    # The live monitor selects known fills after positions_get returns.
    wanted = set(int(ticket) for ticket in filled_tickets())
    weighted_entry = 0.0
    for position in positions:
        if int(position.ticket) not in wanted:
            continue
        summary["pl"] += position.profit
        summary["n_open"] += 1
        summary["open_tickets"].append(int(position.ticket))
        summary["lots_total"] += position.volume
        weighted_entry += position.price_open * position.volume
    if summary["lots_total"] > 0:
        summary["avg_entry"] = weighted_entry / summary["lots_total"]
    return summary


def confirmed_realized_profit(deals, *, entry_in=0, exit_entries=(1, 3)):
    if deals is None or len(deals) < 2:
        return None
    opened_volume = sum(float(getattr(deal, "volume", 0.0) or 0.0)
                        for deal in deals if getattr(deal, "entry", None) == entry_in)
    closed_volume = sum(float(getattr(deal, "volume", 0.0) or 0.0)
                        for deal in deals if getattr(deal, "entry", None) in exit_entries)
    if opened_volume <= 0.0 or closed_volume + 1e-9 < opened_volume:
        return None
    return sum(float(getattr(deal, field, 0.0) or 0.0)
               for deal in deals for field in ("profit", "commission", "swap", "fee"))


def merge_known_tickets(previous, filled):
    known, seen = [], set()
    for value in list(previous) + list(filled):
        ticket = int(value)
        if ticket not in seen:
            known.append(ticket)
            seen.add(ticket)
    return known


def realized_summary_reads(summary, known_tickets, cache, *, on_confirmed=None,
                           entry_in=0, exit_entries=(1, 3)):
    floating = float(summary.get("pl") or 0.0)
    summary["floating_pl"] = floating
    open_tickets = set(int(ticket) for ticket in summary.get("open_tickets") or [])
    missing = []
    if summary.get("positions_complete", True):
        for ticket in known_tickets:
            if ticket in open_tickets or ticket in cache or str(ticket) in cache:
                continue
            deals = yield ReadRequest(ReadOperation.DEALS_POSITION, {"position": ticket},
                                      request_id=f"history:{ticket}")
            realized = confirmed_realized_profit(deals, entry_in=entry_in, exit_entries=exit_entries)
            if realized is None:
                missing.append(ticket)
                continue
            cache[ticket] = float(realized)
            if on_confirmed is not None:
                on_confirmed(ticket, float(realized))
    else:
        missing = [ticket for ticket in known_tickets if ticket not in open_tickets]
    realized = sum(float(value) for value in cache.values())
    complete = bool(summary.get("positions_complete", True) and not missing)
    summary.update(realized_pl=realized, realized_complete=complete,
                   missing_realized_tickets=missing,
                   total_pl=floating + realized if complete else None)
    return summary


def basket_summary_reads(symbol, direction, filled_tickets, known_tickets, cache, *,
                         on_known=None, on_confirmed=None, entry_in=0, exit_entries=(1, 3)):
    summary = yield from floating_summary_reads(symbol, direction, filled_tickets)
    known = merge_known_tickets(known_tickets(), filled_tickets())
    if on_known is not None:
        on_known(known)
    return (yield from realized_summary_reads(summary, known, cache, on_confirmed=on_confirmed,
                                             entry_in=entry_in, exit_entries=exit_entries))
