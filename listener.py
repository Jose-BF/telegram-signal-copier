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
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from telethon import TelegramClient, events

import causal_trace
import config
import alert_graphics
import executor
import journal
import logger
import position_lifecycle_monitor
import pending_actions
import runtime_control
import strategies
import telegram_notifications
from canal2_zone_lifecycle import (
    LIFECYCLE_SCHEMA_VERSION,
    classify_followup as classify_zone_followup,
    is_executable as zone_plan_is_executable,
    is_expired as zone_plan_is_expired,
    merge_plan_record,
    new_plan_record,
    touch_decision as zone_touch_decision,
)
from classifier import classify, classify_async, classify_local
from entry_execution_gate import EntryExecutionGate
from interpretation_firewall import (
    EXECUTABLE_ACTIONS,
    LEVEL_ONLY_ACTIONS,
    NOTIFY_REVIEW_ACTIONS,
    firewall_decision,
    normalize_xauusd_management_price,
    normalize_classifier_outputs,
)
from level_interpreter import (
    align_provider_plan_to_market_context,
    interpret_entry_levels,
)
from parser import (
    canal2_entry_command_key,
    correct_tp_typos,
    is_canal1_signal_text,
    is_canal2_entry,
    levels_consistent_with_direction,
    parse_canal1_text,
    parse_canal2,
    parse_canal2_zone_plan,
    predict_levels,
    predict_sl_from_entry,
    validate_range_vs_entry,
)
from provider_names import provider_display_name
from state import Signal, state
from market_context import compute_market_context


@dataclass(frozen=True)
class _Canal2EntryIntent:
    """Normalized request consumed by the single Canal 2 opening path."""

    message_id: int
    direction: str
    parsed: dict
    raw_text: str
    entry_timestamp: datetime
    telegram_timestamp: datetime | None = None
    reply_to_message_id: int | None = None
    source_kind: str = "telegram_now"
    trigger: dict = field(default_factory=dict)
    lot_multiplier: float = 1.0
    lot_reason: str | None = None
    max_tp_index: int | None = None
    command_key: str | None = None
    is_high_risk: bool = False
    zone_plan_message_id: int | None = None
    zone_thread_root_message_id: int | None = None
    zone_entry_generation: int = 0


def _sig_id(signal: Signal) -> str:
    """Identificador único para journal. Mismo formato que state._key()."""
    return f"{signal.channel}_{signal.message_id}"


_NON_REQUIRED_MANAGEMENT_REASONS = {
    "conditional_plan",
    "conditional_close_text",
    "level_parser_path",
    "non_executable_intent",
    "optional_close_text",
    "optional_suggestion",
}


def _management_requires_execution(signal: Signal, classification: dict,
                                   firewall) -> bool:
    """True only for a direct executable provider order on an open signal."""
    action = str(classification.get("action") or "").upper()
    if signal.status != "open" or action not in EXECUTABLE_ACTIONS:
        return False
    if classification.get("is_optional") or classification.get("is_conditional"):
        return False
    return firewall.reason not in _NON_REQUIRED_MANAGEMENT_REASONS


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


def _entry_reference_from_tick(direction: str, tick: dict | None):
    """Precio contextual para inferir niveles antes/despues del fill."""
    if not tick:
        return None
    direction = (direction or "").upper()
    bid = tick.get("bid")
    ask = tick.get("ask")
    if direction == "BUY" and ask:
        return ask
    if direction == "SELL" and bid:
        return bid
    if bid and ask:
        return (bid + ask) / 2
    return bid or ask


def _log_entry_level_interpretation(sig_id: str, channel: str, original: dict,
                                    interpreted: dict,
                                    reference_price=None) -> None:
    corrections = interpreted.get("corrections") or []
    if not corrections:
        return
    journal.event(
        sig_id,
        "entry_levels_interpreted",
        channel=channel,
        reference_price=reference_price,
        original=original,
        interpreted=interpreted.get("parsed"),
        corrections=corrections,
        provisional=interpreted.get("provisional", False),
    )


async def _apply_interpreted_entry_levels(signal: Signal, parsed: dict,
                                          channel: str,
                                          reference_price=None,
                                          tg_ts: str | None = None) -> dict:
    interpreted = interpret_entry_levels(
        channel, signal.direction, parsed, reference_price=reference_price)
    _log_entry_level_interpretation(
        _sig_id(signal), channel, parsed, interpreted, reference_price)
    await _update_signal_from_parsed(
        signal, interpreted["parsed"], tg_ts=tg_ts)
    return interpreted["parsed"]


def _is_edit_update_kind(update_kind: str | None) -> bool:
    normalized = str(update_kind or "").strip().lower()
    return normalized == "edit" or normalized.endswith("_edit")


def _telegram_message_revision_id(msg, channel: str) -> str:
    text = getattr(msg, "text", None) or getattr(msg, "message", None) or ""
    return causal_trace.message_revision_id(
        chat_id=_message_chat_id(msg, channel),
        message_id=int(msg.id),
        revision_token=_telegram_revision_token(msg),
        text_sha1=_text_sha1(text) if text else None,
        media_sha256=None,
    )


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
        "chat_id": _message_chat_id(msg, channel),
        "message_id": getattr(msg, "id", None),
        "update_kind": update_kind,
        "revision_token": _telegram_revision_token(msg),
        "date_utc": _iso_or_none(getattr(msg, "date", None)),
        "edit_date_utc": _iso_or_none(getattr(msg, "edit_date", None)),
        "is_edit": _is_edit_update_kind(update_kind),
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
        "media_sha256": None,
        "message_revision_id": _telegram_message_revision_id(msg, channel),
    }


def _classification_source(classification: dict) -> str:
    if classification.get("_gemini_failed"):
        return "gemini_failed"
    if classification.get("_reason"):
        return "regex"
    return "gemini"


def _classification_requires_review(classification: dict) -> bool:
    action = classification.get("action")
    if classification.get("_gemini_failed"):
        return True
    if classification.get("requires_review"):
        return True
    if classification.get("is_conditional") or classification.get("is_optional"):
        return True
    if action in {
        "REENTRY_SIGNAL",
        "ENTRY_UPDATE",
        "SIGNAL_RETRACTED",
        "AMBIGUOUS",
        "UNKNOWN",
        "PROTECT_AND_NOTIFY",
        "SIGNAL_UPDATED",
    }:
        return True
    return (
        action not in ("INFORMATIONAL", None)
        and not classification.get("_reason")
        and float(classification.get("confidence") or 0.0) < 0.8
    )


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
        requires_review = (
            any(_classification_requires_review(c) for c in classifications)
            or bool(unhandled_fragments)
        )

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

    sig_id = _sig_id(signal)
    ref = _management_price_reference(signal)
    normalized = normalize_xauusd_management_price(price, ref)
    if normalized is not None:
        if normalized != price_f:
            journal.event(sig_id, "mgmt_price_normalized",
                          action="MOVE_SL_TO_PRICE",
                          raw_price=price_f,
                          normalized_price=normalized,
                          reference_price=ref,
                          raw_snippet=raw_text[:120])
        return normalized

    if ref is None:
        journal.anomaly(sig_id, "channel_msg", "warning",
                        "MOVE_SL_TO_PRICE con precio no absoluto y sin "
                        "referencia XAUUSD; accion ignorada",
                        raw_price=price_f, raw_snippet=raw_text[:120])
        return None

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


def _is_explicit_signal_retraction(text: str) -> bool:
    normalized = str(text or "").lower().replace("’", "'")
    normalized = re.sub(r"[^a-z0-9'\s]", " ", normalized)
    normalized = " ".join(normalized.split())
    return normalized in {
        "this is not a new signal",
        "this was not a new signal",
        "this isn't a new signal",
        "this wasn't a new signal",
        "not a new signal",
    }


def _signal_levels_materially_equal(
    left: Signal,
    right: Signal,
    *,
    level_tolerance: float = 0.5,
    fill_tolerance: float = 1.0,
) -> bool:
    if left.direction != right.direction:
        return False
    if left.sl is None or right.sl is None:
        return False
    if abs(float(left.sl) - float(right.sl)) > level_tolerance:
        return False
    if not left.tps or len(left.tps) != len(right.tps):
        return False
    if any(
        abs(float(left_tp) - float(right_tp)) > level_tolerance
        for left_tp, right_tp in zip(left.tps, right.tps)
    ):
        return False

    left_has_range = left.range_low is not None and left.range_high is not None
    right_has_range = right.range_low is not None and right.range_high is not None
    if left_has_range and right_has_range:
        return (
            abs(float(left.range_low) - float(right.range_low))
            <= level_tolerance
            and abs(float(left.range_high) - float(right.range_high))
            <= level_tolerance
        )
    if left_has_range != right_has_range:
        return False

    if left.market_fill_price is None or right.market_fill_price is None:
        return False
    command_keys_match = (
        left.telegram_entry_command_key is not None
        and left.telegram_entry_command_key == right.telegram_entry_command_key
    )
    return command_keys_match and (
        abs(float(left.market_fill_price) - float(right.market_fill_price))
        <= fill_tolerance
    )


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _select_explicit_duplicate_retraction(
    open_signals: list[Signal],
    now: datetime,
    *,
    max_age_s: float = 180.0,
) -> dict:
    ordered = sorted(
        (signal for signal in open_signals if signal.status == "open"),
        key=lambda signal: _as_naive_utc(signal.timestamp),
        reverse=True,
    )
    if len(ordered) < 2:
        return {"candidate": None, "original": None, "reason": "not_enough_open_signals"}

    candidate = ordered[0]
    age_s = (_as_naive_utc(now) - _as_naive_utc(candidate.timestamp)).total_seconds()
    if age_s < 0 or age_s > float(max_age_s):
        return {"candidate": None, "original": None, "reason": "newest_outside_window"}
    if not candidate.all_filled_tickets and not candidate.pending_tickets:
        return {"candidate": None, "original": None, "reason": "newest_has_no_exposure"}

    matches = [
        original
        for original in ordered[1:]
        if original.channel == candidate.channel
        and _signal_levels_materially_equal(candidate, original)
    ]
    if len(matches) == 1:
        return {
            "candidate": candidate,
            "original": matches[0],
            "reason": "proven_duplicate",
            "age_s": age_s,
        }
    if len(matches) > 1:
        reason = "multiple_matching_originals"
    else:
        reason = "no_materially_identical_original"
    return {"candidate": None, "original": None, "reason": reason, "age_s": age_s}


async def _handle_explicit_signal_retraction(msg, channel: str) -> bool:
    text = getattr(msg, "text", None) or getattr(msg, "message", None) or ""
    if not _is_explicit_signal_retraction(text):
        return False

    msg_date = getattr(msg, "date", None) or datetime.now(timezone.utc)
    result = _select_explicit_duplicate_retraction(
        state.open_signals(channel),
        _as_naive_utc(msg_date),
    )
    evidence_id = f"{channel}_{getattr(msg, 'id', 'unknown')}"
    journal.event(
        evidence_id,
        "provider_signal_retraction_received",
        channel=channel,
        raw_text=text[:240],
        selection_reason=result["reason"],
        tg_ts=_msg_ts_iso(msg),
    )

    candidate = result.get("candidate")
    original = result.get("original")
    if candidate is None or original is None:
        open_ids = [_sig_id(signal) for signal in state.open_signals(channel)]
        journal.anomaly(
            evidence_id,
            "channel_msg",
            "warning",
            "retractacion explicita sin duplicado demostrable; no se tocaron ordenes",
            reason=result["reason"],
            open_signals=open_ids,
            raw_text=text[:240],
        )
        _schedule_detached(notify(
            f"⚠️ {provider_display_name(channel)}\n"
            "RETRACTACIÓN SIN DESTINO SEGURO\n\n"
            f"Mensaje: {text[:180]}\n"
            "El bot no cambió ninguna operación. Revisa MT5."
        ))
        return True

    candidate.requested_close_reason = "PROVIDER_RETRACTED"
    for ticket in candidate.all_filled_tickets:
        pending_actions.enqueue_close_position(
            candidate,
            ticket,
            label=f"PROVIDER_RETRACTED #{ticket}",
        )
    for ticket in candidate.pending_tickets:
        pending_actions.enqueue_cancel_pending(
            candidate,
            ticket,
            label=f"PROVIDER_RETRACTED pending #{ticket}",
        )
    journal.event(
        _sig_id(candidate),
        "provider_duplicate_retraction_applied",
        retraction_message_id=getattr(msg, "id", None),
        retracted_signal_id=_sig_id(candidate),
        original_signal_id=_sig_id(original),
        age_s=round(float(result.get("age_s", 0.0)), 3),
        closed_tickets=list(candidate.all_filled_tickets),
        cancelled_tickets=list(candidate.pending_tickets),
        raw_text=text[:240],
    )
    _schedule_detached(notify(
        f"✅ {provider_display_name(channel)}\n"
        "SEÑAL DUPLICADA RETIRADA\n\n"
        f"Cerrando {len(candidate.all_filled_tickets)} posiciones de la señal "
        f"{candidate.message_id}. La operación original {original.message_id} "
        "se mantiene."
    ))
    return True


def _canal2_duplicate_alias_candidate(message_id: int, direction: str,
                                      timestamp: datetime, parsed: dict,
                                      open_signals: list,
                                      window_s: float, *,
                                      raw_text: str,
                                      is_reply: bool):
    """Find an older canal2 signal that should receive this msg as alias.

    This is stricter than observability-only duplicate detection: we only alias
    plain BUY/SELL NOW triggers. The goal is to avoid doubling exposure while
    still letting edits for the newer message_id update the already-open
    position. Entry levels may already be provisional, so they are not used as
    a dedupe blocker.
    """
    if is_reply:
        return None

    command_key = canal2_entry_command_key(raw_text)
    if command_key is None:
        return None
    for existing in open_signals:
        if existing.channel != "canal2" or existing.status != "open":
            continue
        if existing.message_id == message_id:
            continue
        if existing.direction != direction:
            continue
        if not existing.telegram_entry_was_reply:
            continue
        if existing.telegram_entry_command_key != command_key:
            continue
        existing_ts = (
            existing.telegram_entry_timestamp
            or existing.timestamp
        )
        delta_s = abs((timestamp - existing_ts).total_seconds())
        if delta_s <= window_s:
            return existing
    return None


def _register_canal2_duplicate_alias(existing: Signal, alias_message_id: int,
                                     raw_text: str, timestamp: datetime,
                                     window_s: float):
    state.alias(existing, alias_message_id)
    sig_id = _sig_id(existing)
    alias_sig_id = f"canal2_{alias_message_id}"
    existing_ts = existing.telegram_entry_timestamp or existing.timestamp
    delta_s = abs((timestamp - existing_ts).total_seconds())
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
                    "canal2 duplicate BUY/SELL NOW aliased to existing "
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
    return _same_direction_overlap_candidate(probe, open_signals, window_s)


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
                    "canal1 duplicate sticker aliased to existing "
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
    return await loop.run_in_executor(
        None,
        causal_trace.context_bound_call(fn, *args, **kwargs),
    )


def _schedule_detached(awaitable):
    """Schedule delayed bot work without retaining a Telegram decision."""
    with causal_trace.detached_context(), journal.detached_test_mode():
        return asyncio.ensure_future(awaitable)


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


async def _resolve_notify_chat_id():
    chat_id = config.NOTIFY_CHAT_ID
    if isinstance(chat_id, str) and chat_id.strip().lower() == "me":
        me = await client.get_me()
        return me.id
    if isinstance(chat_id, str) and chat_id.lstrip("-").isdigit():
        return int(chat_id)
    return chat_id


# ─── Notify contextual: decisión ambigua ──────────────────────────────────────
#
# Cuando el classifier (Gemini) devuelve una acción no-INFO con confianza
# 0.5-0.8 (zona ambigua), no aplicamos automáticamente — mandamos al usuario
# un resumen completo del estado del trade + lo que el trader dice + propuesta
# de Gemini para que decida. Mobile-friendly, todo en pantalla.


def _fmt_level(value) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_signed_money(value) -> str:
    try:
        return f"{float(value):+.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _human_channel(channel: str) -> str:
    return provider_display_name(channel)


def _human_review_action(action: str,
                         classification: dict | None = None) -> str:
    classification = classification or {}
    if (str(action or "").upper() == "REENTRY_SIGNAL"
            and classification.get("_reason")
            == "provider_confirmed_additional_entry"):
        direction = str(
            classification.get("entry_direction") or "?").upper()
        price = classification.get("price")
        if price is not None:
            return (f"el trader afirma que añadió otra entrada {direction} "
                    f"en {_fmt_level(price)}")
        return f"el trader afirma que añadió otra entrada {direction}"

    mapping = {
        "REENTRY_SIGNAL": "detectó una posible reentrada",
        "ENTRY_UPDATE": "detectó un cambio de entrada o niveles",
        "LEVEL_UPDATE": "detectó niveles nuevos",
        "LEVEL_CORRECTION": "detectó una corrección de niveles",
        "SIGNAL_UPDATED": "detectó un cambio general de la señal",
        "PROTECT_AND_NOTIFY": "detectó una sugerencia de protección",
        "OPTIONAL_SUGGESTION": "detectó una sugerencia opcional",
        "MOVE_SL_TO_BE": "detectó una orden de mover el SL a BE",
        "MOVE_SL_TO_PRICE": "detectó una orden de cambiar el SL",
        "CLOSE_ALL": "detectó una posible orden de cierre total",
        "CLOSE_FIRST": "detectó una posible toma parcial",
        "CLOSE_PARTIAL": "detectó una toma parcial del proveedor",
        "UNKNOWN": "no pudo interpretar el mensaje con seguridad",
        "AMBIGUOUS": "encontró más de una interpretación posible",
        "INFORMATIONAL": "no pudo clasificar el mensaje",
    }
    return mapping.get(
        str(action or "").upper(),
        "detectó una situación que requiere revisión",
    )


def _review_decision(ctx, classification: dict) -> str:
    action = str(classification.get("action") or "UNKNOWN").upper()
    if classification.get("_gemini_failed"):
        action = "UNKNOWN"
    if action == "REENTRY_SIGNAL":
        if (classification.get("_reason")
                == "provider_confirmed_additional_entry"):
            provider_price = classification.get("price")
            current_price = getattr(ctx, "current_price", None)
            if provider_price is not None and current_price is not None:
                delta = float(current_price) - float(provider_price)
                if abs(delta) < 0.005:
                    distance = "prácticamente en el precio indicado"
                else:
                    relation = "por encima" if delta > 0 else "por debajo"
                    distance = (f"{abs(delta):.2f} {relation} del precio "
                                "indicado")
                return (
                    f"Mercado ahora {_fmt_level(current_price)}: {distance}. "
                    "El bot no abrió otra posición; revísala en MT5."
                )
            return (
                "El bot no abrió otra posición; revisa la entrada adicional "
                "en MT5."
            )
        return "Decide ahora: abrir una entrada adicional o ignorar el mensaje."
    if action in {"ENTRY_UPDATE", "LEVEL_UPDATE", "LEVEL_CORRECTION",
                  "SIGNAL_UPDATED"}:
        return "Decide ahora: mantener los niveles actuales o revisarlos en MT5."
    if action == "MOVE_SL_TO_BE":
        entry = getattr(ctx, "entry_price", None)
        if entry is not None:
            return ("Decide ahora: aplicar BE en la entrada real "
                    f"{_fmt_level(entry)} o mantener el SL actual.")
        return "Decide ahora: aplicar BE o mantener el SL actual."
    if action == "MOVE_SL_TO_PRICE":
        price = classification.get("price")
        if price is not None:
            return ("Decide ahora: aplicar el SL indicado "
                    f"({_fmt_level(price)}) o mantener el actual.")
        return "Decide ahora: cambiar el SL o mantener el actual."
    if action == "CLOSE_ALL":
        return (f"Decide ahora: cerrar las {getattr(ctx, 'n_open', 0)} "
                "posiciones abiertas o mantener la operación.")
    if action in {"CLOSE_FIRST", "CLOSE_PARTIAL"}:
        return "Decide ahora: cerrar una parte o mantener la operación completa."
    if action in {"PROTECT_AND_NOTIFY", "OPTIONAL_SUGGESTION"}:
        return ("Decide ahora: aplicar la sugerencia manualmente o mantener "
                "la gestión actual.")
    return "Decide ahora: interpretar el mensaje y actuar solo si la orden es clara."


def _compact_trader_message(raw_text: str, limit: int = 160) -> str:
    text = " ".join((raw_text or "").strip().split())
    if not text:
        return "(sin texto)"
    if len(text) <= limit:
        return text
    return text[:limit - 3].rstrip() + "..."


def format_review_notification(ctx, classification: dict, raw_text: str) -> str:
    """Render one human-first alert using only verified trade context."""
    action = str(classification.get("action") or "UNKNOWN").upper()
    try:
        elapsed_str = f"{float(ctx.elapsed_min):.0f} min"
    except (TypeError, ValueError):
        elapsed_str = None

    heading = [_human_channel(ctx.channel), str(ctx.direction or "?")]
    if elapsed_str:
        heading.append(elapsed_str)

    market_items = []
    if getattr(ctx, "current_price", None) is not None:
        market_items.append(f"Mercado {_fmt_level(ctx.current_price)}")
    if getattr(ctx, "entry_price", None) is not None:
        market_items.append(f"Entrada {_fmt_level(ctx.entry_price)}")
    if getattr(ctx, "sl", None) is not None:
        market_items.append(f"SL {_fmt_level(ctx.sl)}")
    if getattr(ctx, "be_armed", False):
        market_items.append("BE activo")

    lines = [
        "⚠️ REVISIÓN NECESARIA",
        " · ".join(heading),
        (f"Cuenta: {_fmt_signed_money(ctx.floating_pnl_total)} · "
         f"{ctx.n_open}/{ctx.n_initial} posiciones abiertas"),
    ]
    if market_items:
        lines.append(" · ".join(market_items))
    lines.extend([
        "",
        f"Proveedor: “{_compact_trader_message(raw_text)}”",
        f"Bot: {_human_review_action(action, classification)}; "
        "no ejecutó cambios.",
        _review_decision(ctx, classification),
    ])
    return "\n".join(lines)


def format_review_graph_caption(ctx, classification: dict,
                                raw_text: str) -> str:
    """Compact caption; price context already lives in the verified chart."""
    action = str(classification.get("action") or "UNKNOWN").upper()
    return "\n".join([
        "⚠️ ACCIÓN NECESARIA",
        f"{_human_channel(ctx.channel)} · {ctx.direction}",
        "",
        f"💬 Trader: “{_compact_trader_message(raw_text)}”",
        f"🤖 Bot: {_human_review_action(action, classification)}; "
        "operación sin cambios.",
        "",
        f"👉 {_review_decision(ctx, classification)}",
    ])


async def notify_review_graph(signal: "Signal", ctx, classification: dict,
                              raw_text: str) -> bool:
    """Send a truthful chart, returning False so the caller can send text."""
    if (not config.REVIEW_ALERT_GRAPH_ENABLED
            or not getattr(config, "TELEGRAM_BOT_TOKEN", None)):
        return False
    caption = format_review_graph_caption(ctx, classification, raw_text)
    try:
        png = await asyncio.wait_for(
            asyncio.to_thread(
                alert_graphics.build_live_review_image,
                signal,
                ctx,
                config.MT5_SYMBOL,
                config.REVIEW_ALERT_GRAPH_WINDOW_MIN,
            ),
            timeout=config.REVIEW_ALERT_GRAPH_BUILD_TIMEOUT_S,
        )
        chat_id = await _resolve_notify_chat_id()
        send_budget_s = max(2.0, config.REVIEW_ALERT_GRAPH_SEND_TIMEOUT_S)
        request_timeout_s = max(1.0, send_budget_s / 2.0)
        message_id = await asyncio.wait_for(
            asyncio.to_thread(
                telegram_notifications.send_photo_with_caption,
                config.TELEGRAM_BOT_TOKEN,
                chat_id,
                png,
                caption,
                timeout_s=request_timeout_s,
            ),
            timeout=send_budget_s + 1.0,
        )
        journal.event(
            ctx.signal_id,
            "notify_graph_sent",
            telegram_message_id=message_id,
            image_bytes=len(png),
            tick_window_min=config.REVIEW_ALERT_GRAPH_WINDOW_MIN,
        )
        return True
    except Exception as exc:
        journal.event(
            ctx.signal_id,
            "notify_graph_failed",
            error_type=type(exc).__name__,
            error=str(exc)[:300],
            fallback="text",
        )
        print(f"[Notify Graph] fallback texto para {ctx.signal_id}: {exc}")
        return False


async def notify_ambiguous_decision(signal: "Signal", classification: dict,
                                    raw_text: str):
    """Send one concise human-review alert with fresh MT5 context."""
    try:
        ctx = signal.build_context()
        action = classification.get("action", "?")
        conf = classification.get("confidence", 0)
        reasoning = classification.get("reasoning") or classification.get("_reason") or "-"
        text = format_review_notification(ctx, classification, raw_text)
        print(f"[Notify Ambig] enviado resumen para {ctx.signal_id} "
              f"action={action} conf={conf:.2f}")
        try:
            journal.event(ctx.signal_id, "ambiguous_decision_notified",
                          action=action, confidence=conf, reasoning=reasoning,
                          n_open=ctx.n_open, floating_pnl=ctx.floating_pnl_total,
                          current_price=ctx.current_price)
            if (str(action).upper() == "REENTRY_SIGNAL"
                    and classification.get("_reason")
                    == "provider_confirmed_additional_entry"):
                provider_price = classification.get("price")
                current_price = ctx.current_price
                market_delta = None
                if provider_price is not None and current_price is not None:
                    market_delta = round(
                        float(current_price) - float(provider_price), 5)
                journal.event(
                    ctx.signal_id,
                    "explicit_additional_entry_review",
                    entry_direction=classification.get("entry_direction"),
                    provider_price=provider_price,
                    current_price=current_price,
                    market_delta=market_delta,
                    n_open=ctx.n_open,
                    behavior="notify_only",
                    raw_text=_compact_trader_message(raw_text, limit=300),
                )
        except Exception:
            pass
        graph_sent = await notify_review_graph(
            signal, ctx, classification, raw_text,
        )
        if not graph_sent:
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

# Dispatch completion is separate from the early in-handler dedup claim.
# This prevents a duplicate delivery from being acknowledged while the first
# handler is still running, and lets a failed processed-event write be retried
# without executing the MT5 actions a second time.
_dispatch_inflight_revisions: set[str] = set()
_dispatch_completed_revisions: set[str] = set()
_dispatch_completed_order: list[str] = []
_DISPATCH_COMPLETED_MAX = 2000

# Dedup de acciones de gestion ya clasificadas. Telegram puede entregar el
# mismo reply/edit por poller y handler, o repetirlo en segundos. Las acciones
# idempotentes generan retcode=10025 y ensucian el expediente; las de cierre
# pueden provocar ruido peor. Mantenemos una ventana corta por signal+texto+accion.
_seen_management_actions: dict[tuple, datetime] = {}
_MGMT_DUP_WINDOW_S = 45.0
_MGMT_DUP_MAX = 1000

# Canal2 puede editar el mensaje mientras la apertura market original sigue
# bloqueada en MT5. El gate conserva tambien las aperturas ya confirmadas:
# una entrega posterior del mismo mensaje nunca puede crear otra exposicion.
_entry_execution_gate = EntryExecutionGate(max_committed=1000)
_entry_serial_locks: dict[str, tuple[object, asyncio.Lock]] = {}
_canal2_opening_msg_ids: set[int] = set()
_deferred_canal2_entry_edits: dict[int, dict] = {}
_canal2_zone_plans: dict[int, dict] = {}
_CANAL2_ZONE_PLAN_MAX = 200


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


def _release_dispatch_dedup_claim(
    channel: str,
    msg,
    update_kind: str,
) -> None:
    if update_kind == "new":
        key = (channel, msg.id)
        _seen_new_msg_ids.discard(key)
        try:
            _seen_new_msgs_order.remove(key)
        except ValueError:
            pass
        return
    edit_date = getattr(msg, "edit_date", None)
    if edit_date is None:
        return
    key = (channel, msg.id, edit_date.isoformat())
    _seen_edits.discard(key)
    try:
        _seen_edits_order.remove(key)
    except ValueError:
        pass


def _remember_dispatch_completed(message_revision_id: str) -> None:
    if message_revision_id in _dispatch_completed_revisions:
        return
    _dispatch_completed_revisions.add(message_revision_id)
    _dispatch_completed_order.append(message_revision_id)
    if len(_dispatch_completed_order) > _DISPATCH_COMPLETED_MAX:
        old = _dispatch_completed_order.pop(0)
        _dispatch_completed_revisions.discard(old)


def _entry_open_claim(channel: str, msg_id: int) -> bool:
    return _entry_execution_gate.claim(channel, msg_id)


def _entry_open_finished(channel: str, msg_id: int) -> None:
    _entry_execution_gate.release(channel, msg_id)


def _entry_open_committed(channel: str, msg_id: int) -> None:
    _entry_execution_gate.commit(channel, msg_id)


def _entry_open_in_progress(channel: str, msg_id: int) -> bool:
    return _entry_execution_gate.in_progress(channel, msg_id)


def _entry_open_already_committed(channel: str, msg_id: int) -> bool:
    return _entry_execution_gate.committed(channel, msg_id)


def _entry_serial_lock(channel: str) -> asyncio.Lock:
    """Return one entry-only lock bound to the current asyncio event loop."""
    loop = asyncio.get_running_loop()
    current = _entry_serial_locks.get(channel)
    if current is None or current[0] is not loop:
        lock = asyncio.Lock()
        _entry_serial_locks[channel] = (loop, lock)
        return lock
    return current[1]


def _canal2_open_started(msg_id: int) -> None:
    if _entry_open_claim("canal2", msg_id):
        _canal2_opening_msg_ids.add(msg_id)


def _canal2_open_claim(msg_id: int) -> bool:
    claimed = _entry_open_claim("canal2", msg_id)
    if claimed:
        _canal2_opening_msg_ids.add(msg_id)
    return claimed


def _canal2_open_finished(msg_id: int) -> None:
    """Release a claim only when MT5 did not create exposure."""
    _entry_open_finished("canal2", msg_id)
    _canal2_opening_msg_ids.discard(msg_id)


def _canal2_open_committed(msg_id: int) -> None:
    """Make a successful MT5 exposure claim irreversible in this process."""
    _entry_open_committed("canal2", msg_id)
    _canal2_opening_msg_ids.discard(msg_id)


def _canal2_open_in_progress(msg_id: int) -> bool:
    return _entry_open_in_progress("canal2", msg_id)


def _canal2_open_already_committed(msg_id: int) -> bool:
    return _entry_open_already_committed("canal2", msg_id)


def _defer_canal2_entry_edit(msg, text: str) -> None:
    edit_ts = getattr(msg, "edit_date", None) or getattr(msg, "date", None)
    _deferred_canal2_entry_edits[msg.id] = {
        "text": text,
        "tg_ts": edit_ts.isoformat(timespec="seconds") if edit_ts else None,
    }


def _pop_deferred_canal2_entry_edit(msg_id: int):
    return _deferred_canal2_entry_edits.pop(msg_id, None)


def _merge_canal2_entry_parsed(base: dict, update: dict) -> dict:
    """Fusiona una entrada pendiente con un edit/reply de correccion.

    Canal 2 a menudo manda niveles en oleadas: un reply puede traer solo TP1
    y SL corregido. Conservamos los TPs restantes del mensaje base cuando el
    update trae una lista parcial.
    """
    merged = dict(base or {})
    if "direction" in update and "direction" not in merged:
        merged["direction"] = update["direction"]
    if "range" in update:
        merged["range"] = update["range"]
    if "tps" in update:
        if merged.get("tps") and len(update["tps"]) < len(merged["tps"]):
            merged["tps"] = list(update["tps"]) + list(merged["tps"][len(update["tps"]):])
        else:
            merged["tps"] = list(update["tps"])
    if "sl" in update:
        merged["sl"] = update["sl"]
    return merged


def _format_canal2_entry_text(parsed: dict, high_risk: bool = False) -> str:
    """Reconstruye una entrada canal2 parseable desde niveles ya fusionados."""
    direction = parsed["direction"]
    prefix = "HIGH RISK " if high_risk else ""
    lines = [f"{prefix}XAU USD {direction} NOW"]
    if parsed.get("range"):
        lo, hi = parsed["range"]
        first, second = (hi, lo) if direction == "BUY" else (lo, hi)
        lines.append(f"{first:g} - {second:g}")
    for idx, tp in enumerate(parsed.get("tps") or [], start=1):
        lines.append(f"TP{idx} {tp:g}")
    if parsed.get("sl") is not None:
        lines.append(f"SL {parsed['sl']:g}")
    return "\n".join(lines)


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

    if channel == "canal2":
        sig, route = _resolve_management_reply_target(
            channel,
            reply_id,
            allow_single_open_fallback=False,
        )
        if sig is None and route != "target_signal_closed":
            sig, route = await _recover_canal2_management_target_from_reply_root(
                msg,
                int(reply_id),
            )
    else:
        sig, route = _resolve_management_reply_target(channel, reply_id)
    if sig is None:
        _log_unresolved_management_reply(msg, channel, reply_id, route)
        return True

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


def _resolve_management_reply_target(
        channel: str, reply_id: int, *,
        allow_single_open_fallback: bool = True,
        allow_cross_channel: bool = False):
    """Find the live Signal targeted by a management reply.

    Normal path: reply_id is the original signal id or a live alias.
    Restart path: aliases are in-memory only, so a reply can point to a lost
    alias while one recovered signal remains open in the same channel.
    """
    sig = state.get(channel, reply_id)
    if sig is None and allow_cross_channel:
        other_channel = "canal1" if channel == "canal2" else "canal2"
        sig = state.get(other_channel, reply_id)
    if sig is not None:
        if sig.status == "open":
            return sig, "direct"
        return None, "target_signal_closed"

    same_channel_open = state.open_signals(channel)
    if allow_single_open_fallback and len(same_channel_open) == 1:
        sig = same_channel_open[0]
        journal.event(
            _sig_id(sig),
            "management_reply_routed_by_open_signal",
            reply_to_msg_id=reply_id,
            channel=channel,
            route="single_open_same_channel",
        )
        return sig, "single_open_same_channel"
    if same_channel_open:
        return None, "ambiguous_open_signals"
    return None, "unknown_reply_target"


async def _recover_canal2_management_target_from_reply_root(
        msg, reply_id: int):
    """Inspect the replied root without guessing which live trade owns it."""
    get_reply = getattr(msg, "get_reply_message", None)
    if not callable(get_reply):
        return None, "reply_root_unavailable"
    try:
        root_msg = await get_reply()
    except Exception as exc:
        journal.event(
            f"canal2_{msg.id}",
            "canal2_management_root_recovery_failed",
            reply_to_msg_id=int(reply_id),
            error_type=type(exc).__name__,
            error=str(exc)[:240],
        )
        return None, "reply_root_unavailable"

    root_id = getattr(root_msg, "id", None)
    if root_msg is None or root_id is None or int(root_id) != int(reply_id):
        return None, "reply_root_unavailable"
    root_text = _msg_text(root_msg)
    if not is_canal2_entry(root_text):
        return None, "reply_root_not_entry"

    journal.event(
        f"canal2_{msg.id}",
        "canal2_management_root_identity_unproven",
        reply_to_msg_id=int(reply_id),
        channel="canal2",
        root_direction=parse_canal2(root_text).get("direction"),
        open_signals=[_sig_id(sig) for sig in state.open_signals("canal2")],
    )
    return None, "reply_root_identity_unproven"


def _looks_actionable_management_text(text: str) -> bool:
    t = (text or "").upper()
    result_announcement = bool(re.search(
        r"\b(?:BE|BREAKEVEN|BREAK\s+EVEN|SL|STOP\s*LOSS|TP\s*\d*|"
        r"TARGET\s*\d*)\s+(?:WAS\s+)?(?:HIT|KISSED|REACHED|DONE)\b",
        t,
    ))
    explicit_instruction = bool(re.search(
        r"\b(?:MOVE\s+SL|SL\s+TO|CLOSE(?:\s+(?:ALL|NOW|HALF|PARTIALS?))?|"
        r"TAKE\s+PROFITS?\s+FROM\s+(?:THE\s+)?LAYERS?|"
        r"MAKE\b[^\n]{0,40}\bRISK\s+FREE|SET\s+(?:SL|STOP)|CUT\b)",
        t,
    ))
    if result_announcement and not explicit_instruction:
        return False
    actionable_markers = (
        "MOVE SL",
        "SL TO",
        "RISK FREE",
        "BREAKEVEN",
        "BREAK EVEN",
        "CLOSE",
        "SECURE",
        "PROTECT",
        "CUT ",
        "CUTS",
    )
    return any(marker in t for marker in actionable_markers)


def _unresolved_management_severity(reason: str, actionable: bool) -> str:
    """Classify routing failures without alarming on stale closed replies."""
    if reason == "target_signal_closed":
        return "info"
    if actionable:
        return "critical"
    return "warning"


def _log_unresolved_management_reply(msg, channel: str, reply_id: int,
                                     reason: str) -> None:
    text = _msg_text(msg)
    sig_id = f"{channel}_{getattr(msg, 'id', 'unknown')}"
    text_preview = (text or "")[:200].replace("\n", " | ")
    open_ids = [_sig_id(sig) for sig in state.open_signals(channel)]
    actionable = _looks_actionable_management_text(text)
    severity = _unresolved_management_severity(reason, actionable)
    journal.event(
        sig_id,
        "management_reply_unresolved",
        channel=channel,
        reply_to_msg_id=reply_id,
        reason=reason,
        actionable=actionable,
        open_signals=open_ids,
        text_preview=text_preview,
        tg_ts=_msg_ts_iso(msg),
    )
    journal.anomaly(
        sig_id,
        "channel_msg",
        severity,
        "mensaje de gestion reply no pudo asociarse a una senal viva",
        channel=channel,
        reply_to_msg_id=reply_id,
        reason=reason,
        actionable=actionable,
        open_signals=open_ids,
        text_preview=text_preview,
    )


def _num_entries_for_channel(channel: str) -> int:
    """Número de entradas (market + limits) configurado para este canal."""
    if channel == "canal1":
        return config.STRATEGY_C1_NUM_ENTRIES
    return config.STRATEGY_C2_NUM_ENTRIES


def _rescue_market_capacity(signal: Signal) -> dict:
    """Return whether one more same-sized market leg fits the live cap."""
    current_positions = len(signal.all_filled_tickets)
    lot = float(signal.effective_lot)
    current_lots = round(current_positions * lot, 8)
    projected_lots = round(current_lots + lot, 8)
    max_lots = round(
        float(config.STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL), 8,
    )
    return {
        "allowed": projected_lots <= max_lots + 1e-9,
        "current_positions": current_positions,
        "current_lots": current_lots,
        "projected_lots": projected_lots,
        "max_lots": max_lots,
    }


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
        signal.time_stop_at is not None
        or signal.be_at_tp_index is not None
        or (
            signal.channel == "canal1"
            and config.STRATEGY_C1_BASKET_GUARD_ENABLED
        )
    )
    if not needs_monitor_anyway:
        print(
            "[Trade Monitor] Sin BE/time-stop/proteccion de cesta; "
            "monitor omitido."
        )
        return

    guard_active = bool(
        signal.channel == "canal1"
        and config.STRATEGY_C1_BASKET_GUARD_ENABLED
    )
    print(
        f"[Trade Monitor] Activo para BE/time-stop/proteccion "
        f"(be@idx={signal.be_at_tp_index}, ts={signal.time_stop_at}, "
        f"basket_guard={guard_active})"
    )
    position_lifecycle_monitor.start(signal, [])
    return

async def _open_extra_legs(sig: Signal, msg_id: int) -> None:
    """Expose one atomic opening window to the independent live auditor."""
    sig.opening_extra_legs = True
    try:
        await _open_extra_legs_impl(sig, msg_id)
    finally:
        sig.opening_extra_legs = False


async def _open_extra_legs_impl(sig: Signal, msg_id: int) -> None:
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
            capacity = _rescue_market_capacity(sig)
            if not capacity["allowed"]:
                journal.event(
                    sig_id,
                    "scale_out_leg_skipped_exposure_cap",
                    leg=n,
                    configured_positions=n_legs,
                    **capacity,
                )
                print(
                    f"[{channel}] Scale-out limitado a "
                    f"{capacity['max_lots']:.2f} lot por senal"
                )
                break
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

    position_levels = await _run(
        executor.open_position_levels,
        list(signal.all_filled_tickets),
    )
    if position_levels is None:
        position_levels = {}

    for i, t in enumerate(signal.all_filled_tickets):
        # TP segun override o escalonado
        if t in signal.tp_overrides and signal.tps:
            override_idx = signal.tp_overrides[t]
            override_idx = max(0, min(override_idx, len(signal.tps) - 1))
            tp_i = signal.tps[override_idx]
        else:
            tp_i = signal.tp_for_position(i)

        installed = position_levels.get(int(t), {})
        installed_tp = installed.get("tp")
        point = float(installed.get("point") or 0.01)
        tp_already_installed = (
            tp_i is not None
            and installed_tp not in (None, 0, 0.0)
            and abs(float(installed_tp) - float(tp_i))
            <= max(point / 2.0, 1e-8)
        )
        if tp_already_installed:
            journal.event(
                _sig_id(signal),
                "tp_preserved_installed",
                ticket=t,
                requested_tp=float(tp_i),
                installed_tp=float(installed_tp),
                point=point,
            )

        # ── TP PERSECUCION ───────────────────────────────────────────────
        if (
            tp_i is not None
            and not tp_already_installed
            and tick is not None
            and signal.tps
        ):
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
        tp_to_apply = None if tp_already_installed else tp_i
        if signal.be_armed:
            sl_to_apply = await _run(executor.entry_price, t)
            if sl_to_apply is None:
                # Sin entry legible: aplicar solo TP (no tocar SL existente)
                if tp_to_apply is not None:
                    pending_actions.enqueue_modify_tp(
                        signal, t, tp_to_apply,
                        label=f"TP[{i}]→{tp_to_apply} #{t} (BE preserved)"
                    )
                continue
        else:
            sl_to_apply = signal.sl

        if sl_to_apply is not None and tp_to_apply is not None:
            label_suffix = " (BE)" if signal.be_armed else ""
            pending_actions.enqueue_modify_sltp(
                signal, t, sl_to_apply, tp_to_apply,
                label=f"SL/TP[{i}]→{tp_to_apply} #{t}{label_suffix}"
            )
        elif sl_to_apply is not None:
            pending_actions.enqueue_modify_sl(signal, t, sl_to_apply, label=f"SL #{t}")
        elif tp_to_apply is not None:
            pending_actions.enqueue_modify_tp(
                signal, t, tp_to_apply, label=f"TP[{i}]→{tp_to_apply} #{t}"
            )


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
        capacity = _rescue_market_capacity(signal)
        if not capacity["allowed"]:
            print(
                "[Layered] rescue omitido: limite de exposicion "
                f"{capacity['max_lots']:.2f} lot alcanzado "
                f"({capacity['current_positions']} posiciones)"
            )
            journal.event(
                sig_id,
                "rescue_market_skipped_exposure_cap",
                **capacity,
            )
            signal.entry_mode = "market_only"
            return False
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
# si MT5 cierra primero por su SL, position_lifecycle_monitor.run() lo detecta vía n_open=0
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


def _delayed_action_parent(signal: Signal) -> tuple[str | None, str | None]:
    active = causal_trace.current_context()
    return (
        active.message_revision_id or signal.source_message_revision_id,
        active.decision_id or signal.source_decision_id,
    )


def _enqueue_internal_closes(
    signal: Signal,
    tickets: list[int],
    *,
    source_message_revision_id: str | None,
    parent_decision_id: str | None,
    decision_reason: str,
    label_for_ticket,
) -> None:
    with causal_trace.bind_internal_decision(
        message_revision_id=source_message_revision_id,
        parent_decision_id=parent_decision_id,
        reason=decision_reason,
    ) as decision:
        journal.event(
            _sig_id(signal),
            "bot_internal_decision_started",
            decision_id=decision.decision_id,
            message_revision_id=decision.message_revision_id,
            parent_decision_id=decision.parent_decision_id,
            decision_reason=decision.decision_reason,
        )
        try:
            for ticket in tickets:
                pending_actions.enqueue_close_position(
                    signal,
                    ticket,
                    label=label_for_ticket(ticket),
                )
        finally:
            action_ids = causal_trace.declared_action_ids(decision)
            journal.event(
                _sig_id(signal),
                "bot_internal_decision",
                decision_id=decision.decision_id,
                message_revision_id=decision.message_revision_id,
                parent_decision_id=decision.parent_decision_id,
                decision_reason=decision.decision_reason,
                declared_action_ids=action_ids,
                declared_action_count=len(action_ids),
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
    source_revision_id, parent_decision_id = _delayed_action_parent(signal)
    _schedule_detached(_close_first_be_timeout(
        signal,
        timeout_s,
        source_message_revision_id=source_revision_id,
        parent_decision_id=parent_decision_id,
    ))

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
            f"🛟 {provider_display_name(signal.channel)} · "
            f"{signal.direction}\n"
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


async def _close_first_be_timeout(
    signal: Signal,
    timeout_s: int,
    *,
    source_message_revision_id: str | None = None,
    parent_decision_id: str | None = None,
):
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
        if source_message_revision_id is None:
            source_message_revision_id = (
                signal.source_message_revision_id
            )
        if parent_decision_id is None:
            parent_decision_id = signal.source_decision_id
        _enqueue_internal_closes(
            signal,
            still_open,
            source_message_revision_id=source_message_revision_id,
            parent_decision_id=parent_decision_id,
            decision_reason="close_first_be_timeout_expired",
            label_for_ticket=lambda ticket: (
                f"CLOSE_FIRST BE-timeout #{ticket} ({timeout_s}s)"
            ),
        )
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


async def _be_rescue_timeout(
    signal: Signal,
    timeout_s: int,
    *,
    source_message_revision_id: str | None = None,
    parent_decision_id: str | None = None,
):
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

        if source_message_revision_id is None:
            source_message_revision_id = (
                signal.source_message_revision_id
            )
        if parent_decision_id is None:
            parent_decision_id = signal.source_decision_id
        _enqueue_internal_closes(
            signal,
            still_open,
            source_message_revision_id=source_message_revision_id,
            parent_decision_id=parent_decision_id,
            decision_reason="be_rescue_timeout_expired",
            label_for_ticket=lambda ticket: (
                f"BE_RESCUE timeout #{ticket} ({timeout_s}s)"
            ),
        )
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
    source_revision_id, parent_decision_id = _delayed_action_parent(signal)
    _schedule_detached(_be_rescue_timeout(
        signal,
        timeout_s,
        source_message_revision_id=source_revision_id,
        parent_decision_id=parent_decision_id,
    ))

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


_REVIEW_ACTION_PRIORITY = {
    "UNKNOWN": 100,
    "AMBIGUOUS": 100,
    "REENTRY_SIGNAL": 95,
    "ENTRY_UPDATE": 90,
    "LEVEL_UPDATE": 90,
    "LEVEL_CORRECTION": 90,
    "SIGNAL_UPDATED": 90,
    "CLOSE_ALL": 85,
    "CLOSE_PROFIT_OR_BE": 85,
    "CLOSE_FIRST": 85,
    "MOVE_SL_TO_BE": 85,
    "MOVE_SL_TO_PRICE": 85,
    "PROTECT_AND_NOTIFY": 80,
    "OPTIONAL_SUGGESTION": 80,
    "CLOSE_PARTIAL": 75,
    "TP_HIT_ANNOUNCEMENT": 10,
    "PROGRESS_UPDATE": 5,
    "MARKET_COMMENTARY": 1,
}


def _select_review_classification(signal: Signal, classifications: list[dict],
                                  raw_text: str) -> dict | None:
    """Choose one decision-bearing interpretation for one source message."""
    candidates = []
    for index, classification in enumerate(classifications):
        action = str(classification.get("action") or "UNKNOWN").upper()
        firewall = firewall_decision(signal, classification, raw_text=raw_text)
        confidence = float(classification.get("confidence") or 0.0)
        confidence_review = bool(
            firewall.will_execute
            and action != "INFORMATIONAL"
            and confidence < 0.8
            and not classification.get("_reason")
        )
        needs_review = bool(
            classification.get("_gemini_failed")
            or classification.get("requires_review")
            or firewall.requires_review
            or confidence_review
        )
        if not needs_review:
            continue
        priority = _REVIEW_ACTION_PRIORITY.get(action, 50)
        if classification.get("_gemini_failed"):
            priority = 110
        candidates.append((priority, -index, classification))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


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
    classifications = normalize_classifier_outputs(classifications)
    if not classifications:
        return

    sig_id = _sig_id(signal)
    sl_hit_detected = False
    review_candidate = _select_review_classification(
        signal, classifications, raw_text
    )
    review_notification_sent = False
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

    # A provider can mention a past SL while our live trade is still open.
    # MT5, not wording alone, is authoritative for closing the Signal.
    if raw_text and _SL_HIT_RE.search(raw_text):
        try:
            open_positions = await _run(
                _open_mt5_positions_for_signal, signal)
        except Exception as exc:
            open_positions = None
            query_error = f"{type(exc).__name__}: {exc}"
        else:
            query_error = None

        if open_positions == []:
            duration_s = (datetime.utcnow() - signal.timestamp).total_seconds()
            strategies.record_sl_hit(signal.channel, duration_s)
            signal.status = "closed"
            sl_hit_detected = True
            journal.event(
                sig_id,
                "sl_hit_detected",
                raw_text=raw_text[:200],
                duration_s=round(duration_s, 1),
                evidence="mt5_no_open_positions",
            )
        else:
            journal.event(
                sig_id,
                "sl_hit_message_deferred",
                raw_text=raw_text[:200],
                reason=(
                    "mt5_positions_query_failed"
                    if open_positions is None
                    else "mt5_positions_still_open"
                ),
                open_tickets=(
                    [position.get("ticket") for position in open_positions]
                    if open_positions else []
                ),
                error=query_error,
            )

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
                          provider_stated_be_price=cl.get(
                              "provider_stated_be_price"),
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
            if cl is review_candidate and not review_notification_sent:
                try:
                    await notify_ambiguous_decision(signal, cl, raw_text)
                    review_notification_sent = True
                except Exception as e:
                    print(f"[Notify gemini_failed] error: {e}")
            journal.append_mgmt(
                sig_id, classified="GEMINI_FAILED", applied=False,
                required=False)
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

        firewall = firewall_decision(signal, cl, raw_text=raw_text)
        required_execution = _management_requires_execution(
            signal, cl, firewall)
        journal.event(sig_id, "interpretation_firewall_decision",
                      action=action_name,
                      price=cl.get("price"),
                      provider_stated_be_price=cl.get(
                          "provider_stated_be_price"),
                      confidence=confidence,
                      policy=firewall.policy,
                      will_execute=firewall.will_execute,
                      reason=firewall.reason,
                      requires_review=firewall.requires_review,
                      message_role=cl.get("message_role"),
                      execution_policy=cl.get("execution_policy"),
                      is_conditional=bool(cl.get("is_conditional")),
                      is_optional=bool(cl.get("is_optional")),
                      evidence=cl.get("evidence"),
                      required_execution=required_execution,
                      tg_ts=tg_ts,
                      raw_snippet=raw_text[:120])
        if not firewall.will_execute:
            if (firewall.requires_review
                    and cl is review_candidate
                    and not review_notification_sent):
                try:
                    await notify_ambiguous_decision(signal, cl, raw_text)
                    review_notification_sent = True
                except Exception as e:
                    print(f"[Notify firewall] error: {e}")
            journal.append_mgmt(
                sig_id,
                classified=f"{action_name}_{firewall.policy.upper()}",
                applied=False,
                required=required_execution,
            )
            journal.event(sig_id, "mgmt_msg",
                          action=action_name, price=cl.get("price"),
                          provider_stated_be_price=cl.get(
                              "provider_stated_be_price"),
                          confidence=confidence,
                          will_apply=False,
                          firewall_policy=firewall.policy,
                          firewall_reason=firewall.reason,
                          requires_review=firewall.requires_review,
                          required_execution=required_execution,
                          raw_snippet=raw_text[:120],
                          tg_ts=tg_ts)
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
            if cl is review_candidate and not review_notification_sent:
                try:
                    await notify_ambiguous_decision(signal, cl, raw_text)
                    review_notification_sent = True
                except Exception as e:
                    print(f"[Notify low_conf] error: {e}")
            journal.append_mgmt(sig_id, classified=f"{action_name}_LOWCONF",
                                applied=False, required=True)
            journal.event(sig_id, "mgmt_msg",
                          action=action_name, price=cl.get("price"),
                          provider_stated_be_price=cl.get(
                              "provider_stated_be_price"),
                          confidence=confidence,
                          will_apply=False,
                          low_confidence_notified=True,
                          required_execution=True,
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
            if cl is review_candidate and not review_notification_sent:
                try:
                    await notify_ambiguous_decision(signal, cl, raw_text)
                    review_notification_sent = True
                except Exception as e:
                    print(f"[Notify Ambig] error: {e}")
            # Marcar en journal como pendiente de decisión humana
            journal.append_mgmt(
                sig_id, classified=f"{action_name}_AMBIG", applied=False,
                required=True)
            journal.event(sig_id, "mgmt_msg",
                          action=action_name, price=cl.get("price"),
                          provider_stated_be_price=cl.get(
                              "provider_stated_be_price"),
                          confidence=confidence,
                          will_apply=False,
                          ambiguous_notified=True,
                          required_execution=True,
                          raw_snippet=raw_text[:120],
                          tg_ts=tg_ts)
            continue  # no ejecuta esta acción

        journal.append_mgmt(
            sig_id, classified=cl.get("action", "UNKNOWN"),
            applied=will_apply, required=required_execution)
        # Detalles de la acción al journal
        journal.event(sig_id, "mgmt_msg",
                      action=cl.get("action"), price=cl.get("price"),
                      provider_stated_be_price=cl.get(
                          "provider_stated_be_price"),
                      confidence=cl.get("confidence"),
                      gemini_failed=bool(cl.get("_gemini_failed")),
                      will_apply=will_apply,
                      required_execution=required_execution,
                      raw_snippet=raw_text[:120],
                      tg_ts=tg_ts)
        if not will_apply:
            print(f"[Acción] {cl.get('action')} ignorada — señal {signal.message_id} ya cerrada")
            continue
        await _execute_one_action(signal, cl, raw_text=raw_text)

    if sl_hit_detected:
        await _finalize_signal(signal, closed_by="SL",
                               notes="SL hit detectado en mensaje del canal")


async def _apply_exact_break_even(signal: Signal, *, source: str) -> bool:
    """Queue the real MT5 entry of every still-open ticket as its SL."""
    tickets = list(signal.all_filled_tickets)
    requested = []
    entry_prices = await _run(executor.open_entry_prices, tickets)
    if entry_prices is None:
        journal.event(
            _sig_id(signal),
            "be_armed_classifier",
            source=source,
            semantics="exact_entry_per_ticket",
            n_requested_exact_be=0,
            requested=[],
            closed_tickets_skipped=[],
            mt5_query_failed=True,
        )
        journal.anomaly(
            _sig_id(signal),
            "sl_be",
            "critical",
            "MT5 no respondio al consultar las entradas para aplicar BE",
            direction=signal.direction,
            tickets=tickets,
            source=source,
        )
        return False

    closed_tickets = [
        ticket for ticket in tickets if ticket not in entry_prices
    ]
    for ticket, entry in entry_prices.items():
        entry = float(entry)
        pending_actions.enqueue_modify_sl(
            signal,
            ticket,
            entry,
            label=f"BE #{ticket} -> {entry:.2f}",
            persist_until_signal_close=True,
        )
        requested.append({"ticket": ticket, "entry": entry})

    journal.event(
        _sig_id(signal),
        "be_armed_classifier",
        source=source,
        semantics="exact_entry_per_ticket",
        n_requested_exact_be=len(requested),
        requested=requested,
        closed_tickets_skipped=closed_tickets,
        mt5_query_failed=False,
    )
    if requested:
        signal.be_armed = True
        logger.log_action(signal, "MOVE_SL_TO_BE", requested[0]["entry"])
        return True
    return False


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
                      provider_stated_be_price=classification.get(
                          "provider_stated_be_price"),
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

    if action == "CLOSE_PROFIT_OR_BE":
        context = locals().get("ctx")
        floating_pnl = (
            float(context.floating_pnl_total)
            if context is not None else None
        )
        if floating_pnl is None:
            positions = await _run(
                executor.position_pnls,
                list(signal.all_filled_tickets),
            )
            if positions is not None:
                floating_pnl = sum(float(pnl) for _, pnl in positions)
        if floating_pnl is None:
            journal.anomaly(
                _sig_id(signal),
                "channel_msg",
                "critical",
                "no se pudo resolver CLOSE_PROFIT_OR_BE sin P&L vivo",
                raw_text=raw_text[:240],
            )
            return

        selected_action = (
            "CLOSE_ALL" if floating_pnl > 0 else "MOVE_SL_TO_BE"
        )
        journal.event(
            _sig_id(signal),
            "close_profit_or_be_resolved",
            floating_pnl=floating_pnl,
            selected_action=selected_action,
            threshold=0.0,
            semantics="strict_positive_close_else_exact_entry_be",
        )
        if selected_action == "CLOSE_ALL":
            for ticket in signal.all_filled_tickets:
                pending_actions.enqueue_close_position(
                    signal,
                    ticket,
                    label=f"CLOSE_PROFIT_OR_BE #{ticket}",
                )
            for ticket in signal.pending_tickets:
                pending_actions.enqueue_cancel_pending(
                    signal,
                    ticket,
                    label=f"CANCEL_PENDING #{ticket}",
                )
            signal.status = "closed"
            logger.log_action(signal, action)
            await _finalize_signal(
                signal,
                closed_by="CLOSE_PROFIT_OR_BE",
                notes="selected=CLOSE_ALL; reason=positive_live_basket",
            )
        else:
            await _apply_exact_break_even(
                signal,
                source="CLOSE_PROFIT_OR_BE_action",
            )

    elif action == "CLOSE_ALL":
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

        # Gold Signals sometimes says "close first/best entries" after a
        # layered basket has moved into profit. Our copied positions may all
        # be clustered at market instead. If our basket is not actually in
        # profit, we cannot identify those profitable layers faithfully.
        # Preserve the original trade and treat any explicit BE instruction
        # from the same message independently.
        entries_known = [p["entry"] for p in pos_info if p["entry"] is not None]
        entry_avg = (sum(entries_known) / len(entries_known)
                     if entries_known else None)
        if (signal.channel == "canal2"
                and entry_avg is not None and cur_price is not None):
            price_vs_entry = (cur_price - entry_avg
                              if signal.direction == "BUY"
                              else entry_avg - cur_price)
            decision = _close_first_decision(
                price_vs_entry, config.STRATEGY_C2_CLOSE_FIRST_PROFIT_PTS)
            print(f"[Acción] CLOSE_FIRST canal2: precio vs entry "
                  f"{price_vs_entry:+.2f} pts → decisión={decision}")
            if decision == "defer_layer_mismatch":
                journal.event(
                    _sig_id(signal),
                    "close_first_layer_mismatch_deferred",
                    provider_instruction="close_first_entries",
                    outcome="original_trade_preserved",
                    n_positions=len(pos_info),
                    tickets=[p["ticket"] for p in pos_info],
                    entry_avg=round(entry_avg, 3),
                    current_price=round(cur_price, 3),
                    price_vs_entry=round(price_vs_entry, 3),
                    raw_text=raw_text[:240],
                )
                journal.anomaly(
                    _sig_id(signal),
                    "channel_msg",
                    "warning",
                    "Gold Signals indicó cerrar primeras entradas, pero la "
                    "cesta copiada no estaba en beneficio; se conserva la "
                    "operación original",
                    price_vs_entry=round(price_vs_entry, 3),
                    n_positions=len(pos_info),
                )
                _schedule_detached(notify(
                    f"⚠️ {provider_display_name(signal.channel)}\n"
                    f"CAPAS NO EQUIVALENTES\n\n"
                    f"El proveedor pidió cerrar sus primeras entradas, pero "
                    f"nuestra cesta está en {price_vs_entry:+.2f} puntos "
                    f"respecto a la entrada.\n\n"
                    f"No se cerraron posiciones ni se cambiaron los TP. "
                    f"Una orden explícita de BE se procesa por separado."
                ))
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
        await _apply_exact_break_even(
            signal,
            source="MOVE_SL_TO_BE_action",
        )
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

def _canal2_context_candidate(text: str) -> bool:
    """Cheap guard before classifying a non-entry channel2 message."""
    return bool(re.search(
        r"\b(?:MOVE|CLOSE|TAKE|BOOK|SECURE|PROTECT|CUT|DELETE|"
        r"PUT|ADDED|OPENED|TOOK|OUT|"
        r"SL|STOP\s*LOSS|BE|BREAKEVEN|BREAK\s+EVEN|RISK\s*FREE|"
        r"TP\s*\d*|TARGET\s*\d*|PROFIT|PIPS?|LAYERS?)\b",
        text or "",
        re.IGNORECASE,
    ))


def _canal2_action_names(classification: list[dict]) -> list[str]:
    return [
        str(action.get("action"))
        for action in classification
        if action.get("action")
    ]


_TARGET_REQUIRING_ACTIONS = (
    EXECUTABLE_ACTIONS
    | NOTIFY_REVIEW_ACTIONS
    | LEVEL_ONLY_ACTIONS
    | {"CLOSE_PARTIAL"}
)


def _target_requiring_actions(classification: list[dict]) -> list[dict]:
    """Return intents that need a concrete live signal or human review."""
    return [
        item for item in classification
        if str(item.get("action") or "").upper()
        in _TARGET_REQUIRING_ACTIONS
    ]


_TP_ANNOUNCEMENT_INDEX_RE = re.compile(r"\bTP\s*([1-9])\b", re.IGNORECASE)


def _recent_tp_announcement_target(
    open_signals: list[Signal],
    text: str,
    *,
    observed_at: datetime | None = None,
    max_age_s: float = 180.0,
) -> Signal | None:
    """Resolve a standalone TP announcement from recent observed price hits.

    Attribution is deliberately strict: exactly one open signal must have
    touched the announced TP within the recent window. Ambiguous cases remain
    unassigned and never cause a trading action.
    """
    match = _TP_ANNOUNCEMENT_INDEX_RE.search(text or "")
    if not match:
        return None
    tp_index = int(match.group(1)) - 1
    at = observed_at or datetime.utcnow()
    if at.tzinfo is not None:
        at = at.astimezone(timezone.utc).replace(tzinfo=None)

    candidates = []
    for signal in open_signals:
        hit_at = (getattr(signal, "observed_tp_hits", {}) or {}).get(tp_index)
        if hit_at is None:
            continue
        if hit_at.tzinfo is not None:
            hit_at = hit_at.astimezone(timezone.utc).replace(tzinfo=None)
        age_s = (at - hit_at).total_seconds()
        if -5.0 <= age_s <= max_age_s:
            candidates.append(signal)
    return candidates[0] if len(candidates) == 1 else None


def _canal2_actionable(classification: list[dict]) -> list[dict]:
    return _target_requiring_actions(classification)


def _zone_plan_level_text(plan: dict) -> str:
    zones = plan.get("zones") or []
    if not zones:
        return "sin nivel numerico"
    rendered = []
    for low, high in zones:
        if float(low) == float(high):
            rendered.append(f"{float(low):.2f}")
        else:
            rendered.append(f"{float(low):.2f}-{float(high):.2f}")
    return ", ".join(rendered)


def _format_canal2_zone_plan_notice(plan: dict) -> str:
    provider = provider_display_name("canal2")
    direction = plan.get("direction") or "?"
    zone = _zone_plan_level_text(plan)
    tps = ", ".join(f"{float(value):.2f}" for value in plan.get("tps") or [])
    sl = plan.get("sl")
    levels = (
        f"{direction} | {zone}\n"
        f"TP: {tps or 'pendiente'}\n"
        f"SL: {float(sl):.2f}" if sl is not None else
        f"{direction} | {zone}\nTP: {tps or 'pendiente'}\nSL: pendiente"
    )
    if (
        zone_plan_is_executable(plan)
        and plan.get("execution_eligible", True)
    ):
        if not config.STRATEGY_C2_ZONE_FIRST_TOUCH_EXECUTION_ENABLED:
            return (
                f"{provider}\n"
                f"ZONA EN OBSERVACION\n\n"
                f"{levels}\n\n"
                f"Estado: el primer toque quedara registrado. "
                f"El bot abrira solo cuando el trader indique activacion."
            )
        return (
            f"{provider}\n"
            f"ZONA ARMADA\n\n"
            f"{levels}\n\n"
            f"Estado: esperando el primer toque del precio. "
            f"El bot abrira la operacion automaticamente."
        )

    missing = []
    if direction not in {"BUY", "SELL"}:
        missing.append("direccion")
    zones = plan.get("zones") or []
    if len(zones) != 1:
        missing.append("una zona unica")
    if not plan.get("tps"):
        missing.append("TP")
    if sl is None:
        missing.append("SL")
    return (
        f"{provider}\n"
        f"ZONA REGISTRADA\n\n"
        f"{levels}\n\n"
        f"Estado: faltan {', '.join(missing) or 'datos validos'}. "
        f"El bot no abrira hasta tener un plan completo y sin ambiguedad."
    )


def _format_canal2_zone_activation_notice(
    plan: dict,
    signal: Signal,
    trigger: dict,
) -> str:
    """Human-facing confirmation sent only after MT5 accepted the entry."""
    provider = provider_display_name("canal2")
    direction = str(signal.direction or plan.get("direction") or "?").upper()
    entry = signal.market_fill_price
    if entry is None:
        entry = trigger.get("price")
    entry_text = f"{float(entry):.2f}" if entry is not None else "no disponible"
    position_count = len(signal.all_filled_tickets)
    position_label = "posicion" if position_count == 1 else "posiciones"
    zone = _zone_plan_level_text(plan)
    tps = signal.tps or list(plan.get("tps") or [])
    tp_text = ", ".join(f"{float(value):.2f}" for value in tps)
    sl = signal.sl if signal.sl is not None else plan.get("sl")
    sl_text = f"{float(sl):.2f}" if sl is not None else "pendiente"
    trigger_text = {
        "first_touch": "primer toque de la zona",
        "explicit_active": "activacion indicada por el trader",
        "explicit_reentry": "reentrada indicada por el trader",
    }.get(str(trigger.get("trigger") or ""), "activacion confirmada")

    return (
        f"✅ {provider}\n"
        f"ZONA ACTIVADA · {direction}\n\n"
        f"Entrada real: {entry_text}\n"
        f"Zona: {zone}\n"
        f"Abiertas: {position_count} {position_label}\n"
        f"SL: {sl_text}\n"
        f"TP: {tp_text or 'pendiente'}\n\n"
        f"Origen: {trigger_text}."
    )


def _drop_canal2_zone_plan_aliases(message_id: int) -> None:
    """Remove every cache key owned by a message that became an entry."""
    message_id = int(message_id)
    records = {
        id(record)
        for key, record in _canal2_zone_plans.items()
        if int(key) == message_id
        or int(record.get("message_id", -1)) == message_id
    }
    for key, record in list(_canal2_zone_plans.items()):
        if int(key) == message_id or id(record) in records:
            del _canal2_zone_plans[key]


def _zone_plan_event_payload(plan: dict) -> dict:
    return {
        "lifecycle_schema_version": plan.get(
            "lifecycle_schema_version", LIFECYCLE_SCHEMA_VERSION
        ),
        "message_id": plan.get("message_id"),
        "thread_root_message_id": plan.get("thread_root_message_id"),
        "aliases": list(plan.get("aliases") or []),
        "direction": plan.get("direction"),
        "zones": plan.get("zones") or [],
        "target": plan.get("target"),
        "tps": plan.get("tps") or [],
        "sl": plan.get("sl"),
        "has_open_runner": bool(plan.get("has_open_runner")),
        "source_kind": plan.get("source_kind"),
        "tg_ts": plan.get("tg_ts"),
        "raw_text": str(plan.get("raw_text") or "")[:500],
        "status": plan.get("status"),
        "registered_utc": plan.get("registered_utc"),
        "updated_utc": plan.get("updated_utc"),
        "expires_utc": plan.get("expires_utc"),
        "activation_requested": bool(plan.get("activation_requested")),
        "execution_eligible": plan.get("execution_eligible", True),
        "no_reentry": bool(plan.get("no_reentry")),
        "consumed": bool(plan.get("consumed")),
        "entry_generation": int(plan.get("entry_generation") or 0),
        "entry_generation_id": plan.get("entry_generation_id"),
        "trigger_claim": plan.get("trigger_claim"),
        "confirmed_generation_ids": list(
            plan.get("confirmed_generation_ids") or []
        ),
        "alias_generation_ids": dict(
            plan.get("alias_generation_ids") or {}
        ),
        "last_trigger": dict(plan.get("last_trigger") or {}),
        "first_touch_observed": bool(plan.get("first_touch_observed")),
        "first_touch_evidence": dict(
            plan.get("first_touch_evidence") or {}
        ),
    }


def _register_canal2_zone_plan_alias(
    plan: dict,
    alias_message_id: int,
    *,
    source_message_id: int | None = None,
    emit_event: bool = True,
) -> bool:
    alias_id = int(alias_message_id)
    aliases = plan.setdefault("aliases", [])
    already_registered = (
        alias_id in aliases
        and _canal2_zone_plans.get(alias_id) is plan
    )
    if alias_id not in aliases:
        aliases.append(alias_id)
    _canal2_zone_plans[alias_id] = plan
    alias_generations = plan.setdefault("alias_generation_ids", {})
    source_generation = alias_generations.get(str(source_message_id))
    if source_generation is not None and str(alias_id) not in alias_generations:
        alias_generations[str(alias_id)] = int(source_generation)
        owner = state.get("canal2", int(source_generation))
        if owner is not None:
            state.alias(owner, alias_id)
    if already_registered or not emit_event:
        return False
    journal.event(
        f"canal2_{alias_id}",
        "canal2_zone_plan_alias_registered",
        lifecycle_schema_version=LIFECYCLE_SCHEMA_VERSION,
        zone_plan_message_id=plan.get("message_id"),
        thread_root_message_id=plan.get("thread_root_message_id"),
        alias_message_id=alias_id,
        source_message_id=source_message_id,
    )
    return True


def _merge_canal2_zone_execution_levels(
    plan: dict,
    parsed: dict,
    *,
    raw_text: str,
    tg_ts: str | None,
    message_id: int,
) -> list[str]:
    """Keep future re-entry levels aligned with live Signal management."""
    update = {}
    if parsed.get("range"):
        update["zones"] = [list(parsed["range"])]
    if parsed.get("tps"):
        update["tps"] = list(parsed["tps"])
    if parsed.get("sl") is not None:
        update["sl"] = parsed["sl"]
    if not update:
        return []

    merged, changes = merge_plan_record(
        plan,
        update,
        raw_text=raw_text,
        tg_ts=tg_ts,
    )
    plan.clear()
    plan.update(merged)
    if changes:
        journal.event(
            f"canal2_{int(message_id)}",
            "canal2_zone_plan_updated",
            **_zone_plan_event_payload(plan),
            changed_fields=changes,
            update_message_id=int(message_id),
            update_source="live_signal_management",
        )
    return changes


def restore_canal2_zone_plans_from_journal(path) -> int:
    """Replay only schema-v2 zone lifecycles that remain actionable."""
    source = Path(path)
    _canal2_zone_plans.clear()
    if not source.exists():
        return 0

    records: dict[int, dict] = {}
    aliases: dict[int, int] = {}
    zone_signal_contexts: dict[str, dict] = {}

    with source.open("rb") as handle:
        for raw_line in handle:
            try:
                row = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict):
                continue

            event = row.get("ev")
            sig_id = str(row.get("sig") or "")
            runtime_fill_event = event in {"signal_received", "market_filled"}
            if (
                not runtime_fill_event
                and row.get("lifecycle_schema_version")
                != LIFECYCLE_SCHEMA_VERSION
            ):
                continue

            if event == "canal2_zone_plan_created":
                try:
                    message_id = int(
                        row.get("message_id")
                        or sig_id.removeprefix("canal2_")
                    )
                except (TypeError, ValueError):
                    continue
                try:
                    root_id = int(
                        row.get("thread_root_message_id") or message_id
                    )
                except (TypeError, ValueError):
                    root_id = message_id
                record = {
                    "message_id": message_id,
                    "thread_root_message_id": root_id,
                    "aliases": [message_id],
                    "direction": row.get("direction"),
                    "zones": row.get("zones") or [],
                    "target": row.get("target"),
                    "tps": row.get("tps") or [],
                    "sl": row.get("sl"),
                    "has_open_runner": bool(row.get("has_open_runner")),
                    "source_kind": row.get("source_kind") or "journal_restore",
                    "tg_ts": row.get("tg_ts"),
                    "raw_text": row.get("raw_text") or "",
                    "lifecycle_schema_version": LIFECYCLE_SCHEMA_VERSION,
                    "status": row.get("status") or "draft",
                    "registered_utc": row.get("registered_utc"),
                    "updated_utc": row.get("updated_utc"),
                    "expires_utc": row.get("expires_utc"),
                    "activation_requested": bool(
                        row.get("activation_requested")
                    ),
                    "execution_eligible": row.get(
                        "execution_eligible", True
                    ),
                    "no_reentry": bool(row.get("no_reentry")),
                    "consumed": bool(row.get("consumed")),
                    "entry_generation": int(
                        row.get("entry_generation") or 0
                    ),
                    "entry_generation_id": row.get("entry_generation_id"),
                    "trigger_claim": row.get("trigger_claim"),
                    "confirmed_generation_ids": list(
                        row.get("confirmed_generation_ids") or []
                    ),
                    "alias_generation_ids": dict(
                        row.get("alias_generation_ids") or {}
                    ),
                    "last_trigger": dict(row.get("last_trigger") or {}),
                    "first_touch_observed": bool(
                        row.get("first_touch_observed")
                    ),
                    "first_touch_evidence": dict(
                        row.get("first_touch_evidence") or {}
                    ),
                }
                records[message_id] = record
                aliases[message_id] = message_id
                aliases[root_id] = message_id
                continue

            if event == "canal2_zone_plan_alias_registered":
                try:
                    owner_id = int(row.get("zone_plan_message_id"))
                    alias_id = int(row.get("alias_message_id"))
                except (TypeError, ValueError):
                    continue
                aliases[alias_id] = owner_id
                continue

            if event == "signal_received":
                source_kind = str(row.get("entry_source_kind") or "")
                if not source_kind.startswith("zone_"):
                    continue
                zone_signal_contexts[sig_id] = {
                    "entry_source_kind": source_kind,
                    "zone_plan_message_id": row.get(
                        "zone_plan_message_id"
                    ),
                    "zone_entry_generation": row.get(
                        "zone_entry_generation"
                    ),
                    "trigger": {
                        "trigger": row.get("zone_trigger_kind"),
                        "side": row.get("zone_trigger_side"),
                        "price": row.get("zone_trigger_price"),
                        "time": row.get("zone_trigger_time"),
                        "time_msc": row.get("zone_trigger_time_msc"),
                        "zone": row.get("zone_trigger_range"),
                        "observed_utc": row.get(
                            "zone_trigger_observed_utc"
                        ),
                        "normalized_time_utc": row.get(
                            "zone_trigger_normalized_utc"
                        ),
                        "broker_utc_offset_s": row.get(
                            "zone_trigger_broker_utc_offset_s"
                        ),
                        "clock_basis": row.get(
                            "zone_trigger_clock_basis"
                        ),
                    },
                }
                continue

            if event == "market_filled":
                context = dict(zone_signal_contexts.get(sig_id) or {})
                source_kind = str(
                    row.get("entry_source_kind")
                    or context.get("entry_source_kind")
                    or ""
                )
                if not source_kind.startswith("zone_"):
                    continue
                try:
                    generation_id = int(
                        row.get("generation_message_id")
                        or sig_id.removeprefix("canal2_")
                    )
                    owner_id = int(
                        row.get("zone_plan_message_id")
                        or context.get("zone_plan_message_id")
                        or generation_id
                    )
                except (TypeError, ValueError):
                    continue
                owner_id = aliases.get(owner_id, owner_id)
                record = records.get(owner_id)
                if record is None:
                    continue

                generation = int(
                    row.get("zone_entry_generation")
                    or context.get("zone_entry_generation")
                    or record.get("entry_generation")
                    or 1
                )
                confirmed_ids = record.setdefault(
                    "confirmed_generation_ids", []
                )
                if generation_id not in confirmed_ids:
                    confirmed_ids.append(generation_id)
                record["entry_generation"] = max(
                    int(record.get("entry_generation") or 0), generation
                )
                record["entry_generation_id"] = generation_id
                record["trigger_claim"] = None
                record["activation_requested"] = False
                record["consumed"] = True
                record["status"] = "triggered"
                trigger = dict(context.get("trigger") or {})
                if trigger:
                    record["last_trigger"] = trigger
                aliases_to_bind = (
                    [generation_id]
                    if source_kind == "zone_reentry"
                    else [int(value) for value in record.get("aliases") or []]
                )
                alias_generations = record.setdefault(
                    "alias_generation_ids", {}
                )
                for alias_id in aliases_to_bind:
                    alias_generations[str(alias_id)] = generation_id
                continue

            try:
                owner_id = int(
                    row.get("zone_plan_message_id")
                    or sig_id.removeprefix("canal2_")
                )
            except (TypeError, ValueError):
                continue
            owner_id = aliases.get(owner_id, owner_id)
            record = records.get(owner_id)
            if record is None:
                continue
            if event == "canal2_zone_plan_updated":
                for key in _zone_plan_event_payload(record):
                    if key in row:
                        record[key] = row[key]
            elif event == "canal2_zone_first_touch_observed":
                record["first_touch_observed"] = True
                record["first_touch_evidence"] = dict(
                    row.get("first_touch_evidence")
                    or row.get("trigger")
                    or {}
                )
            elif event == "canal2_zone_plan_transition":
                for key in (
                    "status",
                    "activation_requested",
                    "execution_eligible",
                    "no_reentry",
                    "consumed",
                    "entry_generation",
                    "entry_generation_id",
                    "trigger_claim",
                    "confirmed_generation_ids",
                    "alias_generation_ids",
                    "last_trigger",
                    "expires_utc",
                    "updated_utc",
                ):
                    if key in row:
                        record[key] = row[key]
            elif event == "canal2_zone_entry_confirmed":
                record["consumed"] = True
                record["status"] = "triggered"
                for key in (
                    "entry_generation",
                    "entry_generation_id",
                    "trigger_claim",
                    "confirmed_generation_ids",
                    "alias_generation_ids",
                    "last_trigger",
                ):
                    if key in row:
                        record[key] = row[key]

    terminal_statuses = {"invalidated", "expired"}
    retained_ids = [
        message_id for message_id, record in records.items()
        if record.get("status") not in terminal_statuses
        and not zone_plan_is_expired(record)
    ][-_CANAL2_ZONE_PLAN_MAX:]
    retained_set = set(retained_ids)
    for alias_id, owner_id in aliases.items():
        if owner_id in retained_set:
            record = records[owner_id]
            if alias_id not in record["aliases"]:
                record["aliases"].append(alias_id)
            _canal2_zone_plans[alias_id] = record
            generation_id = (
                record.get("alias_generation_ids") or {}
            ).get(str(alias_id))
            if generation_id is not None:
                signal = state.get("canal2", int(generation_id))
                if signal is not None:
                    state.alias(signal, alias_id)
    return len(retained_ids)


def _unique_canal2_zone_plans() -> list[dict]:
    """Return each aliased zone lifecycle exactly once."""
    seen: set[int] = set()
    plans: list[dict] = []
    for plan in _canal2_zone_plans.values():
        identity = id(plan)
        if identity in seen:
            continue
        seen.add(identity)
        plans.append(plan)
    return plans


def _zone_trigger_evidence(plan: dict, tick: dict, kind: str) -> dict:
    """Freeze the broker-side price and clock that authorized an entry."""
    direction = str(plan.get("direction") or "").upper()
    side = "ask" if direction == "BUY" else "bid"
    price = tick.get(side)
    zones = plan.get("zones") or []
    zone = sorted(float(value) for value in zones[0]) if zones else []
    outside_zone = None
    deviation = None
    if price is not None and len(zone) == 2:
        price = float(price)
        low, high = zone
        outside_zone = not low <= price <= high
        if price < low:
            deviation = low - price
        elif price > high:
            deviation = price - high
        else:
            deviation = 0.0
    return {
        "trigger": kind,
        "side": side,
        "price": price,
        "time": tick.get("time"),
        "time_msc": tick.get("time_msc"),
        "observed_utc": datetime.now(timezone.utc).isoformat(
            timespec="milliseconds"
        ),
        "zone": zone,
        "outside_zone": outside_zone,
        "deviation_from_zone": deviation,
    }


_ZONE_CLOCK_ROUND_S = 15 * 60
_ZONE_CLOCK_MAX_OFFSET_S = 14 * 60 * 60
_ZONE_CLOCK_RESIDUAL_TOLERANCE_S = 30.0


def _zone_entry_timestamp(trigger: dict) -> datetime:
    """Normalize a broker-local tick epoch onto the observed UTC clock.

    Some MT5 brokers expose ``tick.time`` encoded with server-local wall time
    even though the Python API presents it as an epoch.  Comparing that raw
    value with the instant at which we observed the tick lets us identify a
    whole/quarter-hour server offset without guessing the broker timezone.
    The raw and normalized clocks stay in ``trigger`` for deterministic replay.
    """
    observed = datetime.now(timezone.utc)
    observed_raw = trigger.get("observed_utc")
    if observed_raw:
        try:
            observed = datetime.fromisoformat(str(observed_raw))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            else:
                observed = observed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            observed = datetime.now(timezone.utc)
    trigger["observed_utc"] = observed.isoformat(timespec="milliseconds")

    raw_epoch = None
    if trigger.get("time_msc") is not None:
        try:
            raw_epoch = float(trigger["time_msc"]) / 1000.0
        except (TypeError, ValueError):
            raw_epoch = None
    if raw_epoch is None and trigger.get("time") is not None:
        try:
            raw_epoch = float(trigger["time"])
        except (TypeError, ValueError):
            raw_epoch = None

    raw_dt = None
    if raw_epoch is not None:
        try:
            raw_dt = datetime.fromtimestamp(raw_epoch, tz=timezone.utc)
        except (OSError, OverflowError, TypeError, ValueError):
            raw_dt = None

    normalized = observed
    offset_s = None
    residual_s = None
    basis = "observed_utc_fallback"
    if raw_dt is not None:
        delta_s = (raw_dt - observed).total_seconds()
        candidate_offset_s = int(
            round(delta_s / _ZONE_CLOCK_ROUND_S) * _ZONE_CLOCK_ROUND_S
        )
        residual_s = delta_s - candidate_offset_s
        if (
            abs(candidate_offset_s) <= _ZONE_CLOCK_MAX_OFFSET_S
            and abs(residual_s) <= _ZONE_CLOCK_RESIDUAL_TOLERANCE_S
        ):
            offset_s = candidate_offset_s
            normalized = raw_dt - timedelta(seconds=offset_s)
            basis = "broker_time_normalized"

    trigger["raw_broker_time_utc"] = (
        raw_dt.isoformat(timespec="milliseconds") if raw_dt else None
    )
    trigger["broker_utc_offset_s"] = offset_s
    trigger["clock_residual_ms"] = (
        int(round(residual_s * 1000)) if residual_s is not None else None
    )
    trigger["clock_basis"] = basis
    trigger["normalized_time_utc"] = normalized.isoformat(
        timespec="milliseconds"
    )
    return normalized


async def _trigger_canal2_zone_entry(
    plan: dict,
    trigger: dict,
    *,
    generation_message_id: int | None = None,
    telegram_timestamp: datetime | None = None,
) -> Signal | None:
    """Turn one claimed zone generation into normal Canal 2 exposure."""
    kind = str(trigger.get("trigger") or "first_touch")
    is_reentry = kind == "explicit_reentry"
    plan_message_id = int(plan.get("message_id"))
    generation_id = int(
        generation_message_id if is_reentry else plan_message_id
    )
    sig_id = f"canal2_{generation_id}"

    if plan.get("execution_eligible") is False:
        return None
    if not zone_plan_is_executable(plan):
        return None
    if plan.get("status") in {"invalidated", "expired"}:
        return None
    if not is_reentry and plan.get("consumed"):
        return None
    if is_reentry and plan.get("no_reentry"):
        journal.event(
            sig_id,
            "canal2_zone_reentry_blocked",
            zone_plan_message_id=plan_message_id,
            generation_message_id=generation_id,
            reason="provider_no_reentry",
        )
        return None

    confirmed_ids = plan.setdefault("confirmed_generation_ids", [])
    if generation_id in confirmed_ids:
        return state.get("canal2", generation_id)
    if plan.get("trigger_claim") is not None:
        return None

    if trigger.get("price") is None:
        tick = await _run(executor.current_tick_safe)
        if not tick:
            journal.event(
                sig_id,
                "canal2_zone_entry_failed",
                zone_plan_message_id=plan_message_id,
                generation_message_id=generation_id,
                trigger_kind=kind,
                reason="broker_tick_unavailable",
            )
            return None
        trigger = _zone_trigger_evidence(plan, tick, kind)

    claim = f"{kind}:{generation_id}:{trigger.get('time_msc')}"
    plan["trigger_claim"] = claim
    journal.event(
        sig_id,
        "canal2_zone_entry_attempted",
        **_zone_plan_event_payload(plan),
        zone_plan_message_id=plan_message_id,
        generation_message_id=generation_id,
        trigger=trigger,
    )

    raw_text = str(plan.get("raw_text") or "")
    lot_multiplier, lot_reason = strategies.lot_multiplier_for_signal(
        raw_text
    )
    if lot_multiplier <= 0:
        plan["trigger_claim"] = None
        journal.event(
            sig_id,
            "canal2_zone_entry_failed",
            zone_plan_message_id=plan_message_id,
            generation_message_id=generation_id,
            trigger_kind=kind,
            reason="non_positive_lot_multiplier",
        )
        return None

    parsed = {
        "direction": str(plan["direction"]).upper(),
        "range": tuple(float(value) for value in plan["zones"][0]),
        "tps": [float(value) for value in plan.get("tps") or []],
        "sl": float(plan["sl"]),
    }
    source_kind = {
        "first_touch": "zone_first_touch",
        "explicit_active": "zone_explicit_active",
        "explicit_reentry": "zone_reentry",
    }.get(kind, "zone_first_touch")
    intent = _Canal2EntryIntent(
        message_id=generation_id,
        direction=parsed["direction"],
        parsed=parsed,
        raw_text=raw_text,
        entry_timestamp=_zone_entry_timestamp(trigger),
        telegram_timestamp=telegram_timestamp,
        reply_to_message_id=(
            plan_message_id if is_reentry else None
        ),
        source_kind=source_kind,
        trigger=dict(trigger),
        lot_multiplier=lot_multiplier,
        lot_reason=lot_reason,
        max_tp_index=strategies.max_tp_index_for_signal("canal2"),
        is_high_risk=strategies.is_high_risk_signal(raw_text),
        zone_plan_message_id=plan_message_id,
        zone_thread_root_message_id=int(
            plan.get("thread_root_message_id") or plan_message_id
        ),
        zone_entry_generation=int(plan.get("entry_generation") or 0) + 1,
    )

    try:
        signal = await _open_canal2_intent(
            intent,
            label="Canal2_zone",
        )
    except Exception as exc:
        plan["trigger_claim"] = None
        journal.event(
            sig_id,
            "canal2_zone_entry_failed",
            zone_plan_message_id=plan_message_id,
            generation_message_id=generation_id,
            trigger_kind=kind,
            reason="opening_exception",
            error_type=type(exc).__name__,
            error=str(exc)[:240],
        )
        journal.anomaly(
            sig_id,
            "fill",
            "critical",
            "fallo abriendo una zona activa de Gold Signals",
            zone_plan_message_id=plan_message_id,
            generation_message_id=generation_id,
            error_type=type(exc).__name__,
            error=str(exc)[:240],
        )
        return None

    if signal is None:
        signal = state.get("canal2", generation_id)
    if signal is None:
        plan["trigger_claim"] = None
        journal.event(
            sig_id,
            "canal2_zone_entry_failed",
            zone_plan_message_id=plan_message_id,
            generation_message_id=generation_id,
            trigger_kind=kind,
            reason="market_fill_not_confirmed",
        )
        return None

    if state.get("canal2", generation_id) is None:
        state.add(signal)
    if generation_id not in confirmed_ids:
        confirmed_ids.append(generation_id)
    plan["entry_generation"] = int(plan.get("entry_generation") or 0) + 1
    plan["entry_generation_id"] = generation_id
    plan["last_trigger"] = dict(trigger)
    plan["trigger_claim"] = None
    plan["activation_requested"] = False
    plan["consumed"] = True
    plan["status"] = "triggered"

    aliases_to_bind = (
        [generation_id]
        if is_reentry
        else [int(value) for value in plan.get("aliases") or []]
    )
    alias_generations = plan.setdefault("alias_generation_ids", {})
    for alias_id in aliases_to_bind:
        alias_generations[str(alias_id)] = generation_id
        state.alias(signal, alias_id)

    journal.event(
        sig_id,
        "canal2_zone_entry_confirmed",
        **_zone_plan_event_payload(plan),
        zone_plan_message_id=plan_message_id,
        generation_message_id=generation_id,
        trigger=trigger,
        opened_signal_id=_sig_id(signal),
        tickets=list(signal.all_filled_tickets),
    )
    _schedule_detached(notify(
        _format_canal2_zone_activation_notice(plan, signal, trigger)
    ))
    return signal


async def _process_canal2_zone_tick(tick: dict) -> int:
    """Evaluate one fresh broker tick against every unique active plan."""
    opened = 0
    for plan in _unique_canal2_zone_plans():
        if plan.get("consumed") and not plan.get("activation_requested"):
            continue
        if zone_plan_is_expired(plan):
            if plan.get("status") != "expired":
                previous = plan.get("status")
                plan["status"] = "expired"
                plan["activation_requested"] = False
                journal.event(
                    f"canal2_{plan.get('message_id')}",
                    "canal2_zone_plan_transition",
                    **_zone_plan_event_payload(plan),
                    zone_plan_message_id=plan.get("message_id"),
                    from_status=previous,
                    lifecycle_actions=["EXPIRE"],
                )
            continue

        if plan.get("activation_requested"):
            if not zone_plan_is_executable(plan):
                continue
            trigger = _zone_trigger_evidence(plan, tick, "explicit_active")
        else:
            trigger = zone_touch_decision(plan, tick)
            if trigger is not None and not plan.get("first_touch_observed"):
                trigger = dict(trigger)
                _zone_entry_timestamp(trigger)
                plan["first_touch_observed"] = True
                plan["first_touch_evidence"] = dict(trigger)
                journal.event(
                    f"canal2_{plan.get('message_id')}",
                    "canal2_zone_first_touch_observed",
                    **_zone_plan_event_payload(plan),
                    zone_plan_message_id=plan.get("message_id"),
                    execution_enabled=bool(
                        config.STRATEGY_C2_ZONE_FIRST_TOUCH_EXECUTION_ENABLED
                    ),
                )
            if (
                trigger is not None
                and not config.STRATEGY_C2_ZONE_FIRST_TOUCH_EXECUTION_ENABLED
            ):
                continue
        if trigger is None:
            continue
        if await _trigger_canal2_zone_entry(plan, trigger) is not None:
            opened += 1
    return opened


async def canal2_zone_touch_loop(interval_s: float = 0.1) -> None:
    """Observe fresh MT5 ticks without blocking Telegram delivery."""
    last_tick_identity = None
    journal.event(
        "bot",
        "canal2_zone_touch_loop_started",
        interval_s=float(interval_s),
        lifecycle_schema_version=LIFECYCLE_SCHEMA_VERSION,
    )
    while True:
        try:
            actionable = any(
                plan.get("execution_eligible", True)
                and not plan.get("consumed")
                and plan.get("status") not in {"invalidated", "expired"}
                and (
                    config.STRATEGY_C2_ZONE_FIRST_TOUCH_EXECUTION_ENABLED
                    or plan.get("activation_requested")
                    or not plan.get("first_touch_observed")
                )
                for plan in _unique_canal2_zone_plans()
            )
            if not actionable:
                await asyncio.sleep(max(float(interval_s), 0.25))
                continue
            tick = await _run(executor.current_tick_safe)
            if tick:
                identity = tick.get("time_msc") or (
                    tick.get("time"), tick.get("bid"), tick.get("ask")
                )
                if identity != last_tick_identity:
                    last_tick_identity = identity
                    await _process_canal2_zone_tick(tick)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            journal.event(
                "bot",
                "canal2_zone_touch_loop_error",
                error_type=type(exc).__name__,
                error=str(exc)[:240],
            )
            print(
                "[Canal2 zone] Error no fatal observando ticks: "
                f"{type(exc).__name__}: {exc}"
            )
        await asyncio.sleep(float(interval_s))


async def _handle_canal2_zone_plan(msg, text: str, plan: dict,
                                   source_kind: str = "new",
                                   thread_root_message_id: int | None = None
                                   ) -> None:
    """Create or merge one versioned provider-zone lifecycle."""
    root_message_id = (
        int(thread_root_message_id)
        if thread_root_message_id is not None
        else int(msg.id)
    )
    existing = (
        _canal2_zone_plans.get(int(msg.id))
        or _canal2_zone_plans.get(root_message_id)
    )
    if (
        source_kind != "reply_recovery"
        and zone_plan_is_executable(plan)
    ):
        tick = await _run(executor.current_tick_safe)
        reference_price = _entry_reference_from_tick(
            plan.get("direction"),
            tick,
        )
        aligned = align_provider_plan_to_market_context(
            plan.get("direction"),
            plan,
            reference_price=reference_price,
        )
        _log_entry_level_interpretation(
            f"canal2_{msg.id}",
            "canal2",
            plan,
            aligned,
            reference_price,
        )
        plan = aligned["parsed"]
    is_current_lifecycle = (
        existing is not None
        and existing.get("lifecycle_schema_version")
        == LIFECYCLE_SCHEMA_VERSION
    )
    if is_current_lifecycle:
        merged, changes = merge_plan_record(
            existing,
            plan,
            raw_text=text,
            tg_ts=_msg_ts_iso(msg),
        )
        existing.clear()
        existing.update(merged)
        record = existing
        created = False
    else:
        record = new_plan_record(
            plan,
            message_id=int(msg.id),
            root_message_id=root_message_id,
            raw_text=text,
            tg_ts=_msg_ts_iso(msg),
            source_kind=source_kind,
        )
        record["execution_eligible"] = source_kind != "reply_recovery"
        if source_kind == "reply_recovery":
            record["status"] = "draft"
        changes = []
        created = True

    _register_canal2_zone_plan_alias(
        record,
        int(msg.id),
        source_message_id=int(msg.id),
        emit_event=not created,
    )
    _register_canal2_zone_plan_alias(
        record,
        root_message_id,
        source_message_id=int(msg.id),
        emit_event=root_message_id != int(msg.id),
    )
    while len(_canal2_zone_plans) > _CANAL2_ZONE_PLAN_MAX:
        oldest = next(iter(_canal2_zone_plans))
        del _canal2_zone_plans[oldest]

    sig_id = f"canal2_{msg.id}"
    event_name = (
        "canal2_zone_plan_created" if created
        else "canal2_zone_plan_updated"
    )
    journal.event(
        sig_id,
        event_name,
        **_zone_plan_event_payload(record),
        changed_fields=changes,
    )
    # Compatibility event for existing audits. Schema v2 restore ignores it.
    journal.event(
        sig_id,
        "canal2_zone_plan_registered",
        channel="canal2",
        direction=plan.get("direction"),
        zones=plan.get("zones") or [],
        target=plan.get("target"),
        source_kind=source_kind,
        thread_root_message_id=root_message_id,
        tg_ts=_msg_ts_iso(msg),
        lifecycle_schema_version=LIFECYCLE_SCHEMA_VERSION,
        execution_behavior=(
            "armed_waiting_trigger"
            if record.get("status") == "armed"
            and record.get("execution_eligible", True)
            else "observe_only"
        ),
        raw_text=text[:500],
    )
    if source_kind not in ("new", "reply"):
        return

    if (
        zone_plan_is_executable(record)
        and record.get("execution_eligible", True)
    ):
        journal.event(
            sig_id,
            "canal2_zone_plan_waiting_for_trigger",
            **_zone_plan_event_payload(record),
        )
    else:
        journal.event(
            sig_id,
            "canal2_zone_plan_waiting_for_levels",
            **_zone_plan_event_payload(record),
            reason="incomplete_or_ambiguous_levels",
        )
    _schedule_detached(notify(_format_canal2_zone_plan_notice(record)))


async def _recover_canal2_zone_plan_from_reply(msg, reply_id: int):
    """Recover a replied future-zone root after a process restart."""
    cached = _canal2_zone_plans.get(int(reply_id))
    if cached is not None:
        return cached

    get_reply = getattr(msg, "get_reply_message", None)
    if not callable(get_reply):
        return None

    try:
        root_msg = await get_reply()
    except Exception as exc:
        journal.event(
            f"canal2_{msg.id}",
            "canal2_zone_plan_recovery_failed",
            channel="canal2",
            reply_to_msg_id=int(reply_id),
            error_type=type(exc).__name__,
            error=str(exc)[:240],
        )
        return None

    root_id = getattr(root_msg, "id", None)
    if root_msg is None or root_id is None or int(root_id) != int(reply_id):
        return None

    root_text = _msg_text(root_msg)
    plan = parse_canal2_zone_plan(root_text)
    if plan is None:
        return None

    await _handle_canal2_zone_plan(
        root_msg,
        root_text,
        plan,
        source_kind="reply_recovery",
    )
    return _canal2_zone_plans.get(int(reply_id))


async def _handle_canal2_zone_plan_reply(msg, reply_id: int,
                                         plan: dict) -> None:
    """Merge one follow-up into its original versioned zone thread."""
    text = _msg_text(msg)
    lifecycle_actions = classify_zone_followup(text)
    previous_status = plan.get("status", "draft")
    parsed_update = parse_canal2_zone_plan(
        text,
        inherited_direction=plan.get("direction"),
    )
    changed_fields: list[str] = []
    if parsed_update is not None:
        merged, changed_fields = merge_plan_record(
            plan,
            parsed_update,
            raw_text=text,
            tg_ts=_msg_ts_iso(msg),
        )
        plan.clear()
        plan.update(merged)

    if "EXTEND_VALIDITY" in lifecycle_actions:
        merged, expiry_changes = merge_plan_record(
            plan,
            {},
            extend_validity_hours=12,
        )
        plan.clear()
        plan.update(merged)
        changed_fields.extend(expiry_changes)
    if "INVALIDATE" in lifecycle_actions:
        plan["status"] = "invalidated"
        plan["invalidated_by_message_id"] = int(msg.id)
        plan["invalidated_utc"] = _msg_ts_iso(msg)
    if "NO_REENTRY" in lifecycle_actions:
        plan["no_reentry"] = True
        plan["no_reentry_by_message_id"] = int(msg.id)
    if "MISSED" in lifecycle_actions and not plan.get("consumed"):
        plan["status"] = "missed"
    if "REARM" in lifecycle_actions and not plan.get("consumed"):
        plan["execution_eligible"] = True
        plan["status"] = (
            "rearmed" if zone_plan_is_executable(plan) else "draft"
        )
    if "APPROACHING" in lifecycle_actions and not plan.get("consumed"):
        plan["status"] = "approaching"
    if "ACTIVATE" in lifecycle_actions and not plan.get("consumed"):
        plan["activation_requested"] = True
        plan["execution_eligible"] = True
        plan["status"] = (
            "armed" if zone_plan_is_executable(plan)
            else "activation_pending"
        )
    if "REENTRY" in lifecycle_actions:
        if plan.get("status") not in {"invalidated", "expired"}:
            plan["execution_eligible"] = True
        plan["reentry_requested_by_message_id"] = int(msg.id)
        plan["reentry_requested_utc"] = _msg_ts_iso(msg)

    _register_canal2_zone_plan_alias(
        plan,
        int(msg.id),
        source_message_id=int(reply_id),
    )
    if changed_fields:
        journal.event(
            f"canal2_{msg.id}",
            "canal2_zone_plan_updated",
            **_zone_plan_event_payload(plan),
            changed_fields=list(dict.fromkeys(changed_fields)),
            update_message_id=int(msg.id),
        )
    if lifecycle_actions or plan.get("status") != previous_status:
        journal.event(
            f"canal2_{msg.id}",
            "canal2_zone_plan_transition",
            **_zone_plan_event_payload(plan),
            zone_plan_message_id=plan.get("message_id"),
            from_status=previous_status,
            lifecycle_actions=lifecycle_actions,
            transition_message_id=int(msg.id),
        )

    if "REENTRY" in lifecycle_actions:
        if plan.get("no_reentry"):
            journal.event(
                f"canal2_{msg.id}",
                "canal2_zone_reentry_blocked",
                zone_plan_message_id=plan.get("message_id"),
                generation_message_id=int(msg.id),
                reason="provider_no_reentry",
            )
        elif zone_plan_is_executable(plan):
            await _trigger_canal2_zone_entry(
                plan,
                {"trigger": "explicit_reentry"},
                generation_message_id=int(msg.id),
                telegram_timestamp=getattr(msg, "date", None),
            )
    elif (
        plan.get("activation_requested")
        and zone_plan_is_executable(plan)
        and plan.get("status") not in {"invalidated", "expired"}
    ):
        await _trigger_canal2_zone_entry(
            plan,
            {"trigger": "explicit_active"},
            telegram_timestamp=getattr(msg, "date", None),
        )

    zone_invalidated = "INVALIDATE" in lifecycle_actions
    if zone_invalidated:
        classification = [{
            "action": "ZONE_INVALIDATED",
            "price": None,
            "confidence": 1.0,
            "_reason": "provider_invalidated_future_zone",
        }]
    else:
        classification = (
            classify_local(text) if _canal2_context_candidate(text) else []
        )
    actions = _canal2_action_names(classification)
    actionable = (
        [] if zone_invalidated else _canal2_actionable(classification)
    )
    sig_id = f"canal2_{msg.id}"
    journal.event(
        sig_id,
        "canal2_zone_plan_management",
        channel="canal2",
        zone_plan_signal_id=f"canal2_{plan.get('message_id', reply_id)}",
        zone_plan_message_id=plan.get("message_id"),
        thread_root_message_id=plan.get("thread_root_message_id", reply_id),
        direction=plan.get("direction"),
        zones=plan.get("zones") or [],
        actions=actions,
        actionable=bool(actionable),
        zone_plan_status=plan.get("status", "draft"),
        lifecycle_actions=lifecycle_actions,
        execution_behavior="zone_lifecycle",
        text_preview=text[:240].replace("\n", " | "),
        tg_ts=_msg_ts_iso(msg),
    )
    if zone_invalidated:
        _schedule_detached(notify(
            f"ℹ️ {provider_display_name('canal2')}\n"
            f"ZONA INVALIDADA\n\n"
            f"Zona: {_zone_plan_level_text(plan)}\n"
            f"El proveedor indicó que la zona falló. No había una orden "
            f"automática asociada."
        ))
        return
    if not actionable:
        return

    journal.anomaly(
        sig_id,
        "channel_msg",
        "warning",
        "gestion recibida para una zona futura sin posiciones MT5 asociadas",
        zone_plan_signal_id=f"canal2_{plan.get('message_id', reply_id)}",
        actions=actions,
        text_preview=text[:240].replace("\n", " | "),
    )
    _schedule_detached(notify(
        f"⚠️ {provider_display_name('canal2')}\n"
        f"GESTIÓN DE ZONA FUTURA\n\n"
        f"Mensaje: {text[:220]}\n"
        f"Zona: {_zone_plan_level_text(plan)}\n\n"
        f"No había posiciones del bot asociadas a esa zona, así que no se "
        f"ejecutó ninguna orden. El mensaje quedó registrado."
    ))


async def _handle_canal2_standalone(msg, text: str, sig_id: str) -> None:
    """Route non-reply management without guessing between open baskets."""
    if not _canal2_context_candidate(text):
        return

    open_c2 = state.open_signals("canal2")
    classification = classify_local(text)
    if not classification and open_c2:
        classification = await classify_async(text, signal=open_c2[0])
    actionable = _canal2_actionable(classification)
    actions = _canal2_action_names(classification)
    preview = text[:240].replace("\n", " | ")

    if not actionable:
        contextual_target = _recent_tp_announcement_target(open_c2, text)
        if contextual_target is not None:
            journal.event(
                sig_id,
                "standalone_context_attributed",
                channel="canal2",
                target=_sig_id(contextual_target),
                attribution="recent_observed_tp_hit",
                actions=actions,
                actionable=False,
                text_preview=preview,
                tg_ts=_msg_ts_iso(msg),
            )
            return

    route = _standalone_mgmt_route(len(open_c2), bool(actionable))

    if route == "apply":
        target = open_c2[0]
        journal.event(
            sig_id,
            "standalone_mgmt_applied",
            channel="canal2",
            target=_sig_id(target),
            actions=actions,
            text_preview=preview,
            tg_ts=_msg_ts_iso(msg),
        )
        await _execute_action(
            target,
            classification,
            raw_text=text,
            tg_ts=_msg_ts_iso(msg),
        )
        return

    open_ids = [_sig_id(signal) for signal in open_c2]
    event_name = (
        "standalone_mgmt_ambiguous"
        if route == "notify"
        else "standalone_context_observed"
    )
    journal.event(
        sig_id,
        event_name,
        channel="canal2",
        n_open=len(open_c2),
        open_signals=open_ids,
        actions=actions,
        actionable=bool(actionable),
        text_preview=preview,
        tg_ts=_msg_ts_iso(msg),
    )
    if route != "notify":
        return

    journal.anomaly(
        sig_id,
        "channel_msg",
        "warning",
        "mensaje accionable de Gold Signals sin reply y con varias "
        "operaciones abiertas; no se aplica automaticamente",
        open_signals=open_ids,
        actions=actions,
        text_preview=preview,
    )
    _schedule_detached(notify(
        f"⚠️ {provider_display_name('canal2')}\n"
        f"GESTIÓN SIN OPERACIÓN CLARA\n\n"
        f"Mensaje: {text[:220]}\n"
        f"Operaciones abiertas: {len(open_ids)}\n\n"
        f"El bot no actuó porque no puede saber con seguridad a cuál "
        f"corresponde."
    ))


async def _open_canal2_intent(
    intent: _Canal2EntryIntent,
    *,
    label: str = "Canal2",
) -> Signal | None:
    """Open one normalized Canal 2 intent through the established MT5 path."""
    message_id = int(intent.message_id)
    direction = str(intent.direction).upper()
    parsed = dict(intent.parsed)
    text = intent.raw_text or ""
    sig_id_pre = f"canal2_{message_id}"

    existing_signal = state.get("canal2", message_id)
    if existing_signal is not None:
        _canal2_open_committed(message_id)
        journal.event(
            sig_id_pre,
            "canal2_entry_open_already_claimed",
            reason="state_already_contains_signal",
            existing_status=existing_signal.status,
            existing_tickets=list(existing_signal.all_filled_tickets),
            entry_source_kind=intent.source_kind,
            raw_text=text[:500],
        )
        return None

    entry_ts = intent.entry_timestamp
    if entry_ts.tzinfo is not None:
        entry_ts = entry_ts.astimezone(timezone.utc).replace(tzinfo=None)
    telegram_ts = intent.telegram_timestamp
    signal_received_utc = datetime.utcnow()
    tg_to_bot_ms = None
    if telegram_ts is not None:
        tg_naive = telegram_ts
        if tg_naive.tzinfo is not None:
            tg_naive = tg_naive.astimezone(timezone.utc).replace(tzinfo=None)
        tg_to_bot_ms = int(
            (signal_received_utc - tg_naive).total_seconds() * 1000
        )
        if intent.source_kind == "telegram_now" and tg_to_bot_ms > 10000:
            print(
                f"[Canal2] tg->bot delay alto: {tg_to_bot_ms}ms "
                f"(message_id={message_id}). Posible reconexion Telethon."
            )

    effective_lot = max(
        0.01,
        round(config.LOT_SIZE * float(intent.lot_multiplier), 2),
    )
    trigger = intent.trigger or {}
    print(
        f"\n[{label}] Nueva senal: {direction} (msg={message_id})"
        f"{f' [{intent.lot_reason}]' if intent.lot_reason else ''}"
        f"{f' [{intent.source_kind}]' if intent.source_kind != 'telegram_now' else ''}"
        f"{f' [post-SL: TP cap idx {intent.max_tp_index}]' if intent.max_tp_index is not None else ''}"
    )
    journal.event(
        sig_id_pre,
        "signal_received",
        lifecycle_schema_version=(
            LIFECYCLE_SCHEMA_VERSION
            if intent.source_kind.startswith("zone_") else None
        ),
        channel="canal2",
        direction=direction,
        raw_text=text[:500],
        lot_mult=float(intent.lot_multiplier),
        effective_lot=effective_lot,
        tg_ts=(
            telegram_ts.isoformat(timespec="seconds")
            if telegram_ts is not None else None
        ),
        tg_to_bot_ms=tg_to_bot_ms,
        telegram_entry_command_key=intent.command_key,
        telegram_entry_was_reply=intent.reply_to_message_id is not None,
        telegram_entry_reply_to_message_id=intent.reply_to_message_id,
        telegram_entry_ts_utc=entry_ts.isoformat(timespec="milliseconds"),
        entry_source_kind=intent.source_kind,
        zone_plan_message_id=intent.zone_plan_message_id,
        zone_thread_root_message_id=intent.zone_thread_root_message_id,
        zone_entry_generation=intent.zone_entry_generation,
        zone_trigger_kind=trigger.get("trigger"),
        zone_trigger_side=trigger.get("side"),
        zone_trigger_price=trigger.get("price"),
        zone_trigger_time=trigger.get("time"),
        zone_trigger_time_msc=trigger.get("time_msc"),
        zone_trigger_range=trigger.get("zone"),
        zone_trigger_observed_utc=trigger.get("observed_utc"),
        zone_trigger_raw_broker_utc=trigger.get("raw_broker_time_utc"),
        zone_trigger_normalized_utc=trigger.get("normalized_time_utc"),
        zone_trigger_broker_utc_offset_s=trigger.get(
            "broker_utc_offset_s"
        ),
        zone_trigger_clock_residual_ms=trigger.get("clock_residual_ms"),
        zone_trigger_clock_basis=trigger.get("clock_basis"),
    )

    if not _canal2_open_claim(message_id):
        journal.event(
            sig_id_pre,
            "canal2_entry_open_already_claimed",
            reason=(
                "exposure_already_committed"
                if _canal2_open_already_committed(message_id)
                else "market_open_in_progress"
            ),
            entry_source_kind=intent.source_kind,
            raw_text=text[:500],
        )
        return None

    try:
        ctx = await _run(compute_market_context, config.MT5_SYMBOL)
        if ctx:
            journal.event(sig_id_pre, "market_context", **ctx)

        pre_open_tick = await _run(executor.current_tick_safe)
        reference_price = _entry_reference_from_tick(direction, pre_open_tick)
        interpreted = interpret_entry_levels(
            "canal2", direction, parsed, reference_price=reference_price
        )
        _log_entry_level_interpretation(
            sig_id_pre, "canal2", parsed, interpreted, reference_price
        )
        parsed = interpreted["parsed"]
        result = await _run(
            executor.open_market_with_fill,
            direction,
            effective_lot,
            parsed.get("sl"),
            parsed["tps"][0] if parsed.get("tps") else None,
            f"c2_{message_id}",
            config.magic_for("canal2"),
        )
    except Exception:
        _canal2_open_finished(message_id)
        raise

    if not result:
        _canal2_open_finished(message_id)
        journal.event(
            sig_id_pre,
            "market_fill_failed",
            reason="executor.open_market returned None",
            entry_source_kind=intent.source_kind,
        )
        journal.anomaly(
            sig_id_pre,
            "fill",
            "critical",
            "executor.open_market devolvio None; la senal no abrio posicion",
            channel="canal2",
            direction=direction,
            entry_source_kind=intent.source_kind,
        )
        return None

    ticket, fill_price = result
    _canal2_open_committed(message_id)
    market_filled_utc = datetime.utcnow()
    fill_latency_ms = int(
        (market_filled_utc - signal_received_utc).total_seconds() * 1000
    )
    tick_ctx = await _run(executor.current_tick_safe)
    journal.event(
        sig_id_pre,
        "market_filled",
        lifecycle_schema_version=(
            LIFECYCLE_SCHEMA_VERSION
            if intent.source_kind.startswith("zone_") else None
        ),
        ticket=ticket,
        price=fill_price,
        latency_ms=fill_latency_ms,
        bid=tick_ctx.get("bid") if tick_ctx else None,
        ask=tick_ctx.get("ask") if tick_ctx else None,
        spread=tick_ctx.get("spread") if tick_ctx else None,
        tick_time=tick_ctx.get("time") if tick_ctx else None,
        tick_time_msc=tick_ctx.get("time_msc") if tick_ctx else None,
        entry_source_kind=intent.source_kind,
        zone_plan_message_id=intent.zone_plan_message_id,
        zone_thread_root_message_id=intent.zone_thread_root_message_id,
        zone_entry_generation=intent.zone_entry_generation,
        generation_message_id=message_id,
        zone_trigger_kind=trigger.get("trigger"),
        zone_trigger_price=trigger.get("price"),
        zone_trigger_time_msc=trigger.get("time_msc"),
        zone_trigger_observed_utc=trigger.get("observed_utc"),
        zone_trigger_normalized_utc=trigger.get("normalized_time_utc"),
        zone_trigger_broker_utc_offset_s=trigger.get(
            "broker_utc_offset_s"
        ),
        zone_trigger_clock_basis=trigger.get("clock_basis"),
    )

    c2_be_idx = (
        config.STRATEGY_C2_BE_TP_INDEX
        if config.STRATEGY_C2_BE_TP_INDEX >= 0 else None
    )
    c2_target_tp = (
        config.STRATEGY_C2_TARGET_TP_INDEX
        if config.STRATEGY_C2_TARGET_TP_INDEX >= 0 else None
    )
    c2_time_stop = (
        datetime.utcnow() + timedelta(minutes=config.STRATEGY_C2_TIME_STOP_MIN)
        if config.STRATEGY_C2_TIME_STOP_MIN > 0 else None
    )
    sig = Signal(
        channel="canal2",
        message_id=message_id,
        direction=direction,
        timestamp=entry_ts,
        telegram_entry_command_key=intent.command_key,
        telegram_entry_was_reply=intent.reply_to_message_id is not None,
        telegram_entry_reply_to_message_id=intent.reply_to_message_id,
        telegram_entry_timestamp=(telegram_ts or entry_ts),
        market_ticket=ticket,
        market_fill_price=fill_price,
        lot_multiplier=float(intent.lot_multiplier),
        max_tp_index=intent.max_tp_index,
        is_high_risk=intent.is_high_risk,
        time_stop_at=c2_time_stop,
        entry_mode=config.STRATEGY_C2_ENTRY_MODE,
        target_tp_index=c2_target_tp,
        be_at_tp_index=c2_be_idx,
        adverse_action=config.STRATEGY_C2_ADVERSE_ACTION,
        entry_source_kind=intent.source_kind,
        zone_plan_message_id=intent.zone_plan_message_id,
        zone_thread_root_message_id=intent.zone_thread_root_message_id,
        zone_trigger_kind=trigger.get("trigger"),
        zone_trigger_price=trigger.get("price"),
        zone_trigger_time_msc=trigger.get("time_msc"),
        zone_entry_generation=intent.zone_entry_generation,
    )
    state.add(sig)
    _emit_same_direction_overlap_anomaly(sig)
    journal.begin_trade(
        _sig_id(sig),
        channel="canal2",
        direction=direction,
        signal_received_utc=signal_received_utc.isoformat(
            timespec="milliseconds"
        ),
        market_filled_utc=market_filled_utc.isoformat(timespec="milliseconds"),
        fill_latency_ms=fill_latency_ms,
        market_entry_price=fill_price,
        adverse_action=config.STRATEGY_C2_ADVERSE_ACTION,
        entry_source_kind=intent.source_kind,
        zone_plan_message_id=intent.zone_plan_message_id,
        zone_trigger_kind=trigger.get("trigger"),
        zone_trigger_price=trigger.get("price"),
        zone_trigger_time_msc=trigger.get("time_msc"),
        zone_trigger_observed_utc=trigger.get("observed_utc"),
        zone_trigger_normalized_utc=trigger.get("normalized_time_utc"),
        zone_trigger_broker_utc_offset_s=trigger.get(
            "broker_utc_offset_s"
        ),
        zone_trigger_clock_basis=trigger.get("clock_basis"),
        zone_entry_generation=intent.zone_entry_generation,
    )
    _log_strategy_snapshot(
        sig,
        num_entries=config.STRATEGY_C2_NUM_ENTRIES,
        time_stop_min=config.STRATEGY_C2_TIME_STOP_MIN,
    )
    await _open_extra_legs(sig, message_id)
    parsed = await _apply_interpreted_entry_levels(
        sig,
        parsed,
        "canal2",
        reference_price=fill_price,
        tg_ts=(
            telegram_ts.isoformat(timespec="seconds")
            if telegram_ts is not None else None
        ),
    )
    deferred = _pop_deferred_canal2_entry_edit(message_id)
    if deferred:
        deferred_parsed = parse_canal2(deferred["text"])
        journal.event(
            _sig_id(sig),
            "canal2_deferred_entry_edit_applied",
            parsed_keys=sorted(deferred_parsed.keys()),
            tg_ts=deferred.get("tg_ts"),
        )
        await _apply_interpreted_entry_levels(
            sig,
            deferred_parsed,
            "canal2",
            reference_price=sig.market_fill_price,
            tg_ts=deferred.get("tg_ts"),
        )
    logger.log_signal(sig, parsed)
    return sig


async def _process_canal2_new(msg, label: str = "Canal2", dedup: bool = True,
                              entry_serialized: bool = False):
    # Dedup: el poller activo y el event handler pueden ver el mismo mensaje.
    # _new_msg_already_seen marca como visto atómicamente — el que llega primero
    # procesa; el segundo retorna aquí sin hacer nada.
    if dedup and _new_msg_already_seen("canal2", msg.id):
        if _canal2_open_already_committed(msg.id):
            journal.event(
                f"canal2_{msg.id}",
                "canal2_entry_open_already_claimed",
                reason="duplicate_telegram_delivery",
                delivery_dedup=True,
            )
        return

    text = msg.text or ""
    if _is_explicit_signal_retraction(text):
        async with _entry_serial_lock("canal2"):
            await _handle_explicit_signal_retraction(msg, "canal2")
        return
    immediate_entry = is_canal2_entry(text)
    reply_id = (
        msg.reply_to.reply_to_msg_id
        if msg.reply_to and msg.reply_to.reply_to_msg_id
        else None
    )
    if immediate_entry and not entry_serialized:
        async with _entry_serial_lock("canal2"):
            await _process_canal2_new(
                msg,
                label=label,
                dedup=False,
                entry_serialized=True,
            )
        return

    # An immediate BUY/SELL ... NOW remains an entry even when Telegram shows
    # it as a reply to an older signal. Gold Signals uses this exact shape for
    # re-entries; routing replies first used to discard the entry as stale
    # management when the referenced basket was already closed.
    if immediate_entry and reply_id is not None:
        journal.event(
            f"canal2_{msg.id}",
            "canal2_reply_entry_recognized",
            reply_to_msg_id=reply_id,
            direction=(parse_canal2(text).get("direction")),
            routing="new_entry",
            tg_ts=_msg_ts_iso(msg),
            raw_text=text[:500],
        )

    # Management reply, but only after ruling out an explicit new entry.
    if not immediate_entry and reply_id is not None:
        zone_plan = _canal2_zone_plans.get(int(reply_id))
        if (
            zone_plan is None
            and state.get("canal2", int(reply_id)) is None
        ):
            zone_plan = await _recover_canal2_zone_plan_from_reply(
                msg,
                int(reply_id),
            )
        if zone_plan is not None:
            lifecycle_actions = classify_zone_followup(text)
            bound_signal = state.get("canal2", int(reply_id))
            if lifecycle_actions or bound_signal is None:
                await _handle_canal2_zone_plan_reply(
                    msg,
                    int(reply_id),
                    zone_plan,
                )
                return
            _register_canal2_zone_plan_alias(
                zone_plan,
                int(msg.id),
                source_message_id=int(reply_id),
            )
            state.alias(bound_signal, int(msg.id))
            alias_generations = zone_plan.setdefault(
                "alias_generation_ids", {}
            )
            alias_generations.setdefault(
                str(int(msg.id)), int(bound_signal.message_id)
            )
            _merge_canal2_zone_execution_levels(
                zone_plan,
                parse_canal2(text),
                raw_text=text,
                tg_ts=_msg_ts_iso(msg),
                message_id=int(msg.id),
            )

        replied_zone_plan = parse_canal2_zone_plan(text)
        if replied_zone_plan is not None:
            await _handle_canal2_zone_plan(
                msg,
                text,
                replied_zone_plan,
                source_kind="reply",
                thread_root_message_id=int(reply_id),
            )
            return

        sig, route = _resolve_management_reply_target(
            "canal2",
            reply_id,
            allow_single_open_fallback=False,
        )
        if sig is None and route != "target_signal_closed":
            sig, route = await _recover_canal2_management_target_from_reply_root(
                msg,
                int(reply_id),
            )
        if sig:
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
        else:
            _log_unresolved_management_reply(msg, "canal2", reply_id, route)
        return

    if not immediate_entry:
        zone_plan = parse_canal2_zone_plan(text)
        if zone_plan is not None:
            await _handle_canal2_zone_plan(
                msg,
                text,
                zone_plan,
                source_kind="new",
            )
            return
        await _handle_canal2_standalone(
            msg,
            text,
            f"canal2_{msg.id}",
        )
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
        is_reply=reply_id is not None,
        reply_to_msg_id=reply_id,
    )
    if "direction" not in parsed:
        return

    direction = parsed["direction"]
    existing_signal = state.get("canal2", msg.id)
    if existing_signal is not None:
        # The process-local gate is intentionally disposable. After a bot
        # restart, MT5 resync rebuilds Signal objects from the position
        # comments; that reconstructed identity is the durable authority that
        # this Telegram message already created exposure.
        _canal2_open_committed(msg.id)
        journal.event(
            f"canal2_{msg.id}",
            "canal2_entry_open_already_claimed",
            reason="state_already_contains_signal",
            existing_status=existing_signal.status,
            existing_tickets=list(existing_signal.all_filled_tickets),
            raw_text=text[:500],
        )
        return

    duplicate_ts = getattr(msg, "date", None) or datetime.utcnow()
    if getattr(duplicate_ts, "tzinfo", None) is not None:
        duplicate_ts = duplicate_ts.replace(tzinfo=None)
    duplicate = _canal2_duplicate_alias_candidate(
        msg.id, direction, duplicate_ts, parsed,
        state.open_signals("canal2"),
        config.STRATEGY_C2_DUPLICATE_ALIAS_WINDOW_S,
        raw_text=text,
        is_reply=reply_id is not None,
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

    # ── FILTRO 2: Validacion de niveles antes de abrir ──
    if msg.id in _deferred_canal2_entry_edits:
        deferred = _deferred_canal2_entry_edits[msg.id]
        deferred_parsed = parse_canal2(deferred["text"])
        merged = _merge_canal2_entry_parsed(parsed, deferred_parsed)
        parsed = merged
        text = _format_canal2_entry_text(
            merged, high_risk=strategies.is_high_risk_signal(text))
        direction = parsed["direction"]
        _pop_deferred_canal2_entry_edit(msg.id)
        journal.event(
            f"canal2_{msg.id}",
            "canal2_deferred_entry_edit_applied",
            parsed_keys=sorted(deferred_parsed.keys()),
            tg_ts=deferred.get("tg_ts"),
            before_open=True,
        )

    lot_mult, lot_reason = strategies.lot_multiplier_for_signal(text)
    if lot_mult <= 0:
        print(f"\n[{label}] ❌ SEÑAL IGNORADA ({direction}, msg={msg.id}): lot_mult=0")
        return

    # ── POST-SL momentum (point 4) — TP cap ──
    max_tp_idx = strategies.max_tp_index_for_signal("canal2")

    await _open_canal2_intent(
        _Canal2EntryIntent(
            message_id=int(msg.id),
            direction=direction,
            parsed=parsed,
            raw_text=text,
            entry_timestamp=duplicate_ts,
            telegram_timestamp=getattr(msg, "date", None),
            reply_to_message_id=reply_id,
            source_kind="telegram_now",
            lot_multiplier=lot_mult,
            lot_reason=lot_reason,
            max_tp_index=max_tp_idx,
            command_key=canal2_entry_command_key(text),
            is_high_risk=strategies.is_high_risk_signal(text),
        ),
        label=label,
    )
    return


async def _process_canal2_edit(msg, label: str = "Canal2"):
    text = msg.text or ""
    sig = state.get("canal2", msg.id)
    owns_live_signal = sig is not None and sig.status == "open"
    owns_entry_identity = (
        owns_live_signal
        or _canal2_open_in_progress(msg.id)
        or _canal2_open_already_committed(msg.id)
    )
    immediate_entry = is_canal2_entry(text)
    reply_id = (
        msg.reply_to.reply_to_msg_id
        if msg.reply_to and msg.reply_to.reply_to_msg_id
        else None
    )
    if immediate_entry:
        _drop_canal2_zone_plan_aliases(msg.id)

    if (
        not immediate_entry
        and reply_id is not None
        and not owns_entry_identity
    ):
        zone_plan = _canal2_zone_plans.get(int(reply_id))
        if (
            zone_plan is None
            and state.get("canal2", int(reply_id)) is None
        ):
            zone_plan = await _recover_canal2_zone_plan_from_reply(
                msg,
                int(reply_id),
            )
        if zone_plan is not None:
            lifecycle_actions = classify_zone_followup(text)
            bound_signal = state.get("canal2", int(reply_id))
            if lifecycle_actions or bound_signal is None:
                if _edit_already_seen("canal2", msg):
                    return
                await _handle_canal2_zone_plan_reply(
                    msg,
                    int(reply_id),
                    zone_plan,
                )
                return

        replied_zone_plan = parse_canal2_zone_plan(text)
        if replied_zone_plan is not None:
            if _edit_already_seen("canal2", msg):
                return
            await _handle_canal2_zone_plan(
                msg,
                text,
                replied_zone_plan,
                source_kind="reply_edit",
                thread_root_message_id=int(reply_id),
            )
            return

    # An edited re-entry can still be a Telegram reply to an older trade. If
    # this message already owns a live Signal, its edit updates that Signal,
    # not the replied-to historical basket.
    if not owns_entry_identity and not immediate_entry:
        if await _process_management_reply_edit(msg, "canal2", label):
            return

    if sig is None:
        if owns_entry_identity:
            if _edit_already_seen("canal2", msg):
                return
            _defer_canal2_entry_edit(msg, text)
            journal.event(
                f"canal2_{msg.id}",
                "canal2_orphan_entry_edit_deferred",
                reason=(
                    "market_open_in_progress"
                    if _canal2_open_in_progress(msg.id)
                    else "exposure_committed_state_pending"
                ),
                raw_text=text[:500],
                tg_ts=_msg_ts_iso(msg),
            )
            return
        zone_plan = parse_canal2_zone_plan(text)
        if zone_plan is not None:
            if _edit_already_seen("canal2", msg):
                return
            await _handle_canal2_zone_plan(
                msg,
                text,
                zone_plan,
                source_kind="edit",
            )
            return
        if not immediate_entry:
            return
        if _edit_already_seen("canal2", msg):
            print(f"[{label}] Edit huérfano duplicado msg={msg.id} "
                  f"edit_date={getattr(msg, 'edit_date', None)} — ignorado")
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
            # The edit is now the authoritative first delivery. Mark the
            # new-message route as seen so a later poll/event is only logged.
            _new_msg_already_seen("canal2", msg.id)
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

    # Dedup: Telethon a veces re-emite el mismo MessageEdited (mismo edit_date).
    # Sin esto, llegamos a parsear y re-aplicar SL/TP por nada.
    if _edit_already_seen("canal2", msg):
        print(f"[{label}] Edit duplicado msg={msg.id} edit_date={msg.edit_date} — ignorado")
        return

    parsed = parse_canal2(text)
    if not immediate_entry and not any(
            key in parsed for key in ("range", "tps", "sl")):
        return
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
    zone_plan = _canal2_zone_plans.get(int(msg.id))
    if zone_plan is not None:
        _merge_canal2_zone_execution_levels(
            zone_plan,
            parsed,
            raw_text=text,
            tg_ts=_tg_edit_ts,
            message_id=int(msg.id),
        )
    await _apply_interpreted_entry_levels(
        sig, parsed, "canal2",
        reference_price=sig.market_fill_price,
        tg_ts=_tg_edit_ts)


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
        raw_payload = _telegram_raw_payload(msg, channel, kind)
        tg_ts = (
            msg.edit_date
            if raw_payload["is_edit"] and msg.edit_date is not None
            else msg.date or msg.edit_date
        )
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

        raw_receipt = _j.event(
            f"{channel}_{msg.id}",
            "telegram_raw",
            **raw_payload,
        )
        _j.event(f"{channel}_{msg.id}", "handler_entry",
                 kind=kind,
                 channel=channel,
                 message_revision_id=raw_payload["message_revision_id"],
                 msg_type=msg_type,
                 sticker_id=sticker_id,
                 text_preview=text_preview,
                 has_reply=has_reply,
                 reply_to_msg_id=reply_to,
                 tg_ts=tg_ts.isoformat(timespec="seconds") if tg_ts else None,
                 handler_entry_ts=handler_entry.isoformat(timespec="milliseconds"),
                 telegram_to_handler_ms=delay_ms)
        return raw_receipt
    except Exception as e:
        print(f"[DIAG] error _msg_diag: {e}")
        return None


@client.on(events.NewMessage(chats=[config.CANAL_2_ID]))
async def canal2_new(event):
    raw_receipt = _msg_diag(event.message, "canal2", "new")
    await _dispatch_telegram_message(
        event.message,
        "canal2",
        "new",
        raw_receipt=raw_receipt,
    )


@client.on(events.MessageEdited(chats=[config.CANAL_2_ID]))
async def canal2_edit(event):
    raw_receipt = _msg_diag(event.message, "canal2", "edit")
    await _dispatch_telegram_message(
        event.message,
        "canal2",
        "edit",
        raw_receipt=raw_receipt,
    )


# ─── Helper puro de CLOSE_FIRST para Gold Signals ───────────────────────────

def _close_first_decision(price_vs_entry_pts: float,
                          profit_threshold_pts: float = 0.5) -> str:
    """Decide la rama de CLOSE_FIRST canal2:

      "close_half"           → cerrar las capas de menor recorrido cuando
                               nuestra cesta sí tiene beneficio real.
      "defer_layer_mismatch" → conservar la operación cuando el proveedor
                               habla de capas rentables que nosotros no
                               podemos identificar en nuestra cesta.

    Pura — solo decide la rama. La aplicación va en _execute_actions.

    profit_threshold_pts: cuánto profit (en puntos) consideramos "real".
        +0.5 cubre el spread+comisiones típicos de XAUUSD en Vantage.
        Estrictamente '>'  — en el threshold exacto va a la rama segura.
    """
    if price_vs_entry_pts > profit_threshold_pts:
        return "close_half"
    return "defer_layer_mismatch"


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


def _position_lifecycle_monitor_task_anomaly_severity(exc):
    """Decide si una task de position_lifecycle_monitor.run() terminada merece anomaly.

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
                                or direction_changed),
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
    cl = normalize_classifier_outputs(
        await classify_async(text, signal=open_c1[0])
    )
    actionable = _target_requiring_actions(cl)
    actions = [item.get("action") for item in cl if item.get("action")]

    if not actionable:
        contextual_target = _recent_tp_announcement_target(open_c1, text)
        if contextual_target is not None:
            journal.event(
                sig_id,
                "standalone_context_attributed",
                channel="canal1",
                target=_sig_id(contextual_target),
                attribution="recent_observed_tp_hit",
                actions=actions,
                actionable=False,
                text_preview=txt_preview,
            )
            return

    route = _standalone_mgmt_route(len(open_c1), bool(actionable))

    if route == "log":
        journal.event(
            sig_id,
            "standalone_context_observed",
            channel="canal1",
            n_open=len(open_c1),
            open_signals=[_sig_id(signal) for signal in open_c1],
            actions=actions,
            actionable=False,
            text_preview=txt_preview,
        )
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
                f"⚠ {provider_display_name('canal1')}: mensaje de gestión "
                f"SIN reply, destino AMBIGUO.\n\n"
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
        async with _entry_serial_lock("canal1"):
            await _handle_canal1_sticker(msg)
        return

    text = msg.text or ""

    if _is_explicit_signal_retraction(text):
        async with _entry_serial_lock("canal1"):
            await _handle_explicit_signal_retraction(msg, "canal1")
        return

    # Mensaje de gestión (reply a una señal)
    if msg.reply_to and msg.reply_to.reply_to_msg_id:
        reply_id = msg.reply_to.reply_to_msg_id
        sig, route = _resolve_management_reply_target("canal1", reply_id)
        if sig:
            # Gemini con contexto del trade (dirección, P&L, posiciones, etc.)
            cl = await classify_async(text, signal=sig)
            await _execute_action(sig, cl, raw_text=text)
        else:
            _log_unresolved_management_reply(msg, "canal1", reply_id, route)
        return

    # Texto con TP/SL que sigue al sticker
    if is_canal1_signal_text(text):
        async with _entry_serial_lock("canal1"):
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
    raw_receipt = _msg_diag(event.message, "canal1", "new")
    await _dispatch_telegram_message(
        event.message,
        "canal1",
        "new",
        raw_receipt=raw_receipt,
    )


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
         inicial. Si cambia direccion, avisar sin actuar. Un cambio solo
         de rango queda registrado como telemetria, no como anomalia.
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
        await _apply_interpreted_entry_levels(
            sig, parsed, "canal1",
            reference_price=sig.market_fill_price,
            tg_ts=tg_edit_ts,
        )
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
            f"📝 {provider_display_name(sig.channel)} · edición de señal\n"
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
    raw_receipt = _msg_diag(event.message, "canal1", "edit")
    await _dispatch_telegram_message(
        event.message,
        "canal1",
        "edit",
        raw_receipt=raw_receipt,
    )


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
                f"⚠ {provider_display_name('canal1')}: sticker desconocido "
                f"(ID={sticker_id}).\n"
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

    existing_signal = state.get("canal1", msg.id)
    if existing_signal is not None:
        _entry_open_committed("canal1", msg.id)
        journal.event(
            sig_id,
            "canal1_entry_open_already_claimed",
            reason="state_already_contains_signal",
            existing_status=existing_signal.status,
            existing_tickets=list(existing_signal.all_filled_tickets),
            trigger="sticker",
        )
        return

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

    if not _entry_open_claim("canal1", msg.id):
        journal.event(
            sig_id_pre,
            "canal1_entry_open_already_claimed",
            reason=(
                "exposure_already_committed"
                if _entry_open_already_committed("canal1", msg.id)
                else "market_open_in_progress"
            ),
            trigger="sticker",
        )
        return

    # Contexto de mercado al entrar — ver comentario en canal2_new.
    try:
        ctx = await _run(compute_market_context, config.MT5_SYMBOL)
        if ctx:
            journal.event(sig_id_pre, "market_context", **ctx)

        magic = config.magic_for("canal1")
        # 1 sola llamada MT5 que devuelve (ticket, fill_price) — ahorra round-trip extra
        result = await _run(
            executor.open_market_with_fill,
            direction,
            config.LOT_SIZE,
            None,
            None,
            f"c1_{msg.id}",
            magic,
        )
    except Exception:
        _entry_open_finished("canal1", msg.id)
        raise
    if not result:
        _entry_open_finished("canal1", msg.id)
        journal.event(sig_id_pre, "market_fill_failed",
                      reason="executor.open_market returned None")
        journal.anomaly(sig_id_pre, "fill", "critical",
                        "executor.open_market devolvió None — sticker "
                        "recibido pero el bot no abrió posición",
                        channel="canal1", direction=direction)
        return
    ticket, fill_price = result
    _entry_open_committed("canal1", msg.id)

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
        entry_mode=config.STRATEGY_C1_ENTRY_MODE,
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
    await _apply_interpreted_entry_levels(
        sig, {"direction": direction}, "canal1", reference_price=fill_price)
    print(f"[Canal1] Mercado abierto con niveles provisionales, "
          f"esperando texto oficial con TP/SL...")


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

    existing_signal = state.get("canal1", msg.id)
    if existing_signal is not None:
        _entry_open_committed("canal1", msg.id)
        journal.event(
            sig_id_pre,
            "canal1_entry_open_already_claimed",
            reason="state_already_contains_signal",
            existing_status=existing_signal.status,
            existing_tickets=list(existing_signal.all_filled_tickets),
            trigger="text_only",
        )
        return None

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

    if not _entry_open_claim("canal1", msg.id):
        journal.event(
            sig_id_pre,
            "canal1_entry_open_already_claimed",
            reason=(
                "exposure_already_committed"
                if _entry_open_already_committed("canal1", msg.id)
                else "market_open_in_progress"
            ),
            trigger="text_only",
        )
        return state.get("canal1", msg.id)

    # Contexto de mercado al entrar — ver comentario en canal2_new.
    try:
        ctx = await _run(compute_market_context, config.MT5_SYMBOL)
        if ctx:
            journal.event(sig_id_pre, "market_context", **ctx)

        magic = config.magic_for("canal1")
        result = await _run(
            executor.open_market_with_fill,
            direction,
            config.LOT_SIZE,
            None,
            None,
            f"c1_{msg.id}",
            magic,
        )
    except Exception:
        _entry_open_finished("canal1", msg.id)
        raise
    if not result:
        _entry_open_finished("canal1", msg.id)
        journal.event(sig_id_pre, "market_fill_failed",
                      reason="executor.open_market returned None (text-only path)")
        journal.anomaly(sig_id_pre, "fill", "critical",
                        "executor.open_market devolvió None en path "
                        "text-only canal1",
                        channel="canal1", direction=direction)
        return None
    ticket, fill_price = result
    _entry_open_committed("canal1", msg.id)

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
        entry_mode=config.STRATEGY_C1_ENTRY_MODE,
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
            f"ℹ️ {provider_display_name(sig.channel)} · señal abierta desde "
            f"TEXTO sin sticker previo\n"
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
    # El modo describe lo que abrimos en MT5, no el formato del mensaje.
    # Una entrada a precio exacto tambien abre todas las legs scale-out.
    sig.entry_mode = config.STRATEGY_C1_ENTRY_MODE
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
    parsed_to_apply = await _apply_interpreted_entry_levels(
        sig, parsed, "canal1",
        reference_price=sig.market_fill_price,
        tg_ts=_tg_ts,
    )
    journal.event(sig_id, "canal1_text_applied",
                  **_canal1_text_applied_summary(sig, parsed_to_apply))
    logger.log_signal(sig, parsed_to_apply)

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
_POLL_STARTUP_SCAN_LIMIT = 200
_POLL_STARTUP_MAX_MESSAGES = 2000
_POLL_COVERAGE_LOG_INTERVAL_S = 300
_POLL_COVERAGE_OVERLAP_S = 120
_POLL_LEGACY_COVERAGE_LOOKBACK_S = 24 * 60 * 60

# Estado del poller: (channel, msg_id) → edit_date de la última versión vista.
# "UNSEEN" indica que el mensaje aún no fue registrado en el primer scan.
# Usamos un sentinel en lugar de None para distinguir "aún no visto" vs
# "visto y sin editar" (edit_date=None es válido en mensajes sin editar).
_POLLER_UNSEEN = object()
_POLLER_HISTORY_BACKOFF_BASE_S = 15.0
_POLLER_HISTORY_BACKOFF_MAX_S = 120.0
_poller_history_backoff_until: dict[str, float] = {}
_poller_history_failures: dict[str, int] = {}


def _poller_now_monotonic() -> float:
    return time.monotonic()


def _is_transient_telegram_history_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc} {exc!r}"
    if "GetHistoryRequest" not in text:
        return False
    transient_markers = (
        "No workers running",
        "RPCError -500",
        "ServerError",
        "internal issues",
    )
    return any(marker in text for marker in transient_markers)


def _poller_history_backoff_seconds(failures: int) -> float:
    exponent = max(0, failures - 1)
    return min(
        _POLLER_HISTORY_BACKOFF_MAX_S,
        _POLLER_HISTORY_BACKOFF_BASE_S * (2 ** exponent),
    )


def _poller_in_history_backoff(channel_name: str) -> bool:
    until = _poller_history_backoff_until.get(channel_name, 0.0)
    return _poller_now_monotonic() < until


def _poller_record_history_backoff(
        channel_name: str, phase: str, exc: Exception) -> None:
    failures = _poller_history_failures.get(channel_name, 0) + 1
    cooldown_s = _poller_history_backoff_seconds(failures)
    _poller_history_failures[channel_name] = failures
    _poller_history_backoff_until[channel_name] = (
        _poller_now_monotonic() + cooldown_s
    )
    error = str(exc)
    print(
        f"[Poller] Telegram GetHistory temporal {channel_name}; "
        f"backoff {cooldown_s:.0f}s: {error}"
    )
    journal.event(
        "bot",
        "poller_telegram_history_backoff",
        channel=channel_name,
        phase=phase,
        failures=failures,
        cooldown_s=cooldown_s,
        error=error,
    )


def _poller_clear_history_backoff(channel_name: str) -> None:
    failures = _poller_history_failures.pop(channel_name, 0)
    _poller_history_backoff_until.pop(channel_name, None)
    if failures:
        journal.event(
            "bot",
            "poller_telegram_history_recovered",
            channel=channel_name,
            failures=failures,
        )
_poller_msg_state: dict[tuple, object] = {}  # (channel, msg_id) → edit_date
_poller_initialized_channels: set[str] = set()
_poller_last_coverage_log: dict[str, float] = {}


def _as_utc_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _poller_message_edit_token(msg) -> str | None:
    edit_date = _as_utc_datetime(getattr(msg, "edit_date", None))
    return edit_date.isoformat(timespec="seconds") if edit_date else None


def _load_poller_startup_history(
    channel_name: str,
    channel_id: int,
    *,
    path: Path | None = None,
) -> dict:
    """Load the previous coverage boundary and Telegram revisions from JSONL."""
    path = Path(path or journal.EVENTS_FILE)
    message_versions: dict[int, str | None] = {}
    raw_revisions: dict[tuple[int, str], datetime | None] = {}
    raw_revision_dates: dict[tuple[int, str], datetime | None] = {}
    processed_revisions: set[tuple[int, str]] = set()
    previous_event_ts = None
    fallback_coverage_cutoff = None
    explicit_coverage_cutoff = None
    processing_contract_utc = None
    if not path.is_file():
        return {
            "has_channel_history": False,
            "coverage_cutoff": None,
            "message_versions": message_versions,
            "raw_revisions": raw_revisions,
            "processed_revisions": processed_revisions,
            "processing_contract_utc": None,
        }

    with path.open("r", encoding="utf-8", errors="replace") as source:
        for raw_line in source:
            try:
                row = json.loads(raw_line)
            except (TypeError, ValueError):
                continue
            row_ts = _as_utc_datetime(row.get("ts"))
            if row.get("ev") == "session_started":
                fallback_coverage_cutoff = (
                    previous_event_ts - timedelta(
                        seconds=_POLL_LEGACY_COVERAGE_LOOKBACK_S
                    )
                    if previous_event_ts is not None
                    else None
                )
            if row_ts is not None:
                previous_event_ts = row_ts

            if (
                row.get("ev") == "telegram_processing_contract"
                and row.get("channel") == channel_name
            ):
                try:
                    contract_channel_id = int(row.get("channel_id"))
                except (TypeError, ValueError):
                    contract_channel_id = None
                activated = _as_utc_datetime(
                    row.get("activated_utc") or row.get("ts")
                )
                if (
                    contract_channel_id == int(channel_id)
                    and activated is not None
                ):
                    processing_contract_utc = activated

            if (
                row.get("ev") == "telegram_poll_coverage"
                and row.get("channel") == channel_name
            ):
                try:
                    coverage_channel_id = int(row.get("channel_id"))
                except (TypeError, ValueError):
                    coverage_channel_id = None
                covered_through = _as_utc_datetime(
                    row.get("covered_through_utc")
                )
                if (
                    coverage_channel_id == int(channel_id)
                    and covered_through is not None
                ):
                    explicit_coverage_cutoff = covered_through

            if row.get("channel") != channel_name:
                continue
            try:
                row_chat_id = int(row.get("chat_id"))
                message_id = int(row.get("message_id"))
            except (TypeError, ValueError):
                continue
            if row_chat_id != int(channel_id):
                continue
            if row.get("ev") == "telegram_raw":
                edit_token = row.get("edit_date_utc") or "new"
                revision = (message_id, str(edit_token))
                message_versions[message_id] = row.get("edit_date_utc")
                raw_revisions[revision] = row_ts
                raw_revision_dates[revision] = _as_utc_datetime(
                    row.get("date_utc")
                )
            elif row.get("ev") == "telegram_processed":
                revision_token = str(row.get("revision_token") or "new")
                processed_revisions.add((message_id, revision_token))

    for revision, captured_at in raw_revisions.items():
        if (
            processing_contract_utc is None
            or (
                captured_at is not None
                and captured_at < processing_contract_utc
            )
        ):
            processed_revisions.add(revision)

    coverage_cutoff = (
        explicit_coverage_cutoff
        if explicit_coverage_cutoff is not None
        else fallback_coverage_cutoff
    )
    unprocessed_revisions = set(raw_revisions) - processed_revisions
    unresolved_dates = []
    for revision in unprocessed_revisions:
        message_date = raw_revision_dates.get(revision)
        if message_date is None:
            captured_at = raw_revisions.get(revision)
            if captured_at is not None:
                message_date = captured_at - timedelta(
                    seconds=_POLL_LEGACY_COVERAGE_LOOKBACK_S
                )
        if message_date is not None:
            unresolved_dates.append(message_date)
    if unresolved_dates:
        unresolved_cutoff = min(unresolved_dates) - timedelta(
            seconds=_POLL_COVERAGE_OVERLAP_S
        )
        coverage_cutoff = (
            unresolved_cutoff
            if coverage_cutoff is None
            else min(coverage_cutoff, unresolved_cutoff)
        )
    return {
        "has_channel_history": bool(message_versions)
        or bool(processed_revisions)
        or explicit_coverage_cutoff is not None,
        "coverage_cutoff": coverage_cutoff,
        "message_versions": message_versions,
        "raw_revisions": raw_revisions,
        "unprocessed_revisions": unprocessed_revisions,
        "processed_revisions": processed_revisions,
        "processing_contract_utc": processing_contract_utc,
    }


def _poller_record_coverage(
    channel_name: str,
    channel_id: int,
    observed_at: datetime,
    messages: list,
    *,
    force: bool = False,
) -> None:
    """Persist a conservative per-channel watermark without logging each poll."""
    monotonic_now = time.monotonic()
    previous = _poller_last_coverage_log.get(channel_name)
    if (
        not force
        and previous is not None
        and monotonic_now - previous < _POLL_COVERAGE_LOG_INTERVAL_S
    ):
        return

    observed_utc = _as_utc_datetime(observed_at) or datetime.now(timezone.utc)
    covered_through = observed_utc - timedelta(
        seconds=_POLL_COVERAGE_OVERLAP_S
    )
    message_ids = [int(msg.id) for msg in messages]
    journal.event(
        "bot",
        "telegram_poll_coverage",
        channel=channel_name,
        channel_id=int(channel_id),
        covered_through_utc=covered_through.isoformat(),
        overlap_sec=_POLL_COVERAGE_OVERLAP_S,
        observed_messages=len(message_ids),
        latest_message_id=max(message_ids) if message_ids else None,
    )
    _poller_last_coverage_log[channel_name] = monotonic_now


def _poller_startup_action(msg, history: dict) -> str:
    """Return baseline, seen, new or edit for one startup history message."""
    if not history.get("has_channel_history"):
        return "baseline"

    message_id = int(msg.id)
    current_edit = _poller_message_edit_token(msg)
    current_revision = (message_id, current_edit or "new")
    if "processed_revisions" in history:
        processed_revisions = set(history.get("processed_revisions") or ())
        if current_revision in processed_revisions:
            return "seen"
        processed_for_message = {
            token for known_id, token in processed_revisions
            if known_id == message_id
        }
        if processed_for_message:
            return "edit" if current_edit is not None else "seen"
        raw_revisions = set((history.get("raw_revisions") or {}).keys())
        if current_revision in raw_revisions:
            return "new"

    versions = history.get("message_versions") or {}
    if message_id in versions:
        if current_edit is not None and current_edit != versions[message_id]:
            return "edit"
        return "seen"

    cutoff = _as_utc_datetime(history.get("coverage_cutoff"))
    message_date = _as_utc_datetime(getattr(msg, "date", None))
    if cutoff is not None and message_date is not None and message_date > cutoff:
        return "new"
    return "baseline"


def _telegram_revision_token(msg) -> str:
    return _poller_message_edit_token(msg) or "new"


def _message_chat_id(msg, channel_name: str) -> int:
    value = getattr(msg, "chat_id", None)
    if value is not None:
        return int(value)
    return int(
        config.CANAL_2_ID if channel_name == "canal2" else config.CANAL_1_ID
    )


async def _dispatch_telegram_message(
    msg,
    channel_name: str,
    update_kind: str,
    *,
    label: str | None = None,
    raw_receipt=None,
) -> bool:
    """Dispatch once without making live handling wait for journal I/O."""
    del raw_receipt
    revision_token = _telegram_revision_token(msg)
    message_revision_id = _telegram_message_revision_id(msg, channel_name)
    decision_id = causal_trace.new_decision_id()
    if not runtime_control.begin_handler():
        journal.event(
            f"{channel_name}_{msg.id}",
            "telegram_deferred_for_restart",
            channel=channel_name,
            chat_id=_message_chat_id(msg, channel_name),
            message_id=int(msg.id),
            update_kind=update_kind,
            revision_token=revision_token,
            message_revision_id=message_revision_id,
            decision_id=decision_id,
        )
        return False

    owns_dispatch = False
    with causal_trace.bind_message_revision(
        message_revision_id,
        decision_id=decision_id,
    ):
        try:
            if message_revision_id in _dispatch_completed_revisions:
                return True
            if message_revision_id in _dispatch_inflight_revisions:
                return False
            _dispatch_inflight_revisions.add(message_revision_id)
            owns_dispatch = True

            decision_identity = {
                "channel": channel_name,
                "chat_id": _message_chat_id(msg, channel_name),
                "message_id": int(msg.id),
                "update_kind": update_kind,
                "revision_token": revision_token,
                "message_revision_id": message_revision_id,
                "decision_id": decision_id,
            }
            journal.event(
                f"{channel_name}_{msg.id}",
                "telegram_decision_started",
                **decision_identity,
            )

            if update_kind == "new":
                claimed_before = (channel_name, msg.id) in _seen_new_msg_ids
            else:
                edit_date = getattr(msg, "edit_date", None)
                edit_key = (
                    channel_name,
                    msg.id,
                    edit_date.isoformat()
                    if edit_date is not None else revision_token,
                )
                claimed_before = edit_key in _seen_edits

            try:
                if update_kind == "new":
                    if channel_name == "canal2":
                        await _process_canal2_new(
                            msg,
                            label=label or "Canal2",
                        )
                    else:
                        await _process_canal1_new(msg)
                elif channel_name == "canal2":
                    await _process_canal2_edit(
                        msg,
                        label=label or "Canal2",
                    )
                else:
                    await _process_canal1_edit(msg)
            except BaseException as exc:
                declared_action_ids = causal_trace.declared_action_ids()
                failure_fields = {
                    **decision_identity,
                    "declared_action_ids": declared_action_ids,
                    "declared_action_count": len(declared_action_ids),
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:1000],
                }
                try:
                    journal.event(
                        f"{channel_name}_{msg.id}",
                        "telegram_processing_failed",
                        **failure_fields,
                    )
                except BaseException as log_exc:
                    print(
                        "[Telegram] Error registrando el fallo de "
                        f"{channel_name}_{msg.id}: "
                        f"{type(log_exc).__name__}: {log_exc}",
                        flush=True,
                    )
                if not claimed_before:
                    _release_dispatch_dedup_claim(
                        channel_name,
                        msg,
                        update_kind,
                    )
                raise

            declared_action_ids = causal_trace.declared_action_ids()
            processed_fields = {
                **decision_identity,
                "declared_action_ids": declared_action_ids,
                "declared_action_count": len(declared_action_ids),
                "handler_deduplicated": claimed_before,
            }
            journal.event(
                f"{channel_name}_{msg.id}",
                "telegram_processed",
                **processed_fields,
            )
            _remember_dispatch_completed(message_revision_id)
            return True
        finally:
            if owns_dispatch:
                _dispatch_inflight_revisions.discard(
                    message_revision_id
                )
            runtime_control.end_handler()


async def _poller_fetch_startup_messages(
    channel_id: int,
    channel_name: str,
    history: dict,
) -> tuple[list, bool]:
    """Fetch backward until the previous journal coverage boundary is reached."""
    del channel_name  # Included for diagnostic-friendly call sites.
    fetched: list = []
    seen_ids: set[int] = set()
    offset_id = None
    cutoff = _as_utc_datetime(history.get("coverage_cutoff"))

    while True:
        kwargs = {"offset_id": offset_id} if offset_id is not None else {}
        page = await client.get_messages(
            channel_id,
            limit=_POLL_STARTUP_SCAN_LIMIT,
            **kwargs,
        )
        if not page:
            return fetched, True

        new_page = [msg for msg in page if int(msg.id) not in seen_ids]
        if not new_page:
            return fetched, False
        fetched.extend(new_page)
        seen_ids.update(int(msg.id) for msg in new_page)

        if not history.get("has_channel_history"):
            return fetched, True
        page_dates = [
            parsed
            for msg in new_page
            if (parsed := _as_utc_datetime(getattr(msg, "date", None)))
            is not None
        ]
        oldest_date = min(page_dates) if page_dates else None
        if (
            len(page) < _POLL_STARTUP_SCAN_LIMIT
            or cutoff is None
            or oldest_date is None
            or oldest_date <= cutoff
        ):
            return fetched, True
        if len(fetched) >= _POLL_STARTUP_MAX_MESSAGES:
            return fetched, False

        next_offset = min(int(msg.id) for msg in new_page)
        if next_offset == offset_id:
            return fetched, False
        offset_id = next_offset


async def _poller_initial_scan_channel(
    channel_id: int,
    channel_name: str,
) -> bool:
    if _poller_in_history_backoff(channel_name):
        return False
    history = _load_poller_startup_history(channel_name, channel_id)
    scan_started_utc = datetime.now(timezone.utc)
    if history.get("processing_contract_utc") is None:
        journal.event(
            "bot",
            "telegram_processing_contract",
            channel=channel_name,
            channel_id=int(channel_id),
            activated_utc=scan_started_utc.isoformat(),
        )
        history["processing_contract_utc"] = scan_started_utc
    try:
        msgs, coverage_complete = await _poller_fetch_startup_messages(
            channel_id,
            channel_name,
            history,
        )
        _poller_clear_history_backoff(channel_name)
    except Exception as exc:
        if _is_transient_telegram_history_error(exc):
            _poller_record_history_backoff(channel_name, "initial_scan", exc)
            return False
        print(f"[Poller] Error scan inicial {channel_name}: {exc}")
        return False

    if not coverage_complete:
        journal.anomaly(
            "bot",
            "channel_msg",
            "critical",
            "startup catch-up excedio el limite sin alcanzar la cobertura previa",
            channel=channel_name,
            channel_id=channel_id,
            fetched=len(msgs),
            limit=_POLL_STARTUP_MAX_MESSAGES,
        )
        await notify(
            "ATENCION: no pude revisar todo el intervalo sin conexion de "
            f"{provider_display_name(channel_name)}. El canal sigue protegido "
            "por los eventos en vivo, pero hace falta revisar el historial."
        )
        return False

    counts = {"baseline": 0, "seen": 0, "new": 0, "edit": 0}
    for msg in reversed(msgs):
        action = _poller_startup_action(msg, history)
        counts[action] += 1
        key = (channel_name, msg.id)

        if action in {"baseline", "seen"}:
            _poller_msg_state[key] = msg.edit_date
            _new_msg_already_seen(channel_name, msg.id)
            if msg.edit_date:
                _edit_already_seen(channel_name, msg)
            continue

        if action == "new":
            raw_receipt = _msg_diag(
                msg,
                channel_name,
                "startup_catchup_new",
            )
            dispatched = await _poller_dispatch_message(
                msg,
                channel_name,
                "new",
                label="Canal2_catchup" if channel_name == "canal2" else None,
                raw_receipt=raw_receipt,
            )
        else:
            raw_receipt = _msg_diag(
                msg,
                channel_name,
                "startup_catchup_edit",
            )
            dispatched = await _poller_dispatch_message(
                msg,
                channel_name,
                "edit",
                label="Canal2_catchup" if channel_name == "canal2" else None,
                raw_receipt=raw_receipt,
            )
        if dispatched is False:
            print(
                f"[Poller] {channel_name}: recuperacion pausada en "
                f"mensaje {msg.id}; se reintentara sin avanzar cobertura.",
                flush=True,
            )
            return False
        _poller_msg_state[key] = msg.edit_date

    _poller_initialized_channels.add(channel_name)
    cutoff = history.get("coverage_cutoff")
    journal.event(
        "bot",
        "poller_startup_scan",
        channel=channel_name,
        channel_id=channel_id,
        history_known=history.get("has_channel_history", False),
        coverage_cutoff=(cutoff.isoformat() if cutoff else None),
        fetched=len(msgs),
        **{f"count_{key}": value for key, value in counts.items()},
    )
    print(
        f"[Poller] {channel_name}: {len(msgs)} revisados | "
        f"recuperados={counts['new']} edits={counts['edit']} | "
        f"ya vistos={counts['seen']} base={counts['baseline']}"
    )
    _poller_record_coverage(
        channel_name,
        channel_id,
        scan_started_utc,
        msgs,
        force=True,
    )
    return True


async def _poller_poll_or_initialize(channel_id: int, channel_name: str):
    if channel_name not in _poller_initialized_channels:
        await _poller_initial_scan_channel(channel_id, channel_name)
        return
    await _poll_channel(channel_id, channel_name)


async def _poller_dispatch_message(
    msg,
    channel_name: str,
    kind: str,
    *,
    label: str | None = None,
    raw_receipt=None,
) -> bool:
    """Keep one bad message from terminating fallback coverage."""
    try:
        return await _dispatch_telegram_message(
            msg,
            channel_name,
            kind,
            label=label,
            raw_receipt=raw_receipt,
        )
    except Exception as exc:
        journal.anomaly(
            f"{channel_name}_{getattr(msg, 'id', 'unknown')}",
            "channel_msg",
            "critical",
            "fallo procesando mensaje desde el poller; queda pendiente de reintento",
            channel=channel_name,
            message_id=getattr(msg, "id", None),
            message_kind=kind,
            exception_type=type(exc).__name__,
            exception_message=str(exc)[:240],
        )
        return False


async def _poller_expand_active_messages(
    channel_id: int,
    channel_name: str,
    initial_messages: list,
) -> tuple[list, bool]:
    """Page backwards when a burst fills the active ten-message window."""
    fetched = list(initial_messages)
    seen_ids = {int(msg.id) for msg in fetched}

    def reached_known(messages: list) -> bool:
        return any(
            (channel_name, int(msg.id)) in _poller_msg_state
            for msg in messages
        )

    page = list(initial_messages)
    if len(page) < _POLL_MSG_LIMIT or reached_known(page):
        return fetched, True

    while len(fetched) < _POLL_STARTUP_MAX_MESSAGES:
        offset_id = min(int(msg.id) for msg in page)
        page = list(await client.get_messages(
            channel_id,
            limit=_POLL_MSG_LIMIT,
            offset_id=offset_id,
        ))
        if not page:
            return fetched, True
        new_page = [msg for msg in page if int(msg.id) not in seen_ids]
        if not new_page:
            return fetched, False
        fetched.extend(new_page)
        seen_ids.update(int(msg.id) for msg in new_page)
        if len(page) < _POLL_MSG_LIMIT or reached_known(new_page):
            return fetched, True
        page = new_page
    return fetched, False


async def _poll_channel(channel_id: int, channel_name: str):
    """Obtiene los últimos mensajes del canal y despacha nuevos y edits.

    Solo despacha lo que sea genuinamente nuevo desde el último poll:
      • msg no en _poller_msg_state  → dispatch new
      • msg en _poller_msg_state con edit_date distinto → dispatch edit
      • msg visto y sin cambio → no-op

    La dedup global (_new_msg_already_seen, _edit_already_seen) garantiza
    que si Telethon también dispara el mismo evento, se descarta sin trabajo.
    """
    if _poller_in_history_backoff(channel_name):
        return

    poll_started_utc = datetime.now(timezone.utc)
    try:
        initial_msgs = await client.get_messages(
            channel_id,
            limit=_POLL_MSG_LIMIT,
        )
        msgs, coverage_complete = await _poller_expand_active_messages(
            channel_id,
            channel_name,
            list(initial_msgs),
        )
        _poller_clear_history_backoff(channel_name)
    except Exception as e:
        if _is_transient_telegram_history_error(e):
            _poller_record_history_backoff(channel_name, "active_poll", e)
            return
        print(f"[Poller] Error get_messages {channel_name}: {e}")
        return

    all_dispatched = True
    for msg in reversed(msgs):  # oldest-first = orden cronológico natural
        key = (channel_name, msg.id)
        edit_date = msg.edit_date
        prev = _poller_msg_state.get(key, _POLLER_UNSEEN)

        if prev is _POLLER_UNSEEN:
            # Primera vez que vemos este mensaje desde el poller.
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
            raw_receipt = _msg_diag(msg, channel_name, "poll_new")
            dispatched = await _poller_dispatch_message(
                msg,
                channel_name,
                "new",
                label="Canal2_poll" if channel_name == "canal2" else None,
                raw_receipt=raw_receipt,
            )
            if dispatched is False:
                all_dispatched = False
                break
            _poller_msg_state[key] = edit_date

        elif edit_date != prev:
            # Mensaje editado desde el último poll
            raw_receipt = _msg_diag(msg, channel_name, "poll_edit")
            dispatched = await _poller_dispatch_message(
                msg,
                channel_name,
                "edit",
                label="Canal2_poll" if channel_name == "canal2" else None,
                raw_receipt=raw_receipt,
            )
            if dispatched is False:
                all_dispatched = False
                break
            _poller_msg_state[key] = edit_date

    if coverage_complete and all_dispatched:
        _poller_record_coverage(
            channel_name,
            channel_id,
            poll_started_utc,
            list(msgs),
        )
    elif not coverage_complete:
        journal.anomaly(
            "bot",
            "channel_msg",
            "critical",
            "active poll excedio el limite sin alcanzar un mensaje conocido",
            channel=channel_name,
            channel_id=channel_id,
            fetched=len(msgs),
            limit=_POLL_STARTUP_MAX_MESSAGES,
        )


async def poll_loop():
    """Bucle de polling activo para Canal 1 y Canal 2.

    Corre en paralelo con los event handlers de Telethon (no los reemplaza).
    Resuelve el delay estructural de Canal 2 causado por updateChannelTooLong.

    Fase 1 — recuperación desde el último punto cubierto:
        Contrasta los mensajes recientes con telegram_raw. Lo ya registrado
        se marca como visto; mensajes o edits posteriores al último evento de
        la sesión anterior se despachan en orden. En un canal sin historial se
        establece una línea base para no reabrir señales antiguas.

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

    # ── Fase 1: scan inicial con recuperación del intervalo sin cobertura ──
    print("[Poller] Scan inicial — comprobando mensajes durante la parada...")
    for channel_id, channel_name in watched:
        await _poller_initial_scan_channel(channel_id, channel_name)

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
            _poller_poll_or_initialize(channel_id, channel_name)
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
                print(
                    f"[Poller] Limpieza estado: {len(keys)} -> "
                    f"{len(_poller_msg_state)}"
                )


async def poll_loop_supervised(restart_delay_s: float = 2.0):
    """Restart the fallback poller after an unexpected task exit."""
    restart_count = 0
    while True:
        try:
            await poll_loop()
            raise RuntimeError("poll_loop finalizo sin cancelacion")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            restart_count += 1
            journal.anomaly(
                "bot",
                "channel_msg",
                "critical",
                "poller de respaldo detenido; reinicio automatico pendiente",
                restart_count=restart_count,
                exception_type=type(exc).__name__,
                exception_message=str(exc)[:240],
            )
            journal.event(
                "bot",
                "poller_restarting",
                restart_count=restart_count,
                restart_delay_s=float(restart_delay_s),
            )
            await asyncio.sleep(max(0.0, float(restart_delay_s)))


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
        # vez de los ficheros de producción. Las tareas globales se separan
        # del flag; la señal sigue aislada después por su signal_id.
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
                sig, route = _resolve_management_reply_target(
                    "canal2",
                    reply_id,
                    allow_cross_channel=True,
                )
                if sig:
                    parsed_in_reply = parse_canal2(text)
                    _tg_ts = msg.date.isoformat(timespec="seconds") if msg.date else None
                    if parsed_in_reply.get("tps") or parsed_in_reply.get("sl"):
                        print(f"[Test] Reply con niveles reales: "
                              f"{[k for k in ('tps','sl') if k in parsed_in_reply]}")
                        await _update_signal_from_parsed(sig, parsed_in_reply, tg_ts=_tg_ts)
                    cl = await classify_async(text, signal=sig)
                    await _execute_action(sig, cl, raw_text=text, tg_ts=_tg_ts)
                else:
                    _log_unresolved_management_reply(msg, "canal2", reply_id, route)
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
