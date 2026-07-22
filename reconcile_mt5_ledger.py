"""
reconcile_mt5_ledger.py — Reconciliador: journal del bot + historial MT5 → ledger de verdad.

PROBLEMA QUE RESUELVE
─────────────────────
El bot tiene DOS realidades:
  • Lo que el bot CREE que paso  → data/trade_events.jsonl (el journal)
  • Lo que paso DE VERDAD        → MT5 (el broker, historial inmutable)

El journal es el "diario del bot" y es falible: pierde posiciones en los
resyncs, registra P&L incompleto, corrompe timestamps. Auditorias reales
(15-may 2026) encontraron el journal descuadrado $12.56 en una semana.

Este script construye `data/ledger.jsonl` — UNA fila por trade, con la
VERDAD verificada contra MT5. El ledger:
  • Saca TODAS las posiciones de MT5 por patron de comment (no por lo
    que el bot creia tener) → captura Market B perdidos, etc.
  • P&L real de cada deal (profit + swap + commission + fee).
  • Marca discrepancias journal vs MT5.
  • Es IDEMPOTENTE y REGENERABLE: se reconstruye entero desde cero en
    cualquier momento. Ningun bug del bot puede perder datos — MT5 es
    inmutable.

USO
───
  python reconcile_mt5_ledger.py                  # reconcilia todo, escribe ledger.jsonl
  python reconcile_mt5_ledger.py --since 2026-05-01   # solo desde una fecha
  python reconcile_mt5_ledger.py --quiet          # sin resumen por consola

El watcher lo ejecuta al cerrar cada sesion. Tambien se puede correr a mano.
"""

import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from journal import health_verdict
import runtime_paths

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: pip install MetaTrader5")
    sys.exit(1)

DATA_DIR = runtime_paths.active_data_dir()
JOURNAL_FILE = DATA_DIR / "trade_events.jsonl"
LEDGER_FILE = DATA_DIR / "ledger.jsonl"

# Tolerancia (USD) para considerar que journal y MT5 "cuadran".
RECONCILE_TOLERANCE_USD = 0.50
MT5_TIME_OFFSET_TOLERANCE_S = 600
MT5_TIME_OFFSET_MAX_HOURS = 12


# ─── Parsing de comments MT5 ───────────────────────────────────────────────────

# c1_19569 / c2_278 / c2_12497_B / c2_12497_B1..B4 / c2_12015_rescue /
# DCA_c1_19569_4593.5. Telegram reinicia los message_id al cambiar de canal,
# por lo que IDs cortos como c2_278 son produccion valida desde julio de 2026.
# Sufijo _B = doble market legacy; _B1.._B4 = legs del modo scale_out.
_RX_SIG = re.compile(r"^(DCA_)?c([12])_([1-9]\d*)(_B\d*|_rescue)?")
# DCA formato VIEJO (abril 2026): "DCA_4572.0" — sin sig_id. Estos DCAs se
# asocian al market del mismo magic abierto justo antes (proximidad temporal),
# misma heuristica que executor.list_open_positions_grouped.
_RX_DCA_OLD = re.compile(r"^DCA_[\d.]+$")


def _mt5_history_query_end(now_utc: datetime | None = None) -> datetime:
    """Return a future-safe upper bound for broker-server timestamps."""
    now_utc = now_utc or datetime.now(timezone.utc)
    return now_utc + timedelta(days=1)


def parse_sig_role(comment: str):
    """Del comment de un deal devuelve (sig_id, role).

    role: market_a | market_b | scale_out_leg | rescue | dca | None.
    """
    m = _RX_SIG.match(comment or "")
    if not m:
        return None, None
    sig_id = f"canal{m.group(2)}_{m.group(3)}"
    if m.group(1):                       # prefijo "DCA_"
        return sig_id, "dca"
    suf = m.group(4)
    if suf == "_B":                      # doble market legacy (Pos B)
        return sig_id, "market_b"
    if suf and suf.startswith("_B"):     # _B1.._B4 → leg del modo scale_out
        return sig_id, "scale_out_leg"
    if suf == "_rescue":
        return sig_id, "rescue"
    return sig_id, "market_a"


def close_reason_from_comment(comment: str) -> str:
    """Clasifica el motivo de cierre por el comment del deal de salida."""
    c = (comment or "").lower()
    if c.startswith("[sl"):
        return "sl"
    if c.startswith("[tp"):
        return "tp"
    if c.startswith("[be"):
        return "be"
    if "bot_close" in c:
        return "bot_close"
    return "other"


def _ticket_key(ticket) -> str | None:
    if ticket is None:
        return None
    try:
        return str(int(ticket))
    except (TypeError, ValueError):
        return str(ticket)


def _level_history_bucket(journal_row: dict, ticket) -> dict | None:
    key = _ticket_key(ticket)
    if key is None:
        return None
    all_hist = journal_row.setdefault("ticket_level_history", {})
    return all_hist.setdefault(key, {"sl_history": [], "tp_history": []})


def _append_level_history(journal_row: dict, event: dict, status: str):
    """Capture SL/TP request/confirm/failure events by ticket."""
    bucket = _level_history_bucket(journal_row, event.get("ticket"))
    if bucket is None:
        return
    source = event.get("label") or event.get("ev") or "mt5"
    base = {
        "ts": event.get("ts"),
        "source": source,
        "status": status,
    }
    retcode = event.get("retcode", event.get("last_retcode"))
    if retcode is not None:
        base["retcode"] = retcode
    if event.get("attempts") is not None:
        base["attempts"] = event.get("attempts")
    if status == "observed_unattributed":
        for field in (
            "observed_interval_start_utc",
            "observed_interval_end_utc",
            "previous",
            "current",
        ):
            if event.get(field) is not None:
                base[field] = event.get(field)
    current = event.get("current") if isinstance(event.get("current"), dict) else {}
    sl_value = (
        event.get("new_sl") if "new_sl" in event
        else event.get("sl", current.get("sl"))
    )
    tp_value = (
        event.get("new_tp") if "new_tp" in event
        else event.get("tp", current.get("tp"))
    )
    if status == "observed_unattributed":
        changed_fields = set(event.get("changed_fields") or [])
        if changed_fields:
            if "sl" not in changed_fields:
                sl_value = None
            if "tp" not in changed_fields:
                tp_value = None
    if sl_value is not None:
        bucket["sl_history"].append({**base, "sl": sl_value})
    if tp_value is not None:
        bucket["tp_history"].append({**base, "tp": tp_value})


def _parse_iso(ts: str | None):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _journal_time_references(journal: dict) -> list[datetime]:
    refs: list[datetime] = []
    for ev in journal.get("timeline") or []:
        if ev.get("event") not in ("market_filled", "signal_received"):
            continue
        dt = _parse_iso(ev.get("ts"))
        if dt is not None:
            refs.append(dt)
    signal_dt = _parse_iso(journal.get("signal_dt_utc"))
    if signal_dt is not None:
        refs.append(signal_dt)
    return refs


def _infer_mt5_time_offset_s(journal: dict, mt5_positions: list[dict]) -> int | None:
    """Detecta offset horario MT5 vs journal solo si la evidencia es clara.

    En algunos terminals MT5 el deal.time llega en hora de servidor pero el
    reconciliador lo trata como UTC. Para backtesting tick-a-tick necesitamos
    el horario real del journal. No tocamos nada si no hay una diferencia casi
    exacta de horas completas contra market_filled/signal_received.
    """
    raw_opens = [
        _parse_iso(p.get("open_dt_utc"))
        for p in mt5_positions
        if p.get("open_dt_utc")
    ]
    raw_opens = [dt for dt in raw_opens if dt is not None]
    refs = _journal_time_references(journal)
    if not raw_opens or not refs:
        return None

    first_open = min(raw_opens)
    best: tuple[float, int] | None = None
    for ref in refs:
        delta_s = (first_open - ref).total_seconds()
        if delta_s < 1800:
            continue
        hours = round(delta_s / 3600)
        if hours < 1 or hours > MT5_TIME_OFFSET_MAX_HOURS:
            continue
        offset_s = hours * 3600
        residual = abs(delta_s - offset_s)
        if residual > MT5_TIME_OFFSET_TOLERANCE_S:
            continue
        if best is None or residual < best[0]:
            best = (residual, offset_s)
    return best[1] if best else None


def _shift_iso_utc(ts: str | None, offset_s: int) -> str | None:
    dt = _parse_iso(ts)
    if dt is None:
        return ts
    shifted = (dt - timedelta(seconds=offset_s)).astimezone(timezone.utc)
    return shifted.isoformat(timespec="seconds")


def _normalize_mt5_position_times(journal: dict,
                                  mt5_positions: list[dict]) -> tuple[list[dict], int | None]:
    offset_s = _infer_mt5_time_offset_s(journal, mt5_positions)
    normalized = [dict(p) for p in mt5_positions]
    if offset_s is None:
        return normalized, None

    for pos in normalized:
        if pos.get("open_dt_utc"):
            pos["open_dt_mt5_raw"] = pos["open_dt_utc"]
            pos["open_dt_utc"] = _shift_iso_utc(pos["open_dt_utc"], offset_s)
        if pos.get("close_dt_utc"):
            pos["close_dt_mt5_raw"] = pos["close_dt_utc"]
            pos["close_dt_utc"] = _shift_iso_utc(pos["close_dt_utc"], offset_s)
        pos["mt5_time_offset_s"] = offset_s
    return normalized, offset_s


def _derive_post_time_stop_outcome(journal: dict, positions: list[dict],
                                   status: str) -> str | None:
    timeline = journal.get("timeline") or []
    ts_events = [e for e in timeline if e.get("event") == "time_stop_notified"]
    if not ts_events:
        return None
    ts = _parse_iso(ts_events[-1].get("ts"))
    if status in ("open", "partial"):
        return "open_after_time_stop"
    if status == "no_position":
        return "no_position_after_time_stop"

    closed_after = []
    for p in positions:
        close_dt = _parse_iso(p.get("close_dt_utc"))
        if ts is None or close_dt is None or close_dt >= ts:
            closed_after.append(p)
    if not closed_after:
        return "closed_before_time_stop_reconcile_gap"
    reasons = {p.get("close_reason") for p in closed_after}
    pnl = sum(p.get("pnl_net") or 0 for p in closed_after)
    if reasons == {"sl"}:
        return "sl_after_time_stop"
    if any(r == "bot_close" for r in reasons):
        return "manual_or_bot_close_after_time_stop"
    if pnl > 0:
        return "profit_after_time_stop"
    if pnl < 0:
        return "loss_after_time_stop"
    return "flat_after_time_stop"


def _latest_confirmed_level(history: list[dict], field: str):
    for item in reversed(history or []):
        if item.get("status") not in ("confirmed", "snapshot"):
            continue
        if item.get(field) is not None:
            return item.get(field)
    return None


def _is_canal1_range_only_edit_anomaly(anomaly: dict) -> bool:
    if anomaly.get("category") != "channel_msg":
        return False
    if anomaly.get("sl_changed") is not False:
        return False
    if anomaly.get("tps_changed") is not False:
        return False
    if anomaly.get("direction_changed") is not False:
        return False
    detail = str(anomaly.get("detail") or "").lower()
    if "canal1" not in detail and "canal 1" not in detail:
        return False
    previous = anomaly.get("previous") or {}
    new = anomaly.get("new") or {}
    return bool(previous.get("range_low") is not None
                or previous.get("range_high") is not None
                or new.get("range") is not None)


def _normalize_anomalies_for_analysis(anomalies: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for anomaly in anomalies:
        item = dict(anomaly)
        if _is_canal1_range_only_edit_anomaly(item):
            item["severity"] = "info"
            item["code"] = "canal1_range_only_edit"
            item["detail"] = (
                "canal1 editó solo el rango de entrada; SL/TP/dirección "
                "sin cambios, no requiere acción sobre MT5"
            )
        normalized.append(item)
    return normalized


def _same_level_or_none(values: list):
    values = [v for v in values if v is not None]
    if not values:
        return None
    first = values[0]
    if all(v == first for v in values):
        return first
    return None


def _derive_effective_levels(journal: dict, positions: list[dict]) -> tuple:
    source = {"sl": None, "tps": None}
    if journal.get("sl") is not None:
        effective_sl = journal.get("sl")
        source["sl"] = "journal"
    else:
        effective_sl = _same_level_or_none([
            _latest_confirmed_level(p.get("sl_history"), "sl")
            for p in positions
        ])
        source["sl"] = "mt5_confirmed" if effective_sl is not None else None

    if journal.get("tps"):
        effective_tps = list(journal.get("tps"))
        source["tps"] = "journal"
    else:
        effective_tps = [
            _latest_confirmed_level(p.get("tp_history"), "tp")
            for p in positions
        ]
        effective_tps = [tp for tp in effective_tps if tp is not None]
        if not effective_tps:
            effective_tps = None
        source["tps"] = "mt5_confirmed" if effective_tps else None

    return effective_sl, effective_tps, source


def _analysis_exclusions(journal: dict) -> list[dict]:
    """Operational incidents that should not count as strategy edge."""
    exclusions = []
    for event in journal.get("order_lifecycle") or []:
        retcode = event.get("retcode", event.get("last_retcode"))
        if retcode == 10027:
            exclusions.append({
                "code": "mt5_client_autotrading_disabled",
                "retcode": retcode,
                "ts": event.get("ts"),
                "source_event": event.get("ev"),
                "detail": event.get("comment") or event.get("detail"),
            })
            break
    return exclusions


def _with_forensic_position_history(positions: list[dict],
                                    journal: dict) -> list[dict]:
    histories = journal.get("ticket_level_history") or {}
    out = []
    for p in positions:
        row = dict(p)
        keys = [
            _ticket_key(row.get("position_id")),
            _ticket_key(row.get("ticket")),
        ]
        hist = None
        for key in [k for k in keys if k]:
            hist = histories.get(key)
            if hist is None and key.isdigit():
                hist = histories.get(int(key))
            if hist is not None:
                break
        hist = hist or {}
        row["sl_history"] = hist.get("sl_history", row.get("sl_history", []))
        row["tp_history"] = hist.get("tp_history", row.get("tp_history", []))
        out.append(row)
    return out


# ─── Carga del journal ─────────────────────────────────────────────────────────

def _attach_bot_version(index: dict, sessions: list[dict]):
    """Asocia cada signal con la sesión del bot que estaba corriendo al
    recibirla. Para cada sig en el index, busca el `session_started` cuyo
    `started_utc <= signal_dt_utc < next_session_started`. Mutate-in-place:
    pone `d["bot_version"] = {...}` cuando hay match.

    Permite slice de métricas por versión del código: 'tras el deploy X
    mejoró Y%'. Si no hay sesiones (journal viejo), no-op.
    """
    if not sessions:
        return
    sessions_sorted = sorted(sessions, key=lambda s: s.get("started_utc") or "")
    for sid, d in index.items():
        sig_ts = d.get("signal_dt_utc") or ""
        if not sig_ts:
            continue
        # Última sesión iniciada en o antes del momento del signal.
        match = None
        for s in sessions_sorted:
            if (s.get("started_utc") or "") <= sig_ts:
                match = s
            else:
                break
        if match:
            d["bot_version"] = {
                "git_commit": match.get("git_commit"),
                "git_branch": match.get("git_branch"),
                "git_dirty": match.get("git_dirty"),
                "session_started_utc": match.get("started_utc"),
            }


def load_journal_index(path: Path) -> dict:
    """Indexa el journal por sig_id. Devuelve {sig_id: {...campos...}}.

    Extrae lo necesario para reconciliar: metadata de la senal, niveles,
    tp_hits, signal_closed, anomalias categorizadas, entry_quality del
    layered_decision, market_context al entrar, y bot_version de la
    sesion que corria.
    """
    index = {}
    sessions = []  # session_started events (sig='bot'): para _attach_bot_version
    if not path.exists():
        return index

    # T6: eventos clave que entran al `timeline` de cada trade.
    # Batch C adds: time_stop_notified + edits/deletes + scale_out legs.
    # Sin estos faltaba contexto operacional importante en el ledger.
    _TIMELINE_EVS = {
        "signal_received", "market_filled", "range_arrived", "tp_hit",
        "signal_closed", "naked_signal_detected", "mt5_action_failed",
        "canal1_parser_incomplete", "canal1_text_processed_but_naked",
        "anomaly",
        # Batch C — eventos operacionales que cuentan la historia del trade
        "time_stop_notified",
        "channel_msg_deleted",
        "canal1_text_edited",
        "scale_out_leg_filled",
        "scale_out_leg_fill_failed",
        "strategy_snapshot",
        "mt5_order_requested",
        "mt5_order_result",
        "mt5_modify_requested",
        "mt5_modify_confirmed",
        "mt5_close_requested",
        "mt5_close_result",
        "mt5_cancel_requested",
        "mt5_cancel_result",
        "mt5_position_snapshot",
        "mt5_level_change_unattributed",
    }

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        # session_started va fuera del filtro de sigs canal (sig='bot').
        # Lo recogemos aparte para luego asociarlo a cada signal por tiempo.
        if e.get("ev") == "session_started":
            sessions.append({
                "started_utc": e.get("started_utc"),
                "git_commit": e.get("git_commit"),
                "git_branch": e.get("git_branch"),
                "git_dirty": e.get("git_dirty"),
            })
            continue
        sid = e.get("sig")
        ev = e.get("ev")
        if not sid or not sid.startswith("canal") or not ev:
            continue

        d = index.setdefault(sid, {
            "sig_id": sid,
            "channel": sid.split("_")[0],
            "direction": None,
            "signal_dt_utc": None,
            "range": None,
            "tps": None,
            "sl": None,
            "tp_hit_indices": set(),
            "journal_tag": None,
            "journal_total_pl": None,
            "has_signal_closed": False,
            "n_resyncs": 0,
            # Conteo de posiciones que el JOURNAL dice que tuvo la senal.
            # Sirve para detectar cuando MT5 (por comment) identifica menos
            # → senal vieja con DCAs de formato viejo no reconciliables.
            "n_market_filled": 0,
            "n_market_b_filled": 0,
            "n_dca_filled": 0,
            "n_scale_out_legs": 0,
            # ── T5: enriquecimiento del expediente por trade ──
            "anomalies": [],          # rollup de ev='anomaly' por sig
            "entry_quality": None,    # del layered_decision
            "market_context": None,   # del market_context event
            "bot_version": None,      # via _attach_bot_version
            # ── T6: expediente completo (signal, gestión, cronología) ──
            "signal_text": None,      # raw_text del signal_received (canal2)
                                      # o text_preview del canal1_text_processing
            "management": [],         # cada mgmt_msg con clasificación + applied
            "timeline": [],           # hitos clave del trade
            "ticket_level_history": {},
            "strategy_snapshot": None,
            "order_lifecycle": [],
        })

        if ev == "signal_received":
            d["direction"] = e.get("direction")
            d["signal_dt_utc"] = e.get("ts")
            # T6: signal_text — canal2 trae raw_text; canal1 lo rellena
            # más abajo desde canal1_text_processing (fallback).
            if e.get("raw_text") and not d.get("signal_text"):
                d["signal_text"] = e.get("raw_text")
        elif ev == "market_filled":
            d["n_market_filled"] += 1
        elif ev == "market_b_filled":
            d["n_market_b_filled"] += 1
        elif ev == "dca_filled":
            d["n_dca_filled"] += 1
        elif ev == "scale_out_leg_filled":
            d["n_scale_out_legs"] += 1
        elif ev == "range_arrived":
            lo, hi = e.get("range_low"), e.get("range_high")
            if lo is not None and hi is not None:
                d["range"] = [lo, hi]
        elif ev in ("tps_updated", "tps_arrived"):
            if e.get("tps"):
                d["tps"] = list(e["tps"])
        elif ev in ("sl_updated", "sl_arrived"):
            if e.get("sl") is not None:
                d["sl"] = e.get("sl")
        elif ev == "tp_hit":
            ti = e.get("tp_index")
            if ti is not None:
                d["tp_hit_indices"].add(ti)
        elif ev == "signal_closed":
            d["has_signal_closed"] = True
            d["journal_tag"] = e.get("tag")
            d["journal_total_pl"] = e.get("total_pl")
        elif ev == "signal_resync_from_mt5":
            d["n_resyncs"] += 1
        # ── T5: capa de anomalías + contexto al entrar ──
        elif ev == "anomaly":
            d["anomalies"].append({
                "ts": e.get("ts"),
                "category": e.get("category"),
                "severity": e.get("severity"),
                "detail": e.get("detail"),
                # ctx adicional (ticket, retcode, …) preservado tal cual
                **{k: v for k, v in e.items()
                   if k not in ("ts", "sig", "ev", "category", "severity",
                                "detail")},
            })
        elif ev == "layered_decision":
            d["entry_quality"] = {
                "case": e.get("case"),
                "entry": e.get("entry"),
                "range_low": e.get("range_low"),
                "range_high": e.get("range_high"),
                "action_planned": e.get("action_planned"),
            }
        elif ev == "market_context":
            d["market_context"] = {
                "atr_m5_14": e.get("atr_m5_14"),
                "recent_5m_range": e.get("recent_5m_range"),
                "current_price_at_signal": e.get("current_price_at_signal"),
            }
        # ── T6: expediente completo por trade ──
        elif ev == "canal1_text_processing":
            # Fallback de signal_text para canal1 (cuyo signal_received
            # no trae raw_text — solo trigger/sticker_id).
            if not d.get("signal_text"):
                d["signal_text"] = e.get("text_preview")
        elif ev == "mgmt_msg":
            d["management"].append({
                "ts": e.get("ts"),
                "raw_text": e.get("raw_snippet"),
                "classified": e.get("action"),
                "confidence": e.get("confidence"),
                "applied": e.get("will_apply", False),
                "skip_reason": ("ambiguous_notified"
                                if e.get("ambiguous_notified") else None),
            })

        # T6: timeline — eventos clave del trade (OUT del elif chain,
        # se aplica a TODO evento que matchee, incluyendo los ya manejados
        # por las ramas anteriores).
        if ev == "strategy_snapshot":
            d["strategy_snapshot"] = {
                k: v for k, v in e.items()
                if k not in ("ts", "sig", "ev")
            }
        elif ev == "mt5_modify_requested":
            _append_level_history(d, e, "requested")
        elif ev == "mt5_modify_confirmed":
            _append_level_history(d, e, "confirmed")
        elif ev == "mt5_action_failed":
            _append_level_history(d, e, "failed")
        elif ev == "mt5_position_snapshot":
            _append_level_history(d, e, "snapshot")
        elif ev == "mt5_level_change_unattributed":
            _append_level_history(d, e, "observed_unattributed")

        if ev.startswith("mt5_"):
            d["order_lifecycle"].append({
                k: v for k, v in e.items()
                if k not in ("sig",)
            })

        if ev in _TIMELINE_EVS:
            d["timeline"].append({"ts": e.get("ts"), "event": ev})

    # Asocia cada sig con la sesion (session_started) que estaba corriendo
    # al recibirlo → cada fila del ledger lleva su bot_version.
    _attach_bot_version(index, sessions)

    # Solo devolvemos sig_ids que tuvieron signal_received — son senales
    # REALES que el bot intento operar. Los demas sig_ids del journal son
    # mensajes de gestion, msg_dropped, handler_entry de no-senales, etc.
    return {s: d for s, d in index.items() if d.get("signal_dt_utc")}


# ─── Carga de posiciones MT5 ───────────────────────────────────────────────────

def _fetch_deals_synced(t_from: datetime, t_to: datetime,
                        expected_sigs: set | None, *, quiet: bool = False):
    """history_deals_get esperando a que el terminal MT5 acabe el sync.

    PROBLEMA (bug observado 2026-05-18)
    ───────────────────────────────────
    Tras mt5.initialize() el terminal sigue bajando del servidor el
    historial reciente de forma ASINCRONA. history_deals_get consultado de
    inmediato devuelve un historial INCOMPLETO: faltan los deals de hoy y
    sus senales se reconcilian como 'no_position' → ledger erroneo. Ese dia
    el watcher reconcilio las 20 senales de la sesion como sin posicion;
    re-ejecutar reconcile (con MT5 ya sincronizado) lo arreglo.

    SOLUCION
    ────────
    Se reintenta history_deals_get hasta que se cumplen DOS condiciones:
      1. Oraculo — todas las senales que el JOURNAL dice que rellenaron
         posicion recientemente aparecen ya en el historial. El journal es
         un fichero local, siempre actualizado; MT5 es el lado que llega
         tarde. Si el journal conoce un fill que MT5 no, falta sync.
      2. Estabilidad — el conteo de deals no crece entre dos lecturas
         (cubre el caso raro de un deal de cierre aun en vuelo, sin
         oraculo si no hay senales recientes que esperar).
    Si se agotan los reintentos se reconcilia con lo que haya y se avisa
    fuerte: mejor un ledger con aviso que un ledger silenciosamente falso.
    """
    RETRIES, PAUSE = 12, 2.5
    expected = expected_sigs or set()
    deals = mt5.history_deals_get(t_from, t_to) or ()
    prev_count = None
    for attempt in range(RETRIES):
        present = {parse_sig_role(d.comment)[0] for d in deals}
        present.discard(None)
        missing = expected - present
        stable = prev_count is not None and len(deals) == prev_count
        if not missing and stable:
            if not quiet and attempt >= 2:
                print(f"  MT5: historial sincronizado tras "
                      f"~{attempt * PAUSE:.0f}s ({len(deals)} deals)")
            return deals
        prev_count = len(deals)
        time.sleep(PAUSE)
        deals = mt5.history_deals_get(t_from, t_to) or ()
    if not quiet:
        still = expected - {parse_sig_role(d.comment)[0] for d in deals}
        still.discard(None)
        extra = (f"; {len(still)} senal(es) del journal sin aparecer en MT5"
                 if still else "")
        print(f"  ⚠ MT5: sync NO confirmado tras {RETRIES * PAUSE:.0f}s — "
              f"se reconcilia con {len(deals)} deals disponibles{extra}")
    return deals


def _deal_money(deal, field: str) -> float:
    return float(getattr(deal, field, 0.0) or 0.0)


def _deal_time_utc(deal) -> str | None:
    ts = getattr(deal, "time", None)
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(
        timespec="seconds")


def _deal_payload(deal) -> dict | None:
    if deal is None:
        return None
    return {
        "ticket": getattr(deal, "ticket", None),
        "order": getattr(deal, "order", None),
        "position_id": getattr(deal, "position_id", None),
        "entry": getattr(deal, "entry", None),
        "type": getattr(deal, "type", None),
        "time": getattr(deal, "time", None),
        "time_msc": getattr(deal, "time_msc", None),
        "time_utc": _deal_time_utc(deal),
        "symbol": getattr(deal, "symbol", None),
        "magic": getattr(deal, "magic", None),
        "price": getattr(deal, "price", None),
        "volume": getattr(deal, "volume", None),
        "profit": round(_deal_money(deal, "profit"), 2),
        "swap": round(_deal_money(deal, "swap"), 2),
        "commission": round(_deal_money(deal, "commission"), 2),
        "fee": round(_deal_money(deal, "fee"), 2),
        "comment": getattr(deal, "comment", None),
    }


def _pnl_components(deals) -> dict:
    profit = sum(_deal_money(d, "profit") for d in deals)
    swap = sum(_deal_money(d, "swap") for d in deals)
    commission = sum(_deal_money(d, "commission") for d in deals)
    fee = sum(_deal_money(d, "fee") for d in deals)
    net = profit + swap + commission + fee
    return {
        "profit": round(profit, 2),
        "swap": round(swap, 2),
        "commission": round(commission, 2),
        "fee": round(fee, 2),
        "net": round(net, 2),
    }


def load_mt5_positions(t_from: datetime, t_to: datetime,
                       expected_sigs: set | None = None, *,
                       quiet: bool = False) -> dict:
    """Agrupa el historial de deals MT5 por sig_id.

    Devuelve {sig_id: [position_dict, ...]} donde cada position_dict tiene
    el P&L real (profit+swap+commission+fee) y metadata de apertura/cierre.

    Una "posicion" = todos los deals que comparten position_id (apertura
    DEAL_ENTRY_IN entry=0 + cierre DEAL_ENTRY_OUT entry=1, o bien
    DEAL_ENTRY_OUT_BY entry=3 cuando se cierra "by" una posicion opuesta).

    `expected_sigs`: sig_ids que el journal dice que rellenaron posicion
    recientemente. Si se pasa, se espera a que el terminal MT5 termine de
    sincronizar el historial antes de fiarse de el (ver _fetch_deals_synced).
    """
    deals = _fetch_deals_synced(t_from, t_to, expected_sigs, quiet=quiet)

    # ── Tripwire de tipos de deal ──────────────────────────────────────
    # El bug de entry=3 se colo porque el codigo asumia "cierre = entry==1"
    # y NADIE comprobaba si MT5 devolvia otros tipos. Aqui se verifica que
    # todo deal sea de un tipo que esta funcion maneja explicitamente:
    #   0 = DEAL_ENTRY_IN     (apertura)
    #   1 = DEAL_ENTRY_OUT    (cierre normal)
    #   3 = DEAL_ENTRY_OUT_BY (cierre "by" una posicion opuesta)
    # Si aparece otro (2 = INOUT/reverse, o lo que sea), se AVISA fuerte:
    # el P&L de esas posiciones no seria fiable y habria que ampliar este
    # codigo. Asi un caso nuevo nunca vuelve a perderse en silencio.
    _HANDLED_ENTRIES = {0, 1, 3}
    _unknown = sorted({d.entry for d in deals} - _HANDLED_ENTRIES)
    if _unknown:
        print(f"\n{'!' * 70}\n"
              f"!! RECONCILIADOR: MT5 devolvio deals con entry {_unknown} "
              f"NO manejados.\n"
              f"!! El P&L de esas posiciones NO es fiable. "
              f"Ampliar load_mt5_positions().\n"
              f"{'!' * 70}\n")

    # Agrupar deals por position_id
    by_pos = defaultdict(list)
    for d in deals:
        by_pos[d.position_id].append(d)

    result = defaultdict(list)

    for pos_id, dl in by_pos.items():
        dl_sorted = sorted(dl, key=lambda d: d.time_msc)
        open_deal = next((d for d in dl_sorted if d.entry == 0), None)
        # Cierre = entry=1 (DEAL_ENTRY_OUT, cierre normal) o entry=3
        # (DEAL_ENTRY_OUT_BY, cierre "by" una posicion opuesta — hedging).
        # Sin el 3 las posiciones cerradas por close-by se tratarian como
        # abiertas y su P&L no entraria en pnl_real_mt5.
        close_deal = next((d for d in dl_sorted if d.entry in (1, 3)), None)

        # Identificar sig_id y role: el deal de apertura lleva el comment util.
        # SOLO por comment con sig_id explicito — NO heuristica de proximidad.
        # Los DCAs de formato viejo ("DCA_4572.0", sin sig_id) NO se pueden
        # asociar de forma fiable: el dato no existe en MT5. reconcile_signal
        # detecta esos casos comparando con el conteo del journal y marca la
        # fila como no-reconciliable-al-100% en vez de adivinar.
        sig_id = role = None
        for d in dl_sorted:
            s, r = parse_sig_role(d.comment)
            if s:
                sig_id, role = s, r
                break
        if not sig_id:
            continue  # posicion sin sig_id identificable — se ignora

        # P&L neto = profit + swap + commission + fee de TODOS los deals.
        components = _pnl_components(dl_sorted)
        pnl_net = components["net"]

        result[sig_id].append({
            "position_id": pos_id,
            "ticket": open_deal.ticket if open_deal else dl_sorted[0].ticket,
            "role": role,
            "open_price": open_deal.price if open_deal else None,
            "open_dt_utc": (datetime.fromtimestamp(open_deal.time, tz=timezone.utc)
                            .isoformat(timespec="seconds")) if open_deal else None,
            "close_price": close_deal.price if close_deal else None,
            "close_dt_utc": (datetime.fromtimestamp(close_deal.time, tz=timezone.utc)
                             .isoformat(timespec="seconds")) if close_deal else None,
            "close_reason": (close_reason_from_comment(close_deal.comment)
                             if close_deal else None),
            "is_closed": close_deal is not None,
            "pnl_net": pnl_net,
            "pnl_components": components,
            "open_deal": _deal_payload(open_deal),
            "close_deal": _deal_payload(close_deal),
            "deals": [_deal_payload(d) for d in dl_sorted],
            "volume": open_deal.volume if open_deal else None,
            # T6: cronología por leg — v1 mínima (vacíos). La reconstrucción
            # detallada desde events sl_by_ticket queda como follow-up: el
            # campo está aquí ya para que consumidores cuenten con la forma.
            "sl_history": [],
            "tp_history": [],
        })

    return dict(result)


# ─── Reconciliacion de una senal ──────────────────────────────────────────────

def reconcile_signal(sig_id: str, journal: dict | None,
                     mt5_positions: list | None) -> dict:
    """Produce una fila del ledger cruzando journal y MT5 para una senal."""
    journal = journal or {}
    mt5_positions, mt5_time_offset_s = _normalize_mt5_position_times(
        journal, mt5_positions or [])

    flags = []

    # ── Estado de las posiciones ──
    n_pos = len(mt5_positions)
    n_closed = sum(1 for p in mt5_positions if p["is_closed"])
    n_open = n_pos - n_closed

    if n_pos == 0:
        status = "no_position"   # senal recibida pero nunca abrio en MT5
    elif n_open == 0:
        status = "closed"
    elif n_closed == 0:
        status = "open"
    else:
        status = "partial"      # algunas cerradas, otras abiertas

    # ── ¿MT5 capturo TODAS las posiciones que el journal registro? ──
    # Si MT5 identifica MENOS posiciones que las que el journal conto, el
    # pnl_real_mt5 es PARCIAL — no es la verdad completa. Causas posibles:
    #   • senal vieja con DCAs de formato viejo ("DCA_4572.0" sin sig_id)
    #   • una leg del scale_out que no llego a MT5 o con comment corrupto
    # Se marca y NO se usa para cuadrar la contabilidad.
    n_pos_journal = (journal.get("n_market_filled", 0)
                     + journal.get("n_market_b_filled", 0)
                     + journal.get("n_dca_filled", 0)
                     + journal.get("n_scale_out_legs", 0))
    pnl_mt5_complete = True
    if n_pos_journal > 0 and n_pos < n_pos_journal:
        pnl_mt5_complete = False
        flags.append(f"PNL_PARCIAL_mt5_identifico_{n_pos}_de_{n_pos_journal}_pos"
                      f" (posiciones del journal no halladas en MT5)")

    # ── P&L: verdad (MT5) vs lo que el bot creyo ──
    pnl_real = round(sum(p["pnl_net"] for p in mt5_positions if p["is_closed"]), 2)
    pnl_journal = journal.get("journal_total_pl")

    pnl_discrepancy = None
    reconciled_ok = None
    # Solo evaluamos discrepancia si el P&L de MT5 es COMPLETO (fiable).
    if pnl_journal is not None and status == "closed" and pnl_mt5_complete:
        pnl_discrepancy = round(pnl_real - pnl_journal, 2)
        reconciled_ok = abs(pnl_discrepancy) < RECONCILE_TOLERANCE_USD
        if not reconciled_ok:
            flags.append(f"PNL_DISCREPANCY_{pnl_discrepancy:+.2f}")

    # ── Deteccion de anomalias ──
    if journal and not journal.get("has_signal_closed") and status == "closed":
        flags.append("HUERFANO_journal_sin_signal_closed")
    journal_closed_with_mt5_open = (
        bool(journal)
        and bool(journal.get("has_signal_closed"))
        and status in ("open", "partial")
    )
    if journal_closed_with_mt5_open:
        flags.append("journal_cerro_pero_MT5_tiene_pos_abierta")
    if not journal and mt5_positions:
        flags.append("posicion_MT5_sin_senal_en_journal")
    if journal.get("n_resyncs", 0) > 0:
        flags.append(f"vivio_{journal['n_resyncs']}_resync(s)")
    # Market B perdido: SOLO es anomalia si el JOURNAL registro un
    # market_b_filled pero MT5 no lo encuentra. Las senales anteriores al
    # doble market (14-may) no tienen Market B y eso es normal — no flag.
    roles = {p["role"] for p in mt5_positions}
    if journal.get("n_market_b_filled", 0) > 0 and "market_b" not in roles:
        flags.append("MARKET_B_PERDIDO_journal_lo_registro_MT5_no")

    analysis_exclusions = _analysis_exclusions(journal)
    if any(e.get("code") == "mt5_client_autotrading_disabled"
           for e in analysis_exclusions):
        flags.append("MT5_AUTOTRADING_DISABLED_excluir_de_metricas_strategy")

    # ── TP maximo tocado (del journal tp_hit) ──
    tp_hits = journal.get("tp_hit_indices") or set()
    max_tp_touched = max(tp_hits) if tp_hits else None

    # ── Timestamps ──
    open_dts = [p["open_dt_utc"] for p in mt5_positions if p["open_dt_utc"]]
    close_dts = [p["close_dt_utc"] for p in mt5_positions if p["close_dt_utc"]]
    open_dt = min(open_dts) if open_dts else None
    close_dt = max(close_dts) if close_dts else None
    duration_min = None
    if open_dt and close_dt:
        o = _parse_iso(open_dt)
        c = _parse_iso(close_dt)
        if o is not None and c is not None:
            duration_min = round((c - o).total_seconds() / 60, 1)

    positions_for_ledger = _with_forensic_position_history(mt5_positions, journal)
    effective_sl, effective_tps, effective_levels_source = _derive_effective_levels(
        journal, positions_for_ledger)
    post_time_stop_outcome = _derive_post_time_stop_outcome(
        journal, positions_for_ledger, status)
    anomalies = _normalize_anomalies_for_analysis(
        list(journal.get("anomalies", [])))
    if journal_closed_with_mt5_open:
        anomalies.append({
            "ts": close_dt,
            "category": "outcome",
            "severity": "warning",
            "detail": "journal marked signal closed while MT5 still had open positions",
            "derived": True,
            "code": "journal_closed_with_mt5_open_position",
            "n_open": n_open,
            "status": status,
        })
    if post_time_stop_outcome == "sl_after_time_stop":
        anomalies.append({
            "ts": close_dt,
            "category": "outcome",
            "severity": "warning",
            "detail": "trade closed at SL after notify-only time-stop fired",
            "derived": True,
            "outcome": post_time_stop_outcome,
        })

    return {
        "sig_id": sig_id,
        "channel": journal.get("channel") or sig_id.split("_")[0],
        "direction": journal.get("direction"),
        "signal_dt_utc": journal.get("signal_dt_utc"),
        "open_dt_utc": open_dt,
        "close_dt_utc": close_dt,
        "mt5_time_offset_s": mt5_time_offset_s,
        "duration_min": duration_min,
        "status": status,
        # Posiciones reales de MT5
        "n_positions": n_pos,
        "n_closed": n_closed,
        "n_open": n_open,
        "positions": positions_for_ledger,
        # P&L: verdad vs diario
        "pnl_real_mt5": pnl_real,
        "pnl_journal": pnl_journal,
        "pnl_discrepancy": pnl_discrepancy,
        "reconciled_ok": reconciled_ok,
        "pnl_mt5_complete": pnl_mt5_complete,   # False = DCAs viejos sin asociar
        "n_positions_journal": n_pos_journal,
        # Niveles
        "range": journal.get("range"),
        "tps": journal.get("tps"),
        "sl": journal.get("sl"),
        "effective_tps": effective_tps,
        "effective_sl": effective_sl,
        "effective_levels_source": effective_levels_source,
        "max_tp_idx_touched": max_tp_touched,
        # Estado del journal
        "journal_tag": journal.get("journal_tag"),
        "journal_has_signal_closed": journal.get("has_signal_closed", False),
        # Anomalias detectadas (legacy flags + capa estructurada T5)
        "flags": flags,
        "anomalies": anomalies,
        "health": health_verdict(anomalies),
        "analysis_excluded": bool(analysis_exclusions),
        "analysis_exclusions": analysis_exclusions,
        "post_time_stop_outcome": post_time_stop_outcome,
        "entry_quality": journal.get("entry_quality"),
        "market_context": journal.get("market_context"),
        "bot_version": journal.get("bot_version"),
        "strategy_snapshot": journal.get("strategy_snapshot"),
        "order_lifecycle": journal.get("order_lifecycle", []),
        # Expediente completo por trade (T6)
        "signal_text": journal.get("signal_text"),
        "management": journal.get("management", []),
        "timeline": journal.get("timeline", []),
        "reconciled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ─── Orquestacion ─────────────────────────────────────────────────────────────

def main():
    quiet = "--quiet" in sys.argv
    since = None
    for i, a in enumerate(sys.argv):
        if a == "--since" and i + 1 < len(sys.argv):
            since = sys.argv[i + 1]

    if not quiet:
        print("=" * 70)
        print("  RECONCILIADOR — journal del bot + MT5 → ledger de verdad")
        print("=" * 70)

    # 1. Cargar journal
    journal_index = load_journal_index(JOURNAL_FILE)
    if not quiet:
        print(f"\nJournal: {len(journal_index)} senales indexadas")

    # 2. Rango de fechas para MT5: desde el primer evento del journal
    if since:
        t_from = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    else:
        all_dts = [d["signal_dt_utc"] for d in journal_index.values()
                   if d.get("signal_dt_utc")]
        if all_dts:
            earliest = min(all_dts)
            t_from = (datetime.fromisoformat(earliest.replace("Z", "+00:00"))
                      - timedelta(days=1))
        else:
            t_from = datetime.now(timezone.utc) - timedelta(days=30)
    t_to = _mt5_history_query_end()

    # Senales que el journal dice que rellenaron posicion en las ultimas 48h.
    # Son las unicas sujetas a la race de sincronizacion del historial MT5:
    # los deals mas viejos ya estan en la cache local del terminal. El
    # sync-wait de load_mt5_positions espera a que TODAS aparezcan en MT5.
    _recent = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
    expected_sigs = {
        sid for sid, d in journal_index.items()
        if (d.get("signal_dt_utc") or "")[:10] >= _recent
        and (d.get("n_market_filled", 0) + d.get("n_market_b_filled", 0)
             + d.get("n_dca_filled", 0) + d.get("n_scale_out_legs", 0)) > 0
    }
    if not quiet and expected_sigs:
        print(f"Senales recientes con posicion (journal): {len(expected_sigs)} "
              f"→ se esperara el sync de MT5 antes de reconciliar")

    # 3. Cargar MT5
    if not mt5.initialize():
        print(f"ERROR MT5 init: {mt5.last_error()}")
        sys.exit(1)
    try:
        mt5_positions = load_mt5_positions(t_from, t_to, expected_sigs,
                                           quiet=quiet)
    finally:
        mt5.shutdown()
    if not quiet:
        n_pos_total = sum(len(v) for v in mt5_positions.values())
        print(f"MT5: {len(mt5_positions)} senales con posiciones "
              f"({n_pos_total} posiciones, desde {t_from.date()})")

    # 4. Reconciliar.
    # El ledger refleja lo que el bot OPERO conscientemente → su base son
    # las senales del JOURNAL. Para cada una se cruza con MT5.
    # Las posiciones MT5 cuyo sig_id NO esta en el journal se reportan
    # aparte (pueden ser huerfanos reales del bot o ruido de pruebas
    # viejas) — no ensucian el ledger salvo que tengan formato plausible.
    journal_sigs = set(journal_index)
    mt5_sigs = set(mt5_positions)
    unmatched_mt5 = sorted(mt5_sigs - journal_sigs)

    all_sigs = sorted(journal_sigs,
                      key=lambda s: journal_index.get(s, {}).get("signal_dt_utc")
                      or "z")
    rows = []
    for sid in all_sigs:
        jd = journal_index.get(sid)
        if since and jd and jd.get("signal_dt_utc"):
            if jd["signal_dt_utc"] < since:
                continue
        row = reconcile_signal(sid, jd, mt5_positions.get(sid))
        rows.append(row)

    if not quiet and unmatched_mt5:
        print(f"\n⚠ {len(unmatched_mt5)} sig_ids con posiciones en MT5 SIN "
              f"senal en el journal (no entran al ledger):")
        print(f"  {unmatched_mt5[:15]}{' ...' if len(unmatched_mt5) > 15 else ''}")
        print(f"  → probable ruido de pruebas viejas o comments de otro formato.")

    # 5. Escribir ledger (regenera entero — idempotente)
    LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_FILE, "w", encoding="utf-8") as f:
        for row in rows:
            # set() no es JSON-serializable — ya convertido en reconcile_signal
            f.write(json.dumps(row, default=str) + "\n")

    if not quiet:
        _print_summary(rows)
    print(f"\n>>> Ledger escrito: {LEDGER_FILE} ({len(rows)} trades)")
    return rows


def _print_summary(rows):
    closed = [r for r in rows if r["status"] == "closed"]
    open_r = [r for r in rows if r["status"] == "open"]
    partial = [r for r in rows if r["status"] == "partial"]
    no_pos = [r for r in rows if r["status"] == "no_position"]

    # Separar cerrados fiables (P&L MT5 completo) de los de formato viejo
    closed_fiables = [r for r in closed if r["pnl_mt5_complete"]]
    closed_parciales = [r for r in closed if not r["pnl_mt5_complete"]]

    print(f"\n{'─'*70}")
    print(f"RESUMEN")
    print(f"{'─'*70}")
    print(f"  Trades cerrados:   {len(closed)}  "
          f"({len(closed_fiables)} reconciliables, "
          f"{len(closed_parciales)} formato viejo parcial)")
    print(f"  Abiertos:          {len(open_r)}")
    print(f"  Parciales:         {len(partial)}")
    print(f"  Sin posicion:      {len(no_pos)}")

    pnl_fiable = sum(r["pnl_real_mt5"] for r in closed_fiables)
    print(f"\n  P&L REAL (MT5) — trades reconciliables: ${pnl_fiable:+.2f}")
    if closed_parciales:
        print(f"  ({len(closed_parciales)} trades de formato viejo no entran "
              f"al total — DCAs sin sig_id, P&L MT5 incompleto)")

    # Discrepancias (solo de los fiables)
    discrep = [r for r in closed_fiables if r["reconciled_ok"] is False]
    if discrep:
        print(f"\n  ⚠ {len(discrep)} trades RECONCILIABLES con DISCREPANCIA journal vs MT5:")
        for r in discrep:
            print(f"    {r['sig_id']:22s} journal=${r['pnl_journal']:+.2f}  "
                  f"MT5=${r['pnl_real_mt5']:+.2f}  diff=${r['pnl_discrepancy']:+.2f}")
        total_disc = sum(abs(r["pnl_discrepancy"]) for r in discrep)
        print(f"    Descuadre total: ${total_disc:.2f}")
    else:
        print(f"\n  ✓ Sin discrepancias en los trades reconciliables")

    # Huerfanos
    huerfanos = [r for r in closed if not r["journal_has_signal_closed"]]
    if huerfanos:
        print(f"\n  ⚠ {len(huerfanos)} HUERFANOS (cerraron en MT5, journal no lo registro):")
        for r in huerfanos:
            tag = "" if r["pnl_mt5_complete"] else "  (P&L parcial, formato viejo)"
            print(f"    {r['sig_id']:22s} P&L real MT5 = ${r['pnl_real_mt5']:+.2f}{tag}")


if __name__ == "__main__":
    main()
