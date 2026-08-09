from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import causal_trace
import config


@dataclass
class TradeContext:
    """Snapshot completo del estado de un trade en un momento dado.

    Sirve para que cualquier decisión (clasificación de mensajes, asignación
    de TPs, BE move, cierre, notify) tenga acceso al ESTADO REAL en lugar de
    aplicar reglas ciegas. Construido vía Signal.build_context() consultando
    MT5 para datos en vivo (n_open, P&L floating, current_price).

    Campos:
      • channel, signal_id, direction, entry_price : identificación
      • tps, sl                                   : niveles configurados
      • n_initial, n_open                          : conteo (in-memory vs MT5)
      • open_tickets_pnl                           : P&L floating por ticket abierto
      • floating_pnl_total                         : suma
      • elapsed_min                                : tiempo desde signal_received
      • current_price                              : bid/ask relevante
      • be_armed                                   : si BE ya disparó
    """
    channel: str
    signal_id: str
    direction: str
    entry_price: Optional[float]
    tps: list
    sl: Optional[float]
    n_initial: int             # cuántas posiciones se abrieron en total
    n_open: int                # cuántas siguen abiertas en MT5 ahora
    open_tickets_pnl: list     # [(ticket, pnl_floating), ...]
    floating_pnl_total: float
    elapsed_min: float
    current_price: Optional[float]
    be_armed: bool
    # Niveles confirmados directamente en las posiciones MT5 abiertas.
    # El SL/TP del proveedor puede quedar obsoleto tras una gestion posterior.
    effective_sls: list = field(default_factory=list)
    effective_tps: list = field(default_factory=list)

    def summary_oneline(self) -> str:
        """Resumen de 1 línea para logs."""
        return (f"{self.channel} {self.direction} entry={self.entry_price} "
                f"open={self.n_open}/{self.n_initial} "
                f"pl={self.floating_pnl_total:+.2f} "
                f"price={self.current_price} elapsed={self.elapsed_min:.1f}min")


@dataclass
class Signal:
    channel: str          # "canal1" | "canal2"
    message_id: int
    direction: str        # "BUY" | "SELL"
    timestamp: datetime = field(default_factory=datetime.utcnow)

    # Datos del señal (se rellenan al llegar el texto completo)
    range_low:  Optional[float] = None
    range_high: Optional[float] = None
    tps:  list = field(default_factory=list)
    sl:   Optional[float] = None

    # Tickets MT5
    market_ticket:      Optional[int]   = None  # primera orden a mercado
    market_fill_price:  Optional[float] = None  # precio de fill del market
    dca_tickets:     list = field(default_factory=list)   # límites rellenados
    pending_tickets: list = field(default_factory=list)   # límites pendientes

    # ─── Double Market: tickets de markets ADICIONALES al market_ticket ─────
    # Cuando STRATEGY_DOUBLE_MARKET_ENABLED esta activo, abrimos una segunda
    # posicion market simultaneamente con TP distinto (TP3 por defecto).
    # El ticket adicional va aqui y se incluye en `all_filled_tickets`.
    # tp_overrides[ticket] mapea cada ticket a su TP especifico.
    extra_market_tickets:    list = field(default_factory=list)
    extra_market_fill_prices: list = field(default_factory=list)

    # Flags
    dca_placed: bool = False  # Nombre legacy; ahora protege el arranque del monitor.
    levels_predicted: bool = False  # TPs/SL vienen del predictor, no del canal
    status: str = "open"  # "open" | "closed"
    # Transient guard: the live auditor must not adopt a leg while the
    # listener is still between the MT5 fill and the in-memory append.
    opening_extra_legs: bool = False

    # ─── Estrategias por señal (point 1, 4, 7) ──────────────────────────────
    # Multiplicador de lotaje (1.0 normal, 0.5 HIGH RISK)
    lot_multiplier: float = 1.0

    # Cap del índice de TP para escalonado (None = sin cap, usa TP más alto).
    # Post-SL momentum: cap=3 → todas las posiciones >=3 usan tps[3] (TP4),
    # nunca tps[4] (TP5) que tiene mala relación EV/exposición.
    max_tp_index: Optional[int] = None

    # Timestamp en el que se debe cerrar forzosamente (TIME-STOP defensivo).
    # None = desactivado (sin time stop).
    time_stop_at: Optional[datetime] = None

    # ¿Es señal HIGH RISK? (logging y debug)
    is_high_risk: bool = False

    # ─── Estrategias por canal (validadas IS/OOS 2026 con M1 real) ──────────
    # Modo de entrada vivo:
    #   "scale_out"   → N posiciones a mercado con TPs escalonados
    #   "market_only" → 1 sola posición a mercado
    # Valores legacy ("extremes", "intra_dca") se normalizan en config.
    entry_mode: Optional[str] = None

    # Índice (0-based) del TP que cierra TODAS las posiciones (TP único por canal).
    # None = comportamiento escalonado legacy (pos i → tps[i]).
    # Canal 1: TP3 (idx=2) | Canal 2: TP4 (idx=3)
    target_tp_index: Optional[int] = None

    # Índice (0-based) del TP que dispara BE move (mover SL a entry).
    # None = sin BE automático.
    # Canal 2: TP1 (idx=0) protege drawdown OOS de $1050 → $893
    be_at_tp_index: Optional[int] = None

    # ¿BE ya disparado en esta señal? (interno, no tocar)
    be_armed: bool = False

    # Aggregate account-currency protection for Dubai Investing. These fields
    # are persisted as journal transitions and rebuilt after a restart.
    basket_guard_armed: bool = False
    basket_guard_triggered: bool = False
    basket_guard_peak_pl: Optional[float] = None
    basket_guard_trigger_reason: Optional[str] = None
    basket_guard_recovery_pending: bool = False
    basket_guard_close_tickets: list = field(default_factory=list)

    # Tickets cerrados explícitamente por acción CLOSE_FIRST.
    # Se rellena en listener.py al ejecutar el action. Usado en
    # _classify_closures para taggear esos cierres como "CLOSE_FIRST"
    # en vez de "MANUAL" (no son TPs ni SL, son cierres de gestión).
    close_first_tickets: list = field(default_factory=list)

    # ─── Estrategia C — CLOSE_FIRST rescate BE (canal2) ─────────────────────
    # Cuando llega CLOSE_FIRST SIN profit, en vez de cerrar a mercado en
    # pérdida ponemos TP=BE en todas las posiciones y armamos un time-stop.
    # Estos campos coordinan la rama de rescate con su task de time-stop.
    #   close_first_be_armed   : True mientras la rama BE-rescate está activa.
    #   close_first_be_deadline: datetime UTC en que el time-stop debe cerrar
    #                            a mercado lo que quede abierto. None = inactivo.
    close_first_be_armed: bool = False
    close_first_be_deadline: Optional[datetime] = None

    # Rescate BE generico para mensajes "close at breakeven / risk free" que
    # llegan cuando nuestra entrada real esta en perdida. No es CLOSE_FIRST:
    # se arma desde CLOSE_ALL semantico y sus cierres se etiquetan aparte.
    be_rescue_armed: bool = False
    be_rescue_deadline: Optional[datetime] = None
    be_rescue_tickets: list = field(default_factory=list)

    # Override del TP por ticket. Mapping ticket_id → índice de TP en self.tps.
    # Usado por la lógica `rescue_market` (caso C adverse) para asignar TP4/TP5
    # al ticket de rescate (entrada óptima en precio adverso) sin romper el
    # escalonado del resto. Cuando _apply_sl_tp procesa un ticket presente en
    # este dict, usa tps[tp_overrides[ticket]] en lugar de tp_for_position(i).
    tp_overrides: dict = field(default_factory=dict)

    # SL REAL por ticket tras moverlo un mensaje de gestion del canal
    # (MOVE_SL_TO_BE, MOVE_SL_TO_PRICE, BE-trailing). Mapping ticket → sl.
    # `sl` (arriba) es el SL del PROVEEDOR. Cuando el canal mueve el SL de
    # una posicion, MT5 se actualiza pero `sl` NO — y entonces
    # _classify_closures comparaba el cierre contra el SL viejo y lo
    # etiquetaba "MANUAL", y la logica BE-trailing usaba un baseline
    # obsoleto al decidir si un nuevo SL "mejora". pending_actions rellena
    # este dict al CONFIRMAR el MODIFY_SLTP en MT5 (retcode OK), no al
    # encolarlo — asi solo refleja SLs realmente aplicados.
    sl_by_ticket: dict = field(default_factory=dict)

    # TP real por ticket tras una modificacion confirmada por MT5.
    tp_by_ticket: dict = field(default_factory=dict)

    # ─── Layered logic Etapa 2 ──────────────────────────────────────────────
    # Acción a aplicar cuando llega el rango y la posición market quedó FUERA
    # ADVERSA del rango (caso C). Se setea desde config.STRATEGY_Cx_ADVERSE_ACTION
    # en el listener al crear la señal. Por defecto "close" (= safety legacy).
    #   "close"               — cierra market.
    #   "hold_with_limits"    — legacy desactivado; se trata como hold_no_limits.
    #   "hold_no_limits"      — mantiene market sin limits.
    #   "hold_sl_to_extreme"  — mantiene market con SL al extremo del rango.
    adverse_action: str = "close"

    # Bandera interna: ¿ya se aplicó la decisión Etapa 2? (evita re-aplicar
    # cuando el siguiente edit re-llama a _update_signal_from_parsed)
    range_safety_applied: bool = False

    # ─── Pending correction tras typo del canal ─────────────────────────────
    # Cuando el canal manda valores incoherentes con la direccion (typo del
    # trader, ej. SL=4796 para BUY @ 4704), el listener REGISTRA aqui que
    # esta esperando una correccion en lugar de aplicar el valor inconsistente.
    # Estructura:
    #   {
    #     "since_utc": datetime,        # cuando se detecto el typo
    #     "field": "sl" | "tps" | "both",
    #     "received_value": value,
    #     "kept_value": value,           # lo que mantenemos (predictor o anterior)
    #     "reason": str,                 # del helper levels_consistent_with_direction
    #     "notified_urgent": bool,       # ya notificamos URGENT al usuario?
    #   }
    # El watchdog (main._pending_correction_watchdog) revisa cada 30s y
    # notify URGENT a los 60s sin correccion. Cuando llega correccion
    # coherente, se limpia este dict.
    pending_correction: dict = field(default_factory=dict)
    source_message_revision_id: Optional[str] = field(
        default_factory=causal_trace.current_message_revision_id
    )
    source_decision_id: Optional[str] = field(
        default_factory=causal_trace.current_decision_id
    )
    telegram_entry_command_key: Optional[str] = None
    telegram_entry_was_reply: bool = False
    telegram_entry_reply_to_message_id: Optional[int] = None
    telegram_entry_timestamp: Optional[datetime] = None
    # Exact origin of the MT5 exposure. NOW remains the compatibility default.
    entry_source_kind: str = "telegram_now"
    zone_plan_message_id: Optional[int] = None
    zone_thread_root_message_id: Optional[int] = None
    zone_trigger_kind: Optional[str] = None
    zone_trigger_price: Optional[float] = None
    zone_trigger_time_msc: Optional[int] = None
    zone_entry_generation: int = 0

    @property
    def all_filled_tickets(self) -> list:
        """Todos los tickets abiertos: market principal + legs extra.

        Orden: [market_ticket, *extra_market_tickets, *dca_tickets].
        El index dentro de esta lista se usa en tp_for_position() para asignar
        el TP escalonado, salvo que el ticket este en tp_overrides (caso
        rescue_market o double_market).
        """
        tickets = []
        if self.market_ticket:
            tickets.append(self.market_ticket)
        tickets.extend(self.extra_market_tickets)
        tickets.extend(self.dca_tickets)
        deduped = []
        seen = set()
        for ticket in tickets:
            if ticket in seen:
                continue
            seen.add(ticket)
            deduped.append(ticket)
        return deduped

    @property
    def magic(self) -> int:
        """Magic number de MT5 correspondiente al canal de esta señal."""
        return config.magic_for(self.channel)

    @property
    def effective_lot(self) -> float:
        """Lotaje real aplicando el multiplicador. Mínimo 0.01 (lote mínimo)."""
        raw = config.LOT_SIZE * self.lot_multiplier
        # Redondea al lote mínimo del broker (0.01 en Vantage Standard)
        return max(0.01, round(raw, 2))

    def tp_for_position(self, position_index: int,
                        n_total_open: Optional[int] = None) -> Optional[float]:
        """TP que cierra cada posición.

        Modo target_tp_index: TODAS al mismo TP fijo.

        Modo escalonado (default): posición i → tps[i] con cap opcional.
            leg 0 → TP1, leg 1 → TP2, leg 2 → TP3, etc.

        position_index: 0 = market, 1+ = legs extra.
        n_total_open: reservado (ya no usado), mantenido por compatibilidad.
        """
        if not self.tps:
            return None

        # Modo target_tp_index: TP único, ignora contexto
        if self.target_tp_index is not None:
            idx = min(self.target_tp_index, len(self.tps) - 1)
            return self.tps[idx]

        # Escalonado: pos i → tps[i] con cap opcional
        idx = position_index
        if self.max_tp_index is not None:
            idx = min(idx, self.max_tp_index)

        if idx >= len(self.tps):
            return self.tps[-1]
        return self.tps[idx]

    def be_trigger_price(self) -> Optional[float]:
        """Precio del TP que dispara el BE move (mover SL a entry).

        Devuelve None si no hay BE configurado o no hay TPs.
        """
        if self.be_at_tp_index is None or not self.tps:
            return None
        idx = min(self.be_at_tp_index, len(self.tps) - 1)
        return self.tps[idx]

    def build_context(self) -> TradeContext:
        """Construye un snapshot del estado actual del trade consultando MT5.

        SAFE: si MT5 no responde, devuelve TradeContext con datos parciales
        (n_open=0, current_price=None) en vez de crashear.

        Usar para alimentar Gemini, decisiones de TP allocation, BE, notify.
        """
        try:
            import MetaTrader5 as mt5
            import config as _config

            # Posiciones abiertas en MT5 entre las que originalmente abrimos
            open_pnls = []
            effective_sls = []
            effective_tps = []
            n_open = 0
            for ticket in self.all_filled_tickets:
                pos = mt5.positions_get(ticket=ticket)
                if pos:
                    position = pos[0]
                    open_pnls.append((int(ticket), float(position.profit)))
                    if float(getattr(position, "sl", 0.0) or 0.0) > 0:
                        effective_sls.append(float(position.sl))
                    if float(getattr(position, "tp", 0.0) or 0.0) > 0:
                        effective_tps.append(float(position.tp))
                    n_open += 1
            floating_total = sum(p for _, p in open_pnls)

            # Tick actual
            current_price = None
            try:
                tick = mt5.symbol_info_tick(_config.MT5_SYMBOL)
                if tick:
                    # Para BUY el precio relevante para cierre es bid; para SELL ask
                    current_price = float(tick.bid if self.direction == "BUY" else tick.ask)
            except Exception:
                pass

            elapsed_s = (datetime.utcnow() - self.timestamp).total_seconds()

            return TradeContext(
                channel=self.channel,
                signal_id=f"{self.channel}_{self.message_id}",
                direction=self.direction,
                entry_price=self.market_fill_price,
                tps=list(self.tps),
                sl=self.sl,
                n_initial=len(self.all_filled_tickets),
                n_open=n_open,
                open_tickets_pnl=open_pnls,
                floating_pnl_total=round(floating_total, 2),
                elapsed_min=round(elapsed_s / 60, 1),
                current_price=current_price,
                be_armed=self.be_armed,
                effective_sls=effective_sls,
                effective_tps=effective_tps,
            )
        except Exception as e:
            # Defensive: nunca crashear, devolver context mínimo
            print(f"[TradeContext] error build_context: {e}")
            return TradeContext(
                channel=self.channel,
                signal_id=f"{self.channel}_{self.message_id}",
                direction=self.direction,
                entry_price=self.market_fill_price,
                tps=list(self.tps),
                sl=self.sl,
                n_initial=len(self.all_filled_tickets),
                n_open=0,
                open_tickets_pnl=[],
                floating_pnl_total=0.0,
                elapsed_min=0.0,
                current_price=None,
                be_armed=self.be_armed,
            )


class StateManager:
    def __init__(self):
        self._signals: dict[str, Signal] = {}

    def _key(self, channel: str, message_id: int) -> str:
        return f"{channel}_{message_id}"

    def add(self, signal: Signal) -> str:
        key = self._key(signal.channel, signal.message_id)
        # Detector Batch C: overwrite silencioso. Si la key ya existe y
        # apunta a OTRO objeto Signal, el viejo se pierde sin trazo. Esto
        # solo deberia pasar con bugs sutiles (resync incorrecto, doble
        # proceso de la misma senal, etc.). Re-add del mismo objeto (caso
        # idempotente) no se considera overwrite.
        existing = self._signals.get(key)
        if existing is not None and existing is not signal:
            try:
                import journal as _j_so
                _j_so.anomaly(key, "naked", "warning",
                              "state.add sobrescribio una Signal viva — "
                              "perdimos la referencia anterior (posibles "
                              "tickets MT5 huerfanos en el viejo)",
                              previous_status=existing.status,
                              previous_tickets=existing.all_filled_tickets,
                              previous_message_id=existing.message_id,
                              new_message_id=signal.message_id,
                              new_direction=signal.direction)
            except Exception:
                pass
        self._signals[key] = signal
        return key

    def alias(self, signal: Signal, alt_message_id: int) -> str:
        """Registra esta señal bajo un message_id ADICIONAL.

        Caso de uso: canal 1 manda primero un sticker (msg_id=A) que dispara
        la entrada market. Luego, ~30-90s después, manda un mensaje de TEXTO
        con TPs/SL (msg_id=B). Todos los mensajes de gestión posteriores
        (photos con captions tipo 'Move SL to BE', 'TP1 HIT', etc.) son
        REPLY al mensaje B, no al sticker A.

        Sin esta función, state.get(canal, B) devolvería None y el bot
        ignoraría silenciosamente toda la gestión del canal 1. Llamar a
        alias(signal, B) en _handle_canal1_text resuelve el problema.

        El signal sigue siendo el MISMO objeto (no copia) — sus mutaciones
        son visibles bajo cualquiera de sus alias. Cuando se cierra, queda
        cerrado para todos.
        """
        key = self._key(signal.channel, alt_message_id)
        self._signals[key] = signal
        return key

    def get(self, channel: str, message_id: int) -> Optional[Signal]:
        return self._signals.get(self._key(channel, message_id))

    def latest_open(self, channel: str) -> Optional[Signal]:
        candidates = [
            s for s in self._signals.values()
            if s.channel == channel and s.status == "open"
        ]
        return max(candidates, key=lambda s: s.timestamp) if candidates else None

    def open_signals(self, channel: str) -> list[Signal]:
        """Señales abiertas de un canal, DEDUPLICADAS y de más reciente a
        más antigua.

        Un mismo Signal puede estar registrado bajo varios message_id en
        _signals (canal 1 registra un alias del mensaje de texto además del
        sticker — ver alias()). Sin deduplicar por identidad de objeto, una
        señal con alias se contaría dos veces. Lo usa el listener para saber
        si un mensaje de gestión sin reply tiene un destino inequívoco.
        """
        seen: set[int] = set()
        out: list[Signal] = []
        for s in self._signals.values():
            if (s.channel == channel and s.status == "open"
                    and id(s) not in seen):
                seen.add(id(s))
                out.append(s)
        out.sort(key=lambda s: s.timestamp, reverse=True)
        return out

    def close(self, channel: str, message_id: int):
        sig = self.get(channel, message_id)
        if sig:
            sig.status = "closed"


state = StateManager()
