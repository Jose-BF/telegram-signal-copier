"""
mt5_tick_simulator.py — Simulador tick a tick FIEL a MT5 Strategy Tester.

Reglas exactas (las mismas que MT5 al backtest con "Every tick based on real ticks"):

  Aperturas:
    Market BUY  fill = ask del PRIMER tick ≥ entry_time
    Market SELL fill = bid del PRIMER tick ≥ entry_time
    BUY  Limit @ P fills cuando ask ≤ P (a P, no al ask)
    SELL Limit @ P fills cuando bid ≥ P (a P, no al bid)

  Cierres:
    BUY  position: SL cuando bid ≤ SL ; TP cuando bid ≥ TP
    SELL position: SL cuando ask ≥ SL ; TP cuando ask ≤ TP
    Time stop: market close (BUY→bid, SELL→ask)

  BE trigger: cuando precio toca TP_be (SELL→bid ≤ TP_be; BUY→ask ≥ TP_be)
              → SL de cada pos abierta = su propio entry

  Safety check (primer tick + N segundos tras market fill):
    Si entry de market está fuera del rango → cierra market a precio del momento
    + cancela todos los limits.

  P/L por posición = (close - open) * contract_size * lots * sign
                     (sign = +1 BUY, -1 SELL)
  Sin slippage artificial: bid/ask reales de cada tick
  Sin comisión asumida (Vantage Standard STP = 0 commission, está en el spread)

Caso prueba: canal 1 SELL 12:24:24 UTC 2026-04-22 range 4750-4755
"""

import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: pip install MetaTrader5"); sys.exit(1)

DATA_DIR = Path(__file__).parent / "data"
TICKS_CACHE = DATA_DIR / "ticks_cache"
TICKS_CACHE.mkdir(exist_ok=True, parents=True)

SYMBOL = "XAUUSD"
LOT = 0.01

# ── Modelo ────────────────────────────────────────────────────────────────────

@dataclass
class Strategy:
    entry_mode: str            # "market_only" | "extremes" | "intra_dca"
    n_entries: int             # solo aplica a intra_dca
    target_tp_index: int       # 0-based
    be_at_tp_index: int        # -1 = sin BE
    time_stop_min: int         # 0 = sin time stop
    horizon_min: int = 240     # cuánto tiempo seguir ticks
    safety_delay_sec: int = 2  # segundos tras fill market para chequear safety
    # Safety modes (legacy: se aplican cuando range_delay_sec == 0):
    #   "cancel"        — si entry market fuera del rango, cierra TODO.
    #   "tolerance"     — acepta entry hasta `safety_tolerance` $ fuera del rango.
    #   "limit_fallback"— cierra market pero mantiene limits dentro del rango.
    #   "limit_first"   — NO abre market. Solo limits desde el inicio.
    #   "smart"         — asimétrico: hold si favorable, cancel si adverso.
    safety_mode: str = "cancel"
    safety_tolerance: float = 0.0  # solo aplica a "tolerance" (en $ XAUUSD)

    # ── Layered logic (range_delay_sec > 0) ─────────────────────────────────
    # Modela el delay real entre el disparo del market (sticker C1 / "BUY NOW"
    # C2) y la llegada del rango/TPs/SL. Durante esa ventana la posición vive
    # SIN SL/TPs ni limits — sólo procesando ticks.
    range_delay_sec: int = 0

    # Acción cuando llega el rango y el precio actual está FUERA EN CONTRA
    # del rango (caso C). Solo aplica si range_delay_sec > 0.
    #   "close"             — cerrar el market, no abrir limits (= cancel).
    #   "hold_with_limits"  — mantener market + abrir limits del rango (DCA).
    #   "hold_no_limits"    — mantener market sin limits (SL del proveedor).
    #   "hold_sl_to_extreme"— mantener market con SL movido al extremo del
    #                          rango más cercano al precio actual (cap loss).
    adverse_action: str = "close"


@dataclass
class Position:
    pos_id: int
    kind: str                  # "market" | "limit"
    direction: str             # "BUY" | "SELL"
    state: str                 # "pending" | "open" | "closed" | "cancelled"
    sl: Optional[float] = None     # None durante pre-range window (layered)
    tp: Optional[float] = None     # idem
    limit_price: Optional[float] = None
    open_time: Optional[datetime] = None
    open_price: Optional[float] = None
    close_time: Optional[datetime] = None
    close_price: Optional[float] = None
    close_reason: str = ""

    def pl(self, contract_size: float, lot: float) -> float:
        if self.open_price is None or self.close_price is None:
            return 0.0
        delta = (self.close_price - self.open_price) if self.direction == "BUY" \
                else (self.open_price - self.close_price)
        return delta * contract_size * lot


# ── MT5 helpers ───────────────────────────────────────────────────────────────

class MT5Session:
    def __init__(self, symbol=SYMBOL):
        if not mt5.initialize():
            raise RuntimeError(f"MT5 init: {mt5.last_error()}")
        self.symbol = symbol
        si = mt5.symbol_info(symbol)
        if si is None:
            raise RuntimeError(f"Symbol {symbol} no encontrado")
        if not si.visible:
            mt5.symbol_select(symbol, True)
            si = mt5.symbol_info(symbol)
        self.symbol_info = si
        self.contract_size = si.trade_contract_size
        # Offset server-UTC
        cur = mt5.symbol_info_tick(symbol)
        srv_dt = datetime.fromtimestamp(cur.time, tz=timezone.utc)
        utc_now = datetime.now(timezone.utc)
        self.offset_h = round((srv_dt - utc_now).total_seconds() / 3600)
        print(f"[MT5] symbol={symbol} digits={si.digits} contract={si.trade_contract_size} "
              f"server_offset=UTC+{self.offset_h}h")

    def shutdown(self):
        mt5.shutdown()

    def fetch_ticks(self, t_from_utc: datetime, t_to_utc: datetime) -> pd.DataFrame:
        t_from_srv = t_from_utc + timedelta(hours=self.offset_h)
        t_to_srv   = t_to_utc   + timedelta(hours=self.offset_h)
        raw = mt5.copy_ticks_range(self.symbol, t_from_srv, t_to_srv, mt5.COPY_TICKS_ALL)
        if raw is None or len(raw) == 0:
            return pd.DataFrame()
        df = pd.DataFrame(raw)
        # time_msc viene en "server epoch" (server time tratado como si fuera UTC)
        df["time_utc"] = (pd.to_datetime(df["time_msc"], unit="ms", utc=True)
                          - pd.Timedelta(hours=self.offset_h))
        return df.sort_values("time_msc").reset_index(drop=True)

    def get_or_cache_ticks(self, sig_id: int, t_from_utc: datetime,
                            t_to_utc: datetime) -> pd.DataFrame:
        cache_file = TICKS_CACHE / f"sig_{sig_id}.parquet"
        if cache_file.exists():
            df = pd.read_parquet(cache_file)
            df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True)
            return df
        df = self.fetch_ticks(t_from_utc, t_to_utc)
        if len(df) > 0:
            df.to_parquet(cache_file, index=False)
        return df


# ── Validación SL (real MT5 rechaza SL del lado equivocado del entry) ────────

def _sl_is_valid(direction: str, entry_price: float, sl: float) -> bool:
    """MT5 rechaza SL ABOVE entry para BUY y BELOW entry para SELL.

    Ocurre típicamente en layered mode cuando el market se abre lejos del
    rango y la SL del proveedor (calculada para entry-en-rango) queda al lado
    equivocado del entry real.
    """
    if direction == "BUY":
        return sl < entry_price
    else:  # SELL
        return sl > entry_price


# ── Construcción de posiciones según estrategia ───────────────────────────────

def _tp_for_position(tps: list, position_index: int, target_tp_index: int,
                     max_tp_index: Optional[int] = None) -> float:
    """Devuelve el TP que cierra la posicion `position_index`.

    Replica state.Signal.tp_for_position del bot real:
      - target_tp_index >= 0: TODAS las posiciones cierran al MISMO TP fijo.
      - target_tp_index == -1 (ESCALONADO, default real del bot):
          posicion 0 (market) → tps[0] = TP1
          posicion 1 (DCA1)   → tps[1] = TP2
          posicion 2 (DCA2)   → tps[2] = TP3
          ... etc
        Con cap opcional via max_tp_index (post-SL momentum).
    """
    if target_tp_index >= 0:
        # Modo TP fijo
        idx = min(target_tp_index, len(tps) - 1)
        return tps[idx]

    # Modo escalonado
    idx = position_index
    if max_tp_index is not None:
        idx = min(idx, max_tp_index)
    if idx >= len(tps):
        return tps[-1]
    return tps[idx]


def build_positions(sig: dict, strat: Strategy,
                    market_fill_price: Optional[float]) -> list[Position]:
    """Construye posiciones según entry_mode + safety_mode.

    TPs por posicion:
      - target_tp_index >= 0: TODAS al mismo TP fijo (sweep "TP1 fijo", etc).
      - target_tp_index == -1: ESCALONADO (replica bot real).

    Si safety_mode == "limit_first" → NO crea pos market. Solo limits.
    En cualquier otro modo → pos 0 = market (al precio market_fill_price).
    """
    direction = sig["direction"]
    rng = sig.get("range")
    tps = sig["tps"]
    sl = sig["sl"]
    max_tp = getattr(strat, "max_tp_index", None)

    def tp_at(pos_idx):
        return _tp_for_position(tps, pos_idx, strat.target_tp_index, max_tp)

    positions: list[Position] = []
    pid = 0
    is_limit_first = (strat.safety_mode == "limit_first")

    # Pos 0 = market (excepto en limit_first)
    if not is_limit_first:
        if market_fill_price is None:
            return []  # safeguard: no se puede sin precio
        positions.append(Position(
            pos_id=pid, kind="market", direction=direction, state="open",
            sl=sl, tp=tp_at(0), limit_price=None,
            open_price=market_fill_price,
        ))
        pid += 1

    # Limits según modo
    if strat.entry_mode == "extremes" and rng is not None:
        if is_limit_first:
            # 2 limits: uno en cada extremo del rango
            for px in [rng[0], rng[1]]:
                positions.append(Position(
                    pos_id=pid, kind="limit", direction=direction, state="pending",
                    sl=sl, tp=tp_at(pid), limit_price=round(px, 2),
                ))
                pid += 1
        else:
            far = rng[0] if direction == "BUY" else rng[1]
            adverse = (direction == "BUY" and far < market_fill_price) or \
                      (direction == "SELL" and far > market_fill_price)
            if adverse:
                positions.append(Position(
                    pos_id=pid, kind="limit", direction=direction, state="pending",
                    sl=sl, tp=tp_at(pid), limit_price=far,
                ))
                pid += 1

    elif strat.entry_mode == "intra_dca" and rng is not None:
        n = max(2, strat.n_entries)
        step = (rng[1] - rng[0]) / (n - 1)
        prices = [rng[1] - i*step if direction == "BUY" else rng[0] + i*step
                  for i in range(n)]
        if is_limit_first:
            # TODOS los precios como limits (incluyendo el que sería el market)
            for p in prices:
                positions.append(Position(
                    pos_id=pid, kind="limit", direction=direction, state="pending",
                    sl=sl, tp=tp_at(pid), limit_price=round(p, 2),
                ))
                pid += 1
        else:
            # Excluir el primero (cubierto por market). Filtrar adversos.
            for p in prices[1:]:
                adverse = (direction == "BUY" and p < market_fill_price) or \
                          (direction == "SELL" and p > market_fill_price)
                if adverse:
                    positions.append(Position(
                        pos_id=pid, kind="limit", direction=direction, state="pending",
                        sl=sl, tp=tp_at(pid), limit_price=round(p, 2),
                    ))
                    pid += 1

    return positions


# ── Simulación tick a tick ────────────────────────────────────────────────────

def simulate(sig: dict, strat: Strategy, ticks: pd.DataFrame,
             contract_size: float, verbose: bool = False,
             force_fill_price: Optional[float] = None,
             mgmt_events: Optional[list] = None) -> dict:
    """Devuelve dict con posiciones, eventos, P/L total y status.

    `force_fill_price`: si se pasa, el simulador usa ese precio como fill del
    market en vez de calcularlo desde el primer tick (bid/ask). Usado en
    CALIBRACION para reproducir exactamente el fill_price real del bot.

    `mgmt_events`: lista de dicts [{ts, action, price?}] con los mensajes de
    gestion del canal del journal. El simulador los aplica en el loop tick-a-
    tick cuando llega su ts. Acciones soportadas:
      - MOVE_SL_TO_BE      → SL = entry_price de cada pos abierta
      - MOVE_SL_TO_PRICE   → SL = price (con validacion lado correcto)
      - CLOSE_ALL          → cerrar todas las pos abiertas + cancelar pending
      - CLOSE_FIRST_ENTRY  → cerrar solo la pos market original
    Sin este parametro el simulador no aplica gestion del canal — los trades
    real-CON-mgmt divergiran significativamente del P/L real.
    """
    if len(ticks) == 0:
        return {"status": "NO_TICKS", "positions": [], "pl": 0.0, "events": []}

    sig_dt = sig["dt"]
    if sig_dt.tzinfo is None:
        sig_dt = sig_dt.replace(tzinfo=timezone.utc)
    direction = sig["direction"]
    rng = sig.get("range")
    is_limit_first = (strat.safety_mode == "limit_first")
    use_layered = (strat.range_delay_sec > 0)

    events = []  # (time, level, msg)
    def log(t, lvl, msg):
        events.append((t, lvl, msg))
        if verbose:
            print(f"  [{t}] {lvl:>5} | {msg}")

    # Variables de cabecera del layered path (se quedan en None en path normal)
    pre_range_outcome = None
    range_arrival_decision = None

    # 1. Localizar primer tick ≥ sig_dt
    after = ticks[ticks["time_utc"] >= sig_dt]
    if len(after) == 0:
        return {"status": "NO_FILL_TICK", "positions": [], "pl": 0.0, "events": events,
                "entry_distance": None, "would_market_fill": None}
    fill_tick = after.iloc[0]
    # Si hay force_fill_price (calibracion), usarlo. Si no, usar bid/ask real.
    if force_fill_price is not None:
        would_fill_price = float(force_fill_price)
    else:
        would_fill_price = (fill_tick["bid"] if direction == "SELL"
                            else fill_tick["ask"])

    # iteration_start_time: desde dónde el _iterate_ticks empieza a procesar
    # ticks. Default = fill_tick. El layered lo sobrescribe a range_arrival_t.
    iteration_start_time = fill_tick["time_utc"]

    # Métrica diagnóstica: distancia del would-be entry al rango
    entry_distance = 0.0
    if rng is not None:
        if would_fill_price < rng[0]:
            entry_distance = -(rng[0] - would_fill_price)  # negativo = abajo del rango
        elif would_fill_price > rng[1]:
            entry_distance = (would_fill_price - rng[1])    # positivo = arriba del rango

    # 1b. Modo limit_first: NO abrir market. Saltar al loop de ticks.
    if is_limit_first:
        if rng is None:
            # Sin rango no hay limits que poner → no opera
            return {"status": "NO_RANGE_FOR_LIMITS", "positions": [], "pl": 0.0,
                    "events": events, "entry_distance": None,
                    "would_market_fill": would_fill_price}
        positions = build_positions(sig, strat, market_fill_price=None)
        log(fill_tick["time_utc"], "INIT",
            f"LIMIT_FIRST: {len(positions)} limits "
            f"({[p.limit_price for p in positions]}) — sin market, "
            f"would-be market @ {would_fill_price:.2f} (rng={rng})")
        market_pos = None  # marker

    elif use_layered:
        # ── LAYERED LOGIC ──────────────────────────────────────────────────
        # Modela el delay entre disparo del market (sticker C1 / "BUY NOW" C2)
        # y la llegada del rango/TPs/SL. Durante esa ventana la posición vive
        # SIN SL/TPs ni limits.
        fill_price = would_fill_price
        fill_time = fill_tick["time_utc"]
        log(fill_time, "FILL",
            f"MARKET {direction} @ {fill_price:.2f} (sin SL/TP — esperando rango "
            f"+{strat.range_delay_sec}s)")

        # Crear sólo la posición market, sin SL/TP/limits aún
        market_pos = Position(
            pos_id=0, kind="market", direction=direction, state="open",
            sl=None, tp=None, limit_price=None,
            open_price=fill_price, open_time=fill_time,
        )
        positions = [market_pos]

        # Pre-range window: track outcomes pero sin SL/TP placement
        range_arrival_t = fill_time + timedelta(seconds=strat.range_delay_sec)
        pre_range_outcome = "none"
        pre_range_max_tp = 0  # índice del TP más alto tocado durante la ventana

        tps = sig["tps"]
        sl_provider = sig["sl"]
        # En escalonado el "target" del market es tps[0] (TP1).
        # En modo TP fijo es tps[target_tp_index].
        target_tp = _tp_for_position(tps, 0, strat.target_tp_index,
                                     getattr(strat, "max_tp_index", None))

        pre_window = ticks[(ticks["time_utc"] >= fill_time) &
                           (ticks["time_utc"] <= range_arrival_t)]
        if len(pre_window) > 0:
            pre_bids = pre_window["bid"].to_numpy()
            pre_asks = pre_window["ask"].to_numpy()
            # Comprueba el TP más alto tocado de forma vectorizada
            for i, tp in enumerate(tps):
                if direction == "BUY":
                    if (pre_bids >= tp).any() and (i + 1) > pre_range_max_tp:
                        pre_range_max_tp = i + 1
                else:
                    if (pre_asks <= tp).any() and (i + 1) > pre_range_max_tp:
                        pre_range_max_tp = i + 1
            sl_hit_arr = ((pre_bids <= sl_provider) if direction == "BUY"
                          else (pre_asks >= sl_provider))
            if sl_hit_arr.any() and pre_range_outcome == "none":
                pre_range_outcome = "sl_pre_range"

        if pre_range_max_tp > 0:
            # TP touch trumps SL touch en el outcome string
            pre_range_outcome = f"tp{pre_range_max_tp}_pre_range"

        # Range arrival
        arrival_ticks = ticks[ticks["time_utc"] >= range_arrival_t]
        if len(arrival_ticks) == 0:
            # Horizon antes que rango: cerrar al último tick disponible
            last = ticks.iloc[-1]
            close_p = last["ask"] if direction == "SELL" else last["bid"]
            market_pos.close_price = close_p
            market_pos.close_time = last["time_utc"]
            market_pos.close_reason = "EOH_PRE"
            market_pos.state = "closed"
            log(last["time_utc"], "EOH_PRE",
                f"horizon antes que rango → cierra @ {close_p:.2f}")
            return _finalize(positions, events, contract_size, "EOH_PRE_RANGE",
                             entry_distance=entry_distance,
                             would_market_fill=would_fill_price,
                             pre_range_outcome=pre_range_outcome,
                             range_arrival_decision="n/a",
                             final_outcome="EOH_PRE")

        arrival_tick = arrival_ticks.iloc[0]
        # Precio "actual" para evaluar dónde quedó la posición:
        # BUY se cierra al bid → bid mide su valor actual.
        # SELL se cierra al ask → ask mide su valor actual.
        arrival_close_p = (arrival_tick["bid"] if direction == "BUY"
                           else arrival_tick["ask"])

        # Determinar caso A/B/C
        if rng is None:
            case = "no_range"
        elif rng[0] <= arrival_close_p <= rng[1]:
            case = "A_inside"
        else:
            if direction == "BUY":
                favorable = arrival_close_p > rng[1]
            else:
                favorable = arrival_close_p < rng[0]
            case = "B_favorable" if favorable else "C_adverse"

        range_arrival_decision = case
        log(arrival_tick["time_utc"], "RANGE",
            f"@ {arrival_close_p:.2f} → caso {case} "
            f"(rng={rng}, pre={pre_range_outcome})")

        # Helper local para asignar SL validando (lo rechaza si está al lado
        # equivocado del entry — MT5 lo haría también).
        def _set_sl(pos: Position, requested_sl: float, label: str):
            if _sl_is_valid(pos.direction, pos.open_price, requested_sl):
                pos.sl = requested_sl
            else:
                pos.sl = None
                log(arrival_tick["time_utc"], "SL_INV",
                    f"{label} {requested_sl} inválido para {pos.direction}@"
                    f"{pos.open_price:.2f} → sin SL (corre hasta TP/horizon)")

        # Aplicar lógica según caso
        if case in ("A_inside", "B_favorable", "no_range"):
            # Dar SL/TP reales al market y añadir limits según entry_mode
            _set_sl(market_pos, sl_provider, "SL provider")
            market_pos.tp = target_tp
            all_pos = build_positions(sig, strat, fill_price)
            for p in all_pos:
                if p.kind == "limit":
                    p.pos_id = len(positions)
                    positions.append(p)
            log(arrival_tick["time_utc"], "INIT",
                f"caso {case}: SL={market_pos.sl} TP={target_tp}, "
                f"+{sum(1 for p in positions if p.kind=='limit')} limits")

        elif case == "C_adverse":
            if strat.adverse_action == "close":
                close_p = (arrival_tick["ask"] if direction == "SELL"
                           else arrival_tick["bid"])
                market_pos.state = "closed"
                market_pos.close_price = close_p
                market_pos.close_time = arrival_tick["time_utc"]
                market_pos.close_reason = "C_CLOSE"
                log(arrival_tick["time_utc"], "ADV",
                    f"caso C → close @ {close_p:.2f}")
                return _finalize(positions, events, contract_size, "C_ADV_CLOSE",
                                 entry_distance=entry_distance,
                                 would_market_fill=would_fill_price,
                                 pre_range_outcome=pre_range_outcome,
                                 range_arrival_decision=case,
                                 final_outcome="C_CLOSE")
            elif strat.adverse_action == "hold_with_limits":
                _set_sl(market_pos, sl_provider, "SL provider")
                market_pos.tp = target_tp
                all_pos = build_positions(sig, strat, fill_price)
                for p in all_pos:
                    if p.kind == "limit":
                        p.pos_id = len(positions)
                        positions.append(p)
                log(arrival_tick["time_utc"], "ADV",
                    f"caso C → hold + {sum(1 for p in positions if p.kind=='limit')} "
                    f"limits, SL={market_pos.sl} TP={target_tp}")
            elif strat.adverse_action == "hold_no_limits":
                _set_sl(market_pos, sl_provider, "SL provider")
                market_pos.tp = target_tp
                log(arrival_tick["time_utc"], "ADV",
                    f"caso C → hold sin limits, SL={market_pos.sl} TP={target_tp}")
            elif strat.adverse_action == "hold_sl_to_extreme":
                # SL al extremo del rango más cercano al precio actual
                extreme_sl = rng[0] if direction == "BUY" else rng[1]
                _set_sl(market_pos, extreme_sl, f"SL extremo {extreme_sl}")
                market_pos.tp = target_tp
                log(arrival_tick["time_utc"], "ADV",
                    f"caso C → hold SL extremo intentado={extreme_sl} (provider={sl_provider}), "
                    f"final SL={market_pos.sl}, TP={target_tp}")
            elif strat.adverse_action == "rescue_market":
                # Mantener el market original CON SL/TP normal (escalonado).
                _set_sl(market_pos, sl_provider, "SL provider")
                market_pos.tp = target_tp  # tps[0] en escalonado
                # Abrir UNA NUEVA pos market al precio actual ("entrada optima
                # de rescate"). TP del rescue = ULTIMO TP (max recorrido).
                # SL comun (provider) si es valido para el rescue entry.
                rescue_open = (arrival_tick["ask"] if direction == "BUY"
                               else arrival_tick["bid"])
                rescue_sl = (sl_provider
                             if _sl_is_valid(direction, rescue_open, sl_provider)
                             else None)
                rescue_pos = Position(
                    pos_id=len(positions),
                    kind="market",
                    direction=direction,
                    state="open",
                    sl=rescue_sl,
                    tp=tps[-1],
                    open_price=rescue_open,
                    open_time=arrival_tick["time_utc"],
                )
                positions.append(rescue_pos)
                log(arrival_tick["time_utc"], "ADV",
                    f"caso C → rescue_market #{rescue_pos.pos_id} "
                    f"@ {rescue_open:.2f}, TP={rescue_pos.tp}, "
                    f"SL={rescue_pos.sl}")
            else:
                raise ValueError(f"adverse_action desconocido: {strat.adverse_action}")

        # Iteración empezará desde range_arrival_t (no desde fill_time)
        iteration_start_time = range_arrival_t

    else:
        # 2. Market fill normal
        fill_price = would_fill_price
        log(fill_tick["time_utc"], "FILL",
            f"MARKET {direction} @ {fill_price:.2f} (bid={fill_tick['bid']:.2f} "
            f"ask={fill_tick['ask']:.2f} spread=${fill_tick['ask']-fill_tick['bid']:.2f})")
        positions = build_positions(sig, strat, fill_price)
        market_pos = positions[0]
        market_pos.open_time = fill_tick["time_utc"]
        log(fill_tick["time_utc"], "INIT",
            f"{len(positions)} posiciones: market@{fill_price:.2f}, "
            f"{sum(1 for p in positions if p.kind=='limit')} limits "
            f"({[p.limit_price for p in positions if p.kind=='limit']}), "
            f"SL={market_pos.sl} TP={market_pos.tp}")

        # 3. Safety check (tras safety_delay_sec del fill)
        safety_t = fill_tick["time_utc"] + timedelta(seconds=strat.safety_delay_sec)
        safety_ticks = ticks[ticks["time_utc"] >= safety_t]
        if len(safety_ticks) > 0 and rng is not None:
            st = safety_ticks.iloc[0]
            outside = fill_price < rng[0] or fill_price > rng[1]
            distance_abs = abs(entry_distance)

            if outside:
                # Decidir según safety_mode
                if strat.safety_mode == "tolerance" and distance_abs <= strat.safety_tolerance:
                    log(st["time_utc"], "SAFE",
                        f"Entry {fill_price:.2f} fuera de [{rng[0]}-{rng[1]}] "
                        f"pero |{distance_abs:.2f}| ≤ tol={strat.safety_tolerance} → opera")
                elif strat.safety_mode == "cancel" or (
                    strat.safety_mode == "tolerance" and distance_abs > strat.safety_tolerance
                ):
                    # Cerrar market + cancelar limits (comportamiento actual)
                    close_p = st["ask"] if direction == "SELL" else st["bid"]
                    market_pos.state = "closed"
                    market_pos.close_time = st["time_utc"]
                    market_pos.close_price = close_p
                    market_pos.close_reason = "SAFETY"
                    for p in positions[1:]:
                        if p.state == "pending":
                            p.state = "cancelled"
                    log(st["time_utc"], "SAFE",
                        f"Entry {fill_price:.2f} fuera de [{rng[0]}-{rng[1]}] "
                        f"(d={entry_distance:+.2f}) → cierra market @ {close_p:.2f} "
                        f"+ cancela limits")
                    return _finalize(positions, events, contract_size, "SAFETY_CANCEL",
                                     entry_distance=entry_distance,
                                     would_market_fill=would_fill_price)
                elif strat.safety_mode == "limit_fallback":
                    # Cerrar market PERO mantener limits dentro del rango
                    close_p = st["ask"] if direction == "SELL" else st["bid"]
                    market_pos.state = "closed"
                    market_pos.close_time = st["time_utc"]
                    market_pos.close_price = close_p
                    market_pos.close_reason = "SAFETY_M"
                    log(st["time_utc"], "SAFE",
                        f"Entry {fill_price:.2f} fuera de [{rng[0]}-{rng[1]}] "
                        f"(d={entry_distance:+.2f}) → cierra market @ {close_p:.2f} "
                        f"PERO mantiene {sum(1 for p in positions[1:] if p.state=='pending')} "
                        f"limits")
                    market_pos = None  # ya no nos importa el market
                    # No return — seguir con el loop a ver si limits llenan
                elif strat.safety_mode == "smart":
                    # Asimétrico: ¿el slippage va A FAVOR del trade o EN CONTRA?
                    #
                    # Para BUY: el TP está ARRIBA del rango. Si el entry queda
                    # POR ENCIMA del rango (entry > rng[1]) → ya estamos hacia
                    # el TP → favorable. Si queda POR DEBAJO → adverso.
                    #
                    # Para SELL: el TP está ABAJO del rango. Si el entry queda
                    # POR DEBAJO del rango (entry < rng[0]) → ya hacia el TP
                    # → favorable. Si queda POR ENCIMA → adverso.
                    if direction == "BUY":
                        favorable = fill_price > rng[1]   # arriba del rango
                    else:  # SELL
                        favorable = fill_price < rng[0]   # abajo del rango

                    if favorable:
                        # Mantener el market abierto, cancelar limits (ya no
                        # aplican: el precio ya pasó la zona de DCA).
                        for p in positions[1:]:
                            if p.state == "pending":
                                p.state = "cancelled"
                        log(st["time_utc"], "SMART",
                            f"Entry {fill_price:.2f} fuera de [{rng[0]}-{rng[1]}] "
                            f"(d={entry_distance:+.2f}) FAVORABLE → mantiene market, "
                            f"cancela limits")
                    else:
                        # Adverso: cerrar todo como "cancel"
                        close_p = st["ask"] if direction == "SELL" else st["bid"]
                        market_pos.state = "closed"
                        market_pos.close_time = st["time_utc"]
                        market_pos.close_price = close_p
                        market_pos.close_reason = "SAFETY_ADV"
                        for p in positions[1:]:
                            if p.state == "pending":
                                p.state = "cancelled"
                        log(st["time_utc"], "SMART",
                            f"Entry {fill_price:.2f} fuera de [{rng[0]}-{rng[1]}] "
                            f"(d={entry_distance:+.2f}) ADVERSO → cierra todo "
                            f"@ {close_p:.2f}")
                        return _finalize(positions, events, contract_size,
                                         "SAFETY_ADVERSE",
                                         entry_distance=entry_distance,
                                         would_market_fill=would_fill_price)

    # 4. Iterar ticks hasta horizon o cierre total (helper común)
    _iterate_ticks(positions, sig, strat, ticks, iteration_start_time,
                   sig_dt, events, log, mgmt_events=mgmt_events)

    return _finalize(positions, events, contract_size, "OPERATED",
                     entry_distance=entry_distance,
                     would_market_fill=would_fill_price,
                     pre_range_outcome=pre_range_outcome,
                     range_arrival_decision=range_arrival_decision)


def _finalize(positions, events, contract_size, status,
              entry_distance=None, would_market_fill=None,
              pre_range_outcome=None, range_arrival_decision=None,
              final_outcome=None):
    pl = sum(p.pl(contract_size, LOT) for p in positions)
    n_filled = sum(1 for p in positions if p.state == "closed" and p.open_price is not None)
    n_cancelled = sum(1 for p in positions if p.state == "cancelled")

    # Derivar final_outcome del cierre de la posición market si no se pasó
    if final_outcome is None:
        market_pos = next((p for p in positions if p.kind == "market"), None)
        final_outcome = market_pos.close_reason if (market_pos and market_pos.close_reason) else "n/a"

    return {
        "status": status, "positions": positions, "events": events, "pl": pl,
        "entry_distance": entry_distance,
        "would_market_fill": would_market_fill,
        "n_filled": n_filled, "n_cancelled": n_cancelled,
        "pre_range_outcome": pre_range_outcome,
        "range_arrival_decision": range_arrival_decision,
        "final_outcome": final_outcome,
    }


def _apply_mgmt_event(m: dict, positions: list, bid: float, ask: float,
                      tt, log_fn) -> None:
    """Aplica un mensaje de gestion del canal a las posiciones abiertas.

    Acciones:
      MOVE_SL_TO_BE      → SL = entry de cada pos abierta.
      MOVE_SL_TO_PRICE   → SL = m['price'] (validado).
      CLOSE_ALL          → cerrar todas (ask para SELL, bid para BUY).
      CLOSE_FIRST_ENTRY  → cerrar solo la pos market original (pos_id=0).
    """
    action = m.get("action")
    if action == "MOVE_SL_TO_BE":
        for p in positions:
            if p.state == "open" and p.open_price is not None:
                old_sl = p.sl
                p.sl = p.open_price
                log_fn(tt, "MGMT",
                       f"MOVE_SL_TO_BE pos#{p.pos_id} SL "
                       f"{old_sl}→{p.sl:.2f}")
    elif action == "MOVE_SL_TO_PRICE":
        new_sl = m.get("price")
        if new_sl is None:
            return
        try:
            new_sl = float(new_sl)
        except (TypeError, ValueError):
            return
        for p in positions:
            if p.state != "open":
                continue
            if not _sl_is_valid(p.direction, p.open_price, new_sl):
                log_fn(tt, "MGMT",
                       f"MOVE_SL_TO_PRICE pos#{p.pos_id} SL={new_sl} "
                       f"invalido para {p.direction}@{p.open_price:.2f} → ignorado")
                continue
            old_sl = p.sl
            p.sl = new_sl
            log_fn(tt, "MGMT",
                   f"MOVE_SL_TO_PRICE pos#{p.pos_id} SL "
                   f"{old_sl}→{p.sl:.2f}")
    elif action == "CLOSE_ALL":
        for p in positions:
            if p.state == "open":
                close_p = ask if p.direction == "SELL" else bid
                p.close_price = close_p
                p.close_reason = "MGMT_CLOSE"
                p.close_time = tt
                p.state = "closed"
                log_fn(tt, "MGMT",
                       f"CLOSE_ALL pos#{p.pos_id} @ {close_p:.2f}")
            elif p.state == "pending":
                p.state = "cancelled"
                log_fn(tt, "MGMT",
                       f"CLOSE_ALL cancela limit pos#{p.pos_id}")
    elif action == "CLOSE_FIRST_ENTRY":
        # Cerrar solo la pos market original (la primera pos creada)
        for p in positions:
            if p.state == "open" and p.kind == "market" and p.pos_id == 0:
                close_p = ask if p.direction == "SELL" else bid
                p.close_price = close_p
                p.close_reason = "MGMT_CLOSE_FIRST"
                p.close_time = tt
                p.state = "closed"
                log_fn(tt, "MGMT",
                       f"CLOSE_FIRST_ENTRY pos#{p.pos_id} @ {close_p:.2f}")
                break


def _iterate_ticks(positions, sig, strat, ticks, start_time, sig_dt,
                   events, log_fn, mgmt_events=None):
    """Loop tick-a-tick común a path normal y layered.

    Modifica `positions` y `events` in-place. Procesa ticks desde `start_time`
    hasta `horizon` o cierre total. Aplica:
      - mgmt_events del journal (BE/SL_TO_PRICE/CLOSE_ALL/CLOSE_FIRST) en su ts
      - Limit fills
      - BE trigger (si be_at_tp_index >= 0)
      - SL/TP por posición (sólo si tienen SL/TP asignados)
      - Time stop (si time_stop_min > 0)
      - EOH cleanup
    """
    direction = sig["direction"]
    be_active = False
    be_tp_price = None
    if strat.be_at_tp_index >= 0:
        be_tp_price = sig["tps"][min(strat.be_at_tp_index, len(sig["tps"]) - 1)]
    horizon_t = sig_dt + timedelta(minutes=strat.horizon_min)
    time_stop_t = (sig_dt + timedelta(minutes=strat.time_stop_min)
                   if strat.time_stop_min > 0 else None)

    follow = ticks[ticks["time_utc"] >= start_time]
    if len(follow) == 0:
        return

    # Extracción a arrays numpy: ~50x más rápido y mucho menos RAM que iterrows().
    # En sweeps grandes (24k+ simulaciones) iterrows() era el bottleneck principal
    # y disparaba ArrayMemoryError al alocar bloques object-dtype.
    times = follow["time_utc"].to_numpy()
    bids = follow["bid"].to_numpy()
    asks = follow["ask"].to_numpy()
    n = len(times)
    last_idx = n - 1

    # ── Cursor de mgmt_events: ordenados por ts, aplicar cuando tt >= ts ─
    # ts se mantiene como Timestamp tz-aware (consistente con `tt` que viene
    # de DataFrame con time_utc tz-aware UTC).
    mgmt_cursor = 0
    mgmt_list = []
    if mgmt_events:
        for m in mgmt_events:
            try:
                m_ts = pd.to_datetime(m["ts"], utc=True)
                mgmt_list.append({"ts": m_ts, "action": m["action"],
                                  "price": m.get("price")})
            except Exception:
                pass
        mgmt_list.sort(key=lambda x: x["ts"])

    for i in range(n):
        tt = times[i]
        bid = bids[i]
        ask = asks[i]

        if tt > horizon_t:
            break

        # ── Procesar mgmt_events cuyo ts ya paso ─────────────────────────
        while mgmt_cursor < len(mgmt_list) and mgmt_list[mgmt_cursor]["ts"] <= tt:
            _apply_mgmt_event(mgmt_list[mgmt_cursor], positions, bid, ask,
                              tt, log_fn)
            mgmt_cursor += 1
        # Si todas las pos cerraron por mgmt, salir
        if all(p.state in ("closed", "cancelled") for p in positions):
            break

        # Limit fills
        for p in positions:
            if p.state != "pending": continue
            if p.direction == "BUY" and ask <= p.limit_price:
                p.state = "open"
                p.open_time = tt
                p.open_price = p.limit_price
                log_fn(tt, "FILL", f"LIMIT pos#{p.pos_id} BUY @ {p.limit_price:.2f}")
            elif p.direction == "SELL" and bid >= p.limit_price:
                p.state = "open"
                p.open_time = tt
                p.open_price = p.limit_price
                log_fn(tt, "FILL", f"LIMIT pos#{p.pos_id} SELL @ {p.limit_price:.2f}")

        # BE trigger
        if not be_active and be_tp_price is not None:
            touched = ((direction == "BUY" and ask >= be_tp_price) or
                       (direction == "SELL" and bid <= be_tp_price))
            if touched:
                be_active = True
                for p in positions:
                    if p.state == "open" and p.sl is not None:
                        old_sl = p.sl
                        p.sl = p.open_price
                        log_fn(tt, "BE",
                               f"TP_be {be_tp_price} tocado → pos#{p.pos_id} SL "
                               f"{old_sl}→{p.sl:.2f}")

        # SL/TP por posición (check independientemente — SL puede estar None
        # si fue rechazado por inválido, p.ej. SL arriba del entry en BUY).
        for p in positions:
            if p.state != "open": continue
            sl_hit = False
            if p.sl is not None:
                sl_hit = ((p.direction == "BUY" and bid <= p.sl) or
                          (p.direction == "SELL" and ask >= p.sl))
            tp_hit = False
            if p.tp is not None:
                tp_hit = ((p.direction == "BUY" and bid >= p.tp) or
                          (p.direction == "SELL" and ask <= p.tp))

            if sl_hit and tp_hit:
                close_p = p.sl
                reason = "BE" if (be_active and abs(p.sl - p.open_price) < 0.01) else "SL"
                p.close_price = close_p
                p.close_reason = reason
                p.close_time = tt
                p.state = "closed"
                log_fn(tt, reason, f"pos#{p.pos_id} SL+TP en mismo tick → {reason} @ {close_p:.2f}")
            elif sl_hit:
                close_p = p.sl
                reason = "BE" if (be_active and abs(p.sl - p.open_price) < 0.01) else "SL"
                p.close_price = close_p
                p.close_reason = reason
                p.close_time = tt
                p.state = "closed"
                log_fn(tt, reason, f"pos#{p.pos_id} {reason} @ {close_p:.2f}")
            elif tp_hit:
                p.close_price = p.tp
                p.close_reason = "TP"
                p.close_time = tt
                p.state = "closed"
                log_fn(tt, "TP", f"pos#{p.pos_id} TP @ {p.tp:.2f}")

        # Time stop (no aplica si BE ya activo)
        if time_stop_t is not None and tt >= time_stop_t and not be_active:
            for p in positions:
                if p.state == "open":
                    close_p = ask if p.direction == "SELL" else bid
                    p.close_price = close_p
                    p.close_reason = "TIME"
                    p.close_time = tt
                    p.state = "closed"
                    log_fn(tt, "TIME", f"pos#{p.pos_id} TIME-STOP @ {close_p:.2f}")
                elif p.state == "pending":
                    p.state = "cancelled"
                    log_fn(tt, "CANC", f"pos#{p.pos_id} cancela limit por time-stop")
            break

        # Todas cerradas / canceladas → fin
        if all(p.state in ("closed", "cancelled") for p in positions):
            break

    # EOH para las que sigan abiertas/pending (usa el último tick procesado)
    last_t = times[last_idx]
    last_bid = bids[last_idx]
    last_ask = asks[last_idx]
    for p in positions:
        if p.state == "open":
            close_p = last_ask if p.direction == "SELL" else last_bid
            p.close_price = close_p
            p.close_reason = "EOH"
            p.close_time = last_t
            p.state = "closed"
            log_fn(last_t, "EOH", f"pos#{p.pos_id} EOH @ {close_p:.2f}")
        elif p.state == "pending":
            p.state = "cancelled"
            log_fn(last_t, "CANC", f"pos#{p.pos_id} cancela limit (EOH)")


# ── Demo: una señal ───────────────────────────────────────────────────────────

def main():
    print("="*84)
    print("  SIMULADOR TICK-BASED FIEL A MT5 — prototipo (1 señal)")
    print("="*84)

    # 1. Cargar señal del 12:24
    df = pd.read_parquet(DATA_DIR / "canal1_signals_2026.parquet")
    df["dt"] = pd.to_datetime(df["dt"], utc=True)
    target = datetime(2026, 4, 22, 12, 24, tzinfo=timezone.utc)
    cand = df[(df["dt"] >= target) & (df["dt"] < target + timedelta(minutes=2))]
    row = cand.iloc[0]
    sig = {
        "id": int(row["id"]),
        "dt": row["dt"].to_pydatetime(),
        "direction": row["direction"],
        "range": ((row["range_low"], row["range_high"])
                  if pd.notna(row["range_low"]) else None),
        "tps": [row[f"tp{i}"] for i in range(1, 6) if pd.notna(row[f"tp{i}"])],
        "sl": float(row["sl"]),
    }
    print(f"\n  Señal {sig['id']}: {sig['dt']} {sig['direction']}")
    print(f"  Range: {sig['range']}, TPs: {sig['tps']}, SL: {sig['sl']}")

    # 2. Estrategia (la que recomendamos antes — extremes TP3+TS60)
    strat = Strategy(
        entry_mode="extremes",
        n_entries=2,
        target_tp_index=2,    # TP3
        be_at_tp_index=-1,    # sin BE
        time_stop_min=60,
        horizon_min=240,
        safety_delay_sec=2,
    )
    print(f"  Estrategia: {strat}")

    # 3. MT5: descargar (o usar cache) ticks
    mt5s = MT5Session(SYMBOL)
    try:
        t_from = sig["dt"] - timedelta(seconds=30)
        t_to   = sig["dt"] + timedelta(minutes=strat.horizon_min)
        print(f"\n  Pidiendo ticks {t_from} → {t_to} ...")
        ticks = mt5s.get_or_cache_ticks(sig["id"], t_from, t_to)
        print(f"  Ticks: {len(ticks)} (cache: {TICKS_CACHE / f'sig_{sig['id']}.parquet'})")
        if len(ticks) == 0:
            print("  ERROR: 0 ticks. Verifica history disponible en MT5.")
            return

        # 4. Simular
        print(f"\n{'─'*84}\n  EVENTOS DE LA SIMULACIÓN (verbose):\n{'─'*84}")
        result = simulate(sig, strat, ticks, mt5s.contract_size, verbose=True)

        # 5. Resumen
        print(f"\n{'─'*84}\n  RESUMEN POR POSICIÓN:\n{'─'*84}")
        for p in result["positions"]:
            if p.state == "cancelled":
                print(f"  pos#{p.pos_id} ({p.kind:6s}) CANCELLED limit={p.limit_price}")
                continue
            pl = p.pl(mt5s.contract_size, LOT)
            print(f"  pos#{p.pos_id} ({p.kind:6s}) "
                  f"open @ {p.open_price:.2f} ({p.open_time.time() if p.open_time else '?'}) "
                  f"close {p.close_reason:5s} @ {p.close_price:.2f} "
                  f"({p.close_time.time() if p.close_time else '?'})  "
                  f"P/L = ${pl:+.2f}")

        print(f"\n  📊 P/L TOTAL (lote {LOT}): ${result['pl']:+.2f}")
        print(f"  Status: {result['status']}")
        print(f"  Eventos totales: {len(result['events'])}")

    finally:
        mt5s.shutdown()


if __name__ == "__main__":
    main()
