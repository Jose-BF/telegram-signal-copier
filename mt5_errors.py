"""
Clasificación de códigos de error de MT5 y manejo de stops_level del broker.
"""

import MetaTrader5 as mt5
import config

# Códigos que justifican reintentar inmediatamente
TRANSIENT = {
    10004,  # TRADE_RETCODE_REQUOTE           — requote
    10008,  # TRADE_RETCODE_PLACED            — placed pero no ejecutado aún
    10021,  # TRADE_RETCODE_PRICE_OFF         — precio desactualizado
    10018,  # TRADE_RETCODE_MARKET_CLOSED     — mercado cerrado (puede reabrir)
    10027,  # TRADE_RETCODE_CLIENT_DISABLES_AT — algoritmo desactivado temporalmente
}

# Códigos por precio/stops — encolar para reintento en cada tick
STOPS_RELATED = {
    10016,  # TRADE_RETCODE_INVALID_STOPS     — SL/TP inválidos (muy cerca del precio)
    10017,  # TRADE_RETCODE_TRADE_DISABLED    — negociación desactivada
    10015,  # TRADE_RETCODE_INVALID_PRICE     — precio inválido
}

# Códigos que indican que la posición ya no existe
POSITION_GONE = {
    10036,  # TRADE_RETCODE_POSITION_CLOSED    - posicion ya cerrada
    10013,  # TRADE_RETCODE_INVALID            — operación inválida (pos. cerrada)
    10011,  # TRADE_RETCODE_ERROR              — error general (a veces pos. cerrada)
}

# Códigos permanentes — no reintentar
PERMANENT = {
    10019,  # TRADE_RETCODE_NO_MONEY           — sin margen
    10014,  # TRADE_RETCODE_INVALID_VOLUME     — lote inválido
    10020,  # TRADE_RETCODE_PRICE_CHANGED      — (a veces reintentar)
    10030,  # TRADE_RETCODE_INVALID_FILL       — llenado inválido
}


def classify(retcode: int) -> str:
    if retcode == mt5.TRADE_RETCODE_DONE:
        return "OK"
    # 10025 = TRADE_RETCODE_NO_CHANGES — la posición ya tenía esos valores exactos.
    # No es un fallo: el objetivo estaba logrado. Tratar como OK para no loguear
    # como mt5_action_failed en pending_actions y no contaminar execution quality.
    if retcode == 10025:
        return "OK"
    if retcode in STOPS_RELATED:
        return "STOPS"
    if retcode in TRANSIENT:
        return "TRANSIENT"
    if retcode in POSITION_GONE:
        return "POSITION_GONE"
    if retcode in PERMANENT:
        return "PERMANENT"
    return "UNKNOWN"


def get_stops_level_pts() -> float:
    """Devuelve la distancia mínima entre precio actual y SL/TP, en dólares de XAUUSD."""
    info = mt5.symbol_info(config.MT5_SYMBOL)
    if not info:
        return 0.0
    # stops_level viene en puntos del símbolo. Para XAUUSD un punto = 0.01
    return info.trade_stops_level * info.point


def adjust_sl_to_legal(direction: str, desired_sl: float) -> float | None:
    """
    Si el SL deseado está dentro del stops_level, lo ajusta al mínimo legal.
    Devuelve None si no se puede obtener tick.
    """
    tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
    if not tick:
        return None
    min_dist = get_stops_level_pts()
    if direction == "BUY":
        # Para BUY, SL < bid. Máximo legal = bid - min_dist
        max_legal = tick.bid - min_dist
        return min(desired_sl, max_legal)
    else:
        # Para SELL, SL > ask. Mínimo legal = ask + min_dist
        min_legal = tick.ask + min_dist
        return max(desired_sl, min_legal)
