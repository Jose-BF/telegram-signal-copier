"""
Handlers de Telethon para Canal 1 y Canal 2.

Canal 2 flujo:
  NewMessage (sin reply)  → señal inicial → mercado inmediato
  MessageEdited           -> anade rango/TPs/SL -> aplica SL/TP y monitor
  NewMessage (con reply)  → mensaje de gestión → acción

Canal 1 flujo:
  NewMessage sticker      → mercado inmediato (dirección por file_id)
  NewMessage texto        -> anade rango/TPs/SL -> aplica SL/TP y monitor
  NewMessage (con reply)  → mensaje de gestión → acción
"""

import asyncio
import hashlib
import re
from datetime import datetime, timedelta

from telethon import TelegramClient, events

import config
import executor
import journal
import logger
import dca_monitor
import pending_actions
import strategies
from classifier import classify, classify_async
from parser import (
    correct_tp_typos,
    is_canal1_signal_text,
    is_canal2_entry,
    levels_consistent_with_direction,
    parse_canal1_text,
    parse_canal2,
    predict_levels,
    predict_sl_from_entry,
    validate_range_vs_entry,
)
from state import Signal, state
from market_context import compute_market_context


def _sig_id(signal: Signal) -> str:
    """Identificador único para journal. Mismo formato que state._key()."""
    return f"{signal.channel}_{signal.message_id}"


def _text_sha1(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


def _iso_or_none(value) -> str | None:
    if value is None:
        return None
    try:
        return value.isoformat(timespec="seconds")
    except TypeError:
        return value.isoformat()
    except Exception:
        return str(value)


def _telegram_raw_payload(msg, channel: str, update_kind: str) -> dict:
    text = getattr(msg, "text", None) or getattr(msg, "message", None) or ""
    reply_to = getattr(msg, "reply_to", None)
    reply_id = getattr(reply_to, "reply_to_msg_id", None)
    sticker_id = None
    if getattr(msg, "sticker", None):
        try:
            sticker_id = msg.sticker.id
        except Exception:
            sticker_id = None

    return {
        "channel": channel,
        "message_id": getattr(msg, "id", None),
        "update_kind": update_kind,
        "date_utc": _iso_or_none(getattr(msg, "date", None)),
        "edit_date_utc": _iso_or_none(getattr(msg, "edit_date", None)),
        "is_edit": update_kind == "edit",
        "is_reply": reply_id is not None,
        "reply_to_msg_id": reply_id,
        "has_text": bool(text),
        "text": text,
        "text_len": len(text),
        "text_sha1": _text_sha1(text) if text else None,
        "has_media": bool(
            getattr(msg, "sticker", None)
            or getattr(msg, "photo", None)
            or getattr(msg, "document", None)
        ),
        "sticker_id": sticker_id,
        "has_photo": bool(getattr(msg, "photo", None)),
        "has_document": bool(getattr(msg, "document", None)),
    }


def _classification_source(classification: dict) -> str:
    if classification.get("_gemini_failed"):
        return "gemini_failed"
    if classification.get("_reason"):
        return "regex"
    return "gemini"


def _unhandled_management_fragments(raw_text: str, actions: list[str]) -> list[str]:
    if not raw_text:
        return []
    if any(str(action or "").startswith("CLOSE_") for action in actions):
        return []

    text = " ".join(raw_text.lower().split())
    patterns = [
        r"\bclose\s+for\s+now(?:\s+in\s+profit)?\b",
        r"\bclose\s+(?:your\s+|my\s+|the\s+)?first\s+(?:entries|entry)\b",
        r"\bclose\s+(?:all|everything|the\s+trade|this\s+trade)\b",
        r"\bclose\s+tp\s*\d+\b",
        r"\bout\s+(?:this\s+|of\s+this\s+|of\s+the\s+)?trade\b",
    ]
    fragments = []
    for pattern in patterns:
        match = re.search(pattern, text)
        if match and match.group(0) not in fragments:
            fragments.append(match.group(0))
    return fragments


def _log_telegram_understood(
    sig_id: str,
    *,
    channel: str,
    message_id: int | None,
    kind: str,
    parser: str,
    raw_text: str = "",
    parsed: dict | None = None,
    classifications=None,
    target_signal_id: str | None = None,
    tg_ts: str | None = None,
    is_edit: bool = False,
    is_reply: bool = False,
    reply_to_msg_id: int | None = None,
) -> None:
    try:
        parsed = parsed or {}
        if isinstance(classifications, dict):
            classifications = [classifications]
        classifications = list(classifications or [])
        rng = parsed.get("range")
        range_low = range_high = None
        if isinstance(rng, (list, tuple)) and len(rng) >= 2:
            range_low, range_high = rng[0], rng[1]

        confidences = [
            float(c.get("confidence"))
            for c in classifications
            if c.get("confidence") is not None
        ]
        sources = sorted({
            _classification_source(c)
            for c in classifications
        })
        actions = [c.get("action") for c in classifications]
        unhandled_fragments = []
        coverage_status = "not_evaluated"
        if kind == "management" and raw_text:
            unhandled_fragments = _unhandled_management_fragments(raw_text, actions)
            coverage_status = "partial" if unhandled_fragments else "covered"
        requires_review = any(
            c.get("_gemini_failed")
            or (
                c.get("action") not in ("INFORMATIONAL", None)
                and not c.get("_reason")
                and float(c.get("confidence") or 0.0) < 0.8
            )
            for c in classifications
        ) or bool(unhandled_fragments)

        journal.event(
            sig_id,
            "telegram_understood",
            channel=channel,
            message_id=message_id,
            target_signal_id=target_signal_id,
            kind=kind,
            parser=parser,
            parsed_keys=list(parsed.keys()),
            direction=parsed.get("direction"),
            range_low=range_low,
            range_high=range_high,
            tps=list(parsed.get("tps") or []),
            n_tps=len(parsed.get("tps") or []),
            sl=parsed.get("sl"),
            actions=actions,
            parser_sources=sources,
            confidence_min=min(confidences) if confidences else None,
            confidence_max=max(confidences) if confidences else None,
            gemini_failed=any(c.get("_gemini_failed") for c in classifications),
            requires_review=requires_review,
            is_edit=is_edit,
            is_reply=is_reply,
            reply_to_msg_id=reply_to_msg_id,
            tg_ts=tg_ts,
            raw_text_len=len(raw_text or ""),
            raw_text_sha1=_text_sha1(raw_text or ""),
            coverage_status=coverage_status,
            unhandled_text_fragments=unhandled_fragments,
        )
    except Exception as e:
        print(f"[TelegramPerception] telegram_understood error: {e}")


def _management_price_reference(signal: Signal) -> float | None:
    """Precio de referencia para expandir shorthand de gestion en XAUUSD."""
    candidates = [
        signal.market_fill_price,
        signal.range_low,
        signal.range_high,
        signal.sl,
        *list(signal.tps or []),
    ]
    for value in candidates:
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if 1000 <= price <= 9999:
            return price
    return None


def _normalize_management_sl_price(signal: Signal, price, raw_text: str = ""):
    """Normaliza precios abreviados de gestion: "SL to 85" -> 4585.

    Gold Standard a veces envia niveles cortos despues de una senal de XAUUSD
    ("Move SL to 75", "Move SL to 85"). En MT5 eso nunca puede ser 75.0/85.0:
    se interpreta contra la centena del precio real de la senal.
    """
    try:
        price_f = float(price)
    except (TypeError, ValueError):
        return None

    if 1000 <= price_f <= 9999:
        return price_f

    sig_id = _sig_id(signal)
    ref = _management_price_reference(signal)
    if ref is None:
        journal.anomaly(sig_id, "channel_msg", "warning",
                        "MOVE_SL_TO_PRICE con precio no absoluto y sin "
                        "referencia XAUUSD; accion ignorada",
                        raw_price=price_f, raw_snippet=raw_text[:120])
        return None

    if 0 <= price_f < 100:
        base = int(ref / 100) * 100
        normalized = base + price_f
        if abs(normalized - ref) > 50:
            normalized += 100 if normalized < ref else -100
        normalized = round(normalized, 3)
        journal.event(sig_id, "mgmt_price_normalized",
                      action="MOVE_SL_TO_PRICE",
                      raw_price=price_f,
                      normalized_price=normalized,
                      reference_price=ref,
                      raw_snippet=raw_text[:120])
        return normalized

    journal.anomaly(sig_id, "channel_msg", "warning",
                    "MOVE_SL_TO_PRICE con precio implausible para XAUUSD; "
                    "accion ignorada",
                    raw_price=price_f, reference_price=ref,
                    raw_snippet=raw_text[:120])
    return None


def _log_strategy_snapshot(signal: Signal, *, num_entries: int | None = None,
                           time_stop_min: int | None = None):
    """Registra la config efectiva de la senal para analisis posterior."""
    try:
        journal.event(
            _sig_id(signal), "strategy_snapshot",
            entry_mode=signal.entry_mode,
            num_entries=num_entries,
            target_tp_index=signal.target_tp_index,
            be_at_tp_index=signal.be_at_tp_index,
            time_stop_min=time_stop_min,
            time_stop_at=signal.time_stop_at,
            adverse_action=signal.adverse_action,
            effective_lot=signal.effective_lot,
            magic=signal.magic,
        )
    except Exception:
        pass


def _realized_pl(signal: Signal):
    """Best-effort: PnL realizado de los tickets de esta señal vía MT5 history_deals.

    Devuelve float o None si no se puede calcular. Usado por finalize_trade
    para registrar el PnL final cuando la señal cierra.

    Implementación: consulta por `position=ticket` en lugar de ventana temporal.
    En MT5, el ticket del deal de apertura se convierte en position_id, y
    history_deals_get(position=...) devuelve TODOS los deals (apertura+cierre)
    de esa posición sin depender de timezone. Esto evita el bug de naive UTC
    vs local time que dejaba la ventana desfasada y devolvía lista vacía
    (los deals canal2_40/46 del 2026-04-23 quedaron con pnl=None por esto).
    """
    try:
        import MetaTrader5 as mt5
        tickets = signal.all_filled_tickets
        if not tickets:
            return None
        total = 0.0
        found_any = False
        for ticket in tickets:
            deals = mt5.history_deals_get(position=ticket)
            if not deals:
                continue
            if any(d.magic == signal.magic for d in deals):
                # El deal de apertura conserva el magic del bot. Un cierre
                # manual desde MT5 puede venir con magic=0, pero sigue siendo
                # parte de esta posicion y debe contar en el P/L realizado.
                for d in deals:
                    total += d.profit + d.commission + d.swap
                found_any = True
        return round(total, 2) if found_any else None
    except Exception as e:
        print(f"[Journal] PnL realizado no calculable: {e}")
        return None


def _same_direction_overlap_candidate(new_signal: Signal, open_signals: list,
                                      window_s: float = 2.0):
    """Devuelve una senal abierta casi simultanea, mismo canal y direccion.

    Observabilidad pura: no bloquea ni deduplica. Sirve para marcar casos como
    canal2_12828/canal2_12829, donde dos BUY consecutivos se pisan y una
    puede quedar naked.
    """
    for existing in open_signals:
        if existing is new_signal:
            continue
        if existing.status != "open":
            continue
        if existing.channel != new_signal.channel:
            continue
        if existing.direction != new_signal.direction:
            continue
        delta_s = abs((new_signal.timestamp - existing.timestamp).total_seconds())
        if delta_s <= window_s:
            return existing
    return None


def _emit_same_direction_overlap_anomaly(new_signal: Signal,
                                         window_s: float = 2.0):
    existing = _same_direction_overlap_candidate(
        new_signal, state.open_signals(new_signal.channel), window_s)
    if existing is None:
        return

    sig_id = _sig_id(new_signal)
    previous_id = _sig_id(existing)
    delta_s = abs((new_signal.timestamp - existing.timestamp).total_seconds())
    journal.event(sig_id, "duplicate_signal_suspected",
                  previous_signal_id=previous_id,
                  previous_message_id=existing.message_id,
                  new_message_id=new_signal.message_id,
                  direction=new_signal.direction,
                  delta_s=round(delta_s, 3),
                  behavior="observability_only")
    journal.anomaly(sig_id, "channel_msg", "warning",
                    "senal casi duplicada: mismo canal/direccion dentro de "
                    f"{window_s:.1f}s; no se bloquea, se marca para analisis",
                    previous_signal_id=previous_id,
                    previous_message_id=existing.message_id,
                    new_message_id=new_signal.message_id,
                    direction=new_signal.direction,
                    delta_s=round(delta_s, 3))


def _canal2_duplicate_alias_candidate(message_id: int, direction: str,
                                      timestamp: datetime, parsed: dict,
                                      open_signals: list,
                                      window_s: float):
    """Find an older naked canal2 signal that should receive this msg as alias.

    This is stricter than observability-only duplicate detection: we only alias
    plain BUY/SELL NOW triggers, and only when the existing signal has no
    range/TP/SL yet. The goal is to avoid doubling exposure while still letting
    edits for the newer message_id update the already-open position.
    """
    if any(k in parsed for k in ("range", "tps", "sl")):
        return None

    probe = Signal(channel="canal2", message_id=message_id,
                   direction=direction, timestamp=timestamp)
    existing = _same_direction_overlap_candidate(probe, open_signals, window_s)
    if existing is None:
        return None
    if existing.range_low is not None or existing.tps or existing.sl is not None:
        return None
    return existing


def _register_canal2_duplicate_alias(existing: Signal, alias_message_id: int,
                                     raw_text: str, timestamp: datetime,
                                     window_s: float):
    state.alias(existing, alias_message_id)
    sig_id = _sig_id(existing)
    alias_sig_id = f"canal2_{alias_message_id}"
    delta_s = abs((timestamp - existing.timestamp).total_seconds())
    journal.event(sig_id, "canal2_duplicate_alias_registered",
                  alias_message_id=alias_message_id,
                  alias_signal_id=alias_sig_id,
                  direction=existing.direction,
                  delta_s=round(delta_s, 3),
                  raw_text=(raw_text or "")[:160],
                  behavior="alias_without_new_order")
    journal.event(alias_sig_id, "canal2_duplicate_alias_to_existing",
                  existing_signal_id=sig_id,
                  direction=existing.direction,
                  delta_s=round(delta_s, 3))
    journal.anomaly(sig_id, "channel_msg", "warning",
                    "canal2 duplicate BUY/SELL NOW aliased to existing naked "
                    "signal; skipped new market order",
                    alias_message_id=alias_message_id,
                    alias_signal_id=alias_sig_id,
                    direction=existing.direction,
                    delta_s=round(delta_s, 3),
                    window_s=window_s)


def _canal1_duplicate_sticker_candidate(message_id: int, direction: str,
                                        timestamp: datetime,
                                        open_signals: list,
                                        window_s: float):
    probe = Signal(channel="canal1", message_id=message_id,
                   direction=direction, timestamp=timestamp)
    existing = _same_direction_overlap_candidate(probe, open_signals, window_s)
    if existing is None:
        return None
    if existing.range_low is not None or existing.tps or existing.sl is not None:
        return None
    return existing


def _register_canal1_duplicate_sticker_alias(existing: Signal,
                                             alias_message_id: int,
                                             sticker_id: int,
                                             timestamp: datetime,
                                             window_s: float):
    state.alias(existing, alias_message_id)
    sig_id = _sig_id(existing)
    alias_sig_id = f"canal1_{alias_message_id}"
    delta_s = abs((timestamp - existing.timestamp).total_seconds())
    journal.event(sig_id, "canal1_duplicate_sticker_alias_registered",
                  alias_message_id=alias_message_id,
                  alias_signal_id=alias_sig_id,
                  direction=existing.direction,
                  sticker_id=sticker_id,
                  delta_s=round(delta_s, 3),
                  behavior="alias_without_new_order")
    journal.event(alias_sig_id, "canal1_duplicate_sticker_alias_to_existing",
                  existing_signal_id=sig_id,
                  direction=existing.direction,
                  sticker_id=sticker_id,
                  delta_s=round(delta_s, 3))
    journal.anomaly(sig_id, "channel_msg", "warning",
                    "canal1 duplicate sticker aliased to existing naked "
                    "signal; skipped new market order",
                    alias_message_id=alias_message_id,
                    alias_signal_id=alias_sig_id,
                    direction=existing.direction,
                    sticker_id=sticker_id,
                    delta_s=round(delta_s, 3),
                    window_s=window_s)


def _message_age_seconds(msg) -> float | None:
    ts = getattr(msg, "date", None) or getattr(msg, "edit_date", None)
    if ts is None:
        return None
    if getattr(ts, "tzinfo", None) is not None:
        ts = ts.replace(tzinfo=None)
    return (datetime.utcnow() - ts).total_seconds()


def _should_skip_stale_entry_signal(msg, max_age_s: float):
    age_s = _message_age_seconds(msg)
    if age_s is None or max_age_s <= 0:
        return False, age_s
    return age_s > max_age_s, age_s


def _log_stale_entry_skip(sig_id: str, channel: str, msg, trigger: str,
                          max_age_s: float, age_s: float | None,
                          direction: str | None = None,
                          text: str = "") -> None:
    preview = (text or "")[:250].replace("\n", " | ")
    payload = {
        "reason": "stale_entry_signal",
        "channel": channel,
        "trigger": trigger,
        "direction": direction,
        "age_s": round(age_s, 1) if age_s is not None else None,
        "max_age_s": max_age_s,
        "tg_ts": _msg_ts_iso(msg),
        "text_preview": preview,
    }
    journal.event(sig_id, "signal_skipped", **payload)
    journal.anomaly(
        sig_id, "channel_msg", "critical",
        "senal de entrada Telegram llego demasiado tarde; no se abre market",
        **payload,
    )


def _should_recover_canal2_orphan_entry_edit(msg, text: str,
                                             max_age_s: float):
    if msg.reply_to and msg.reply_to.reply_to_msg_id:
        return False, _message_age_seconds(msg)
    if not is_canal2_entry(text):
        return False, _message_age_seconds(msg)

    parsed = parse_canal2(text)
    if "direction" not in parsed:
        return False, _message_age_seconds(msg)

    age_s = _message_age_seconds(msg)
    if age_s is None:
        return False, None
    return age_s <= max_age_s, age_s


def _canal1_text_applied_summary(signal: Signal, parsed: dict) -> dict:
    has_range = "range" in parsed
    has_tps = bool(parsed.get("tps"))
    has_sl = parsed.get("sl") is not None
    return {
        "source": "canal1_text",
        "parsed_keys": list(parsed.keys()),
        "has_range": has_range,
        "has_tps": has_tps,
        "has_sl": has_sl,
        "n_tps": len(parsed.get("tps") or []),
        "levels_without_range": (not has_range and (has_tps or has_sl)),
        "entry_mode": signal.entry_mode,
        "current_n_tps": len(signal.tps),
        "current_has_sl": signal.sl is not None,
    }


def _breakeven_close_guard_applies(classification: dict, raw_text: str) -> bool:
    if classification.get("action") != "CLOSE_ALL":
        return False
    text = (raw_text or "").lower()
    if not text:
        return False

    re = __import__("re")
    be_or_protect = (
        re.search(r"\b(?:be|breakeven)\b", text)
        or re.search(r"risk.?free", text)
        or re.search(r"\b0\s*%?\s*risk\b", text)
        or re.search(r"\bprotect(?:ed|ion)?\b", text)
        or re.search(r"\bsecure\b", text)
    )
    if not be_or_protect:
        return False

    # Hard-close explicito: si el canal dice "close all", obedecemos incluso
    # si menciona BE. El guard cubre cierres semanticos tipo "close this trade
    # at breakeven / make it risk free", donde BE puede no ser nuestro BE.
    hard_close = re.search(
        r"\bclose\s+(?:all|everything|the\s+rest)\b"
        r"|\bout\s+of\s+trade\b"
        r"|\bsetup\s+(?:is\s+)?(?:invalid|invalidated)\b",
        text,
    )
    return hard_close is None


def _be_close_negative_decision(floating_pl: float,
                                tolerance_usd: float = 2.0) -> str:
    return "rescue_tp_be" if floating_pl < -abs(tolerance_usd) else "allow_close"


def _open_mt5_positions_for_signal(signal: Signal) -> list[dict] | None:
    """Return live MT5 positions that belong to this Signal.

    None means MT5 could not be queried. Empty list means verified clear.
    """
    import MetaTrader5 as _mt5

    positions = _mt5.positions_get()
    if positions is None:
        return None

    sig_id = _sig_id(signal)
    open_positions = []
    for pos in positions:
        if getattr(pos, "magic", None) != signal.magic:
            continue
        parsed = executor._parse_signal_id_from_comment(
            getattr(pos, "comment", "") or "")
        if not parsed:
            continue
        parsed_sig_id = f"{parsed[0]}_{parsed[1]}"
        if parsed_sig_id != sig_id:
            continue
        ticket = getattr(pos, "ticket", None)
        open_positions.append({
            "ticket": int(ticket) if ticket is not None else None,
            "comment": getattr(pos, "comment", None),
            "magic": getattr(pos, "magic", None),
            "symbol": getattr(pos, "symbol", None),
            "volume": getattr(pos, "volume", None),
            "price_open": getattr(pos, "price_open", None),
            "sl": getattr(pos, "sl", None),
            "tp": getattr(pos, "tp", None),
        })
    return open_positions


async def _finalize_integrity_allows(signal: Signal, closed_by: str) -> bool:
    sig_id = _sig_id(signal)
    try:
        open_positions = await _run(_open_mt5_positions_for_signal, signal)
    except Exception as e:
        signal.status = "open"
        journal.event(sig_id, "signal_integrity_snapshot",
                      phase="before_finalize",
                      can_finalize=False,
                      reason="mt5_positions_query_exception",
                      closed_by=closed_by,
                      error_type=type(e).__name__,
                      error=str(e)[:200])
        journal.anomaly(
            sig_id, "mt5", "critical",
            "No pude verificar posiciones abiertas antes de cerrar la senal",
            code="finalize_integrity_check_failed",
            phase="before_finalize",
            closed_by=closed_by,
            error_type=type(e).__name__,
            error=str(e)[:200],
        )
        return False

    if open_positions is None:
        signal.status = "open"
        journal.event(sig_id, "signal_integrity_snapshot",
                      phase="before_finalize",
                      can_finalize=False,
                      reason="mt5_positions_query_none",
                      closed_by=closed_by)
        journal.anomaly(
            sig_id, "mt5", "critical",
            "MT5 no devolvio posiciones al verificar cierre de senal",
            code="finalize_integrity_check_failed",
            phase="before_finalize",
            closed_by=closed_by,
        )
        return False

    if open_positions:
        open_tickets = [p.get("ticket") for p in open_positions]
        signal.status = "open"
        journal.event(sig_id, "signal_integrity_snapshot",
                      phase="before_finalize",
                      can_finalize=False,
                      reason="mt5_positions_still_open",
                      closed_by=closed_by,
                      open_tickets=open_tickets,
                      open_positions=open_positions,
                      state_tickets=list(signal.all_filled_tickets))
        journal.anomaly(
            sig_id, "outcome", "critical",
            "Finalize bloqueado: MT5 aun tiene posiciones vivas de esta senal",
            code="finalize_blocked_mt5_positions_open",
            phase="before_finalize",
            closed_by=closed_by,
            open_tickets=open_tickets,
            open_positions=open_positions,
            state_tickets=list(signal.all_filled_tickets),
        )
        return False

    return True


async def _finalize_signal(signal: Signal, closed_by: str, notes: str = ""):
    """Cierra el trade en el journal con la información disponible.

    Se llama cuando la señal pasa a status="closed" por una vía conocida
    (CLOSE_ALL, SL hit detectado, time-stop legacy). El PnL se intenta
    calcular vía MT5 history_deals; si falla, queda None y se podrá
    completar manualmente con el JSONL.

    Antes de finalize_trade emite un evento `pos_summary` con detalle de
    cada posicion (tipo, tp asignado, P&L individual). Util para diagnostico
    posterior — especialmente para validar la estrategia double_market.
    """
    try:
        sig_id = _sig_id(signal)
        if not await _finalize_integrity_allows(signal, closed_by):
            return

        now = datetime.utcnow()
        duration_s = (now - signal.timestamp).total_seconds()
        # Pequeña espera para que el deal de cierre llegue al historial de MT5
        await asyncio.sleep(0.5)

        # ── pos_summary: detalle por posicion (tipo + TP asignado + P&L) ──
        try:
            import MetaTrader5 as _mt5
            is_scale_out = (signal.entry_mode == "scale_out")
            positions_info = []
            for ticket in signal.all_filled_tickets:
                if ticket == signal.market_ticket:
                    pos_type = "market_a"
                elif ticket in signal.extra_market_tickets:
                    # En scale_out las extra son legs (1 por TP); en legacy
                    # doble market es la Pos B.
                    pos_type = "scale_out_leg" if is_scale_out else "market_b"
                else:
                    pos_type = "dca"
                tp_override = signal.tp_overrides.get(ticket)
                # Sumar P&L del ticket via history_deals
                deals = _mt5.history_deals_get(position=ticket)
                pl_ticket = sum(d.profit for d in deals) if deals else None
                close_price = next((d.price for d in (deals or [])
                                   if getattr(d, "entry", None) == 1), None)
                positions_info.append({
                    "ticket": ticket,
                    "type": pos_type,
                    "tp_override_idx": tp_override,
                    "pl": round(pl_ticket, 2) if pl_ticket is not None else None,
                    "close_price": close_price,
                })
            journal.event(sig_id, "pos_summary",
                          n_positions=len(positions_info),
                          positions=positions_info,
                          entry_mode=signal.entry_mode,
                          had_double_market=bool(signal.extra_market_tickets)
                                             and not is_scale_out)
        except Exception as e:
            print(f"[Journal] pos_summary error (no critico): {e}")

        pnl = _realized_pl(signal)
        journal.finalize_trade(
            sig_id,
            closed_at_utc=now.isoformat(timespec="milliseconds"),
            closed_by=closed_by,
            duration_sec=round(duration_s, 1),
            total_pnl_usd=pnl,
            sl_final=signal.sl,
            n_tickets_opened=len(signal.all_filled_tickets),
            notes=notes,
        )
    except Exception as e:
        print(f"[Journal] _finalize_signal error: {e}")

client = TelegramClient("signal_session", config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH)

# ─── Helper async→sync MT5 ────────────────────────────────────────────────────

async def _run(fn, *args, **kwargs):
    """Ejecuta una función MT5 síncrona en un thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))


# ─── Notificaciones al usuario por Telegram ───────────────────────────────────

# _notify_peer: legacy (fallback Telethon sin bot token). Mantenido para
# compatibilidad con test_notifications.py que lo resetea con _ln._notify_peer=None.
_notify_peer = None


async def notify(text: str):
    """Envía una notificación al usuario vía Telegram.

    Si config.TELEGRAM_BOT_TOKEN está configurado, usa el Bot HTTP API:
    el bot cangrejo manda el mensaje al grupo NOTIFY_CHAT_ID → push real en mobile.

    Si no hay token (o falla), cae al método legacy con client.send_message()
    (mensajes propios del usuario no generan push notification).

    Nunca lanza excepción: si Telegram falla, registra y continúa.

    INSTRUMENTACION (2026-05-14): cada intento se loguea al journal como
    notify_sent (OK) o notify_failed (con error). Permite diagnosticar
    despues si las notificaciones llegaron sin tener que mirar el cmd vivo.
    """
    token = getattr(config, "TELEGRAM_BOT_TOKEN", None)
    text_preview = text[:120].replace("\n", " | ")

    if token:
        import urllib.request
        import urllib.error
        import json as _json

        # Resolver chat_id: "me" → ID numérico del propio usuario vía Telethon.
        # El Bot API no entiende "me"; necesita el user_id del destinatario.
        chat_id = config.NOTIFY_CHAT_ID
        if isinstance(chat_id, str) and chat_id.strip().lower() == "me":
            try:
                me = await client.get_me()
                chat_id = me.id
            except Exception as exc:
                print(f"[Notify] No se pudo resolver 'me': {exc}")
                journal.event("bot", "notify_failed",
                              method="bot_api", reason=f"resolve_me: {exc}",
                              text_preview=text_preview)
                return

        def _http_send():
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = _json.dumps({
                "chat_id": chat_id,
                "text": text,
            }).encode("utf-8")
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"HTTP {exc.code}: {body}") from exc

        try:
            status = await asyncio.to_thread(_http_send)
            journal.event("bot", "notify_sent",
                          method="bot_api", chat_id=str(chat_id),
                          status=status, text_preview=text_preview,
                          text_len=len(text))
        except Exception as e:
            print(f"[Notify] ERROR enviando notificación (Bot API): {e} | texto={text[:80]!r}")
            journal.event("bot", "notify_failed",
                          method="bot_api", chat_id=str(chat_id),
                          error=str(e)[:300], text_preview=text_preview,
                          text_len=len(text))
        return

    # Fallback: Telethon del usuario (sin push notification en mobile)
    global _notify_peer
    try:
        if _notify_peer is None:
            chat = config.NOTIFY_CHAT_ID
            if isinstance(chat, str) and chat.lstrip("-").isdigit():
                chat = int(chat)
            _notify_peer = await client.get_entity(chat)
        await client.send_message(_notify_peer, text)
        journal.event("bot", "notify_sent",
                      method="telethon_fallback", text_preview=text_preview,
                      text_len=len(text),
                      warning="NO push notification on mobile (telethon fallback)")
    except Exception as e:
        print(f"[Notify] ERROR enviando notificación (fallback Telethon): {e} | texto={text[:80]!r}")
        journal.event("bot", "notify_failed",
                      method="telethon_fallback", error=str(e)[:300],
                      text_preview=text_preview, text_len=len(text))


# ─── Notify contextual: decisión ambigua ──────────────────────────────────────
#
# Cuando el classifier (Gemini) devuelve una acción no-INFO con confianza
# 0.5-0.8 (zona ambigua), no aplicamos automáticamente — mandamos al usuario
# un resumen completo del estado del trade + lo que el trader dice + propuesta
# de Gemini para que decida. Mobile-friendly, todo en pantalla.

async def notify_ambiguous_decision(signal: "Signal", classification: dict,
                                    raw_text: str):
    """Resumen contextual al usuario cuando hay duda de qué hacer.

    Recoge un TradeContext fresco (consulta MT5), formatea info clave +
    quote del mensaje del trader + propuesta de Gemini con su reasoning,
    y manda por notify(). El bot NO actúa — espera decisión del usuario.

    No bloquea: si Telegram falla, log y continúa.
    """
    try:
        ctx = signal.build_context()

        # Marca TPs hit (precio cruzó en dirección correcta)
        def _tp_hit_marker(tp_value: float) -> str:
            cur = ctx.current_price
            if cur is None or tp_value is None:
                return ""
            if ctx.direction == "BUY" and cur >= tp_value:
                return "✅"
            if ctx.direction == "SELL" and cur <= tp_value:
                return "✅"
            return ""

        tps_str = " ".join(
            f"TP{i+1}({tp:.1f}){_tp_hit_marker(tp)}"
            for i, tp in enumerate(ctx.tps[:5])
        ) if ctx.tps else "(sin TPs)"

        sl_str = f"{ctx.sl:.2f}" if ctx.sl else "(sin SL)"
        if ctx.be_armed and ctx.sl is not None:
            sl_str += " [BE armado]"

        cur_str = f"{ctx.current_price:.2f}" if ctx.current_price else "n/a"
        action = classification.get("action", "?")
        conf = classification.get("confidence", 0)
        reasoning = classification.get("reasoning") or classification.get("_reason") or "-"

        # Trim del mensaje del trader para no inflar
        trader_msg = (raw_text or "").strip().replace("\n\n", "\n")
        if len(trader_msg) > 250:
            trader_msg = trader_msg[:247] + "..."

        text = (
            f"⚠️ DECISIÓN REQUERIDA — {ctx.signal_id}\n"
            f"\n"
            f"📊 {ctx.direction} @{ctx.entry_price} (hace {ctx.elapsed_min:.0f} min)\n"
            f"💰 P&L floating: ${ctx.floating_pnl_total:+.2f}\n"
            f"🎯 Posiciones: {ctx.n_open} abiertas de {ctx.n_initial} originales\n"
            f"\n"
            f"📈 TPs: {tps_str}\n"
            f"🛡️ SL: {sl_str}\n"
            f"📍 Precio actual: {cur_str}\n"
            f"\n"
            f"💬 Trader dice:\n"
            f"\"{trader_msg}\"\n"
            f"\n"
            f"🤖 Gemini propone: {action}\n"
            f"   Confianza: {conf:.2f} (zona ambigua 0.5-0.8)\n"
            f"   Razón: {reasoning}\n"
            f"\n"
            f"❓ Opciones:\n"
            f"  1️⃣ Cerrar todo ahora → asegurar ${ctx.floating_pnl_total:+.2f}\n"
            f"  2️⃣ Dejar correr según TPs/SL actuales\n"
            f"  3️⃣ Mover SL al precio actual ({cur_str}) para asegurar\n"
            f"\n"
            f"(Bot NO actuará — decide y ejecuta tú en MT5.\n"
            f"En próxima fase añadiremos respuesta automática)"
        )
        print(f"[Notify Ambig] enviado resumen para {ctx.signal_id} "
              f"action={action} conf={conf:.2f}")
        try:
            journal.event(ctx.signal_id, "ambiguous_decision_notified",
                          action=action, confidence=conf, reasoning=reasoning,
                          n_open=ctx.n_open, floating_pnl=ctx.floating_pnl_total,
                          current_price=ctx.current_price)
        except Exception:
            pass
        await notify(text)
    except Exception as e:
        print(f"[Notify Ambig] error enviando notify: {e}")


# ─── Deduplicación de eventos Edit ────────────────────────────────────────────
#
# Telethon a veces emite el mismo MessageEdited más de una vez (mismo
# message_id, misma edit_date) cuando hay reconexión o latencia. Sin
# dedup, cada repetición vuelve a llamar al parser y, si los TPs son
# distintos a los previos (no lo son, pero el bot no lo sabe), reaplica
# SL/TP innecesariamente. Más grave: si la edición coincide con la
# llegada del rango, _handle_range_arrival_safety se ejecutaría dos veces
# (range_safety_applied previene la lógica, pero nos ahorramos el trabajo).
#
# Clave: (channel, message_id, edit_date_isoformat). Set acotado para no
# crecer indefinidamente — solo conservamos las últimas N entradas.

_seen_edits: set[tuple] = set()
_seen_edits_order: list[tuple] = []
_SEEN_EDITS_MAX = 1000

# ─── Deduplicación de mensajes NUEVOS (poller + event system) ─────────────────
#
# El poller activo y los handlers de Telethon pueden ver el mismo mensaje
# nuevo. _new_msg_already_seen garantiza que solo uno de los dos lo procesa.
# Misma mecánica que _seen_edits pero sin edit_date (los nuevos no la tienen).

_seen_new_msg_ids: set[tuple] = set()
_seen_new_msgs_order: list[tuple] = []
_SEEN_NEW_MAX = 500

# Dedup de acciones de gestion ya clasificadas. Telegram puede entregar el
# mismo reply/edit por poller y handler, o repetirlo en segundos. Las acciones
# idempotentes generan retcode=10025 y ensucian el expediente; las de cierre
# pueden provocar ruido peor. Mantenemos una ventana corta por signal+texto+accion.
_seen_management_actions: dict[tuple, datetime] = {}
_MGMT_DUP_WINDOW_S = 45.0
_MGMT_DUP_MAX = 1000

# Canal2 puede editar el mensaje mientras la apertura market original sigue
# bloqueada en MT5. Esos edits no son huerfanos reales: se cachean y se aplican
# cuando la apertura termina, evitando duplicar la entrada.
_canal2_opening_msg_ids: set[int] = set()
_deferred_canal2_entry_edits: dict[int, dict] = {}


def _new_msg_already_seen(channel: str, msg_id: int) -> bool:
    """True si este mensaje nuevo ya fue despachado. Lo marca como visto si no."""
    key = (channel, msg_id)
    if key in _seen_new_msg_ids:
        return True
    _seen_new_msg_ids.add(key)
    _seen_new_msgs_order.append(key)
    if len(_seen_new_msgs_order) > _SEEN_NEW_MAX:
        old = _seen_new_msgs_order.pop(0)
        _seen_new_msg_ids.discard(old)
    return False


def _canal2_open_started(msg_id: int) -> None:
    _canal2_opening_msg_ids.add(msg_id)


def _canal2_open_claim(msg_id: int) -> bool:
    if msg_id in _canal2_opening_msg_ids:
        return False
    _canal2_opening_msg_ids.add(msg_id)
    return True


def _canal2_open_finished(msg_id: int) -> None:
    _canal2_opening_msg_ids.discard(msg_id)


def _canal2_open_in_progress(msg_id: int) -> bool:
    return msg_id in _canal2_opening_msg_ids


def _defer_canal2_entry_edit(msg, text: str) -> None:
    edit_ts = getattr(msg, "edit_date", None) or getattr(msg, "date", None)
    _deferred_canal2_entry_edits[msg.id] = {
        "text": text,
        "tg_ts": edit_ts.isoformat(timespec="seconds") if edit_ts else None,
    }


def _pop_deferred_canal2_entry_edit(msg_id: int):
    return _deferred_canal2_entry_edits.pop(msg_id, None)


def _edit_already_seen(channel: str, msg) -> bool:
    """True si este edit ya fue procesado. Marca como visto si no."""
    edit_date = getattr(msg, "edit_date", None)
    if edit_date is None:
        return False  # sin edit_date no podemos dedupar — procesa siempre
    key = (channel, msg.id, edit_date.isoformat())
    if key in _seen_edits:
        return True
    _seen_edits.add(key)
    _seen_edits_order.append(key)
    if len(_seen_edits_order) > _SEEN_EDITS_MAX:
        old = _seen_edits_order.pop(0)
        _seen_edits.discard(old)
    return False


# ─── Helpers de configuración por canal ───────────────────────────────────────

def _normalise_management_text(raw_text: str) -> str:
    return " ".join((raw_text or "").split()).lower()[:240]


def _management_price_key(price):
    if price is None:
        return None
    try:
        return round(float(price), 5)
    except (TypeError, ValueError):
        return str(price)


def _management_action_already_seen(sig_id: str, action: str, raw_text: str,
                                    price=None, *, now: datetime | None = None,
                                    window_s: float = _MGMT_DUP_WINDOW_S) -> bool:
    text_key = _normalise_management_text(raw_text)
    if not text_key:
        return False
    now = now or datetime.utcnow()
    key = (sig_id, action or "", text_key, _management_price_key(price))
    last = _seen_management_actions.get(key)
    if last is not None and (now - last).total_seconds() <= window_s:
        return True
    _seen_management_actions[key] = now
    if len(_seen_management_actions) > _MGMT_DUP_MAX:
        cutoff_s = window_s * 4
        for old_key, seen_at in list(_seen_management_actions.items()):
            if (now - seen_at).total_seconds() > cutoff_s:
                del _seen_management_actions[old_key]
    return False


def _msg_text(msg) -> str:
    return getattr(msg, "text", None) or getattr(msg, "message", None) or ""


def _msg_ts_iso(msg) -> str | None:
    ts = getattr(msg, "edit_date", None) or getattr(msg, "date", None)
    return ts.isoformat(timespec="seconds") if ts else None


async def _process_management_reply_edit(msg, channel: str,
                                         label: str = "") -> bool:
    """Procesa edits en replies de gestion.

    En un edit de reply, msg.id es el del reply de gestion. La senal real esta
    en reply_to_msg_id, por eso state.get(channel, msg.id) lo ignoraba.
    """
    reply_to = getattr(msg, "reply_to", None)
    reply_id = getattr(reply_to, "reply_to_msg_id", None)
    if not reply_id:
        return False

    other_channel = "canal1" if channel == "canal2" else "canal2"
    sig = state.get(channel, reply_id) or state.get(other_channel, reply_id)
    if sig is None or sig.status != "open":
        return False

    text = _msg_text(msg)
    if not text:
        return True

    if _edit_already_seen(channel, msg):
        if label:
            print(f"[{label}] Edit gestion duplicado msg={msg.id} "
                  f"edit_date={getattr(msg, 'edit_date', None)} ignorado")
        return True

    tg_ts = _msg_ts_iso(msg)
    if channel == "canal2":
        parsed = parse_canal2(text)
        if parsed.get("tps") or parsed.get("sl"):
            await _update_signal_from_parsed(sig, parsed, tg_ts=tg_ts)

    cl = await classify_async(text, signal=sig)
    await _execute_action(sig, cl, raw_text=text, tg_ts=tg_ts)
    return True


def _num_entries_for_channel(channel: str) -> int:
    """Número de entradas (market + limits) configurado para este canal."""
    if channel == "canal1":
        return config.STRATEGY_C1_NUM_ENTRIES
    return config.STRATEGY_C2_NUM_ENTRIES


# --- Monitor de trade vivo ---------------------------------------------------

async def _place_dca(signal: Signal):
    """Arranca el monitor vivo de BE/time-stop sin generar niveles DCA.

    El nombre se mantiene temporalmente por compatibilidad con Signal.dca_placed
    y con los handlers existentes. El runtime activo usa scale_out; las legs
    adicionales se abren a mercado en _open_extra_legs().
    """
    if signal.dca_placed:
        return
    signal.dca_placed = True

    needs_monitor_anyway = (
        signal.time_stop_at is not None or signal.be_at_tp_index is not None
    )
    if not needs_monitor_anyway:
        print("[Trade Monitor] Sin BE/time-stop configurados; monitor omitido.")
        return

    print(f"[Trade Monitor] Activo para BE/time-stop "
          f"(be@idx={signal.be_at_tp_index}, ts={signal.time_stop_at})")
    dca_monitor.start(signal, [])
    return

async def _open_extra_legs(sig: Signal, msg_id: int) -> None:
    """Abre las posiciones market ADICIONALES a la inicial, segun el modo.

    Modo "scale_out" (semana de prueba 2026-05-17): abre N-1 posiciones
    market extra de golpe (N = STRATEGY_Cx_NUM_ENTRIES), todas a LOT_SIZE,
    SIN tp_override. El escalonado normal de _apply_sl_tp asigna pos k -> TPk
    => una posicion por TP, que se cierran en parciales segun el precio
    toca cada TP. Sin DCA, sin Pos B con override.

    Modo legacy "doble market" (STRATEGY_DOUBLE_MARKET_ENABLED): 1 sola
    posicion extra (Pos B) con TP override a STRATEGY_DOUBLE_MARKET_TP_INDEX.

    Se llama justo tras abrir el market inicial, antes de que lleguen los
    niveles: las legs abren "desnudas" y _apply_sl_tp les pone SL/TP al
    llegar el rango (mismo patron que el market inicial).

    El modo se lee de config (no de sig.entry_mode) porque en canal 1 el
    entry_mode aun no esta fijado en este punto del flujo.
    """
    channel = sig.channel
    entry_mode = (config.STRATEGY_C1_ENTRY_MODE if channel == "canal1"
                  else config.STRATEGY_C2_ENTRY_MODE)
    magic = config.magic_for(channel)
    cprefix = "c1" if channel == "canal1" else "c2"
    sig_id = _sig_id(sig)

    # ── Modo scale_out: N-1 legs market, escalonado natural (sin override) ──
    if entry_mode == "scale_out":
        n_legs = _num_entries_for_channel(channel)
        opened = 0
        n_attempted = max(0, n_legs - 1)
        for n in range(1, n_legs):   # n = 1..N-1 (la leg 0 es el market inicial)
            result = await _run(executor.open_market_with_fill, sig.direction,
                                config.LOT_SIZE, None, None,
                                f"{cprefix}_{msg_id}_B{n}", magic)
            if not result:
                journal.event(sig_id, "scale_out_leg_fill_failed", leg=n)
                print(f"[{channel}] Scale-out leg B{n} FALLO")
                continue
            ticket_l, fill_l = result
            sig.extra_market_tickets.append(ticket_l)
            sig.extra_market_fill_prices.append(fill_l)
            opened += 1
            journal.event(sig_id, "scale_out_leg_filled",
                          ticket=ticket_l, price=fill_l, leg=n,
                          fill_a=sig.market_fill_price)
        print(f"[{channel}] Scale-out: {opened + 1}/{n_legs} posiciones market "
              f"abiertas (1 inicial + {opened} legs), TPs escalonados")

        # C1 (Batch C): si faltaron legs por fallo de fill, emitir anomaly
        # según severidad. Antes solo se logueaban los `*_fill_failed` por
        # leg y la señal seguía degenerada en silencio.
        summary = _scale_out_fill_summary(n_attempted, opened)
        if summary["severity"]:
            journal.anomaly(sig_id, "fill", summary["severity"],
                            f"scale_out partial fill — {opened}/{n_attempted} "
                            f"legs extra (1 inicial + {opened} de "
                            f"{n_attempted} extra = {opened+1}/{n_legs} total)",
                            channel=channel,
                            n_legs_expected=n_legs,
                            n_legs_filled=opened + 1,
                            n_extra_attempted=n_attempted,
                            n_extra_filled=opened,
                            fill_ratio=round(summary["fill_ratio"], 2))
        return

    # ── Modo legacy doble market: 1 Pos B con TP override ──
    if not config.STRATEGY_DOUBLE_MARKET_ENABLED:
        return

    result_b = await _run(executor.open_market_with_fill, sig.direction,
                          config.LOT_SIZE, None, None,
                          f"{cprefix}_{msg_id}_B", magic)
    if not result_b:
        journal.event(sig_id, "market_b_fill_failed",
                      reason="executor.open_market returned None")
        print(f"[{channel}] Market B FAILED — operando solo con Market A")
        return

    ticket_b, fill_price_b = result_b
    sig.extra_market_tickets.append(ticket_b)
    sig.extra_market_fill_prices.append(fill_price_b)
    sig.tp_overrides[ticket_b] = config.STRATEGY_DOUBLE_MARKET_TP_INDEX

    journal.event(sig_id, "market_b_filled",
                  ticket=ticket_b, price=fill_price_b,
                  tp_index=config.STRATEGY_DOUBLE_MARKET_TP_INDEX,
                  fill_a=sig.market_fill_price,
                  spread_a_to_b=(fill_price_b - sig.market_fill_price)
                                 if sig.market_fill_price else None)
    print(f"[{channel}] Market B abierto: ticket={ticket_b} @ {fill_price_b} "
          f"(TP override → TP{config.STRATEGY_DOUBLE_MARKET_TP_INDEX + 1})")


async def _apply_sl_tp(signal: Signal):
    """Aplica SL común y TP por posición a TODAS las entradas ya abiertas.

    Modos de asignación de TP (delegado a Signal.tp_for_position):
      - target_tp_index None: ESCALONADO. Posición i → tps[i] (market→TP1,
        DCA1→TP2, etc.), con cap opcional por max_tp_index.
      - target_tp_index N: TODAS las posiciones cierran al mismo TP fijo.
      - tp_overrides[ticket]: override per-ticket (usado por rescue_market
        y double_market para asignar TP especifico).

    SL: si BE ya está armado (signal.be_armed=True), preserva el BE de cada
    ticket (SL = su propio entry) en vez de re-aplicar signal.sl del proveedor.
    Sin esto, cada vez que llega un edit del canal con TPs/SL nuevos, _apply_sl_tp
    DESHACIA el BE retrocediendo SL al del proveedor (visto sesion 2026-05-12).

    TP PERSECUCION (commit 2026-05-15): si el precio actual ya paso el TP
    objetivo de una posicion, MT5 rechazaria ese TP con 10016 (lado
    equivocado). En vez de insistir 600 veces (caso real canal2_12405,
    pos quedo sin TP y cerro en BE=$0 mientras el precio hacia TP5):
      - si hay un TP mas alto AUN por delante del precio → usar ese
        (la posicion persigue un objetivo mas ambicioso).
      - si el precio ya paso TODOS los TPs → cerrar la pos a mercado
        (cobra el precio actual, mejor que cualquier TP).

    Cada modify va por la cola con reintentos tick-a-tick."""
    if signal.sl is None and not signal.tps:
        return

    # Tick actual + stops_level — para la logica de persecucion de TP.
    tick = None
    min_dist = 0.30
    try:
        import MetaTrader5 as _mt5
        tick = await _run(_mt5.symbol_info_tick, config.MT5_SYMBOL)
        si = await _run(_mt5.symbol_info, config.MT5_SYMBOL)
        if si:
            min_dist = si.trade_stops_level * si.point
    except Exception:
        pass
    direction = signal.direction

    for i, t in enumerate(signal.all_filled_tickets):
        # TP segun override o escalonado
        if t in signal.tp_overrides and signal.tps:
            override_idx = signal.tp_overrides[t]
            override_idx = max(0, min(override_idx, len(signal.tps) - 1))
            tp_i = signal.tps[override_idx]
        else:
            tp_i = signal.tp_for_position(i)

        # ── TP PERSECUCION ───────────────────────────────────────────────
        if tp_i is not None and tick is not None and signal.tps:
            if direction == "BUY":
                # TP valido si esta a > min_dist por encima del bid actual
                tp_valido = tp_i > tick.bid + min_dist
                tps_por_delante = [tp for tp in signal.tps
                                   if tp > tick.bid + min_dist]
                next_tp = max(tps_por_delante) if tps_por_delante else None
                cur_price = tick.bid
            else:  # SELL
                tp_valido = tp_i < tick.ask - min_dist
                tps_por_delante = [tp for tp in signal.tps
                                   if tp < tick.ask - min_dist]
                next_tp = min(tps_por_delante) if tps_por_delante else None
                cur_price = tick.ask

            if not tp_valido:
                if next_tp is not None:
                    # Perseguir: usar el TP mas ambicioso aun por delante
                    print(f"[TP-chase] #{t}: precio {cur_price:.2f} ya paso "
                          f"TP={tp_i} → persigo TP por delante = {next_tp}")
                    journal.event(_sig_id(signal), "tp_chase_advanced",
                                  ticket=t, old_tp=tp_i, new_tp=next_tp,
                                  current_price=cur_price, direction=direction)
                    tp_i = next_tp
                else:
                    # El precio paso el ultimo TP CONOCIDO. Pero ¿es la lista
                    # COMPLETA o el canal aun va a mandar mas TPs?
                    #
                    # Fix 2026-05-16 (punto 3 capa B): el canal manda TPs en
                    # oleadas. Cerrar a mercado en cuanto se pasa el ultimo TP
                    # conocido es prematuro si la lista esta incompleta.
                    # Caso real canal2_12513: cerro 2 markets con [4529] solo.
                    #
                    # Solo cerramos a mercado si la lista parece DEFINITIVA:
                    #   - NO son TPs del predictor (levels_predicted=False), Y
                    #   - hay >=4 TPs (el canal 2 manda 4-5; con 4+ asumimos
                    #     lista razonablemente completa).
                    # Si la lista parece incompleta → NO cerrar: dejar SL
                    # protector trailing y esperar los TPs que faltan (el
                    # tp_chase los recogera en la proxima llamada).
                    lista_completa = (not signal.levels_predicted
                                      and len(signal.tps) >= 4)
                    if lista_completa:
                        print(f"[TP-chase] #{t}: precio {cur_price:.2f} paso TODOS "
                              f"los TPs (lista completa {len(signal.tps)} TPs) "
                              f"→ cierre a mercado")
                        journal.event(_sig_id(signal), "tp_chase_close_market",
                                      ticket=t, last_tp_objetivo=tp_i,
                                      current_price=cur_price, direction=direction,
                                      tps=list(signal.tps))
                        pending_actions.enqueue_close_position(
                            signal, t,
                            label=f"TP_CHASE_CLOSE #{t} (precio paso todos los TPs)")
                        continue  # no encolar modify — ya cerramos esta pos
                    else:
                        # Lista incompleta → trailing protector + esperar
                        if direction == "BUY":
                            trailing_sl = round(tick.bid - min_dist - 0.5, 2)
                        else:
                            trailing_sl = round(tick.ask + min_dist + 0.5, 2)
                        print(f"[TP-chase] #{t}: precio paso ultimo TP conocido "
                              f"pero lista INCOMPLETA ({len(signal.tps)} TPs, "
                              f"predicted={signal.levels_predicted}) → NO cierro, "
                              f"SL trailing {trailing_sl} + espero mas TPs")
                        journal.event(_sig_id(signal), "tp_chase_hold_incomplete",
                                      ticket=t, current_price=cur_price,
                                      n_tps=len(signal.tps),
                                      levels_predicted=signal.levels_predicted,
                                      trailing_sl=trailing_sl)
                        pending_actions.enqueue_modify_sl(
                            signal, t, trailing_sl,
                            label=f"TP_CHASE_TRAIL #{t}→{trailing_sl} (lista TPs incompleta)")
                        continue  # no encolar el modify normal

        # SL: si BE armado, usar entry de este ticket. Si no, signal.sl.
        if signal.be_armed:
            sl_to_apply = await _run(executor.entry_price, t)
            if sl_to_apply is None:
                # Sin entry legible: aplicar solo TP (no tocar SL existente)
                if tp_i is not None:
                    pending_actions.enqueue_modify_tp(
                        signal, t, tp_i, label=f"TP[{i}]→{tp_i} #{t} (BE preserved)"
                    )
                continue
        else:
            sl_to_apply = signal.sl

        if sl_to_apply is not None and tp_i is not None:
            label_suffix = " (BE)" if signal.be_armed else ""
            pending_actions.enqueue_modify_sltp(
                signal, t, sl_to_apply, tp_i,
                label=f"SL/TP[{i}]→{tp_i} #{t}{label_suffix}"
            )
        elif sl_to_apply is not None:
            pending_actions.enqueue_modify_sl(signal, t, sl_to_apply, label=f"SL #{t}")
        elif tp_i is not None:
            pending_actions.enqueue_modify_tp(signal, t, tp_i, label=f"TP[{i}]→{tp_i} #{t}")


# ─── Gestión de señal completa ────────────────────────────────────────────────

async def _handle_range_arrival_safety(signal: Signal, lo: float, hi: float) -> bool:
    """Etapa 2 de la lógica layered: decisión al llegar el rango.

    Devuelve True si la señal se cerró (caller debe abortar el resto del flow)
    o False si debe continuar (aplicar SL/TPs y limits).

    Tolerancia: si |entry - extremo del rango| ≤ STRATEGY_RANGE_TOLERANCE_USD,
    se trata como A_inside (evita falsos positivos por ruido de spread cuando
    el entry está apenas fuera del rango — visto en logs reales con 0.45$ de
    diferencia clasificado como B_favorable y aplicando lógica equivocada).

    Casos:
      A_inside     → continuar normal (return False).
      B_favorable  → continuar normal: aplica SL/TP y monitor BE/time-stop si procede.
      C_adverse    → aplicar `signal.adverse_action`:
        - rescue_market      → mantener market original + abrir un market nuevo
                                en precio actual (entrada óptima de rescate);
                                SL común; el rescue recibe el ÚLTIMO TP via
                                tp_overrides (mejor entrada → mayor recorrido).
                                ⭐ Default consensuado.
        - close              → cerrar market + cancelar pending. (legacy)
        - hold_with_limits   → legacy desactivado; se trata como hold_no_limits.
        - hold_no_limits     → mantener market sin limits. SL del proveedor.
        - hold_sl_to_extreme → mantener market con SL movido al extremo
    """
    if signal.range_safety_applied:
        return False  # ya decidido en un edit anterior
    signal.range_safety_applied = True

    if not signal.market_ticket:
        return False  # nada que evaluar (no se llegó a abrir market)

    entry = await _run(executor.entry_price, signal.market_ticket)
    if entry is None:
        return False  # no podemos evaluar — dejar que el flow normal continúe

    # Determinar caso A/B/C con tolerancia
    tol = config.STRATEGY_RANGE_TOLERANCE_USD
    if (lo - tol) <= entry <= (hi + tol):
        case = "A_inside"
    else:
        if signal.direction == "BUY":
            favorable = entry > hi  # entró por encima → ya en profit en BUY
        else:  # SELL
            favorable = entry < lo  # entró por debajo → ya en profit en SELL
        case = "B_favorable" if favorable else "C_adverse"

    print(f"[Layered] entry={entry:.2f} rango=[{lo}-{hi}] dir={signal.direction} "
          f"tol=±{tol} → caso {case}")

    sig_id = _sig_id(signal)
    journal.event(sig_id, "layered_decision",
                  case=case, entry=entry, range_low=lo, range_high=hi,
                  tolerance=tol, action_planned=signal.adverse_action if case == "C_adverse" else "normal_flow")
    journal.update_trade(sig_id, range_decision=case)

    if case in ("A_inside", "B_favorable"):
        return False  # flujo normal: aplica SL/TP y arranca monitor si procede

    # ── Caso C adverso ──
    action = signal.adverse_action

    if action == "rescue_market":
        # Mantener el original + abrir nuevo market a precio actual.
        # El rescue es la entrada óptima (precio adverso = mejor para BUY/SELL).
        # SL común, TP del rescue = último TP disponible (max recorrido).
        try:
            tick = await _run(executor.current_tick)
        except Exception as e:
            print(f"[Layered] rescue_market: no pude leer tick ({e}) → fallback close")
            action = "close"
        else:
            current_price = (tick["ask"] if signal.direction == "BUY"
                             else tick["bid"])
            # Sanity: el precio actual TIENE que ser adverso vs original entry
            adverse_now = ((signal.direction == "BUY" and current_price < entry)
                           or (signal.direction == "SELL" and current_price > entry))
            if not adverse_now:
                # Precio ya se movió a favor → no hace falta rescue. PERO el
                # SL del proveedor está calculado para una entry en el rango,
                # y NUESTRO entry está desplazado. Si quedó al lado equivocado,
                # MT5 lo rechazará 27.000 veces y bloqueará el event loop
                # (visto en sesión 2026-05-06, canal2_12161). Adaptamos:
                # preservamos la distancia que el trader le dio al SL pero la
                # aplicamos desde NUESTRO entry desplazado.
                sl_invalid = (signal.sl is not None and (
                    (signal.direction == "BUY" and signal.sl >= entry) or
                    (signal.direction == "SELL" and signal.sl <= entry)
                ))
                if sl_invalid:
                    original_sl = signal.sl
                    if signal.direction == "BUY":
                        # SL del trader estaba bajo el rango → distancia = lo - sl
                        sl_dist = max(0.0, lo - original_sl)
                        new_sl = entry - sl_dist
                    else:  # SELL
                        # SL del trader estaba sobre el rango → distancia = sl - hi
                        sl_dist = max(0.0, original_sl - hi)
                        new_sl = entry + sl_dist
                    print(f"[Layered] rescue omitido (precio favorable). SL del "
                          f"proveedor {original_sl} inválido para entry "
                          f"{entry:.2f} → adapto a {new_sl:.2f} "
                          f"(preserva distancia ${sl_dist:.2f})")
                    signal.sl = new_sl
                    journal.event(sig_id, "sl_adapted_for_displaced_entry",
                                  original_sl=original_sl,
                                  new_sl=round(new_sl, 2),
                                  sl_dist_usd=round(sl_dist, 2),
                                  entry=round(entry, 2),
                                  range_low=lo, range_high=hi,
                                  direction=signal.direction)
                else:
                    print(f"[Layered] rescue omitido (precio favorable). SL del "
                          f"proveedor {signal.sl} ya es válido para entry "
                          f"{entry:.2f} → mantengo.")
                signal.entry_mode = "market_only"  # solo el original
                return False

            effective_lot = signal.effective_lot
            magic = signal.magic
            comment = f"c{signal.channel[-1]}_{signal.message_id}_rescue"
            rescue_ticket = await _run(
                executor.open_market, signal.direction, effective_lot,
                signal.sl, None, comment, magic,
            )
            if rescue_ticket:
                signal.dca_tickets.append(rescue_ticket)
                # Override: rescue ticket → último TP disponible (cuando los
                # tps reales lleguen, _apply_sl_tp respeta este override).
                # Usamos un índice grande; _apply_sl_tp lo recortará a len-1.
                signal.tp_overrides[rescue_ticket] = 99
                signal.entry_mode = "market_only"  # no abrir legs adicionales
                print(f"[Layered] caso C → rescue_market #{rescue_ticket} a "
                      f"{current_price:.2f} (override TP=último, lot={effective_lot})")
                journal.event(sig_id, "rescue_market_opened",
                              ticket=rescue_ticket, price=current_price,
                              original_entry=entry, original_ticket=signal.market_ticket,
                              tp_override="last")
                return False  # flujo normal aplica SL/TP a ambos tickets
            else:
                print(f"[Layered] rescue_market: open_market falló → fallback close")
                journal.event(sig_id, "rescue_market_failed",
                              reason="open_market returned None")
                action = "close"

    if action == "close":
        print(f"[Layered] caso C → close market {signal.market_ticket}")
        pending_actions.enqueue_close_position(
            signal, signal.market_ticket,
            label=f"layered C close: entry {entry:.2f} fuera de [{lo}-{hi}]"
        )
        for t in signal.pending_tickets:
            pending_actions.enqueue_cancel_pending(
                signal, t, label=f"layered C close pend #{t}"
            )
        signal.status = "closed"
        signal.dca_placed = True  # evita que _place_dca arranque monitor
        return True

    elif action == "hold_with_limits":
        print("[Layered] caso C -> hold_with_limits legacy desactivado; mantengo sin limits")
        signal.entry_mode = "market_only"
        return False  # flujo normal aplica SL/TP y arranca monitor si procede

    elif action == "hold_no_limits":
        print(f"[Layered] caso C → hold sin limits")
        signal.entry_mode = "market_only"
        return False  # SL/TP del proveedor se aplican normal

    elif action == "hold_sl_to_extreme":
        # Extremo del rango más cercano al precio (BUY adverso → range_low,
        # SELL adverso → range_high). Sobrescribe el SL del proveedor.
        extreme_sl = lo if signal.direction == "BUY" else hi
        print(f"[Layered] caso C → hold con SL→extremo {extreme_sl} (overrides "
              f"provider {signal.sl})")
        signal.sl = extreme_sl
        signal.entry_mode = "market_only"
        return False

    else:
        print(f"[Layered] adverse_action desconocido '{action}' → caso close (fallback)")
        pending_actions.enqueue_close_position(
            signal, signal.market_ticket,
            label=f"layered C unknown action {action}: entry {entry:.2f}"
        )
        signal.status = "closed"
        signal.dca_placed = True
        return True


async def _update_signal_from_parsed(signal: Signal, parsed: dict,
                                     tg_ts: str | None = None):
    sltp_changed = False
    sig_id = _sig_id(signal)

    if "range" in parsed and signal.range_low is None:
        lo, hi = parsed["range"]

        # ── VALIDACION RANGE vs ENTRY (anti-typo del canal) ──────────────
        # Caso real canal2_12338 (sesion 2026-05-13): canal mando range
        # 4780-4785 para SELL @ 4680.41 (typo de +100$). Sin esta guard,
        # el predictor genera TPs/SL del lado equivocado y MT5 rechaza
        # 357 veces hasta el cap.
        entry_for_range = signal.market_fill_price
        range_validation = None
        if entry_for_range is not None:
            range_validation = validate_range_vs_entry(
                signal.direction, entry_for_range, lo, hi)

        if range_validation is not None and not range_validation["ok"]:
            # Range absurdo: NO aplicar, marcar pending, pero SI poner SL
            # provisional desde entry para no dejar la posicion descubierta.
            print(f"[Validator] ⚠ {sig_id}: RANGE rechazado — {range_validation['reason']}")
            journal.event(sig_id, "range_rejected_inconsistent",
                          received_range_low=lo, received_range_high=hi,
                          entry=entry_for_range,
                          direction=signal.direction,
                          min_dist_usd=range_validation["min_dist_usd"],
                          reason=range_validation["reason"])

            # SL protector desde entry (operacion no queda descubierta)
            sl_was_none = signal.sl is None
            if sl_was_none:
                provisional_sl = predict_sl_from_entry(
                    signal.direction, entry_for_range)
                signal.sl = provisional_sl
                signal.levels_predicted = True
                journal.event(sig_id, "protective_sl_from_entry",
                              sl=provisional_sl,
                              entry=entry_for_range,
                              reason="range_rejected_inconsistent")
                print(f"[Validator] SL protector desde entry: {provisional_sl} "
                      f"(entry={entry_for_range}, direccion={signal.direction})")
                sltp_changed = True

            # Marcar pending_correction (mismo mecanismo que SL/TPs)
            signal.pending_correction = {
                "since_utc": datetime.utcnow().isoformat(timespec="seconds"),
                "field": "range",
                "received_range": [lo, hi],
                "kept_sl": signal.sl,
                "kept_tps": list(signal.tps) if signal.tps else None,
                "reason": range_validation["reason"],
                "notified_urgent": False,
            }

            # Notify INFO al usuario
            try:
                asyncio.create_task(notify(
                    f"⚠ {sig_id}: range del canal incoherente RECHAZADO\n"
                    f"\n"
                    f"Direccion: {signal.direction}  Entry: {entry_for_range}\n"
                    f"Range recibido: [{lo}-{hi}] (a {range_validation['min_dist_usd']:.0f}$ del entry)\n"
                    f"\n"
                    f"Bot NO usa este range para predictor.\n"
                    f"SL protector aplicado: {signal.sl} (desde entry).\n"
                    f"Esperando correccion del canal o TPs/SL coherentes.\n"
                    f"Si no llega en 60s recibiras URGENT."
                ))
            except Exception:
                pass
            # NO continuar con range_arrived ni layered_decision ni predictor
            # Salimos del bloque "range" pero seguimos procesando tps/sl
            # del parsed que vengan en este mismo edit (validador maneja).
        else:
            signal.range_low, signal.range_high = lo, hi

            # ── JOURNAL: range_arrived ──
            range_arrived_utc = datetime.utcnow()
            range_delay_sec = (range_arrived_utc - signal.timestamp).total_seconds()
            journal.event(sig_id, "range_arrived",
                          range_low=lo, range_high=hi,
                          delay_sec=round(range_delay_sec, 1),
                          tg_ts=tg_ts)
            journal.update_trade(sig_id,
                                 range_arrived_utc=range_arrived_utc.isoformat(timespec="milliseconds"),
                                 range_delay_sec=round(range_delay_sec, 1),
                                 range_low=lo, range_high=hi)

            # ── ETAPA 2 layered: decisión al llegar el rango ──
            closed = await _handle_range_arrival_safety(signal, lo, hi)
            if closed:
                await _finalize_signal(signal, closed_by="LAYERED_C_CLOSE",
                                       notes="adverse_action=close en caso C")
                return

            # Sin SL/TPs del canal todavía → aplicamos predicción provisional
            if signal.sl is None and not signal.tps:
                pred = predict_levels(signal.direction, lo, hi)
                signal.tps = pred["tps"]
                signal.sl = pred["sl"]
                signal.levels_predicted = True
                print(f"[Predictor] {signal.direction} provisional → "
                      f"TPs={pred['tps']} SL={pred['sl']}")
                journal.event(sig_id, "predictor_levels",
                              tps=pred["tps"], sl=pred["sl"])
                sltp_changed = True

    # ── VALIDACION COHERENCIA DIRECCIONAL antes de aceptar TPs/SL ──────────
    # Si el canal manda valores incoherentes con la direccion (typo del
    # trader: SL al lado equivocado, TPs al lado equivocado), NO los aplicamos.
    # Conservamos los valores anteriores (predictor o ultimo edit valido) y
    # marcamos signal.pending_correction para que el watchdog notifique
    # URGENT si pasan 60s sin correccion del canal.
    #
    # Casos reales (sesion 2026-05-13):
    #   canal2_12334: canal mando "SL 4796" para BUY @ 4704.84 (typo: 4696)
    #   canal2_12338: canal mando range 4780-4785 para SELL @ 4680.41 (typo: 4680)
    entry_for_validation = signal.market_fill_price
    new_tps_candidate = parsed.get("tps") if "tps" in parsed else None
    new_sl_candidate = parsed.get("sl") if "sl" in parsed else None

    # ── CORRECCION DE TYPOS en TPs antes de validar ───────────────────────
    # Si el canal manda un TP con typo evidente (magnitud absurda, ej.
    # "46700" en vez de "4700"), intentamos CORREGIRLO logicamente por
    # interpolacion con los vecinos validos — en lugar de rechazar y perder
    # la informacion. Caso real canal2_12382 (2026-05-14).
    if new_tps_candidate and entry_for_validation is not None:
        corrected_tps, corrections = correct_tp_typos(
            signal.direction, entry_for_validation, new_tps_candidate)
        if corrections:
            for c in corrections:
                print(f"[TypoFix] {sig_id}: TP{c['index']+1} typo "
                      f"{c['original']} → corregido a {c['corrected']} "
                      f"(interpolado {c['target_interpolado']})")
            journal.event(sig_id, "tp_typo_corrected",
                          corrections=corrections,
                          original_tps=list(new_tps_candidate),
                          corrected_tps=corrected_tps,
                          entry=entry_for_validation)
            new_tps_candidate = corrected_tps
            # Reflejar la correccion en parsed para que se aplique abajo
            parsed["tps"] = corrected_tps

    validation = None
    if entry_for_validation is not None and (new_tps_candidate or new_sl_candidate is not None):
        validation = levels_consistent_with_direction(
            signal.direction, entry_for_validation,
            tps=new_tps_candidate, sl=new_sl_candidate,
        )

    # Detectamos si HAY problema y QUE campo afecta
    tps_rejected = (validation is not None and new_tps_candidate
                    and not validation["tps_ok"])
    sl_rejected = (validation is not None and new_sl_candidate is not None
                   and not validation["sl_ok"])

    if tps_rejected or sl_rejected:
        # Marcar pending correction y emitir journal event
        field_label = "both" if (tps_rejected and sl_rejected) else \
                     ("tps" if tps_rejected else "sl")
        problem_str = validation.get("any_problem", "incoherent")
        signal.pending_correction = {
            "since_utc": datetime.utcnow().isoformat(timespec="seconds"),
            "field": field_label,
            "received_tps": new_tps_candidate if tps_rejected else None,
            "received_sl": new_sl_candidate if sl_rejected else None,
            "kept_tps": list(signal.tps) if signal.tps else None,
            "kept_sl": signal.sl,
            "reason": problem_str,
            "notified_urgent": False,
        }
        journal.event(sig_id, "levels_rejected_inconsistent",
                      field=field_label,
                      received_tps=new_tps_candidate if tps_rejected else None,
                      received_sl=new_sl_candidate if sl_rejected else None,
                      kept_tps=list(signal.tps) if signal.tps else None,
                      kept_sl=signal.sl,
                      tps_problems=validation["tps_problems"],
                      sl_problem=validation["sl_problem"],
                      entry=entry_for_validation,
                      direction=signal.direction)
        print(f"[Validator] ⚠ {sig_id}: valores incoherentes RECHAZADOS — "
              f"field={field_label} | {problem_str}")
        # Notify info al usuario (no urgent — watchdog hace urgent a 60s)
        try:
            asyncio.create_task(notify(
                f"⚠ {sig_id}: el canal envió valores incoherentes\n"
                f"\n"
                f"Direccion: {signal.direction}  Entry: {entry_for_validation}\n"
                f"Problema: {problem_str}\n"
                f"\n"
                f"Bot NO aplica los valores invalidos. Esperando correccion\n"
                f"del canal. Si no llega en 60s recibiras URGENT."
            ))
        except Exception:
            pass
    else:
        # Si AHORA llegan valores coherentes Y antes habia pending_correction,
        # limpiar el pending y loggear corrected.
        if signal.pending_correction:
            journal.event(sig_id, "levels_corrected",
                          previous_field=signal.pending_correction.get("field"),
                          new_tps=new_tps_candidate, new_sl=new_sl_candidate,
                          elapsed_s=round(
                              (datetime.utcnow() - datetime.fromisoformat(
                                  signal.pending_correction["since_utc"])
                              ).total_seconds(), 1))
            print(f"[Validator] ✓ {sig_id}: correccion recibida, aplicando valores coherentes")
            signal.pending_correction = {}

    # Valores reales del parser siempre ganan. Si estábamos en modo "predicted",
    # lo señalamos en logs para saber que entraron los reales.
    # GUARD: solo aplicamos si no fueron rechazados por el validador.
    if "tps" in parsed and parsed["tps"] != signal.tps and not tps_rejected:
        # Validate predictor accuracy: si teníamos predicted TPs y ahora llegan
        # reales, comparar y loguear precisión. Esto permite analizar si el
        # predictor está bien calibrado o necesita reajuste.
        if signal.levels_predicted and signal.tps:
            real_tps = parsed["tps"]
            predicted = list(signal.tps)
            n_compare = min(len(predicted), len(real_tps))
            diffs = [round(real_tps[i] - predicted[i], 2) for i in range(n_compare)]
            max_diff = max((abs(d) for d in diffs), default=0)
            journal.event(sig_id, "prediction_validated",
                          predicted_tps=predicted,
                          real_tps=real_tps,
                          diffs_per_tp=diffs,
                          max_diff_usd=max_diff,
                          accurate_within_2usd=(max_diff <= 2.0))
            print(f"[Predictor] TPs reales: {parsed['tps']} (sustituyen provisionales) "
                  f"max_diff=${max_diff:.2f}")
        # Solo registramos como "tps_arrived" la primera vez (cuando aún no había)
        first_tps = not signal.tps

        # ── FUSION anti-encogimiento (fix 2026-05-16, punto 3 capa A) ────
        # El canal manda los TPs en OLEADAS: primero TP1 solo, luego los
        # 4-5 completos. Si el canal manda [4529] y ya teniamos 4 TPs
        # (predictor o edit anterior), reemplazar a secas ENCOGE la lista
        # a 1 — y el tp_chase cree que "el precio paso todos los TPs".
        # Caso real canal2_12513 (2026-05-15): cerro 2 markets prematuro.
        #
        # FUSION: los TPs del canal sustituyen los primeros N (son los
        # reales) pero conservamos los TPs extra que ya teniamos como
        # estimacion hasta que el canal mande la lista completa.
        new_tps = parsed["tps"]
        if signal.tps and len(new_tps) < len(signal.tps):
            fused = list(new_tps) + list(signal.tps[len(new_tps):])
            print(f"[TPs] {sig_id}: canal manda {len(new_tps)} TPs, fusiono "
                  f"con los {len(signal.tps)} previos → {fused} "
                  f"(canal manda en oleadas, no encogemos la lista)")
            journal.event(sig_id, "tps_fused_partial",
                          channel_tps=list(new_tps),
                          previous_tps=list(signal.tps),
                          fused_tps=fused)
            signal.tps = fused
        else:
            signal.tps = list(new_tps)
        sltp_changed = True
        if first_tps:
            tps_arrived_utc = datetime.utcnow()
            journal.event(sig_id, "tps_arrived", tps=signal.tps, tg_ts=tg_ts)
            journal.update_trade(sig_id,
                                 tps_arrived_utc=tps_arrived_utc.isoformat(timespec="milliseconds"),
                                 tps_initial=list(signal.tps))
        else:
            journal.event(sig_id, "tps_updated", tps=signal.tps)

    if "sl" in parsed and parsed["sl"] != signal.sl and not sl_rejected:
        if signal.levels_predicted:
            print(f"[Predictor] SL real: {parsed['sl']} (sustituye provisional)")
        first_sl = signal.sl is None
        signal.sl = parsed["sl"]
        sltp_changed = True
        if first_sl:
            journal.event(sig_id, "sl_arrived", sl=signal.sl)
            journal.update_trade(sig_id, sl_initial=signal.sl)
        else:
            journal.event(sig_id, "sl_updated", sl=signal.sl)

    # Al llegar el SL real, salimos del modo predicted
    if signal.levels_predicted and "sl" in parsed:
        signal.levels_predicted = False

    # ── Fallback de seguridad: SL sin TPs → evitar TP=0 en MT5 ──────────────
    # Si tras procesar el update tenemos SL pero TPs vacíos, intentamos predecir.
    # Prioridad: (1) rango disponible → predict_levels estadístico del canal.
    #            (2) precio de fill disponible → TPs por ratio R:R (0.5R/1R/1.5R/2R).
    # Si ninguno disponible, dejamos TP=0 y avisamos (SL acota la pérdida).
    if signal.sl is not None and not signal.tps:
        if signal.range_low is not None:
            pred = predict_levels(signal.direction, signal.range_low, signal.range_high)
            signal.tps = pred["tps"]
            signal.levels_predicted = True
            sltp_changed = True
            print(f"[Predictor] ⚠️ TPs ausentes → fallback desde rango: {signal.tps}")
            journal.event(sig_id, "predictor_tp_fallback",
                          source="range", tps=signal.tps)
        elif signal.market_fill_price is not None:
            dist = abs(signal.market_fill_price - signal.sl)
            if dist > 0:
                mult = 1.0 if signal.direction == "BUY" else -1.0
                signal.tps = [
                    round(signal.market_fill_price + mult * dist * r, 2)
                    for r in (0.5, 1.0, 1.5, 2.0)
                ]
                signal.levels_predicted = True
                sltp_changed = True
                print(f"[Predictor] ⚠️ TPs ausentes → fallback R:R desde fill "
                      f"{signal.market_fill_price}: {signal.tps}")
                journal.event(sig_id, "predictor_tp_fallback",
                              source="rr_ratio", tps=signal.tps,
                              fill_price=signal.market_fill_price)
        else:
            print(f"[Predictor] ⚠️ TPs ausentes y sin rango ni fill — TP=0 en MT5. "
                  f"SL={signal.sl} acota la pérdida.")

    if sltp_changed:
        await _apply_sl_tp(signal)

    # Arranca el monitor de BE/time-stop en cuanto tengamos el rango.
    if not signal.dca_placed and signal.range_low is not None:
        await _place_dca(signal)


# ─── Acciones de gestión ──────────────────────────────────────────────────────

# Detecta variantes de "SL hit" en mensajes del canal. Defensa en profundidad:
# si MT5 cierra primero por su SL, dca_monitor.run() lo detecta vía n_open=0
# y finaliza. Si el mensaje del canal llega antes que MT5 reporte el cierre
# (o si MT5 no llegó a aplicar el SL por freeze level), este regex dispara
# _finalize_signal aquí. Cubre las redacciones reales vistas en JSONL:
#   "SL hit", "SL already hit", "SL was hit", "SL just hit", "SL has been hit"
#   "stop loss hit", "stop loss reached", "stop loss triggered"
#   "❌" emoji rojo (fraseo de DT Investing)
_SL_HIT_RE = __import__("re").compile(
    r"\bsl\s+(?:was\s+|already\s+|just\s+|has\s+been\s+)?hit\b"
    r"|\bsl\s+(?:reached|triggered)\b"
    r"|❌"
    r"|\bstop\s+loss\s+(?:was\s+|already\s+|just\s+|has\s+been\s+)?hit\b"
    r"|\bstop\s+loss\s+(?:reached|triggered)\b",
    __import__("re").IGNORECASE,
)


async def _close_first_be_rescue(signal: Signal, pos_info: list,
                                 cur_price: float, entry_avg: float):
    """Estrategia C — rama RESCATE BE de CLOSE_FIRST (canal2 sin profit).

    En lugar de cerrar a mercado en pérdida, pone TP=BE en TODAS las
    posiciones abiertas y arma un time-stop. Si el precio rebota al entry
    (caso canal2_12691, rebotó en 7s) las posiciones cierran limpias en
    BE. Si no rebota, el time-stop las cierra a mercado tras N segundos.

    pos_info: lista de dicts {ticket, pnl, entry, tp, recorrido} de las
              posiciones abiertas (construida en la rama CLOSE_FIRST).
    cur_price: precio actual relevante (bid para BUY, ask para SELL).
    entry_avg: entry promedio de las posiciones abiertas.
    """
    sig_id = _sig_id(signal)

    # Idempotencia: si el canal manda CLOSE_FIRST dos veces, la rama BE ya
    # está armada — no re-armar otro time-stop (evita tasks duplicadas).
    if signal.close_first_be_armed:
        print(f"[CLOSE_FIRST BE] {sig_id} ya tiene rescate BE armado — ignorado")
        journal.event(sig_id, "close_first_be_rearm_skipped")
        return

    # stops_level del broker para que el TP no sea rechazado con INVALID_STOPS
    try:
        import mt5_errors
        stops_level_pts = await _run(mt5_errors.get_stops_level_pts)
    except Exception:
        stops_level_pts = 0.0

    tp_be = _safe_tp_be(signal.direction, entry_avg, cur_price,
                        stops_level_pts or 0.0)

    tickets = [p["ticket"] for p in pos_info]
    for t in tickets:
        pending_actions.enqueue_modify_tp(
            signal, t, tp_be,
            label=f"CLOSE_FIRST rescate BE #{t} tp={tp_be:.2f}")

    # Arma el time-stop. La task duerme el timeout y cierra a mercado lo que
    # siga abierto. close_first_be_armed coordina con CLOSE_ALL: si el canal
    # manda cerrar todo antes, _finalize_signal lo desarma y el timeout
    # no actúa.
    timeout_s = config.STRATEGY_C2_CLOSE_FIRST_BE_TIMEOUT_S
    signal.close_first_be_armed = True
    signal.close_first_be_deadline = datetime.utcnow() + timedelta(seconds=timeout_s)
    asyncio.ensure_future(_close_first_be_timeout(signal, timeout_s))

    journal.event(sig_id, "close_first_be_armed",
                  n_positions=len(tickets), tickets=tickets,
                  tp_be=round(tp_be, 2), entry_avg=round(entry_avg, 2),
                  current_price=round(cur_price, 2),
                  price_vs_entry=round(
                      (cur_price - entry_avg) if signal.direction == "BUY"
                      else (entry_avg - cur_price), 2),
                  timeout_s=timeout_s, stops_level_pts=stops_level_pts)
    # Anomaly info — para auditar cuántas veces se dispara la rama de rescate.
    journal.anomaly(sig_id, "channel_msg", "info",
                    f"CLOSE_FIRST sin profit — rama rescate BE: TP=BE "
                    f"({tp_be:.2f}) en {len(tickets)} posiciones, time-stop "
                    f"{timeout_s}s",
                    n_positions=len(tickets), tp_be=round(tp_be, 2),
                    timeout_s=timeout_s)

    try:
        asyncio.create_task(notify(
            f"🛟 Canal 2 {sig_id}: CLOSE_FIRST sin profit\n"
            f"\n"
            f"El canal pidió cerrar primeras entradas pero la posición NO "
            f"está en profit (precio {cur_price:.2f} vs entry "
            f"{entry_avg:.2f}).\n"
            f"\n"
            f"En vez de cerrar en pérdida, se ha puesto TP=BE ({tp_be:.2f}) "
            f"en las {len(tickets)} posiciones. Si el precio rebota al entry "
            f"cierran limpias; si no, se cierran a mercado en {timeout_s}s."
        ))
    except Exception:
        pass


async def _close_first_be_timeout(signal: Signal, timeout_s: int):
    """Time-stop de la rama RESCATE BE. Tras `timeout_s` segundos, cierra a
    mercado las posiciones que sigan abiertas (el precio no rebotó al BE).

    Defensivo: nunca lanza excepción al caller (es fire-and-forget).
    Se auto-cancela si signal.close_first_be_armed fue desarmado (el canal
    mandó CLOSE_ALL, o todas cerraron por TP-BE).
    """
    sig_id = _sig_id(signal)
    try:
        await asyncio.sleep(timeout_s)

        # Desarmado mientras dormíamos → nada que hacer (CLOSE_ALL, finalize)
        if not signal.close_first_be_armed:
            return
        if signal.status != "open":
            signal.close_first_be_armed = False
            return

        # ¿Qué sigue abierto?
        all_tickets = list(signal.all_filled_tickets)
        open_positions = await _run(executor.position_pnls, all_tickets)
        still_open = [t for t, _ in open_positions] if open_positions else []

        if not still_open:
            # El precio rebotó: todas cerraron por TP-BE. Éxito.
            journal.event(sig_id, "close_first_be_timeout_resolved",
                          outcome="all_closed_by_be_before_timeout",
                          timeout_s=timeout_s)
            signal.close_first_be_armed = False
            signal.close_first_be_deadline = None
            return

        # Quedan posiciones abiertas → cerrar a mercado (el precio no rebotó)
        for t in still_open:
            pending_actions.enqueue_close_position(
                signal, t,
                label=f"CLOSE_FIRST BE-timeout #{t} ({timeout_s}s)")
        signal.close_first_tickets.extend(still_open)
        journal.event(sig_id, "close_first_be_timeout_executed",
                      n_closed_at_market=len(still_open),
                      closed_tickets=still_open, timeout_s=timeout_s)
        journal.anomaly(sig_id, "channel_msg", "info",
                        f"CLOSE_FIRST rescate BE: time-stop {timeout_s}s "
                        f"expiró sin rebote — {len(still_open)} posiciones "
                        f"cerradas a mercado",
                        n_closed=len(still_open), timeout_s=timeout_s)
        signal.close_first_be_armed = False
        signal.close_first_be_deadline = None
    except Exception as e:
        print(f"[CLOSE_FIRST BE-timeout] error: {type(e).__name__}: {e}")
        try:
            journal.anomaly(sig_id, "channel_msg", "warning",
                            f"_close_first_be_timeout crasheó: "
                            f"{type(e).__name__}: {str(e)[:200]}",
                            exc_type=type(e).__name__)
        except Exception:
            pass


async def _be_rescue_timeout(signal: Signal, timeout_s: int):
    """Time-stop del rescate BE generico para CLOSE_ALL semantico."""
    sig_id = _sig_id(signal)
    try:
        await asyncio.sleep(timeout_s)
        if not signal.be_rescue_armed:
            return

        open_positions = await _run(executor.position_pnls,
                                    signal.all_filled_tickets)
        still_open = [t for t, _ in open_positions] if open_positions else []
        if not still_open:
            journal.event(sig_id, "be_rescue_timeout_resolved",
                          outcome="all_closed_by_be_before_timeout",
                          timeout_s=timeout_s)
            signal.be_rescue_armed = False
            signal.be_rescue_deadline = None
            return

        for t in still_open:
            pending_actions.enqueue_close_position(
                signal, t, label=f"BE_RESCUE timeout #{t} ({timeout_s}s)")
        signal.be_rescue_tickets.extend(still_open)
        journal.event(sig_id, "be_rescue_timeout_executed",
                      n_closed_at_market=len(still_open),
                      closed_tickets=still_open,
                      timeout_s=timeout_s)
        journal.anomaly(sig_id, "channel_msg", "info",
                        f"BE rescue: time-stop {timeout_s}s expiro sin "
                        f"rebote; {len(still_open)} posiciones cerradas "
                        "a mercado",
                        n_closed=len(still_open), timeout_s=timeout_s)
        signal.be_rescue_armed = False
        signal.be_rescue_deadline = None
    except Exception as e:
        print(f"[BE_RESCUE-timeout] error: {type(e).__name__}: {e}")
        try:
            journal.anomaly(sig_id, "channel_msg", "warning",
                            f"_be_rescue_timeout crasheo: "
                            f"{type(e).__name__}: {str(e)[:200]}",
                            exc_type=type(e).__name__)
        except Exception:
            pass


async def _close_all_be_rescue(signal: Signal, pos_info: list,
                               cur_price: float, entry_avg: float,
                               raw_text: str = ""):
    """Rescate BE para CLOSE_ALL que realmente significa breakeven/risk-free.

    Si el canal pide cerrar en BE pero nuestra cuenta esta perdiendo mas que
    el umbral, no materializamos la perdida de inmediato: ponemos TP=BE en
    las posiciones abiertas y armamos un time-stop corto.
    """
    sig_id = _sig_id(signal)
    if signal.be_rescue_armed:
        print(f"[BE_RESCUE] {sig_id} ya armado - ignorado")
        journal.event(sig_id, "be_rescue_rearm_skipped")
        return

    try:
        import mt5_errors
        stops_level_pts = await _run(mt5_errors.get_stops_level_pts)
    except Exception:
        stops_level_pts = 0.0

    tp_be = _safe_tp_be(signal.direction, entry_avg, cur_price,
                        stops_level_pts or 0.0)
    tickets = [p["ticket"] for p in pos_info]
    for t in tickets:
        pending_actions.enqueue_modify_tp(
            signal, t, tp_be,
            label=f"CLOSE_ALL_BE_RESCUE #{t} tp={tp_be:.2f}")

    timeout_s = config.STRATEGY_BE_CLOSE_RESCUE_TIMEOUT_S
    signal.be_rescue_armed = True
    signal.be_rescue_deadline = datetime.utcnow() + timedelta(seconds=timeout_s)
    asyncio.ensure_future(_be_rescue_timeout(signal, timeout_s))

    journal.event(sig_id, "close_all_be_rescue_armed",
                  tp_be=tp_be,
                  entry_avg=round(entry_avg, 2),
                  current_price=cur_price,
                  tickets=tickets,
                  timeout_s=timeout_s,
                  raw_snippet=(raw_text or "")[:160])
    journal.anomaly(sig_id, "channel_msg", "warning",
                    "CLOSE_ALL semantico de breakeven/risk-free llego con "
                    "P/L real negativo; armado rescate TP=BE en vez de "
                    "cerrar a mercado",
                    tp_be=tp_be, n_positions=len(tickets),
                    timeout_s=timeout_s)
    try:
        await notify(
            f"BE rescue armado - {sig_id}\n"
            f"\n"
            f"El canal pidio cerrar/proteger en breakeven, pero nuestra "
            f"entrada real estaba en perdida.\n"
            f"Accion: TP=BE {tp_be:.2f} en {len(tickets)} posiciones.\n"
            f"Time-stop: {timeout_s}s si no rebota."
        )
    except Exception as e:
        print(f"[BE_RESCUE notify] error: {e}")


async def _maybe_handle_breakeven_close_negative(
        signal: Signal, classification: dict, raw_text: str, ctx=None) -> bool:
    if not config.STRATEGY_BE_CLOSE_NEGATIVE_GUARD_ENABLED:
        return False
    if not _breakeven_close_guard_applies(classification, raw_text):
        return False

    try:
        ctx = ctx or signal.build_context()
        pl = float(ctx.floating_pnl_total or 0.0)
        decision = _be_close_negative_decision(
            pl, config.STRATEGY_BE_CLOSE_NEGATIVE_TOLERANCE_USD)
        journal.event(_sig_id(signal), "be_close_negative_guard",
                      floating_pl=pl,
                      tolerance_usd=config.STRATEGY_BE_CLOSE_NEGATIVE_TOLERANCE_USD,
                      decision=decision,
                      raw_snippet=(raw_text or "")[:160])
        if decision == "allow_close":
            return False

        open_positions = await _run(executor.position_pnls,
                                    signal.all_filled_tickets)
        if not open_positions:
            return False
        cur_price = ctx.current_price
        entries = []
        pos_info = []
        for idx, (ticket, pnl) in enumerate(open_positions):
            entry = await _run(executor.entry_price, ticket)
            if entry is not None:
                entries.append(entry)
            tp_obj = signal.tp_for_position(idx)
            recorrido = abs(tp_obj - cur_price) if (
                tp_obj is not None and cur_price is not None) else 0.0
            pos_info.append({"ticket": ticket, "pnl": pnl, "entry": entry,
                             "tp": tp_obj, "recorrido": recorrido})
        if cur_price is None or not entries:
            journal.anomaly(_sig_id(signal), "channel_msg", "warning",
                            "BE close guard no pudo armar rescate: faltan "
                            "precio actual o entries; se permite CLOSE_ALL",
                            has_current_price=cur_price is not None,
                            n_entries=len(entries))
            return False

        await _close_all_be_rescue(signal, pos_info, cur_price,
                                   sum(entries) / len(entries),
                                   raw_text=raw_text)
        logger.log_action(signal, "CLOSE_ALL_BE_RESCUE")
        return True
    except Exception as e:
        print(f"[BE_CLOSE_GUARD] error: {type(e).__name__}: {e}")
        journal.anomaly(_sig_id(signal), "channel_msg", "warning",
                        f"BE close guard fallo; se permite CLOSE_ALL: "
                        f"{type(e).__name__}: {str(e)[:160]}",
                        exc_type=type(e).__name__)
        return False


async def _execute_actions(signal: Signal, classifications, raw_text: str = "",
                           tg_ts: str | None = None):
    """Orquesta la ejecución de TODAS las acciones detectadas en un mensaje.

    classify() ahora puede devolver varias acciones por mensaje (ej:
    "TP1 hit. Move SL to BE" → [INFORMATIONAL, MOVE_SL_TO_BE]). Iteramos en
    el orden devuelto. Si una acción cierra la señal (CLOSE_ALL), las
    siguientes se descartan defensivamente para evitar tocar tickets
    que ya hemos enviado a cerrar.

    Acepta también un único dict por compatibilidad con código antiguo.
    """
    if isinstance(classifications, dict):
        classifications = [classifications]
    if not classifications:
        return

    sig_id = _sig_id(signal)
    sl_hit_detected = False
    _log_telegram_understood(
        sig_id,
        channel=signal.channel,
        message_id=signal.message_id,
        kind="management",
        parser="classifier",
        raw_text=raw_text,
        classifications=classifications,
        target_signal_id=sig_id,
        tg_ts=tg_ts,
    )

    # Captura SL hit para post-SL momentum (point 4): solo una vez aunque haya
    # varias acciones en el mismo mensaje.
    if raw_text and _SL_HIT_RE.search(raw_text):
        duration_s = (datetime.utcnow() - signal.timestamp).total_seconds()
        strategies.record_sl_hit(signal.channel, duration_s)
        signal.status = "closed"
        sl_hit_detected = True
        journal.event(sig_id, "sl_hit_detected",
                      raw_text=raw_text[:200], duration_s=round(duration_s, 1))

    for cl in classifications:
        # Si una acción anterior cerró la señal, no aplicamos las restantes
        # (excepto INFORMATIONAL que solo loguea).
        will_apply = (signal.status == "open"
                      or cl.get("action") == "INFORMATIONAL")

        action_name = cl.get("action", "")
        confidence = cl.get("confidence") or 0.0
        if _management_action_already_seen(
                sig_id, action_name, raw_text, cl.get("price")):
            journal.event(sig_id, "mgmt_msg_duplicate_skipped",
                          action=action_name,
                          price=cl.get("price"),
                          confidence=confidence,
                          raw_snippet=raw_text[:120],
                          tg_ts=tg_ts)
            continue

        # ── GEMINI FAILED GATE (fix C1) ─────────────────────────────────
        # Si Gemini fallo los 3 retries y devolvio el fallback INFORMATIONAL
        # con _gemini_failed=True, el mensaje quedo SIN clasificar. Antes
        # se silenciaba (INFORMATIONAL = no-op); ahora notifica al usuario
        # con prefix [REVIEW] para que decida manualmente. Justifica el
        # 18% de errores 503 observados durante la medicion del 2026-05-07.
        if cl.get("_gemini_failed"):
            print(f"[REVIEW] Gemini fallo todos los retries para {_sig_id(signal)} "
                  f"— notify al usuario, mensaje sin clasificar.")
            try:
                await notify(
                    f"[REVIEW] Gemini no clasificable — {_sig_id(signal)}\n"
                    f"\n"
                    f"Mensaje del trader:\n"
                    f"\"{(raw_text or '')[:300]}\"\n"
                    f"\n"
                    f"El bot intento clasificar 3 veces y Gemini fallo "
                    f"(probable 503/timeout). Decide manualmente y ejecuta en MT5."
                )
            except Exception as e:
                print(f"[Notify gemini_failed] error: {e}")
            journal.append_mgmt(sig_id, classified="GEMINI_FAILED", applied=False)
            journal.event(sig_id, "mgmt_msg",
                          action="GEMINI_FAILED", price=None,
                          confidence=0.0,
                          gemini_failed=True,
                          will_apply=False,
                          gemini_failed_notified=True,
                          raw_snippet=raw_text[:120],
                          tg_ts=tg_ts)
            # Capa de anomalía estructurada — para queriar 'cuántos mensajes
            # ha perdido Gemini hoy' sin grepear logs (T1 batch A silenciosos).
            journal.anomaly(sig_id, "channel_msg", "warning",
                            "Gemini classifier falló 3 retries — mensaje "
                            "del canal sin clasificar, requiere revisión "
                            "manual",
                            raw_snippet=raw_text[:120])
            continue

        # ── LOW CONFIDENCE GATE (fix C1) ────────────────────────────────
        # Si Gemini devuelve action no-INFO con confianza muy baja (<0.5),
        # ni siquiera Gemini esta seguro. Notify y NO ejecutar. Mas
        # urgente que el ambiguous gate (0.5-0.8) que sigue activo abajo.
        # No aplica a regex matches (siempre tienen _reason o conf >= 0.85).
        is_low_confidence = (
            will_apply
            and action_name not in ("INFORMATIONAL",)
            and confidence < 0.5
            and not cl.get("_reason")  # regex match → conf alta, skip
        )
        if is_low_confidence:
            print(f"[REVIEW] {action_name} CONF={confidence:.2f} BAJA — "
                  f"notify al usuario, NO ejecuto automaticamente.")
            try:
                await notify(
                    f"[REVIEW] Baja confianza ({confidence:.2f}) — "
                    f"{_sig_id(signal)}\n"
                    f"\n"
                    f"Gemini propone: {action_name}"
                    + (f" @ {cl.get('price')}" if cl.get('price') else "") + "\n"
                    f"Razon: {cl.get('reasoning') or '(sin razon)'}\n"
                    f"\n"
                    f"Mensaje del trader:\n"
                    f"\"{(raw_text or '')[:200]}\"\n"
                    f"\n"
                    f"El bot NO ejecuto. Decide y aplica manualmente en MT5 "
                    f"si lo consideras correcto."
                )
            except Exception as e:
                print(f"[Notify low_conf] error: {e}")
            journal.append_mgmt(sig_id, classified=f"{action_name}_LOWCONF",
                                applied=False)
            journal.event(sig_id, "mgmt_msg",
                          action=action_name, price=cl.get("price"),
                          confidence=confidence,
                          will_apply=False,
                          low_confidence_notified=True,
                          raw_snippet=raw_text[:120],
                          tg_ts=tg_ts)
            continue

        # ── AMBIGUOUS DECISION GATE ─────────────────────────────────────
        # Si Gemini devuelve una acción NO-INFO con confianza ambigua
        # (0.5-0.8), NO aplicamos automáticamente — pedimos al usuario
        # que decida via notify contextual con resumen completo.
        is_ambiguous = (
            will_apply
            and action_name not in ("INFORMATIONAL",)
            and 0.5 <= confidence < 0.8
            and not cl.get("_reason")  # regex match (siempre confianza alta) → no aplica
        )
        if is_ambiguous:
            print(f"[Acción] {action_name} CONF={confidence:.2f} AMBIGUO — "
                  f"notify al usuario, NO ejecuto automáticamente.")
            try:
                await notify_ambiguous_decision(signal, cl, raw_text)
            except Exception as e:
                print(f"[Notify Ambig] error: {e}")
            # Marcar en journal como pendiente de decisión humana
            journal.append_mgmt(sig_id, classified=f"{action_name}_AMBIG", applied=False)
            journal.event(sig_id, "mgmt_msg",
                          action=action_name, price=cl.get("price"),
                          confidence=confidence,
                          will_apply=False,
                          ambiguous_notified=True,
                          raw_snippet=raw_text[:120],
                          tg_ts=tg_ts)
            continue  # no ejecuta esta acción

        journal.append_mgmt(sig_id, classified=cl.get("action", "UNKNOWN"), applied=will_apply)
        # Detalles de la acción al journal
        journal.event(sig_id, "mgmt_msg",
                      action=cl.get("action"), price=cl.get("price"),
                      confidence=cl.get("confidence"),
                      gemini_failed=bool(cl.get("_gemini_failed")),
                      will_apply=will_apply,
                      raw_snippet=raw_text[:120],
                      tg_ts=tg_ts)
        if not will_apply:
            print(f"[Acción] {cl.get('action')} ignorada — señal {signal.message_id} ya cerrada")
            continue
        await _execute_one_action(signal, cl, raw_text=raw_text)

    if sl_hit_detected:
        await _finalize_signal(signal, closed_by="SL",
                               notes="SL hit detectado en mensaje del canal")


async def _execute_one_action(signal: Signal, classification: dict, raw_text: str = ""):
    """Ejecuta una sola acción sobre la señal."""
    action = classification.get("action", "INFORMATIONAL")
    price  = classification.get("price")
    conf   = classification.get("confidence", 0)

    if action == "MOVE_SL_TO_PRICE" and price is not None:
        price = _normalize_management_sl_price(signal, price, raw_text)
        if price is None:
            print(f"[Accion] MOVE_SL_TO_PRICE ignorada: precio invalido "
                  f"en mensaje {raw_text[:80]!r}")
            return
        classification = {**classification, "price": price}

    if action == "INFORMATIONAL":
        # Si Gemini falló y devolvió INFORMATIONAL como fallback, registramos
        # un aviso para que el journal/forensics capture la pérdida de info.
        if classification.get("_gemini_failed"):
            print(f"[Acción] ⚠ Gemini falló — mensaje no clasificado, asumido "
                  f"INFORMATIONAL: {raw_text[:80]!r}")
        return

    print(f"[Acción] {action} (confianza={conf:.2f}) sobre señal {signal.message_id}")

    # ── DECISION CONTEXT SNAPSHOT ────────────────────────────────────
    # ANTES de ejecutar cualquier mgmt action, capturamos el estado
    # completo del trade. Esto permite analizar después: en qué situación
    # estaba el trade cuando se aplicó cada acción, y si fue un buen
    # momento o no. Crítico para distinguir SKILL vs LUCK en el análisis.
    try:
        ctx = signal.build_context()
        journal.event(_sig_id(signal), "decision_context",
                      action=action,
                      price=price,
                      confidence=conf,
                      gemini_reasoning=classification.get("reasoning"),
                      regex_reason=classification.get("_reason"),
                      ctx_n_open=ctx.n_open,
                      ctx_n_initial=ctx.n_initial,
                      ctx_floating_pl=ctx.floating_pnl_total,
                      ctx_current_price=ctx.current_price,
                      ctx_elapsed_min=ctx.elapsed_min,
                      ctx_be_armed=ctx.be_armed,
                      ctx_summary=ctx.summary_oneline())
    except Exception as e:
        print(f"[Acción] decision_context error: {e}")

    if action == "CLOSE_ALL":
        if await _maybe_handle_breakeven_close_negative(
                signal, classification, raw_text, locals().get("ctx")):
            return
        # El cierre se encola con reintento — si falla por transient, reintenta;
        # si no hay posición (ya cerrada por SL/TP), se considera hecho.
        for t in signal.all_filled_tickets:
            pending_actions.enqueue_close_position(signal, t, label=f"CLOSE_ALL #{t}")
        for t in signal.pending_tickets:
            pending_actions.enqueue_cancel_pending(signal, t, label=f"CANCEL_PENDING #{t}")
        signal.status = "closed"
        logger.log_action(signal, action)
        await _finalize_signal(signal, closed_by="CLOSE_ALL",
                               notes=f"reason={classification.get('_reason', 'classifier')}")

    elif action == "CLOSE_FIRST":
        # CONTEXTUAL por RECORRIDO RESTANTE (commit 2026-05-15).
        #
        # El trader pide "close your first entries / make risk free": cerrar
        # algunas posiciones para asegurar y dejar correr el resto. La clave
        # (consensuada con el usuario): "ver las operaciones que tienen MAYOR
        # RECORRIDO y dejar esas abiertas".
        #
        # RECORRIDO RESTANTE de una posición = distancia del precio actual a
        # su TP objetivo. Posición con TP lejano = mucho potencial → MANTENER.
        # Posición con TP casi alcanzado = poco potencial → CERRAR (asegurar).
        #
        # ZONAS: posiciones con mismo entry (±0.3$) Y mismo TP son "idénticas"
        # — se cierran juntas o se mantienen juntas, nunca media zona (queja
        # real del usuario: "por qué cerrar 1 de 2 al mismo precio").
        #
        # Reemplaza la lógica anterior (cerrar markets a ciegas) que cortaba
        # el upside — casos canal2_12347 / 12390 (sesión 2026-05-13/14).
        all_tickets = list(signal.all_filled_tickets)
        open_positions = await _run(executor.position_pnls, all_tickets)
        if not open_positions:
            print(f"[Acción] CLOSE_FIRST: sin posiciones abiertas — nada que cerrar")
            return
        open_set = {t for t, _ in open_positions}

        # Precio actual para calcular recorrido
        tick_ctx = await _run(executor.current_tick_safe)
        cur_price = None
        if tick_ctx:
            cur_price = (tick_ctx.get("bid") if signal.direction == "BUY"
                         else tick_ctx.get("ask"))

        # Info por posición: ticket, pnl, entry, tp_objetivo, recorrido
        pos_info = []
        for idx, t in enumerate(all_tickets):
            if t not in open_set:
                continue
            pnl = next((p for tk, p in open_positions if tk == t), 0.0)
            entry = await _run(executor.entry_price, t)
            if t in signal.tp_overrides and signal.tps:
                tp_idx = max(0, min(signal.tp_overrides[t], len(signal.tps) - 1))
                tp_obj = signal.tps[tp_idx]
            else:
                tp_obj = signal.tp_for_position(idx)
            if tp_obj is not None and cur_price is not None:
                recorrido = abs(tp_obj - cur_price)
            else:
                recorrido = 0.0
            pos_info.append({"ticket": t, "pnl": pnl, "entry": entry,
                             "tp": tp_obj, "recorrido": recorrido})

        # ── ESTRATEGIA C: bifurcación canal2 — rescate BE si NO hay profit ──
        # Análisis 19+20 may: cuando canal2 manda CLOSE_FIRST sin que la
        # posición esté en profit, cerrar a mercado materializa la pérdida
        # (canal2_12691: −$26.55). En su lugar ponemos TP=BE + time-stop:
        # si el precio rebota al entry las posiciones cierran limpias.
        # Canal 1 NO aplica (CLOSE_FIRST allí cierra 1 sola posición market).
        entries_known = [p["entry"] for p in pos_info if p["entry"] is not None]
        entry_avg = (sum(entries_known) / len(entries_known)
                     if entries_known else None)
        if (signal.channel == "canal2"
                and config.STRATEGY_C2_CLOSE_FIRST_BE_ENABLED
                and entry_avg is not None and cur_price is not None):
            price_vs_entry = (cur_price - entry_avg
                              if signal.direction == "BUY"
                              else entry_avg - cur_price)
            decision = _close_first_decision(
                price_vs_entry, config.STRATEGY_C2_CLOSE_FIRST_PROFIT_PTS)
            print(f"[Acción] CLOSE_FIRST canal2: precio vs entry "
                  f"{price_vs_entry:+.2f} pts → decisión={decision}")
            if decision == "set_tp_be_all":
                await _close_first_be_rescue(signal, pos_info,
                                             cur_price, entry_avg)
                logger.log_action(signal, action)
                return
            # decision == "close_half" → hay profit real, cae a la lógica
            # clásica de abajo (cerrar mitad a mercado, asegurar parciales).

        # Ordenar por recorrido ASC (menor recorrido = cerrar primero)
        pos_info.sort(key=lambda p: p["recorrido"])

        # n_close = floor(n/2) — mantener MÁS (alineado con "dejar correr"),
        # mínimo 1 (el trader pide cerrar algo).
        n_total = len(pos_info)
        n_close = max(1, n_total // 2)

        # Ajuste por ZONAS idénticas: si la frontera parte un bloque de
        # posiciones idénticas (mismo entry±0.3 y mismo TP), incluir el
        # bloque entero en to_close (no cerrar media zona).
        def _identicas(a, b):
            same_tp = (a["tp"] is not None and b["tp"] is not None
                       and abs(a["tp"] - b["tp"]) < 0.05)
            same_entry = (a["entry"] is not None and b["entry"] is not None
                          and abs(a["entry"] - b["entry"]) <= 0.3)
            return same_tp and same_entry

        while (0 < n_close < n_total
               and _identicas(pos_info[n_close - 1], pos_info[n_close])):
            n_close += 1  # extender para no partir la zona idéntica

        to_close = pos_info[:n_close]
        to_keep = pos_info[n_close:]

        print(f"[Acción] CLOSE_FIRST por recorrido: {n_total} abiertas → "
              f"cierro {len(to_close)} de menor recorrido "
              f"{[round(p['recorrido'],2) for p in to_close]} | "
              f"mantengo {len(to_keep)} de mayor recorrido "
              f"{[round(p['recorrido'],2) for p in to_keep]}")
        for p in to_close:
            pending_actions.enqueue_close_position(
                signal, p["ticket"],
                label=f"CLOSE_FIRST #{p['ticket']} recorrido={p['recorrido']:.2f}"
            )
        signal.close_first_tickets.extend([p["ticket"] for p in to_close])
        journal.event(_sig_id(signal), "close_first_executed",
                      n_total_open=n_total,
                      n_closed=len(to_close),
                      closed_tickets=[p["ticket"] for p in to_close],
                      closed_recorridos=[round(p["recorrido"], 2) for p in to_close],
                      kept_tickets=[p["ticket"] for p in to_keep],
                      kept_recorridos=[round(p["recorrido"], 2) for p in to_keep],
                      current_price=cur_price,
                      mode="contextual_recorrido")
        logger.log_action(signal, action)

    elif action == "CLOSE_AT_TP" and price:
        # price viene como número de TP (1..5). En modo escalonado, posición i
        # cierra en tps[i], así que TP_n cierra el ticket en posición n-1.
        tp_idx = int(price) - 1
        tickets = signal.all_filled_tickets
        if 0 <= tp_idx < len(tickets):
            target = tickets[tp_idx]
            pending_actions.enqueue_close_position(
                signal, target, label=f"CLOSE_AT_TP{tp_idx+1} #{target}"
            )
            logger.log_action(signal, action, float(price))
        else:
            # No hay ticket asignado a ese TP (ej: el canal cierra TP4 pero solo
            # hay 2 tickets abiertos). Lo registramos sin tocar nada — el canal
            # probablemente está informando que YA cerró ese TP en su lado.
            print(f"[Acción] CLOSE_AT_TP{int(price)} sin ticket asignado "
                  f"(tickets={len(tickets)}). Ignorado.")

    elif action == "MOVE_SL_TO_BE":
        if signal.market_ticket:
            # BE por ticket: cada posición se mueve a SU propio entry, no al
            # del market original. Esto es lo correcto cuando hay DCAs con
            # entries distintos (intra_dca).
            #
            # GUARD anti-Invalid-stops (sesion 2026-05-13, canal2_12347):
            # mismo guard que dca_monitor._arm_be. Si la posicion esta del
            # lado adverso al BE (ej. SELL con ask >= entry), MT5 rechaza
            # con 10016 y la cola reintenta hasta el cap de 30s. Caso real
            # tras mensaje "Make your trade risk free" cuando precio ya
            # estaba peor que entry: 382 reintentos de spam Invalid stops.
            try:
                import MetaTrader5 as _mt5
                tick = await _run(_mt5.symbol_info_tick, config.MT5_SYMBOL)
                si = await _run(_mt5.symbol_info, config.MT5_SYMBOL)
                min_dist = (si.trade_stops_level * si.point) if si else 0.30
            except Exception:
                tick = None
                min_dist = 0.30

            # ── ESTRATEGIA C+D para BE imposible ─────────────────────────
            # Cuando el trader pide MOVE_SL_TO_BE pero el precio actual ya
            # esta del lado adverso al entry (ej. SELL @ 4677 con ask=4680),
            # MT5 rechaza SL=entry. En lugar de SKIP simple (mantener SL del
            # proveedor lejos), aplicamos:
            #
            #   C. SL TRAILING TIGHT desde precio actual:
            #      SELL: nuevo_sl = ask + min_dist + buffer (ej. ask+0.5)
            #      BUY:  nuevo_sl = bid - min_dist - buffer
            #      Solo si MEJORA (mas cerca al precio que el SL actual);
            #      si SL del proveedor ya esta mas cerca, no tocar.
            #
            #   D. NOTIFY URGENT: alerta inmediata al usuario para que
            #      decida manualmente si el bot decidio aplicar trailing
            #      tight (porque el BE literal era imposible).
            #
            # Asi limitamos perdida (vs SL del provider que esta lejos) sin
            # cerrar la pos (que perderia upside si el precio rebota), y el
            # usuario queda informado de la situacion no-estandar.
            BUFFER_USD = 0.5
            moved_be = 0
            moved_trailing = 0
            skipped_no_improvement = 0
            trailing_details = []
            for t in signal.all_filled_tickets:
                entry = await _run(executor.entry_price, t)
                if entry is None:
                    continue

                # Validar BE contra precio actual
                be_valid = True
                if tick is not None:
                    if signal.direction == "SELL":
                        if entry <= tick.ask + min_dist:
                            be_valid = False
                    else:  # BUY
                        if entry >= tick.bid - min_dist:
                            be_valid = False

                if be_valid:
                    # Camino normal: BE = entry
                    pending_actions.enqueue_modify_sl(
                        signal, t, entry, label=f"BE #{t} → {entry:.2f}"
                    )
                    moved_be += 1
                    continue

                # BE imposible → estrategia C: trailing tight desde precio actual
                if tick is None:
                    # Sin tick no podemos calcular trailing → SKIP defensivo
                    skipped_no_improvement += 1
                    continue

                # SL actual REAL de este ticket: si gestión ya lo movió antes,
                # sl_by_ticket lo tiene; si no, el SL del proveedor. Antes esto
                # leía signal.sl (proveedor), que queda obsoleto cuando un
                # mensaje previo movió el SL → podía aplicar un SL PEOR que el
                # que ya estaba en la orden (bug 2026-05-18, consecuencia B).
                current_sl = signal.sl_by_ticket.get(t, signal.sl)
                if signal.direction == "SELL":
                    trailing_sl = round(tick.ask + min_dist + BUFFER_USD, 2)
                    # Solo aplicar si mejora SL actual (mas cerca = menor)
                    improves = current_sl is None or trailing_sl < current_sl
                else:  # BUY
                    trailing_sl = round(tick.bid - min_dist - BUFFER_USD, 2)
                    improves = current_sl is None or trailing_sl > current_sl

                if not improves:
                    print(f"[Acción] BE→TRAILING SKIP ticket {t}: trailing "
                          f"{trailing_sl} no mejora SL actual {current_sl}")
                    skipped_no_improvement += 1
                    continue

                pending_actions.enqueue_modify_sl(
                    signal, t, trailing_sl,
                    label=f"BE_TRAILING #{t} → {trailing_sl:.2f} (entry={entry:.2f} adverso)"
                )
                moved_trailing += 1
                trailing_details.append({
                    "ticket": t, "entry": entry, "trailing_sl": trailing_sl,
                    "current_ask": tick.ask, "current_bid": tick.bid,
                    "previous_sl": current_sl,
                })
                print(f"[Acción] BE→TRAILING ticket {t}: BE imposible "
                      f"(entry={entry:.2f}, ask/bid adverso) → SL trailing "
                      f"{trailing_sl} (mejora vs anterior {current_sl})")

            total_moved = moved_be + moved_trailing
            journal.event(_sig_id(signal), "be_armed_classifier",
                          n_moved_be=moved_be,
                          n_moved_trailing=moved_trailing,
                          n_skipped_no_improvement=skipped_no_improvement,
                          trailing_details=trailing_details,
                          source="MOVE_SL_TO_BE_action")

            # Migrado a anomaly() — severity=warning, NO dispara notify (el
            # bot YA aplicó trailing como mejor opción; mata el spam del 19,
            # donde 8 BE imposibles generaron 8 pushes URGENT). Aparecerá
            # en el parte de sesión sin interrumpir en el momento.
            if moved_trailing > 0:
                journal.anomaly(_sig_id(signal), "sl_be", "warning",
                                f"BE imposible — SL trailing aplicado a "
                                f"{moved_trailing} pos",
                                direction=signal.direction,
                                n_moved_be=moved_be,
                                n_moved_trailing=moved_trailing,
                                n_skipped=skipped_no_improvement)

            if total_moved:
                signal.be_armed = True  # coherente con el be_armed del monitor
                logger.log_action(signal, action,
                                  await _run(executor.entry_price, signal.market_ticket))

    elif action == "MOVE_SL_TO_PRICE" and price:
        for t in signal.all_filled_tickets:
            pending_actions.enqueue_modify_sl(
                signal, t, price, label=f"SL→{price} #{t}"
            )
        for t in signal.pending_tickets:
            pending_actions.enqueue_modify_sl(
                signal, t, price, label=f"SL→{price} pend #{t}"
            )
        logger.log_action(signal, action, price)

    elif action == "PROTECT_AND_NOTIFY":
        # Notificar al usuario con contexto completo del trade. NO actuar
        # automáticamente — el bot solo enseña la situación, el usuario
        # decide en MT5. Esto cubre el caso del trader diciendo cosas
        # ambiguas como "secure profits if you're satisfied".
        try:
            await notify_ambiguous_decision(signal, classification, raw_text)
        except Exception as e:
            print(f"[Acción] PROTECT_AND_NOTIFY error: {e}")

    elif action == "SIGNAL_UPDATED":
        # Trader dijo "entries cambiaron". Lógica acordada con usuario:
        #   • Si en profit > $0.5: cerrar todo (asegurar lo ganado)
        #   • Si flat (entre -2 y +0.5): mover SL a precio actual (no tocar TPs)
        #   • Si en loss < -$2: NO TOCAR, notificar al usuario
        # En cualquier caso, también notificar para que el usuario decida si
        # esperar la nueva señal o ignorar.
        try:
            ctx = signal.build_context()
            pl = ctx.floating_pnl_total
            cur = ctx.current_price
            print(f"[Acción] SIGNAL_UPDATED: P&L={pl:+.2f} cur={cur} → "
                  f"{'cierre' if pl > 0.5 else 'SL→precio' if pl > -2 else 'no tocar'}")

            if pl > 0.5:
                # Cerrar todo — asegurar profit
                for t in signal.all_filled_tickets:
                    pending_actions.enqueue_close_position(
                        signal, t, label=f"SIGNAL_UPDATED close (profit) #{t}"
                    )
                signal.status = "closed"
                await _finalize_signal(signal, closed_by="SIGNAL_UPDATED",
                                       notes=f"profit secured P&L={pl:+.2f}")
            elif pl > -2 and cur is not None:
                # Mover SL a precio actual (asegurar BE+, no perder lo poco
                # que tenemos). Mantener TPs vigentes.
                for t in signal.all_filled_tickets:
                    pending_actions.enqueue_modify_sl(
                        signal, t, cur,
                        label=f"SIGNAL_UPDATED SL→{cur:.2f} (BE+) #{t}"
                    )
            # Si pl < -2: no tocamos. Solo notify abajo.

            # Notificar siempre (el usuario decide qué hacer ahora)
            await notify_ambiguous_decision(signal, classification, raw_text)
            journal.event(_sig_id(signal), "signal_updated_handled",
                          floating_pnl=pl, current_price=cur,
                          action_taken=("close" if pl > 0.5
                                        else "sl_to_current" if pl > -2
                                        else "no_action"))
        except Exception as e:
            print(f"[Acción] SIGNAL_UPDATED error: {e}")

    elif action == "HIGH_RISK_WARNING":
        # Solo notificar — no tocar el trade. La estrategia ya tiene
        # filtros de high-risk en strategies.py al recibir la señal.
        # Este handler cubre el caso raro de high-risk anunciado DESPUÉS
        # del fill (poco común pero el trader lo hace a veces).
        try:
            await notify(
                f"⚠️ HIGH RISK WARNING — {_sig_id(signal)}\n"
                f"Trader marca este trade como high risk:\n"
                f"\"{(raw_text or '')[:200]}\"\n"
                f"Considera reducir lot manualmente si quieres."
            )
        except Exception as e:
            print(f"[Acción] HIGH_RISK_WARNING notify error: {e}")


# Alias retrocompat: código antiguo que llamaba a _execute_action(sig, dict)
# sigue funcionando porque _execute_actions detecta el dict y lo envuelve.
_execute_action = _execute_actions


# ─── Canal 2 procesado (reutilizable) ─────────────────────────────────────────

async def _process_canal2_new(msg, label: str = "Canal2", dedup: bool = True):
    # Dedup: el poller activo y el event handler pueden ver el mismo mensaje.
    # _new_msg_already_seen marca como visto atómicamente — el que llega primero
    # procesa; el segundo retorna aquí sin hacer nada.
    if dedup and _new_msg_already_seen("canal2", msg.id):
        return

    text = msg.text or ""

    # Mensaje de gestión (reply a una señal)
    if msg.reply_to and msg.reply_to.reply_to_msg_id:
        reply_id = msg.reply_to.reply_to_msg_id
        sig = state.get("canal2", reply_id) or state.get("canal1", reply_id)
        if sig and sig.status == "open":
            # PRIMERO: si el reply trae TPs/SL reales (formato típico de
            # Canal 2: "TP1 4689.50\nSL 4701"), actualizar la señal con esos
            # valores. Sin esto, el bot sigue con los provisionales de
            # predict_levels y el TP en MT5 queda desalineado del canal.
            parsed_in_reply = parse_canal2(text)
            _tg_ts = msg.date.isoformat(timespec="seconds") if msg.date else None
            _log_telegram_understood(
                _sig_id(sig),
                channel="canal2",
                message_id=msg.id,
                kind="reply_levels",
                parser="parse_canal2",
                raw_text=text,
                parsed=parsed_in_reply,
                target_signal_id=_sig_id(sig),
                tg_ts=_tg_ts,
                is_reply=True,
                reply_to_msg_id=reply_id,
            )
            if parsed_in_reply.get("tps") or parsed_in_reply.get("sl"):
                print(f"[{label}] Reply con niveles reales: {[k for k in ('tps','sl') if k in parsed_in_reply]}")
                await _update_signal_from_parsed(sig, parsed_in_reply, tg_ts=_tg_ts)
            # DESPUÉS: pasar por el classifier (con CONTEXTO del trade para
            # que Gemini decida con info en vivo: dirección, P&L, posiciones
            # abiertas, TPs, SL, etc.). El regex local sigue actuando primero.
            cl = await classify_async(text, signal=sig)
            await _execute_action(sig, cl, raw_text=text, tg_ts=_tg_ts)
        return

    if not is_canal2_entry(text):
        return

    parsed = parse_canal2(text)
    _log_telegram_understood(
        f"canal2_{msg.id}",
        channel="canal2",
        message_id=msg.id,
        kind="entry_signal",
        parser="parse_canal2",
        raw_text=text,
        parsed=parsed,
        tg_ts=_msg_ts_iso(msg),
    )
    if "direction" not in parsed:
        return

    direction = parsed["direction"]
    duplicate_ts = datetime.utcnow()
    duplicate = _canal2_duplicate_alias_candidate(
        msg.id, direction, duplicate_ts, parsed,
        state.open_signals("canal2"),
        config.STRATEGY_C2_DUPLICATE_ALIAS_WINDOW_S,
    )
    if duplicate is not None:
        _register_canal2_duplicate_alias(
            duplicate, msg.id, text, duplicate_ts,
            config.STRATEGY_C2_DUPLICATE_ALIAS_WINDOW_S,
        )
        print(f"[{label}] Canal2 duplicate alias: msg={msg.id} -> "
              f"{_sig_id(duplicate)} (no new order)")
        return

    # ── FILTRO 1: Skip RE-ENTER / 14h SELL (estrategias del análisis) ──
    max_entry_age_s = config.STRATEGY_ENTRY_MAX_TG_DELAY_S
    stale, age_s = _should_skip_stale_entry_signal(msg, max_entry_age_s)
    if stale:
        print(f"\n[{label}] SENAL STALE ignorada ({direction}, msg={msg.id}): "
              f"age={age_s:.1f}s > {max_entry_age_s:.1f}s")
        _log_stale_entry_skip(
            f"canal2_{msg.id}", "canal2", msg, "text",
            max_entry_age_s, age_s, direction=direction, text=text)
        return

    skip, reason = strategies.should_skip_signal(text, direction, "canal2")
    if skip:
        print(f"\n[{label}] ❌ SEÑAL IGNORADA ({direction}, msg={msg.id}): {reason}")
        return

    # ── FILTRO 2: Lot multiplier (HIGH RISK → lot/2) ──
    lot_mult, lot_reason = strategies.lot_multiplier_for_signal(text)
    if lot_mult <= 0:
        print(f"\n[{label}] ❌ SEÑAL IGNORADA ({direction}, msg={msg.id}): lot_mult=0")
        return

    # ── POST-SL momentum (point 4) — TP cap ──
    max_tp_idx = strategies.max_tp_index_for_signal("canal2")

    print(f"\n[{label}] Nueva señal: {direction} (msg={msg.id})"
          f"{f' [{lot_reason}]' if lot_reason else ''}"
          f"{f' [post-SL: TP cap idx {max_tp_idx}]' if max_tp_idx is not None else ''}")

    # Calcula el lotaje efectivo (mínimo 0.01)
    effective_lot = max(0.01, round(config.LOT_SIZE * lot_mult, 2))

    # ── JOURNAL: signal_received antes del fill para medir latencia ──
    sig_id_pre = f"canal2_{msg.id}"
    signal_received_utc = datetime.utcnow()
    # tg_to_bot_ms: delay Telegram→bot (campo computado para análisis rápido).
    # Negativo o muy alto = posible reconexión Telethon, mensaje stale.
    tg_to_bot_ms = None
    if msg.date:
        # msg.date viene timezone-aware (UTC); utcnow() es naive UTC. Forzamos
        # ambos a naive UTC restando tzinfo para no mezclar.
        tg_naive = msg.date.replace(tzinfo=None)
        tg_to_bot_ms = int((signal_received_utc - tg_naive).total_seconds() * 1000)
        if tg_to_bot_ms > 10000:
            print(f"[Canal2] ⚠ tg→bot delay alto: {tg_to_bot_ms}ms "
                  f"(msg.date={msg.date}). Posible reconexión Telethon — "
                  f"señal podría estar stale.")
    journal.event(sig_id_pre, "signal_received",
                  channel="canal2", direction=direction,
                  raw_text=text[:500], lot_mult=lot_mult,
                  effective_lot=effective_lot,
                  tg_ts=msg.date.isoformat(timespec="seconds") if msg.date else None,
                  tg_to_bot_ms=tg_to_bot_ms)

    # Contexto de mercado (ATR M5x14 + range reciente + precio): permite al
    # análisis posterior separar fallos de ejecución vs régimen adverso.
    if not _canal2_open_claim(msg.id):
        journal.event(sig_id_pre, "canal2_entry_open_already_in_progress",
                      reason="duplicate_new_or_recovered_edit",
                      raw_text=text[:500])
        return

    try:
        ctx = await _run(compute_market_context, config.MT5_SYMBOL)
    except Exception:
        _canal2_open_finished(msg.id)
        raise
    if ctx:
        journal.event(sig_id_pre, "market_context", **ctx)

    magic = config.magic_for("canal2")
    # open_market_with_fill devuelve (ticket, fill_price) en una sola llamada MT5
    # — evita el round-trip extra de entry_price() (~5ms ahorrados en hot path).
    try:
        result = await _run(
            executor.open_market_with_fill, direction, effective_lot,
            parsed.get("sl"), parsed["tps"][0] if parsed.get("tps") else None,
            f"c2_{msg.id}", magic)
    except Exception:
        _canal2_open_finished(msg.id)
        raise
    if not result:
        _canal2_open_finished(msg.id)
        journal.event(sig_id_pre, "market_fill_failed",
                      reason="executor.open_market returned None")
        journal.anomaly(sig_id_pre, "fill", "critical",
                        "executor.open_market devolvió None — la señal "
                        "no abrió posición",
                        channel="canal2", direction=direction)
        return
    ticket, fill_price = result

    # Timestamp justo tras el retorno de MT5 (más preciso que tras llamada extra)
    market_filled_utc = datetime.utcnow()
    fill_latency_ms = int((market_filled_utc - signal_received_utc).total_seconds() * 1000)
    # Tick context: bid/ask/spread inmediatamente tras el fill — para luego
    # poder analizar slippage real, condiciones de mercado en la apertura.
    tick_ctx = await _run(executor.current_tick_safe)
    journal.event(sig_id_pre, "market_filled",
                  ticket=ticket, price=fill_price, latency_ms=fill_latency_ms,
                  bid=tick_ctx.get("bid") if tick_ctx else None,
                  ask=tick_ctx.get("ask") if tick_ctx else None,
                  spread=tick_ctx.get("spread") if tick_ctx else None)

    # ── Estrategia Canal 2 (semana de prueba): intra_dca, TPs escalonados ──
    # N entradas equiespaciadas en el rango. TPs por orden de apertura
    # (ticket i → tps[i]); -1 en config → None aquí para activar escalonado.
    # BE_TP_INDEX -1 → sin BE auto (los mensajes del canal lo gestionan vía
    # classifier). Time-stop sólo notifica (no cierra) según
    # STRATEGY_TIME_STOP_NOTIFY_ONLY.
    c2_be_idx = config.STRATEGY_C2_BE_TP_INDEX if config.STRATEGY_C2_BE_TP_INDEX >= 0 else None
    c2_target_tp = (config.STRATEGY_C2_TARGET_TP_INDEX
                    if config.STRATEGY_C2_TARGET_TP_INDEX >= 0 else None)
    c2_time_stop = (datetime.utcnow() + timedelta(minutes=config.STRATEGY_C2_TIME_STOP_MIN)
                    if config.STRATEGY_C2_TIME_STOP_MIN > 0 else None)

    sig = Signal(
        channel="canal2",
        message_id=msg.id,
        direction=direction,
        market_ticket=ticket,
        market_fill_price=fill_price,
        lot_multiplier=lot_mult,
        max_tp_index=max_tp_idx,
        is_high_risk=(lot_mult != 1.0),
        time_stop_at=c2_time_stop,
        entry_mode=config.STRATEGY_C2_ENTRY_MODE,
        target_tp_index=c2_target_tp,
        be_at_tp_index=c2_be_idx,
        adverse_action=config.STRATEGY_C2_ADVERSE_ACTION,
    )
    state.add(sig)
    _emit_same_direction_overlap_anomaly(sig)
    journal.begin_trade(
        _sig_id(sig),
        channel="canal2",
        direction=direction,
        signal_received_utc=signal_received_utc.isoformat(timespec="milliseconds"),
        market_filled_utc=market_filled_utc.isoformat(timespec="milliseconds"),
        fill_latency_ms=fill_latency_ms,
        market_entry_price=fill_price,
        adverse_action=config.STRATEGY_C2_ADVERSE_ACTION,
    )
    _log_strategy_snapshot(
        sig,
        num_entries=config.STRATEGY_C2_NUM_ENTRIES,
        time_stop_min=config.STRATEGY_C2_TIME_STOP_MIN,
    )
    # Abre posiciones market extra (modo scale_out, o doble market legacy).
    await _open_extra_legs(sig, msg.id)
    await _update_signal_from_parsed(sig, parsed)
    deferred = _pop_deferred_canal2_entry_edit(msg.id)
    if deferred:
        deferred_parsed = parse_canal2(deferred["text"])
        journal.event(
            _sig_id(sig),
            "canal2_deferred_entry_edit_applied",
            parsed_keys=sorted(deferred_parsed.keys()),
            tg_ts=deferred.get("tg_ts"),
        )
        await _update_signal_from_parsed(
            sig, deferred_parsed, tg_ts=deferred.get("tg_ts"))
    _canal2_open_finished(msg.id)
    logger.log_signal(sig, parsed)


async def _process_canal2_edit(msg, label: str = "Canal2"):
    text = msg.text or ""

    if await _process_management_reply_edit(msg, "canal2", label):
        return

    sig  = state.get("canal2", msg.id)

    if sig is None:
        if not is_canal2_entry(text):
            return
        if _edit_already_seen("canal2", msg):
            print(f"[{label}] Edit huérfano duplicado msg={msg.id} "
                  f"edit_date={getattr(msg, 'edit_date', None)} — ignorado")
            return

        if _canal2_open_in_progress(msg.id):
            _defer_canal2_entry_edit(msg, text)
            journal.event(
                f"canal2_{msg.id}",
                "canal2_orphan_entry_edit_deferred",
                reason="market_open_in_progress",
                raw_text=text[:500],
                tg_ts=_msg_ts_iso(msg),
            )
            return

        max_age_s = config.STRATEGY_C2_ORPHAN_EDIT_MAX_AGE_S
        recover, age_s = _should_recover_canal2_orphan_entry_edit(
            msg, text, max_age_s)
        sig_id = f"canal2_{msg.id}"
        if recover:
            journal.event(sig_id, "canal2_orphan_entry_edit_recovered",
                          age_s=round(age_s, 1) if age_s is not None else None,
                          max_age_s=max_age_s,
                          raw_text=text[:500])
            await _process_canal2_new(msg, label=f"{label}_recover",
                                      dedup=False)
        else:
            journal.anomaly(
                sig_id, "channel_msg", "warning",
                "canal2 entry edit sin Signal en memoria ignorado por stale",
                age_s=round(age_s, 1) if age_s is not None else None,
                max_age_s=max_age_s,
                raw_text=text[:500],
            )
        return

    if sig.status != "open":
        return
    if not is_canal2_entry(text):
        return

    # Dedup: Telethon a veces re-emite el mismo MessageEdited (mismo edit_date).
    # Sin esto, llegamos a parsear y re-aplicar SL/TP por nada.
    if _edit_already_seen("canal2", msg):
        print(f"[{label}] Edit duplicado msg={msg.id} edit_date={msg.edit_date} — ignorado")
        return

    parsed = parse_canal2(text)
    _tg_edit_ts = msg.edit_date.isoformat(timespec="seconds") if msg.edit_date else None
    _log_telegram_understood(
        _sig_id(sig),
        channel="canal2",
        message_id=msg.id,
        kind="levels_update",
        parser="parse_canal2",
        raw_text=text,
        parsed=parsed,
        target_signal_id=_sig_id(sig),
        tg_ts=_tg_edit_ts,
        is_edit=True,
    )
    print(f"[{label}] Edit señal {msg.id}: {list(parsed.keys())} tg_edit={_tg_edit_ts}")
    await _update_signal_from_parsed(sig, parsed, tg_ts=_tg_edit_ts)


# ─── Canal 2 handlers ─────────────────────────────────────────────────────────
#
# DIAGNOSTIC: añadimos un timestamp justo al ENTRAR el handler, antes de
# CUALQUIER procesamiento. Esto separa:
#   • Tiempo Telegram→Telethon→handler (delay externo, fuera de nuestro control)
#   • Tiempo handler→signal_received event (procesamiento nuestro)
# Sesiones 04-29..05-04 mostraron canal 2 con delay tg→bot 21s mediano vs
# canal 1 con 1s. Necesitamos saber dónde está exactamente.

def _msg_diag(msg, channel: str, kind: str):
    """Logea handler_entry con info completa del mensaje para diagnostico.

    Captura tipo de mensaje (sticker/text/photo/other), sticker_id si aplica,
    preview de texto, y reply context. Sin esto es imposible saber por que un
    mensaje no genero signal_received (visto en sesion 2026-05-06: canal1_19442
    llego pero no se proceso, sin pista en logs de que tipo era ni por que).
    """
    import journal as _j
    try:
        handler_entry = datetime.utcnow()
        tg_ts = msg.date or msg.edit_date
        if tg_ts:
            tg_naive = tg_ts.replace(tzinfo=None)
            delay_ms = int((handler_entry - tg_naive).total_seconds() * 1000)
        else:
            delay_ms = None

        # Detectar tipo y capturar info útil
        msg_type = "unknown"
        sticker_id = None
        text_preview = None
        has_reply = False
        reply_to = None

        if getattr(msg, "sticker", None):
            msg_type = "sticker"
            try:
                sticker_id = msg.sticker.id
            except Exception:
                sticker_id = None
        elif getattr(msg, "photo", None):
            msg_type = "photo"
            text_preview = (msg.text or msg.message or "")[:120] if (msg.text or msg.message) else None
        elif getattr(msg, "document", None):
            msg_type = "document"
        else:
            msg_type = "text"
            txt = msg.text or msg.message or ""
            text_preview = txt[:120].replace("\n", " | ") if txt else None

        if getattr(msg, "reply_to", None) and getattr(msg.reply_to, "reply_to_msg_id", None):
            has_reply = True
            reply_to = msg.reply_to.reply_to_msg_id

        _j.event(f"{channel}_{msg.id}", "telegram_raw",
                 **_telegram_raw_payload(msg, channel, kind))
        _j.event(f"{channel}_{msg.id}", "handler_entry",
                 kind=kind,
                 channel=channel,
                 msg_type=msg_type,
                 sticker_id=sticker_id,
                 text_preview=text_preview,
                 has_reply=has_reply,
                 reply_to_msg_id=reply_to,
                 tg_ts=tg_ts.isoformat(timespec="seconds") if tg_ts else None,
                 handler_entry_ts=handler_entry.isoformat(timespec="milliseconds"),
                 telegram_to_handler_ms=delay_ms)
    except Exception as e:
        print(f"[DIAG] error _msg_diag: {e}")


@client.on(events.NewMessage(chats=[config.CANAL_2_ID]))
async def canal2_new(event):
    _msg_diag(event.message, "canal2", "new")
    await _process_canal2_new(event.message)


@client.on(events.MessageEdited(chats=[config.CANAL_2_ID]))
async def canal2_edit(event):
    _msg_diag(event.message, "canal2", "edit")
    await _process_canal2_edit(event.message)


# ─── Helpers PUROS de la estrategia C — CLOSE_FIRST canal2 (rescate BE) ───
# Análisis de 5 señales CLOSE_FIRST perdedoras (19+20 mayo): el peor caso
# (canal2_12691, −$26.55) tuvo el precio rebotando al entry en 7 segundos
# pero la lógica clásica ya había cerrado 2 a mercado en pérdida y dejado
# las 3 restantes hit SL del proveedor. La estrategia C aprovecha el rebote:
# pone TP=BE en LAS 5 cuando NO hay profit + time-stop 60s defensivo.

def _close_first_decision(price_vs_entry_pts: float,
                          profit_threshold_pts: float = 0.5) -> str:
    """Decide la rama de CLOSE_FIRST canal2:

      "close_half"    → lógica clásica (cerrar mitad a mercado, BE en resto).
                        Se aplica solo cuando hay profit REAL (> threshold).
      "set_tp_be_all" → rama RESCATE BE para todas las posiciones + time-stop.
                        Para los casos en loss o BE donde el proveedor
                        está cerrando por reversal inminente, no por profit.

    Pura — solo decide la rama. La aplicación va en _execute_actions.

    profit_threshold_pts: cuánto profit (en puntos) consideramos "real".
        +0.5 cubre el spread+comisiones típicos de XAUUSD en Vantage.
        Estrictamente '>'  — en el threshold exacto va a la rama segura.
    """
    if price_vs_entry_pts > profit_threshold_pts:
        return "close_half"
    return "set_tp_be_all"


def _safe_tp_be(direction: str, entry: float, current_price: float,
                stops_level_pts: float, padding_pts: float = 0.05) -> float:
    """TP=BE seguro que respeta el stops_level del broker.

    MT5 rechaza con INVALID_STOPS (10016) si el TP está demasiado cerca
    del precio actual. Calculamos:
        - candidato ideal: entry ± padding (queremos cerrar en BE exacto)
        - mínimo legal: current_price ± (stops_level + padding)
    Devolvemos el que esté MÁS LEJOS en la dirección favorable (BUY: max;
    SELL: min), garantizando que MT5 acepte el MODIFY.

    Para BUY el TP debe estar POR ENCIMA del precio actual + stops_level.
    Para SELL el TP debe estar POR DEBAJO del precio actual − stops_level.

    Pura — usable desde tests sin MT5.
    """
    if direction == "BUY":
        candidate = entry + padding_pts
        min_legal = current_price + stops_level_pts + padding_pts
        return max(candidate, min_legal)
    else:
        candidate = entry - padding_pts
        max_legal = current_price - stops_level_pts - padding_pts
        return min(candidate, max_legal)


# ─── Helpers PUROS de los detectores Batch C (silent failures de estado) ──

def _scale_out_fill_summary(n_legs_attempted: int,
                            n_legs_filled: int) -> dict:
    """Severidad de fallos de fill en _open_extra_legs (modo scale_out).

    Antes solo loguebamos cada `scale_out_leg_fill_failed` por separado.
    Si fallaban TODAS las legs extra, la señal se quedaba con UNA SOLA
    posicion en vez de N — degeneración silenciosa.

    Reglas (basadas en cuántas legs faltaron):
      - 0 attempts            → None (no es scale_out)
      - filled == 0           → "critical" (todas fallaron)
      - filled / attempts < 0.5 → "critical" (degradación severa)
      - filled / attempts < 1.0 → "warning"  (parcial razonable)
      - 100% filled           → None (caso normal)
    """
    if n_legs_attempted == 0:
        return {"severity": None, "fill_ratio": 1.0}
    ratio = n_legs_filled / n_legs_attempted
    if ratio == 1.0:
        severity = None
    elif ratio < 0.5:
        severity = "critical"
    else:
        severity = "warning"
    return {"severity": severity, "fill_ratio": ratio}


def _dca_monitor_task_anomaly_severity(exc):
    """Decide si una task de dca_monitor.run() terminada merece anomaly.

    asyncio.create_task() NO re-lanza excepciones — la task muere en
    silencio y la señal queda sin vigilancia (TPs, SL, time-stop). Esta
    es la check que el callback usa para decidir si emitir anomaly.

      - None  (terminó normal)            → None  (señal cerró bien)
      - CancelledError (shutdown ordenado) → None
      - cualquier otra excepción          → "critical"
    """
    if exc is None:
        return None
    import asyncio as _aio_b3
    if isinstance(exc, _aio_b3.CancelledError):
        return None
    return "critical"


def _detect_state_add_overwrite(new_signal, state_mgr) -> bool:
    """¿Vamos a sobrescribir una key ya existente en state._signals?

    StateManager.add usa `(channel, msg_id)` como key. Si ya existe ese key
    y apunta a OTRO objeto Signal, el viejo se pierde silenciosamente
    (resync incorrecto, bug raro de doble proceso, etc.). Re-add del mismo
    objeto (idempotencia) NO es overwrite.

    Pura — no muta state. Se llama ANTES de state.add() en el caller.
    """
    key = f"{new_signal.channel}_{new_signal.message_id}"
    existing = state_mgr._signals.get(key)
    if existing is None:
        return False
    return existing is not new_signal


# ─── Helpers PUROS de los detectores Batch B (silent failures de canal) ────

def _classify_deleted_msg_impact(channel: str, msg_id: int,
                                  state_mgr) -> dict:
    """Decide el impacto de un mensaje BORRADO por el canal.

    Cuando el canal hace delete sobre un mensaje que ya procesamos:
      - signal_open: era una señal con posición ABIERTA → riesgo alto, el
        proveedor está retractando un trade que nosotros tenemos vivo.
      - signal_closed: era una señal ya cerrada → solo informativo.
      - management: era un alias de canal1 (msg de texto que dispara la
        gestión) — la posición principal sigue vigente bajo otro msg_id.
      - unknown: el msg_id no estaba en state — chatter, info, o pre-bot.

    Diferenciar "management" del "signal_open" requiere comparar contra
    el msg_id PRINCIPAL del Signal — si match, es la señal; si difiere
    pero apunta al mismo objeto, es un alias.
    """
    key = f"{channel}_{msg_id}"
    sig = state_mgr._signals.get(key)
    if sig is None:
        return {"kind": "unknown", "sig_id": None}

    sig_id_real = f"{sig.channel}_{sig.message_id}"

    # Alias: el msg_id borrado NO es el principal pero apunta al mismo Signal.
    if sig.message_id != msg_id:
        return {"kind": "management", "sig_id": sig_id_real}

    # Es el msg_id principal → según status
    if sig.status == "open":
        return {"kind": "signal_open", "sig_id": sig_id_real}
    return {"kind": "signal_closed", "sig_id": sig_id_real}


def _diff_canal1_edit(prev_signal, new_parsed: dict) -> dict:
    """Compara el parsed nuevo del edit vs la Signal viva.

    Canal 1 antes NO tenía handler de MessageEdited (solo canal 2). Si el
    proveedor editaba el mensaje de texto para corregir TPs/SL, perdíamos
    el cambio — la posición en MT5 seguía con los niveles viejos. Este
    helper devuelve qué cambió para decidir si el bot debe alertar.

    Ausencia de un campo en `new_parsed` (parser no lo encontró en el
    texto editado) NO cuenta como cambio — el parser puede extraer menos
    info de un edit que del texto original sin que haya nada que actualizar.
    """
    sl_changed = ("sl" in new_parsed
                  and new_parsed["sl"] != prev_signal.sl)
    tps_changed = ("tps" in new_parsed
                   and list(new_parsed["tps"]) != list(prev_signal.tps))
    direction_changed = ("direction" in new_parsed
                         and new_parsed["direction"] != prev_signal.direction)
    range_low_changed = False
    range_high_changed = False
    if "range" in new_parsed and new_parsed["range"]:
        new_lo, new_hi = new_parsed["range"]
        range_low_changed = (new_lo != prev_signal.range_low)
        range_high_changed = (new_hi != prev_signal.range_high)
    range_changed = range_low_changed or range_high_changed

    return {
        "material_change": bool(sl_changed or tps_changed
                                or direction_changed or range_changed),
        "sl_changed": sl_changed,
        "tps_changed": tps_changed,
        "direction_changed": direction_changed,
        "range_changed": range_changed,
        "previous": {
            "sl": prev_signal.sl,
            "tps": list(prev_signal.tps),
            "direction": prev_signal.direction,
            "range_low": prev_signal.range_low,
            "range_high": prev_signal.range_high,
        },
        "new": {
            "sl": new_parsed.get("sl"),
            "tps": list(new_parsed.get("tps", [])),
            "direction": new_parsed.get("direction"),
            "range": new_parsed.get("range"),
        },
    }


def _strict_vs_loose_canal1_filter(text: str) -> dict:
    """Compara is_canal1_signal_text (STRICT actual) vs la lógica LOOSE previa.

    Tras commit d4bf1a6 endurecimos el filtro para exigir TP con nivel
    numérico (cerró el bug canal1_19778, que abrió 4 posiciones naked sobre
    un "GOLD UPDATE"). Loggear los casos donde LOOSE pasaba y STRICT
    rechaza nos da dos beneficios:
      a) Visibilidad de cuántos chatter mensajes filtra bien el strict.
      b) Detección rápida si un día STRICT rechaza una señal real
         (regresión del filtro — habría que ajustar el regex).

    LOOSE = (BUY|SELL|LONG|SHORT) + (XAU|GOLD|ORO) + 'TP' como substring.
    STRICT = lo mismo pero 'TP' con nivel numérico (regex).
    """
    import re as _re_b3
    t = (text or "").upper()
    has_dir = ("BUY" in t) or ("SELL" in t) or ("LONG" in t) or ("SHORT" in t)
    has_gold = ("XAU" in t) or ("GOLD" in t) or ("ORO" in t)
    loose_has_tp = "TP" in t
    loose = has_dir and has_gold and loose_has_tp
    strict = is_canal1_signal_text(text)
    return {
        "loose": loose,
        "strict": strict,
        "strict_blocked_loose_signal": (loose and not strict),
    }


# ─── Canal 1 handlers ─────────────────────────────────────────────────────────

def _standalone_mgmt_route(n_open_signals: int, has_actionable: bool) -> str:
    """Decide qué hacer con un mensaje de gestión de canal1 SIN reply.

    Un mensaje suelto (no-reply) no dice explícitamente a qué señal se
    refiere. Regla acordada con el usuario: solo se EJECUTA una acción si el
    destino es INEQUÍVOCO — exactamente una señal canal1 abierta. Con >1
    abiertas el bot no sabe a cuál va → notifica al usuario para que decida.
    Sin acción accionable (chatter / informativo) → solo se registra.

    Devuelve: "apply" | "notify" | "log".
    """
    if not has_actionable:
        return "log"
    if n_open_signals == 1:
        return "apply"
    if n_open_signals >= 2:
        return "notify"
    return "log"   # 0 señales abiertas → nada que gestionar


async def _handle_canal1_standalone(msg, text: str, sig_id: str):
    """Procesa un mensaje suelto (no-reply, no-señal) de canal1.

    Antes estos mensajes se descartaban SIEMPRE como 'text_not_canal1_signal'
    — y con ellos órdenes de gestión que el canal manda sin responder a la
    señal ("close in profits", "move sl..."). Ahora se clasifican. Ver
    _standalone_mgmt_route para la regla del destino.
    """
    txt_preview = (text or "")[:120].replace("\n", " | ")
    open_c1 = state.open_signals("canal1")

    if not open_c1:
        # Sin señal canal1 abierta no hay nada que gestionar — solo registrar.
        journal.event(sig_id, "msg_dropped",
                      reason="text_not_canal1_signal",
                      text_preview=txt_preview)
        return

    # Clasificar con la señal más reciente como contexto del trade.
    cl = await classify_async(text, signal=open_c1[0])
    actionable = [a for a in cl
                  if a.get("action") not in ("INFORMATIONAL", None)]
    route = _standalone_mgmt_route(len(open_c1), bool(actionable))

    if route == "log":
        journal.event(sig_id, "msg_dropped",
                      reason="standalone_text_not_actionable",
                      text_preview=txt_preview)
    elif route == "apply":
        target = open_c1[0]
        journal.event(sig_id, "standalone_mgmt_applied",
                      target=_sig_id(target),
                      actions=[a.get("action") for a in actionable],
                      text_preview=txt_preview)
        await _execute_action(target, cl, raw_text=text)
    else:  # notify — destino ambiguo (>1 señal canal1 abierta)
        open_ids = [_sig_id(s) for s in open_c1]
        journal.event(sig_id, "standalone_mgmt_ambiguous",
                      n_open=len(open_c1), open_signals=open_ids,
                      actions=[a.get("action") for a in actionable],
                      text_preview=txt_preview)
        # Capa de anomalía estructurada para el ledger (T3 del plan).
        # warning → no auto-notify; el notify rico de abajo se mantiene
        # porque este caso REQUIERE acción humana y el texto rico es
        # más útil que el genérico de _notify_critical.
        journal.anomaly(sig_id, "channel_msg", "warning",
                        f"mensaje accionable canal1 con destino ambiguo "
                        f"({len(open_c1)} señales abiertas) — requiere "
                        f"acción humana",
                        n_open=len(open_c1), open_signals=open_ids,
                        actions=[a.get("action") for a in actionable])
        try:
            asyncio.create_task(notify(
                f"⚠ Canal1: mensaje de gestión SIN reply, destino AMBIGUO.\n\n"
                f"Texto: {text[:200]}\n"
                f"Acción(es) detectada(s): "
                f"{[a.get('action') for a in actionable]}\n"
                f"Señales canal1 abiertas ({len(open_c1)}): "
                f"{', '.join(open_ids)}\n\n"
                f"El bot NO actuó — no sabe a cuál se refiere. Revísalo en "
                f"MT5 y aplica la gestión manualmente si corresponde."
            ))
        except Exception:
            pass


async def _process_canal1_new(msg):
    sig_id = f"canal1_{msg.id}"

    # Dedup: poller + event handler comparten despacho.
    if _new_msg_already_seen("canal1", msg.id):
        return

    # Sticker → entrada inmediata
    if msg.sticker:
        await _handle_canal1_sticker(msg)
        return

    text = msg.text or ""

    # Mensaje de gestión (reply a una señal)
    if msg.reply_to and msg.reply_to.reply_to_msg_id:
        reply_id = msg.reply_to.reply_to_msg_id
        sig = state.get("canal1", reply_id) or state.get("canal2", reply_id)
        if sig and sig.status == "open":
            # Gemini con contexto del trade (dirección, P&L, posiciones, etc.)
            cl = await classify_async(text, signal=sig)
            await _execute_action(sig, cl, raw_text=text)
        else:
            journal.event(sig_id, "msg_dropped",
                          reason="reply_to_unknown_signal",
                          reply_to_msg_id=reply_id,
                          text_preview=(text or "")[:120].replace("\n", " | "))
        return

    # Texto con TP/SL que sigue al sticker
    if is_canal1_signal_text(text):
        await _handle_canal1_text(msg, text)
    else:
        # B3 (Batch B): detectar si STRICT rechaza un texto que LOOSE
        # habría aceptado. Sin esto, el tightening del filtro
        # is_canal1_signal_text (commit d4bf1a6 — cerró bug canal1_19778)
        # es invisible en logs. Queremos:
        #   1) Confirmar que STRICT bloquea el chatter como esperábamos.
        #   2) Detectar inmediatamente si STRICT bloquea una señal real
        #      (regresión del filtro — habría que ajustar regex).
        try:
            cmp = _strict_vs_loose_canal1_filter(text)
            if cmp["strict_blocked_loose_signal"]:
                journal.anomaly(sig_id, "channel_msg", "info",
                                "filtro STRICT canal1 rechazó un texto "
                                "que el LOOSE aceptaba — verificar que no "
                                "sea una señal real con formato distinto",
                                text_preview=text[:200].replace("\n", " | "))
        except Exception:
            pass

        # Texto suelto de canal1 que NO es señal: chatter ("Gold crazy today")
        # o gestión sin reply ("close in profits"). Antes se descartaba
        # siempre — se perdían órdenes de gestión que el canal manda sin
        # responder a la señal. Ahora se clasifica y, si el destino es
        # inequívoco, se aplica (ver _handle_canal1_standalone).
        await _handle_canal1_standalone(msg, text, sig_id)


@client.on(events.NewMessage(chats=[config.CANAL_1_ID]))
async def canal1_new(event):
    _msg_diag(event.message, "canal1", "new")
    await _process_canal1_new(event.message)


async def _process_canal1_edit(msg):
    """Procesa MessageEdited de canal1.

    Antes canal1 NO tenía handler de edits (solo canal2). Si el proveedor
    editaba el texto de una señal para corregir TPs/SL, perdíamos el
    cambio y la posición MT5 seguía con los niveles viejos. Es exactamente
    el tipo de fallo silencioso del Batch B — el bot no se entera de que
    los niveles cambiaron y solo reaccionamos al SL/TP viejo.

    Comportamiento:
      1. Solo si el msg_id pertenece a una señal/alias canal1 viva.
      2. Re-parsear el texto del edit.
      3. _diff_canal1_edit decide si hubo cambio material.
      4. Si cambian TPs/SL, reaplicar con el mismo validador del texto
         inicial. Si cambia direccion o solo rango, avisar sin actuar.
    """
    if await _process_management_reply_edit(msg, "canal1", "Canal1"):
        return

    sig = state.get("canal1", msg.id)
    if sig is None or sig.status != "open":
        return

    text = msg.text or msg.message or ""
    if not text:
        return

    # Dedup de Telethon: a veces re-emite el mismo edit
    if _edit_already_seen("canal1", msg):
        return

    parsed = parse_canal1_text(text)
    diff = _diff_canal1_edit(sig, parsed)

    sig_id = _sig_id(sig)
    _log_telegram_understood(
        sig_id,
        channel="canal1",
        message_id=msg.id,
        kind="levels_update",
        parser="parse_canal1_text",
        raw_text=text,
        parsed=parsed,
        target_signal_id=sig_id,
        tg_ts=_msg_ts_iso(msg),
        is_edit=True,
    )
    journal.event(sig_id, "canal1_text_edited",
                  source_msg_id=msg.id,
                  material_change=diff["material_change"],
                  sl_changed=diff["sl_changed"],
                  tps_changed=diff["tps_changed"],
                  direction_changed=diff["direction_changed"],
                  range_changed=diff["range_changed"],
                  text_preview=text[:250].replace("\n", " | "))

    if not diff["material_change"]:
        return

    if ((diff["sl_changed"] or diff["tps_changed"])
            and not diff["direction_changed"]):
        tg_edit_ts = (
            msg.edit_date.isoformat(timespec="seconds")
            if msg.edit_date else None
        )
        journal.event(sig_id, "canal1_text_edit_auto_applied",
                      source_msg_id=msg.id,
                      sl_changed=diff["sl_changed"],
                      tps_changed=diff["tps_changed"],
                      range_changed=diff["range_changed"],
                      previous=diff["previous"],
                      new=diff["new"])
        await _update_signal_from_parsed(sig, parsed, tg_ts=tg_edit_ts)
        return

    # Cambio material → anomaly + notify rico (la posición MT5 sigue con
    # los niveles VIEJOS, el operador tiene que decidir si reajustar)
    severity = "critical" if diff["direction_changed"] else "warning"
    journal.anomaly(sig_id, "channel_msg", severity,
                    "canal1 editó el mensaje de señal — niveles cambiaron "
                    "tras la apertura, MT5 NO se actualiza automáticamente",
                    source_msg_id=msg.id,
                    previous=diff["previous"], new=diff["new"],
                    sl_changed=diff["sl_changed"],
                    tps_changed=diff["tps_changed"],
                    direction_changed=diff["direction_changed"])

    # Notify rico: contexto humano sobre qué cambió y qué hacer
    changes = []
    if diff["sl_changed"]:
        changes.append(f"SL: {diff['previous']['sl']} → {diff['new']['sl']}")
    if diff["tps_changed"]:
        changes.append(
            f"TPs: {diff['previous']['tps']} → {diff['new']['tps']}")
    if diff["direction_changed"]:
        changes.append(
            f"⚠ DIRECCIÓN: {diff['previous']['direction']} → "
            f"{diff['new']['direction']} (revisar URGENTE)")
    if diff["range_changed"]:
        prev_rng = (diff["previous"]["range_low"],
                    diff["previous"]["range_high"])
        changes.append(
            f"Range: {prev_rng} → {diff['new']['range']}")

    try:
        asyncio.create_task(notify(
            f"📝 Canal 1: edición de mensaje señal {sig_id}\n"
            f"\n"
            f"Cambios: \n  • " + "\n  • ".join(changes) + "\n"
            f"\n"
            f"La posición MT5 NO se ha actualizado automáticamente.\n"
            f"Revisa el ticket #{sig.market_ticket} y ajusta si procede."
        ))
    except Exception:
        pass


@client.on(events.MessageEdited(chats=[config.CANAL_1_ID]))
async def canal1_edit(event):
    _msg_diag(event.message, "canal1", "edit")
    await _process_canal1_edit(event.message)


@client.on(events.MessageDeleted(chats=[config.CANAL_1_ID, config.CANAL_2_ID]))
async def channel_message_deleted(event):
    """El canal borra un mensaje que ya procesamos.

    Antes el bot no se enteraba — si el proveedor retractaba una señal
    (delete del mensaje original) seguíamos con la posición abierta sin
    ninguna alerta. Ahora clasificamos el impacto y notificamos según
    severidad.

    Notas Telethon:
      - event.deleted_ids: lista de int (msg_ids borrados).
      - event.chat_id: ID del chat. Telethon a veces no resuelve canal por
        deleted_ids, por lo que comprobamos AMBOS canales para cada id.
    """
    try:
        chat_id = event.chat_id
    except Exception:
        chat_id = None

    # Determinar canal por chat_id, con fallback a probar los dos
    candidate_channels = []
    if chat_id == config.CANAL_1_ID:
        candidate_channels = ["canal1"]
    elif chat_id == config.CANAL_2_ID:
        candidate_channels = ["canal2"]
    else:
        # Telethon a veces no resuelve correctamente, probamos los 2
        candidate_channels = ["canal1", "canal2"]

    for msg_id in (event.deleted_ids or []):
        impact = None
        impact_channel = None
        for ch in candidate_channels:
            cand = _classify_deleted_msg_impact(ch, msg_id, state)
            if cand["kind"] != "unknown":
                impact = cand
                impact_channel = ch
                break
        if impact is None:
            # Mensaje borrado de canal que NO procesamos — chatter, info,
            # mensaje pre-bot. Solo logueamos con bajo detalle (no anomaly).
            journal.event(f"{candidate_channels[0]}_{msg_id}",
                          "channel_msg_deleted",
                          chat_id=chat_id, kind="unknown")
            continue

        sig_id = impact["sig_id"]
        kind = impact["kind"]
        journal.event(sig_id, "channel_msg_deleted",
                      chat_id=chat_id, kind=kind, msg_id=msg_id,
                      channel=impact_channel)

        if kind == "signal_open":
            # CRÍTICO: el proveedor retractó una señal con posición viva
            journal.anomaly(sig_id, "channel_msg", "critical",
                            "canal borró el mensaje de una señal con "
                            "posición ABIERTA — proveedor retractó el "
                            "trade, MT5 sigue con la posición viva",
                            msg_id=msg_id, channel=impact_channel)
        elif kind == "signal_closed":
            # Histórico, no urgente, pero queda registrado en el journal
            journal.anomaly(sig_id, "channel_msg", "info",
                            "canal borró el mensaje de una señal ya "
                            "cerrada — solo informativo",
                            msg_id=msg_id, channel=impact_channel)
        elif kind == "management":
            # Borraron un msg de gestión que ya aplicamos — pueden estar
            # corrigiendo algo. Warning para revisar contexto.
            journal.anomaly(sig_id, "channel_msg", "warning",
                            "canal borró un mensaje (alias) de la señal — "
                            "puede ser corrección de gestión",
                            msg_id=msg_id, channel=impact_channel)


async def _handle_canal1_sticker(msg):
    sticker_id = msg.sticker.id
    sig_id = f"canal1_{msg.id}"

    if sticker_id == config.CANAL1_BUY_STICKER_ID:
        direction = "BUY"
    elif sticker_id == config.CANAL1_SELL_STICKER_ID:
        direction = "SELL"
    else:
        # Sticker no es uno de los IDs de BUY/SELL configurados. Antes solo
        # se printeaba a consola y el evento se perdia — no quedaba rastro
        # en el journal, asi que era imposible diagnosticar despues "por que
        # no se abrio esta operacion" (visto en sesion 2026-05-06).
        # Ahora lo logueamos para poder detectar variantes nuevas de stickers.
        print(f"[Canal1] Sticker desconocido ID={sticker_id} (msg={msg.id})")
        print(f"         → Si es BUY:  añade CANAL1_BUY_STICKER_ID={sticker_id} al .env")
        print(f"         → Si es SELL: añade CANAL1_SELL_STICKER_ID={sticker_id} al .env")
        journal.event(sig_id, "sticker_unknown",
                      sticker_id=sticker_id,
                      configured_buy=config.CANAL1_BUY_STICKER_ID,
                      configured_sell=config.CANAL1_SELL_STICKER_ID,
                      tg_ts=msg.date.isoformat(timespec="seconds") if msg.date else None)
        # Notificamos al usuario para que pueda añadir el sticker al .env si
        # corresponde a una entrada nueva — sin esto las señales se pierden
        # silenciosamente.
        try:
            asyncio.create_task(notify(
                f"⚠ Canal 1: sticker desconocido (ID={sticker_id}).\n"
                f"Si era una entrada BUY/SELL, añade el ID al .env "
                f"como CANAL1_BUY_STICKER_ID o CANAL1_SELL_STICKER_ID."
            ))
        except Exception:
            pass
        return

    # ── FILTRO: RE-ENTER no aplica al sticker (HIGH RISK tampoco) ──
    _log_telegram_understood(
        sig_id,
        channel="canal1",
        message_id=msg.id,
        kind="entry_signal",
        parser="sticker_id",
        parsed={"direction": direction},
        tg_ts=_msg_ts_iso(msg),
    )

    skip, reason = strategies.should_skip_signal("", direction, "canal1")
    if skip:
        print(f"\n[Canal1] ❌ SEÑAL IGNORADA ({direction}, msg={msg.id}): {reason}")
        return

    # ── POST-SL para Canal 1 ──
    duplicate_ts = datetime.utcnow()
    duplicate = _canal1_duplicate_sticker_candidate(
        msg.id, direction, duplicate_ts,
        state.open_signals("canal1"),
        config.STRATEGY_C1_DUPLICATE_STICKER_WINDOW_S,
    )
    if duplicate is not None:
        _register_canal1_duplicate_sticker_alias(
            duplicate, msg.id, sticker_id, duplicate_ts,
            config.STRATEGY_C1_DUPLICATE_STICKER_WINDOW_S,
        )
        print(f"[Canal1] Duplicate sticker alias: msg={msg.id} -> "
              f"{_sig_id(duplicate)} (no new order)")
        return

    max_entry_age_s = config.STRATEGY_ENTRY_MAX_TG_DELAY_S
    stale, age_s = _should_skip_stale_entry_signal(msg, max_entry_age_s)
    if stale:
        print(f"\n[Canal1] STICKER STALE ignorado ({direction}, msg={msg.id}): "
              f"age={age_s:.1f}s > {max_entry_age_s:.1f}s")
        _log_stale_entry_skip(
            sig_id, "canal1", msg, "sticker",
            max_entry_age_s, age_s, direction=direction)
        return

    max_tp_idx = strategies.max_tp_index_for_signal("canal1")

    print(f"\n[Canal1] Sticker {direction} (msg={msg.id})"
          f"{f' [post-SL: TP cap idx {max_tp_idx}]' if max_tp_idx is not None else ''}")

    # ── JOURNAL: signal_received antes del fill ──
    sig_id_pre = f"canal1_{msg.id}"
    signal_received_utc = datetime.utcnow()
    # tg_to_bot_ms: ver canal2_new arriba para explicación
    tg_to_bot_ms = None
    if msg.date:
        tg_naive = msg.date.replace(tzinfo=None)
        tg_to_bot_ms = int((signal_received_utc - tg_naive).total_seconds() * 1000)
        if tg_to_bot_ms > 10000:
            print(f"[Canal1] ⚠ tg→bot delay alto: {tg_to_bot_ms}ms "
                  f"(msg.date={msg.date}). Posible reconexión Telethon.")
    journal.event(sig_id_pre, "signal_received",
                  channel="canal1", direction=direction,
                  trigger="sticker", sticker_id=sticker_id,
                  tg_ts=msg.date.isoformat(timespec="seconds") if msg.date else None,
                  tg_to_bot_ms=tg_to_bot_ms)

    # Contexto de mercado al entrar — ver comentario en canal2_new.
    ctx = await _run(compute_market_context, config.MT5_SYMBOL)
    if ctx:
        journal.event(sig_id_pre, "market_context", **ctx)

    magic = config.magic_for("canal1")
    # 1 sola llamada MT5 que devuelve (ticket, fill_price) — ahorra round-trip extra
    result = await _run(executor.open_market_with_fill, direction, config.LOT_SIZE,
                        None, None, f"c1_{msg.id}", magic)
    if not result:
        journal.event(sig_id_pre, "market_fill_failed",
                      reason="executor.open_market returned None")
        journal.anomaly(sig_id_pre, "fill", "critical",
                        "executor.open_market devolvió None — sticker "
                        "recibido pero el bot no abrió posición",
                        channel="canal1", direction=direction)
        return
    ticket, fill_price = result

    market_filled_utc = datetime.utcnow()
    fill_latency_ms = int((market_filled_utc - signal_received_utc).total_seconds() * 1000)
    tick_ctx = await _run(executor.current_tick_safe)
    journal.event(sig_id_pre, "market_filled",
                  ticket=ticket, price=fill_price, latency_ms=fill_latency_ms,
                  bid=tick_ctx.get("bid") if tick_ctx else None,
                  ask=tick_ctx.get("ask") if tick_ctx else None,
                  spread=tick_ctx.get("spread") if tick_ctx else None)

    sig = Signal(
        channel="canal1",
        message_id=msg.id,
        direction=direction,
        market_ticket=ticket,
        market_fill_price=fill_price,
        max_tp_index=max_tp_idx,
        time_stop_at=strategies.time_stop_for_signal(datetime.utcnow()),
        adverse_action=config.STRATEGY_C1_ADVERSE_ACTION,
    )
    state.add(sig)
    journal.begin_trade(
        _sig_id(sig),
        channel="canal1",
        direction=direction,
        signal_received_utc=signal_received_utc.isoformat(timespec="milliseconds"),
        market_filled_utc=market_filled_utc.isoformat(timespec="milliseconds"),
        fill_latency_ms=fill_latency_ms,
        market_entry_price=fill_price,
        adverse_action=config.STRATEGY_C1_ADVERSE_ACTION,
    )
    _log_strategy_snapshot(
        sig,
        num_entries=config.STRATEGY_C1_NUM_ENTRIES,
        time_stop_min=config.STRATEGY_C1_TIME_STOP_MIN,
    )
    # Abre posiciones market extra (modo scale_out, o doble market legacy).
    await _open_extra_legs(sig, msg.id)
    print(f"[Canal1] Mercado abierto, esperando texto con TP/SL...")


def _should_accept_canal1_text(sig, now=None) -> bool:
    """Decide si el texto canal1 debe asociarse con el sticker abierto.

    Reglas:
      - sig None: rechazar (no hay señal abierta).
      - sig SIN TPs: aceptar SIEMPRE, sin importar el timestamp. Esto cubre:
          * Canal 1 que tarda más de 5min en mandar el texto.
          * Tras un restart del bot, signal.timestamp = position.time de MT5
            (puede ser de hace horas). Sin esta excepción, el texto que
            llegaba después del restart se rechazaba y la posición quedaba
            naked sin TPs/SL (visto sesión 2026-05-06 canal1_19439).
      - sig CON TPs: aplicar cutoff de 5min. El texto adicional para una
        señal vigente puede actualizar TPs/SL si llegó pronto, pero si llega
        muy tarde es probablemente texto canal1 antiguo asociado erróneamente
        con un sticker más reciente (ej. al recoger histórico al arrancar).

    Función pura (sin side effects) para que sea testeable sin mockear
    el listener entero.
    """
    if sig is None:
        return False
    if not sig.tps:
        return True
    if now is None:
        now = datetime.utcnow()
    cutoff = now - timedelta(minutes=5)
    return sig.timestamp >= cutoff


async def _open_canal1_from_text(msg, parsed: dict):
    """Abre market canal1 desde un mensaje de TEXTO (sin sticker previo).

    Caso: el sticker no llego al bot (rate-limit Telethon, reconexion,
    primer mensaje tras restart, etc.) pero el texto si llega. El texto
    canal1 contiene direction + range + TPs + SL — info suficiente para
    abrir la posicion sin esperar al sticker.

    Casos reales perdidos antes de este fix:
      - sesion 2026-05-08: canal1_19511, 19515, 19521, 19531
      - sesion 2026-05-12: canal1_19574

    Devuelve la Signal creada (registrada en state) o None si no se pudo
    (filtro de strategy, fallo del executor, etc.). El caller continua su
    flujo normal con sig=None y el texto se rechaza con el log defensivo
    habitual ("no_open_canal1_signal").
    """
    direction = parsed.get("direction")
    if direction not in ("BUY", "SELL"):
        return None

    sig_id_pre = f"canal1_{msg.id}"

    # ── GUARDA: el texto debe traer NIVELES, no solo una dirección ──────────
    # Una entrada real trae TPs (o un rango del que derivarlos). Sin niveles
    # es comentario, no señal — abrir un market aquí deja la posición naked.
    # Defensa en profundidad tras is_canal1_signal_text (bug canal1_19778,
    # 2026-05-19: el bot abrió 4 posiciones sobre un "GOLD UPDATE" y nunca
    # les puso SL → −$129 cerradas a mano).
    if not parsed.get("tps") and not parsed.get("range"):
        print(f"[Canal1] ❌ TEXTO-only sin niveles — NO se abre (msg={msg.id})")
        journal.event(sig_id_pre, "signal_skipped",
                      reason="text_only_sin_niveles_no_es_senal",
                      trigger="text_only", direction=direction,
                      parsed_keys=sorted(parsed.keys()))
        return None

    # ── FILTRO: RE-ENTER no aplica al text-only (HIGH RISK tampoco) ──
    skip, reason = strategies.should_skip_signal("", direction, "canal1")
    if skip:
        print(f"\n[Canal1] ❌ TEXTO IGNORADO ({direction}, msg={msg.id}): {reason}")
        journal.event(sig_id_pre, "signal_skipped",
                      reason=reason, trigger="text_only", direction=direction)
        return None

    # ── POST-SL para Canal 1 ──
    max_tp_idx = strategies.max_tp_index_for_signal("canal1")

    max_entry_age_s = config.STRATEGY_ENTRY_MAX_TG_DELAY_S
    stale, age_s = _should_skip_stale_entry_signal(msg, max_entry_age_s)
    if stale:
        print(f"\n[Canal1] TEXTO-only STALE ignorado ({direction}, msg={msg.id}): "
              f"age={age_s:.1f}s > {max_entry_age_s:.1f}s")
        _log_stale_entry_skip(
            sig_id_pre, "canal1", msg, "text_only",
            max_entry_age_s, age_s, direction=direction, text=_msg_text(msg))
        return None

    print(f"\n[Canal1] Texto-only {direction} (msg={msg.id}, sticker no llego)"
          f"{f' [post-SL: TP cap idx {max_tp_idx}]' if max_tp_idx is not None else ''}")

    # ── JOURNAL: signal_received antes del fill ──
    signal_received_utc = datetime.utcnow()
    tg_to_bot_ms = None
    if msg.date:
        tg_naive = msg.date.replace(tzinfo=None)
        tg_to_bot_ms = int((signal_received_utc - tg_naive).total_seconds() * 1000)
        if tg_to_bot_ms > 10000:
            print(f"[Canal1] ⚠ tg→bot delay alto: {tg_to_bot_ms}ms "
                  f"(msg.date={msg.date}). Posible reconexion Telethon.")
    journal.event(sig_id_pre, "signal_received",
                  channel="canal1", direction=direction,
                  trigger="text_only",
                  tg_ts=msg.date.isoformat(timespec="seconds") if msg.date else None,
                  tg_to_bot_ms=tg_to_bot_ms)

    # Contexto de mercado al entrar — ver comentario en canal2_new.
    ctx = await _run(compute_market_context, config.MT5_SYMBOL)
    if ctx:
        journal.event(sig_id_pre, "market_context", **ctx)

    magic = config.magic_for("canal1")
    result = await _run(executor.open_market_with_fill, direction, config.LOT_SIZE,
                        None, None, f"c1_{msg.id}", magic)
    if not result:
        journal.event(sig_id_pre, "market_fill_failed",
                      reason="executor.open_market returned None (text-only path)")
        journal.anomaly(sig_id_pre, "fill", "critical",
                        "executor.open_market devolvió None en path "
                        "text-only canal1",
                        channel="canal1", direction=direction)
        return None
    ticket, fill_price = result

    market_filled_utc = datetime.utcnow()
    fill_latency_ms = int((market_filled_utc - signal_received_utc).total_seconds() * 1000)
    tick_ctx = await _run(executor.current_tick_safe)
    journal.event(sig_id_pre, "market_filled",
                  ticket=ticket, price=fill_price, latency_ms=fill_latency_ms,
                  bid=tick_ctx.get("bid") if tick_ctx else None,
                  ask=tick_ctx.get("ask") if tick_ctx else None,
                  spread=tick_ctx.get("spread") if tick_ctx else None)

    sig = Signal(
        channel="canal1",
        message_id=msg.id,
        direction=direction,
        market_ticket=ticket,
        market_fill_price=fill_price,
        max_tp_index=max_tp_idx,
        time_stop_at=strategies.time_stop_for_signal(datetime.utcnow()),
        adverse_action=config.STRATEGY_C1_ADVERSE_ACTION,
    )
    state.add(sig)
    journal.begin_trade(
        _sig_id(sig),
        channel="canal1",
        direction=direction,
        signal_received_utc=signal_received_utc.isoformat(timespec="milliseconds"),
        market_filled_utc=market_filled_utc.isoformat(timespec="milliseconds"),
        fill_latency_ms=fill_latency_ms,
        market_entry_price=fill_price,
        adverse_action=config.STRATEGY_C1_ADVERSE_ACTION,
        trigger="text_only",
    )
    _log_strategy_snapshot(
        sig,
        num_entries=config.STRATEGY_C1_NUM_ENTRIES,
        time_stop_min=config.STRATEGY_C1_TIME_STOP_MIN,
    )
    # Abre posiciones market extra (modo scale_out, o doble market legacy).
    await _open_extra_legs(sig, msg.id)
    print(f"[Canal1] Mercado abierto desde texto-only (sin sticker), aplicando TPs/SL...")

    # Notificar al usuario para que sepa que ocurrio esto (puede indicar un
    # problema con la recepcion de stickers que vale la pena investigar).
    try:
        asyncio.create_task(notify(
            f"ℹ️ Canal 1: senal abierta desde TEXTO sin sticker previo\n"
            f"\n"
            f"Direccion: {direction}\n"
            f"Ticket MT5: #{ticket}\n"
            f"Entry: {fill_price}\n"
            f"\n"
            f"El sticker no llego al bot (rate-limit, reconexion, etc).\n"
            f"El texto contenia toda la info asi que se abrio igual.\n"
            f"Verifica en MT5."
        ))
    except Exception:
        pass

    return sig


async def _handle_canal1_text(msg, text: str):
    sig = state.latest_open("canal1")
    msg_sig_id = f"canal1_{msg.id}"

    # ── NUEVO: rescate text-only si no hay sticker abierto ────────────────
    # Casos reales (sesiones 2026-05-08 y 2026-05-12): el sticker no llega
    # al bot pero el texto si. Antes el bot rechazaba con
    # "no_open_canal1_signal" y la senal se perdia. Ahora si el texto trae
    # direction valida, abrimos market desde el propio texto.
    if sig is None:
        parsed_pre = parse_canal1_text(text)
        if parsed_pre.get("direction") in ("BUY", "SELL"):
            sig = await _open_canal1_from_text(msg, parsed_pre)
            # Si _open_canal1_from_text fallo (filtro, executor) sig sigue
            # None y se rechaza mas abajo con el log defensivo habitual.

    # ── Logging defensivo: por que rechazamos el texto (si rechazamos) ────
    # Sesion 2026-05-07: canal1_19484 y canal1_19498 quedaron NAKED porque
    # el texto canal1 (msg_id distinto al sticker) no se aplico. Sin este
    # log no podiamos saber si era el cutoff, latest_open=None, o el filtro
    # del parser. Ahora dejamos rastro explicito en el journal.
    if not _should_accept_canal1_text(sig):
        if sig is None:
            reason = "no_open_canal1_signal"
            extra = {}
        elif sig.tps:
            elapsed_s = (datetime.utcnow() - sig.timestamp).total_seconds()
            reason = "linked_signal_already_has_tps_and_old"
            extra = {
                "linked_signal_id": _sig_id(sig),
                "linked_n_tps": len(sig.tps),
                "linked_elapsed_s": round(elapsed_s, 1),
            }
        else:
            reason = "rejected_by_should_accept_unknown"
            extra = {"linked_signal_id": _sig_id(sig) if sig else None}
        journal.event(msg_sig_id, "canal1_text_rejected",
                      reason=reason,
                      text_preview=text[:250].replace("\n", " | "),
                      **extra)
        print(f"[Canal1] Texto rechazado msg={msg.id}: {reason}")
        return

    # ── ALIAS CRÍTICO ──────────────────────────────────────────────────────
    # En canal 1, todos los mensajes de gestión posteriores (photos con
    # captions tipo "TP1 HIT, Move SL to BE", "Close all", etc.) son REPLY
    # a este mensaje de TEXTO (msg.id), NO al sticker original.
    state.alias(sig, msg.id)
    sig_id = _sig_id(sig)
    print(f"[Canal1] Alias registrado: msg.id {msg.id} → señal sticker #{sig.message_id}")

    # Loguear que ESTAMOS procesando el texto. Si tras esto NO aparecen
    # tps_arrived/sl_arrived, el problema esta en parser o en
    # _update_signal_from_parsed (no en _handle_canal1_text). Critico para
    # diagnostico futuro de bugs como el de canal1_19498 (2026-05-07).
    journal.event(sig_id, "canal1_text_processing",
                  source_msg_id=msg.id,
                  text_preview=text[:250].replace("\n", " | "))

    parsed = parse_canal1_text(text)
    _log_telegram_understood(
        sig_id,
        channel="canal1",
        message_id=msg.id,
        kind="levels_update",
        parser="parse_canal1_text",
        raw_text=text,
        parsed=parsed,
        target_signal_id=sig_id,
        tg_ts=_msg_ts_iso(msg),
    )

    # Si el parser NO extrajo tps/sl del texto del canal, hay un bug del
    # parser para este formato concreto. Logueamos antes de aplicar para
    # tener evidencia exacta del texto y de lo que el parser saco.
    if "tps" not in parsed or "sl" not in parsed:
        journal.event(sig_id, "canal1_parser_incomplete",
                      parsed_keys=list(parsed.keys()),
                      has_tps="tps" in parsed,
                      has_sl="sl" in parsed,
                      text_preview=text[:300].replace("\n", " | "))

    # ── Routing Canal 1 ──────────────────────────────────────────────────
    sig.target_tp_index = (config.STRATEGY_C1_TARGET_TP_INDEX
                           if config.STRATEGY_C1_TARGET_TP_INDEX >= 0 else None)
    sig.entry_mode = config.STRATEGY_C1_ENTRY_MODE if "range" in parsed else "market_only"
    sig.be_at_tp_index = (config.STRATEGY_C1_BE_TP_INDEX
                          if config.STRATEGY_C1_BE_TP_INDEX >= 0 else None)
    if config.STRATEGY_C1_TIME_STOP_MIN > 0:
        sig.time_stop_at = sig.timestamp + timedelta(
            minutes=config.STRATEGY_C1_TIME_STOP_MIN
        )

    print(f"[Canal1] Texto señal {sig.message_id}: {list(parsed.keys())} | "
          f"entry_mode={sig.entry_mode} target_tp_idx={sig.target_tp_index} "
          f"time_stop={sig.time_stop_at}")
    _log_strategy_snapshot(
        sig,
        num_entries=config.STRATEGY_C1_NUM_ENTRIES,
        time_stop_min=config.STRATEGY_C1_TIME_STOP_MIN,
    )
    _tg_ts = msg.date.isoformat(timespec="seconds") if msg.date else None
    await _update_signal_from_parsed(sig, parsed, tg_ts=_tg_ts)
    journal.event(sig_id, "canal1_text_applied",
                  **_canal1_text_applied_summary(sig, parsed))
    logger.log_signal(sig, parsed)

    # ── DEFENSA POST-PROCESAMIENTO ─────────────────────────────────────
    # Si tras procesar el texto la signal sigue SIN tps NI sl, la posicion
    # esta NAKED en MT5 sin proteccion. Bug critico — notify URGENT al
    # usuario para accion manual inmediata. Sesion 2026-05-07: canal1_19484
    # y canal1_19498 quedaron asi sin que el bot avisara.
    if not sig.tps and not sig.sl:
        print(f"[Canal1] 🚨 NAKED tras procesar texto — signal {sig_id} "
              f"sin TPs ni SL, ticket={sig.market_ticket}")
        journal.event(sig_id, "canal1_text_processed_but_naked",
                      parsed_keys=list(parsed.keys()),
                      ticket=sig.market_ticket,
                      text_preview=text[:250].replace("\n", " | "))
        # Migrado a anomaly() — _notify_critical dispara la alerta
        # de Telegram automáticamente (T3 del plan).
        journal.anomaly(sig_id, "naked", "critical",
                        "texto canal1 procesado pero parser no extrajo "
                        "TPs/SL — posición abierta sin protección",
                        ticket=sig.market_ticket,
                        direction=sig.direction,
                        entry=sig.market_fill_price,
                        parsed_keys=list(parsed.keys()),
                        text_preview=text[:250].replace("\n", " | "))


# ─── Helper de simulación: genera bloques de texto listos para copiar ────────
#
# Trigger: mensaje en TEST_CHANNEL que empieza con "(c1)", "(c2)", "[c1]" o
# "[c2]" (case-insensitive) y contiene BUY o SELL.
#
# El bot lee el precio actual de XAUUSD vía MT5, genera un rango ±1.5 alrededor
# del mid, calcula TPs/SL con el patrón calibrado del canal, y responde con
# bloques de texto listos para copy-paste. NO abre ninguna orden.

import re as _re_helper

# Acepta "(c1) buy", "[c1] buy" y también "c1 buy" sin paréntesis.
# Antes solo aceptaba con paréntesis/corchetes — los logs mostraban "c2 buy"
# como ignorado pese a ser un trigger válido para el helper de simulación.
# NOTA: NO captura "/c1 buy" (esa es la rama del prefijo /c1 más abajo).
_SIM_PREFIX = _re_helper.compile(
    r"^\s*[\(\[]?\s*c([12])\s*[\)\]]?\s+",
    _re_helper.IGNORECASE,
)


async def _maybe_handle_sim_helper(event, text: str) -> bool:
    """Si el mensaje es un trigger de simulación, responde con datos y devuelve True.
    Si no, devuelve False para que siga el routing normal del canal de pruebas.

    Modificadores opcionales tras BUY/SELL para testear los 3 casos del layered:
      • (sin nada)      → caso A (in-range): rango centrado en precio actual.
      • adverse / adv / a → caso C: rango DESPLAZADO para que el market quede
                            fuera del rango en sentido adverso. Permite probar
                            la rama `rescue_market` cuando el precio se mueve.
      • favor / fav / f   → caso B: rango DESPLAZADO para que el market quede
                            fuera del rango pero en profit (DCAs como
                            promediado de retroceso).
    """
    m = _SIM_PREFIX.match(text)
    if not m:
        return False
    canal_num = m.group(1)
    body = text[m.end():].strip().upper()

    # Detectar dirección
    if "BUY" in body:
        direction = "BUY"
    elif "SELL" in body:
        direction = "SELL"
    else:
        await event.respond(
            "Helper sim: indica BUY o SELL. Ej: `(c2) buy`, "
            "`(c2) buy adverse`, `(c1) sell favor`."
        )
        return True

    # Detectar escenario layered (palabra suelta, no parte de otra)
    tokens = set(_re_helper.findall(r"\b[A-Z]+\b", body))
    if tokens & {"ADVERSE", "ADV", "A"}:
        scenario = "adverse"
    elif tokens & {"FAVOR", "FAV", "F"}:
        scenario = "favor"
    else:
        scenario = "inrange"

    return await _send_sim_data(event, canal_num, direction, scenario)


# Offsets REALISTAS de Canal 2 calibrados sobre 490 señales (2026):
#   Ancho rango = 4 pts (mediana, p25-p75 todos en 4)
#   TPs (median desde entry): +3, +5, +7, +9, +14 (TP5 está en 99.8% de señales)
#   SL = extremo opuesto ±4 (mediana=4, p75=5)
#
# Estos valores difieren del _TP_OFFSETS=(2,4,6,8) del parser, que es
# CONSERVADOR adrede para usar como fallback en producción. Aquí queremos
# imitar lo que el canal típicamente publica.
_SIM_RANGE_HALF = 2.0                  # mid ±2 → ancho 4
_SIM_TP_OFFSETS = (3, 5, 7, 9, 14)
_SIM_SL_OFFSET  = 4
# Distancia de desplazamiento del rango para forzar B/C (ancho rango = 4,
# desplazamos +4 → el extremo cercano queda a 2$ del precio actual, fuera
# de la tolerancia de ±1$ de _handle_range_arrival_safety).
_SIM_SCENARIO_SHIFT = 4.0


def _sim_range_for_scenario(direction: str, mid: float, scenario: str
                            ) -> tuple[float, float]:
    """Devuelve (range_low, range_high) desplazado según el escenario.

    Para que el clasificador A/B/C salga como queremos al llegar el rango:
      • inrange → rango centrado en mid → entry IN range  → caso A.
      • favor   → rango desplazado de modo que mid quede del lado FAVORABLE
                  del rango (BUY: rango ABAJO, mid ARRIBA → entry > hi → favor).
      • adverse → rango desplazado de modo que mid quede del lado ADVERSO
                  (BUY: rango ARRIBA, mid ABAJO → entry < lo → adverse).
    """
    half = _SIM_RANGE_HALF
    shift = _SIM_SCENARIO_SHIFT

    if direction == "BUY":
        # favor: rango por debajo del precio (mid > range_high)
        # adverse: rango por encima del precio (mid < range_low)
        center_offset = {"inrange": 0.0, "favor": -shift, "adverse": +shift}[scenario]
    else:  # SELL
        # favor: rango por encima del precio (mid < range_low)
        # adverse: rango por debajo del precio (mid > range_high)
        center_offset = {"inrange": 0.0, "favor": +shift, "adverse": -shift}[scenario]

    center = mid + center_offset
    return round(center - half, 2), round(center + half, 2)


async def _send_sim_data(event, canal_num: str, direction: str,
                         scenario: str = "inrange") -> bool:
    # Precio actual de MT5 (modo lectura, sin orden)
    try:
        tick = await _run(executor.current_tick)
    except Exception as e:
        await event.respond(f"Helper sim: no pude leer precio de MT5: {e}")
        return True

    mid = tick["mid"]
    range_low, range_high = _sim_range_for_scenario(direction, mid, scenario)

    if direction == "BUY":
        entry = range_high
        tps = [round(entry + off, 2) for off in _SIM_TP_OFFSETS]
        sl  = round(range_low - _SIM_SL_OFFSET, 2)
        # En el canal real el rango BUY se escribe alto-bajo (ej: "4703-4698")
        range_str = f"{range_high:.2f}-{range_low:.2f}"
    else:  # SELL
        entry = range_low
        tps = [round(entry - off, 2) for off in _SIM_TP_OFFSETS]
        sl  = round(range_high + _SIM_SL_OFFSET, 2)
        # En el canal real el rango SELL se escribe bajo-alto (ej: "4720-4725")
        range_str = f"{range_low:.2f}-{range_high:.2f}"

    tps_block = "\n".join(f"TP{i+1} {tp:.2f}" for i, tp in enumerate(tps))

    # Etiqueta informativa del escenario layered que se va a testear
    scenario_label = {
        "inrange": "🅰️ Caso A (in-range) — flujo normal, market dentro del rango",
        "favor":   "🅱️ Caso B (favor) — market FUERA del rango pero a favor; "
                   "DCAs quedan al lado adverso por si retrocede el precio",
        "adverse": "🆘 Caso C (adverse) — market FUERA del rango contra; "
                   "se intentará `rescue_market` SI el precio sigue moviéndose "
                   "adverso entre el entry y la edición del rango",
    }[scenario]

    if canal_num == "1":
        block_c1 = (
            f"{direction} GOLD NOW  {range_str}\n"
            f"{tps_block}\n"
            f"SL {sl:.2f}"
        )
        reply = (
            f"**SIM Canal 1** | bid={tick['bid']:.2f} ask={tick['ask']:.2f} | "
            f"escenario={scenario}\n\n"
            f"{scenario_label}\n\n"
            f"Necesitas el sticker {direction} del canal real para disparar el "
            f"market. Alternativa con prefijo `/c1`:\n\n"
            f"```\n/c1 {block_c1}\n```\n\n"
            f"O el bloque limpio para pegar tras el sticker real:\n\n"
            f"```\n{block_c1}\n```"
        )
    else:  # Canal 2 — flujo real: NEW → EDIT (rango) → REPLY (TP1+SL)
        block_entry = f"XAU USD {direction} NOW"
        block_edit = f"{block_entry}\n\n{range_str}"
        block_reply = f"TP1 {tps[0]:.2f}\n\nSL {sl:.2f}"
        # Bloque informativo con todos los TPs (TP2-TP5 los suele añadir el
        # canal después en edits/replies adicionales — útil para tu referencia)
        all_tps_info = " | ".join(f"TP{i+1}={tp:.2f}" for i, tp in enumerate(tps))
        reply = (
            f"**SIM Canal 2** | bid={tick['bid']:.2f} ask={tick['ask']:.2f} | "
            f"escenario={scenario}\n\n"
            f"{scenario_label}\n\n"
            f"**Paso 1** — manda este mensaje (dispara market):\n\n"
            f"```\n{block_entry}\n```\n\n"
            f"**Paso 2** — EDITA ese mismo mensaje añadiendo el rango:\n\n"
            f"```\n{block_edit}\n```\n\n"
            f"**Paso 3** — RESPONDE (reply) a ese mensaje con TP1 y SL "
            f"(formato real del canal para protección):\n\n"
            f"```\n{block_reply}\n```\n\n"
            f"_Referencia (todos los TPs por si quieres añadirlos en edits "
            f"o replies adicionales):_ {all_tps_info}"
        )

    await event.respond(reply)
    return True


# ─── Poller activo — bypass del updateChannelTooLong de Telethon ─────────────
#
# Diagnóstico (2026-05-05): Canal 2 sufre batching de exactamente 60s en la
# entrega de sus updates porque Telegram envía updateChannelTooLong cuando el
# cliente tiene pendientes >N events del canal. Telethon entonces llama a
# channels.getChannelDifference y, entre llamadas, duerme 60s. Resultado:
#   • Canal 2 new messages: p50=12s, p90=43s, max=58s de delay
#   • Canal 2 edits (rango/TPs/SL): p50=63s, p90=133s, max=605s (10 min!)
#   • Canal 1 new messages: p50=1.3s, p90=1.7s — sin problema
#
# Solución: poller activo que llama a client.get_messages() cada 1 segundo
# para ambos canales. Detecta mensajes nuevos y edits directamente, sin
# pasar por el mecanismo de eventos de Telethon. Latencia resultante: ~1s.
#
# Los event handlers de Telethon SIGUEN activos como fallback. La
# deduplicación (_new_msg_already_seen + _edit_already_seen) garantiza que
# quien llega primero (poller, casi siempre) procesa el mensaje, y el que
# llega tarde es un no-op silencioso.

_POLL_INTERVAL_S = 0.5   # segundos de sleep entre ciclos de poll
_POLL_MSG_LIMIT  = 10    # últimos N mensajes a revisar por canal en cada ciclo

# Estado del poller: (channel, msg_id) → edit_date de la última versión vista.
# "UNSEEN" indica que el mensaje aún no fue registrado en el primer scan.
# Usamos un sentinel en lugar de None para distinguir "aún no visto" vs
# "visto y sin editar" (edit_date=None es válido en mensajes sin editar).
_POLLER_UNSEEN = object()
_poller_msg_state: dict[tuple, object] = {}  # (channel, msg_id) → edit_date


async def _poll_channel(channel_id: int, channel_name: str):
    """Obtiene los últimos mensajes del canal y despacha nuevos y edits.

    Solo despacha lo que sea genuinamente nuevo desde el último poll:
      • msg no en _poller_msg_state  → dispatch new
      • msg en _poller_msg_state con edit_date distinto → dispatch edit
      • msg visto y sin cambio → no-op

    La dedup global (_new_msg_already_seen, _edit_already_seen) garantiza
    que si Telethon también dispara el mismo evento, se descarta sin trabajo.
    """
    try:
        msgs = await client.get_messages(channel_id, limit=_POLL_MSG_LIMIT)
    except Exception as e:
        print(f"[Poller] Error get_messages {channel_name}: {e}")
        return

    for msg in reversed(msgs):  # oldest-first = orden cronológico natural
        key = (channel_name, msg.id)
        edit_date = msg.edit_date
        prev = _poller_msg_state.get(key, _POLLER_UNSEEN)

        if prev is _POLLER_UNSEEN:
            # Primera vez que vemos este mensaje desde el poller.
            _poller_msg_state[key] = edit_date
            # CRITICAL FIX (sesion 2026-05-08): NO pre-marcar via
            # _new_msg_already_seen aqui. Bug raiz: pre-marcar provocaba
            # que el handler _process_canal1_new (que tiene su propio
            # check de dedup) viera el msg ya marcado y retornara sin
            # procesar -> el sticker se descartaba. Visto en canal1_19510,
            # 19514, 19520, 19530 (sesion 2026-05-08): handler_entry se
            # loguea pero signal_received nunca se emite.
            #
            # Ahora siempre llamamos al handler. La dedup vs el event
            # handler de Telethon ocurre DENTRO de _process_canalN_new
            # (que es el unico que llama _new_msg_already_seen). Eso
            # garantiza single-processing sin race con el poller.
            _msg_diag(msg, channel_name, "poll_new")
            if channel_name == "canal2":
                await _process_canal2_new(msg, label="Canal2_poll")
            else:
                await _process_canal1_new(msg)

        elif edit_date != prev:
            # Mensaje editado desde el último poll
            _poller_msg_state[key] = edit_date
            if channel_name == "canal2":
                _msg_diag(msg, channel_name, "poll_edit")
                await _process_canal2_edit(msg, label="Canal2_poll")
            # Canal 1 no usa edits para señales → no hace falta dispatch


async def poll_loop():
    """Bucle de polling activo para Canal 1 y Canal 2.

    Corre en paralelo con los event handlers de Telethon (no los reemplaza).
    Resuelve el delay estructural de Canal 2 causado por updateChannelTooLong.

    Fase 1 — scan inicial (sin procesar):
        Lee los mensajes recientes de ambos canales y los marca como "ya
        vistos" sin despacharlos. Esto evita que el bot re-procese señales
        activas o históricas cuando arranca o reinicia. También marca los
        edits como vistos para no re-aplicar SL/TPs de señales previas.

    Fase 2 — polling activo:
        Cada POLL_INTERVAL_S segundos revisa ambos canales. Solo procesa
        lo que es nuevo desde la fase 1.
    """
    # Breve espera para que Telethon complete el handshake de sesión
    await asyncio.sleep(4)

    watched = [
        (config.CANAL_2_ID, "canal2"),
        (config.CANAL_1_ID, "canal1"),
    ]

    # ── Fase 1: scan inicial ────────────────────────────────────────────────
    print("[Poller] Scan inicial — marcando mensajes recientes como vistos...")
    for channel_id, channel_name in watched:
        try:
            msgs = await client.get_messages(channel_id, limit=_POLL_MSG_LIMIT)
            for msg in msgs:
                key = (channel_name, msg.id)
                _poller_msg_state[key] = msg.edit_date
                # Marcar como "visto" en los sets globales de dedup
                _new_msg_already_seen(channel_name, msg.id)
                if msg.edit_date:
                    _edit_already_seen(channel_name, msg)
            print(f"[Poller] {channel_name}: {len(msgs)} mensajes marcados "
                  f"(sin procesar — señales previas)")
        except Exception as e:
            print(f"[Poller] Error scan inicial {channel_name}: {e}")

    print(f"[Poller] Activo. Polling cada {_POLL_INTERVAL_S}s | "
          f"canales: {[c for _, c in watched]} | limit={_POLL_MSG_LIMIT}")
    journal.event("bot", "poller_started",
                  interval_s=_POLL_INTERVAL_S,
                  msg_limit=_POLL_MSG_LIMIT,
                  channels=[c for _, c in watched])

    # ── Fase 2: polling activo ──────────────────────────────────────────────
    # Los dos canales se pollan EN PARALELO con asyncio.gather. Esto elimina
    # la espera acumulada de las llamadas API secuenciales (~150ms × N canales)
    # y reduce el ciclo efectivo a sleep + max(RTT_canal1, RTT_canal2) en vez
    # de sleep + RTT_canal1 + RTT_canal2. Con 0.5s sleep: ciclo ~0.65s, 185 calls/min.
    _poll_count = 0
    while True:
        await asyncio.sleep(_POLL_INTERVAL_S)
        await asyncio.gather(*[
            _poll_channel(channel_id, channel_name)
            for channel_id, channel_name in watched
        ])

        # Limpieza periódica del estado interno (cada 7200 polls ≈ 1 hora)
        # para evitar crecimiento ilimitado de _poller_msg_state.
        _poll_count += 1
        if _poll_count % 7200 == 0:
            if len(_poller_msg_state) > 1000:
                # Descartar las 500 entradas más antiguas (dict es ordered en 3.7+)
                keys = list(_poller_msg_state.keys())
                for k in keys[:500]:
                    del _poller_msg_state[k]
                print(f"[Poller] Limpieza estado: {len(keys)} → {len(_poller_msg_state)}")


# ─── Canal de pruebas ─────────────────────────────────────────────────────────
#
# Uso: crea un canal/grupo privado en Telegram y ponle su ID en TEST_CHANNEL_ID
# del .env. El bot enrutará los mensajes según el contenido:
#
#   • Sticker BUY/SELL de Canal 1          → flujo Canal 1 (sticker)
#   • Texto que empieza con "/c1 ..."      → flujo Canal 1 (texto post-sticker)
#   • Texto de entrada Canal 2              → flujo Canal 2 (nueva señal)
#   • Edición de mensaje Canal 2            → flujo Canal 2 (añadir TPs/SL)
#   • Reply a señal                         → acción de gestión (BE, close, etc.)
#
# Esto permite simular cualquier escenario mandando mensajes tú mismo.

if config.TEST_CHANNEL_ID:
    print(f"[Test] Canal de pruebas activo: {config.TEST_CHANNEL_ID}")

    @client.on(events.NewMessage(chats=[config.TEST_CHANNEL_ID]))
    async def test_channel_new(event):
        # ContextVar: marca toda esta cadena async como TEST. Cualquier
        # journal.event/begin_trade/finalize_trade que dispare por debajo
        # ruteará a trade_events_TEST.jsonl / trade_journal_TEST.csv en
        # vez de los ficheros de producción. asyncio.create_task copia el
        # contexto, así que dca_monitor heredará el flag automáticamente.
        token = journal._test_context.set(True)
        try:
            msg  = event.message
            text = msg.text or ""

            # 0) Helper de SIMULACIÓN: "(c1) buy" o "(c2) sell" →
            #    el bot lee precio actual de XAUUSD y responde con un bloque
            #    formateado listo para copiar y pegar como señal real. NO ejecuta.
            if await _maybe_handle_sim_helper(event, text):
                return

            # 1) Sticker → Canal 1
            if msg.sticker:
                await _handle_canal1_sticker(msg)
                return

            # 2) Reply → niveles (TP/SL formato Canal 2) y/o acción de gestión.
            # Replicamos el flujo del Canal 2 real (_process_canal2_new): primero
            # parse niveles y aplica si vienen, luego classify para acciones.
            # Sin esto, un reply "TP1 4750\nSL 4720" en el canal de pruebas solo
            # disparaba MOVE_SL_TO_PRICE y los TPs nunca se actualizaban — los
            # tickets se quedaban con los TPs provisionales del predictor.
            if msg.reply_to and msg.reply_to.reply_to_msg_id:
                reply_id = msg.reply_to.reply_to_msg_id
                sig = state.get("canal1", reply_id) or state.get("canal2", reply_id)
                if sig and sig.status == "open":
                    parsed_in_reply = parse_canal2(text)
                    _tg_ts = msg.date.isoformat(timespec="seconds") if msg.date else None
                    if parsed_in_reply.get("tps") or parsed_in_reply.get("sl"):
                        print(f"[Test] Reply con niveles reales: "
                              f"{[k for k in ('tps','sl') if k in parsed_in_reply]}")
                        await _update_signal_from_parsed(sig, parsed_in_reply, tg_ts=_tg_ts)
                    cl = await classify_async(text, signal=sig)
                    await _execute_action(sig, cl, raw_text=text, tg_ts=_tg_ts)
                return

            # 3) Prefijo /c1 → texto Canal 1 (después de un sticker simulado)
            if text.lower().startswith("/c1 "):
                body = text[4:]
                # fingimos que es mensaje de texto Canal 1
                msg.text = body
                await _handle_canal1_text(msg, body)
                return

            # 4) Texto de entrada Canal 2
            if is_canal2_entry(text):
                await _process_canal2_new(msg, label="Test→Canal2")
                return

            print(f"[Test] Mensaje ignorado (no coincide con formato): {text[:60]}")
        finally:
            journal._test_context.reset(token)

    @client.on(events.MessageEdited(chats=[config.TEST_CHANNEL_ID]))
    async def test_channel_edit(event):
        # Mismo wrap que test_channel_new — el edit también tiene que rutear
        # journal a los ficheros TEST. Además, journal.is_test_signal() ya
        # tendría True para esta señal porque begin_trade se disparó bajo
        # contextvar=True; la contextvar adicional es por si el edit dispara
        # eventos de una señal que aún no se hubiera marcado (defensa en
        # profundidad).
        token = journal._test_context.set(True)
        try:
            await _process_canal2_edit(event.message, label="Test→Canal2")
        finally:
            journal._test_context.reset(token)
