"""Unrounded linear contract hypothesis, not certified native MT5 valuation."""

from decimal import Decimal
import math


def linear_profit_value(path, params, index):
    raw = (Decimal(1 if params["action"] == 0 else -1)
           * (Decimal(str(params["price_close"])) - Decimal(str(params["price_open"])))
           * Decimal(str(path.contract_size)) * Decimal(str(params["volume"])))
    orientation = path.conversion_orientation
    if orientation != "identity":
        if not bool(path.fx_valid[index]):
            return None
        if orientation == "account_base_profit_quote":
            quote = path.fx_ask[index] if raw >= 0 else path.fx_bid[index]
        elif orientation == "profit_base_account_quote":
            quote = path.fx_bid[index] if raw >= 0 else path.fx_ask[index]
        else:
            return None
        if not math.isfinite(float(quote)) or quote <= 0:
            return None
        raw = (raw / Decimal(str(quote)) if orientation == "account_base_profit_quote"
               else raw * Decimal(str(quote)))
    value = float(raw)
    return value if math.isfinite(value) else None
