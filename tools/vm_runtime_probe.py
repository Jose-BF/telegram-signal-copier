"""Read-only MT5 connectivity probe for an unattended Windows task session."""
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

import MetaTrader5 as mt5


def main():
    connected = mt5.initialize(timeout=15000)
    terminal = mt5.terminal_info() if connected else None
    account = mt5.account_info() if connected else None
    positions = mt5.positions_get() if connected else None
    orders = mt5.orders_get() if connected else None
    result = dict(
        utc=datetime.now(timezone.utc).isoformat(),
        initialized=connected,
        connected=getattr(terminal, "connected", False),
        trade_allowed=getattr(terminal, "trade_allowed", False),
        account_available=account is not None,
        positions=None if positions is None else len(positions),
        orders=None if orders is None else len(orders),
    )
    mt5.shutdown()
    Path(sys.argv[1]).write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
