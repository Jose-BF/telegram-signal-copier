"""Pure transport arbitration shared by runtime and offline scheduling controls."""

POLICY_ID = "management-first-read-burst4-reserve1-v1"
MAX_CONSECUTIVE_TRADES = 4
TRANSPORT_KINDS = ("trade", "management", "read")


def admission_available(pending: int, capacity: int, *, management: bool) -> bool:
    return pending < capacity + int(management)


def next_transport_kind(waiters, consecutive_trades: int, max_trades: int = MAX_CONSECUTIVE_TRADES):
    """Choose a class, not a request: asyncio does not promise FIFO within it."""
    management, entries, reads = (waiters[key] for key in ("management", "trade", "read"))
    if reads and (not (management or entries) or consecutive_trades >= max_trades):
        return "read"
    if management:
        return "management"
    if entries:
        return "trade"
    return None
