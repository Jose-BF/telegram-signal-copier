"""
Análisis de patrones del bot — herramienta para sesiones de revisión.

Uso:
    python analysis/patterns.py                    # toda la historia
    python analysis/patterns.py 2026-04-27         # solo señales del día X
    python analysis/patterns.py --from 2026-04-20  # desde fecha
    python analysis/patterns.py --csv              # exporta CSV de señales

Lee data/trade_events.jsonl y produce:

  1) Tabla por señal con TODAS las métricas relevantes
  2) Agregados por canal/dirección/hora
  3) Detección de anomalías (race condition residual, ghost signals,
     mgmt ignorados, DCAs post-TP, etc.)
  4) Distribuciones (latencias, range delays, time-to-TP, etc.)

Diseñado para ser ejecutado al final de cada sesión y servir de input
visual para identificar qué mejorar en la siguiente iteración.
"""
import argparse
import csv
import io
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# UTF-8 stdout en Windows para no romper con emojis o acentos
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import runtime_paths


EVENTS_FILE = runtime_paths.active_data_dir(ROOT) / "trade_events.jsonl"


# ─── CLI ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("date", nargs="?", default=None,
                   help="Fecha YYYY-MM-DD (default: todas)")
    p.add_argument("--from", dest="date_from",
                   help="Filtra señales desde YYYY-MM-DD inclusive")
    p.add_argument("--to", dest="date_to",
                   help="Filtra señales hasta YYYY-MM-DD inclusive")
    p.add_argument("--csv", action="store_true",
                   help="Exporta tabla por señal a analysis/patterns_signals.csv")
    p.add_argument("--channel", choices=["canal1", "canal2"], default=None,
                   help="Filtra a un solo canal (canal1 o canal2). Default: ambos.")
    p.add_argument("--file", default=str(EVENTS_FILE),
                   help=f"Path al JSONL (default: {EVENTS_FILE})")
    return p.parse_args()


# ─── Carga + agrupación ───────────────────────────────────────────────────────

def load_events(path: Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def group_by_signal(events: list[dict], date=None, date_from=None,
                    date_to=None, channel=None
                    ) -> tuple[dict[str, list[dict]], list[dict]]:
    """Devuelve (signals_dict, heartbeats_list).

    Filtra por fecha de signal_received: solo señales cuyo signal_received
    cae en el rango pedido. Esto evita el bug del cruce de día (un
    signal_closed a las 00:05 sigue contando para la sesión de ayer).

    Si channel ('canal1'|'canal2') se pasa, filtra por ese canal usando el
    prefijo del sig_id (ej: 'canal1_12345'). Heartbeats no se filtran por
    canal (son globales del bot).
    """
    target_sigs = set()
    for e in events:
        if e.get("ev") != "signal_received":
            continue
        ts = e.get("ts", "")[:10]
        if date and ts != date:
            continue
        if date_from and ts < date_from:
            continue
        if date_to and ts > date_to:
            continue
        sig = e.get("sig", "")
        if channel and not sig.startswith(channel + "_"):
            continue
        target_sigs.add(sig)

    signals = defaultdict(list)
    heartbeats = []
    for e in events:
        sig = e.get("sig", "")
        if sig == "bot":
            ts = e.get("ts", "")[:10]
            include = ((date is None or ts == date)
                       and (date_from is None or ts >= date_from)
                       and (date_to is None or ts <= date_to))
            if include:
                heartbeats.append(e)
        elif sig in target_sigs:
            signals[sig].append(e)
    return signals, heartbeats


# ─── Extracción de métricas por señal ─────────────────────────────────────────

def _ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def extract_metrics(sig_id: str, evs: list[dict]) -> dict:
    """Reduce la lista de eventos a un dict de métricas por señal."""
    by_ev = defaultdict(list)
    for e in evs:
        by_ev[e.get("ev", "")].append(e)

    first = lambda name: by_ev[name][0] if by_ev[name] else None
    last  = lambda name: by_ev[name][-1] if by_ev[name] else None

    recv  = first("signal_received")
    fill  = first("market_filled")
    rng   = first("range_arrived")
    ldec  = first("layered_decision")
    tps_a = first("tps_arrived")
    sl_a  = first("sl_arrived")
    closed = first("signal_closed")
    closures = first("positions_closed_by_mt5")  # nuevo evento detallado

    dca_filled = by_ev["dca_filled"]
    dca_skipped = by_ev["dca_skipped_proximity"]
    mgmt = by_ev["mgmt_msg"]

    # ── Latencias clave ──
    lat_fill_ms = fill.get("latency_ms") if fill else None
    range_delay_s = rng.get("delay_sec") if rng else None
    tps_arrival_s = None
    if tps_a and recv:
        t1 = _ts(recv.get("ts"))
        t2 = _ts(tps_a.get("ts"))
        if t1 and t2:
            tps_arrival_s = round((t2 - t1).total_seconds(), 1)

    # ── DCAs: detecta race condition (mismo ms y position_index) ──
    dca_keys = [(d.get("ts", "")[:23], d.get("position_index"), d.get("level"))
                for d in dca_filled]
    dca_dupes = len(dca_keys) - len(set(dca_keys))
    n_dca = len(dca_filled)
    n_dca_after_tp = 0
    # ¿Hay DCAs que se llenaron DESPUÉS del primer TP_HIT informativo?
    tp_hit_ts = None
    for m in mgmt:
        snip = (m.get("raw_snippet") or "").lower()
        if "tp" in snip and ("hit" in snip or "reached" in snip):
            tp_hit_ts = _ts(m.get("ts"))
            break
    if tp_hit_ts:
        for d in dca_filled:
            d_ts = _ts(d.get("ts"))
            if d_ts and d_ts > tp_hit_ts:
                n_dca_after_tp += 1

    # ── Mgmt: cuántos aplicados / ignorados / qué acciones ──
    mgmt_actions = Counter(m.get("action", "?") for m in mgmt)
    mgmt_applied = sum(1 for m in mgmt if m.get("will_apply"))
    mgmt_ignored = sum(
        1 for message in mgmt
        if message.get("required_execution") is True
        and not message.get("will_apply")
    )

    # ── Cierre + tag ──
    final_tag = closed.get("tag") if closed else None
    pnl = closed.get("total_pl") if closed else None
    if closures:
        # Mejor info: el desglose por ticket
        closure_tags = closures.get("summary_by_tag", {})
    else:
        closure_tags = {}

    # ── Tiempos derivados ──
    t_recv = _ts(recv.get("ts")) if recv else None
    t_close = _ts(closed.get("ts")) if closed else None
    duration_min = None
    if t_recv and t_close:
        duration_min = round((t_close - t_recv).total_seconds() / 60, 1)

    return {
        "sig_id": sig_id,
        "channel": recv.get("channel") if recv else "?",
        "direction": recv.get("direction") if recv else "?",
        "received_ts": recv.get("ts") if recv else None,
        "received_hour": int(recv.get("ts", "0000-00-00T00")[11:13]) if recv else None,
        "fill_price": fill.get("price") if fill else None,
        "fill_latency_ms": lat_fill_ms,
        "range_delay_s": range_delay_s,
        "range_low": rng.get("range_low") if rng else None,
        "range_high": rng.get("range_high") if rng else None,
        "layered_case": ldec.get("case") if ldec else None,
        "layered_action": ldec.get("action_planned") if ldec else None,
        "tps_arrival_s": tps_arrival_s,
        "tps_initial": tps_a.get("tps") if tps_a else None,
        "sl_initial": sl_a.get("sl") if sl_a else None,
        "n_dca": n_dca,
        "n_dca_skipped": len(dca_skipped),
        "dca_dupes": dca_dupes,
        "n_dca_after_tp_msg": n_dca_after_tp,
        "n_mgmt": len(mgmt),
        "n_mgmt_applied": mgmt_applied,
        "n_mgmt_ignored": mgmt_ignored,
        "mgmt_actions": dict(mgmt_actions),
        "closure_tags": closure_tags,
        "final_tag": final_tag,
        "pnl": pnl,
        "duration_min": duration_min,
        "is_closed": closed is not None,
        # Bot execution quality (NO juzga outcome — solo ejecución)
        "execution": _compute_execution(evs),
    }


def _compute_skill(evs: list[dict]) -> dict:
    """DEPRECATED: usa _compute_execution. Mantengo por backward compat
    con scripts antiguos que puedan importar esto.
    """
    return _compute_execution(evs)


def _compute_execution(evs: list[dict]) -> dict:
    """Calcula bot_execution_quality para una señal.

    NO juzga outcome (P&L) — solo si el bot ejecutó disciplinadamente.
    Las decisiones de mercado las hace el canal.
    """
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from bot_execution_quality import classify_execution
        return classify_execution(evs)
    except Exception as e:
        return {"execution_score": None, "category": "ERROR",
                "factors": {}, "issues": [f"error: {e}"]}


# ─── Detección de anomalías ───────────────────────────────────────────────────

def detect_anomalies(metrics: list[dict]) -> list[tuple[str, str]]:
    """Devuelve [(sig_id, descripción), ...] de cosas raras a investigar."""
    out = []
    for m in metrics:
        sid = m["sig_id"]

        # Race condition residual: DCAs duplicados (mismo ms+pos+level)
        if m["dca_dupes"] > 0:
            out.append((sid, f"⚠ {m['dca_dupes']} DCA duplicado(s) — race condition"))

        # Ghost signal: nunca se cerró
        if not m["is_closed"]:
            out.append((sid, "⚠ señal nunca finalizada (ghost) — Bug 2 fix debería cubrirlo"))

        # DCAs después de TP HIT mensaje: SOLO es anomalía si MT5 confirmó
        # que ese TP realmente hit (cerró posiciones por TP). Si el canal
        # dijo "TP1 HIT" pero MT5 no lo confirmó (señal premature/wrong feed),
        # las DCAs posteriores son CORRECTAS — el bot debe seguir vigilando
        # niveles porque el TP no se ha materializado en nuestro broker.
        # Visto en sesión 29-abr canal2_12015: TP1 anunciado a las 09:01 pero
        # MT5 no tocó hasta 09:41. DCAs entre medias fueron correctas.
        if m["n_dca_after_tp_msg"] > 0:
            # Solo flag si el final_tag NO indica wins escalonados (= MT5 confirmó TPs)
            tp_hits_real = sum(1 for tag in (m.get("closure_tags") or {})
                              if tag.startswith("TP"))
            if tp_hits_real == 0:
                # MT5 no confirmó ningún TP → DCAs después del mensaje SÍ son raras
                out.append((sid, f"⚠ {m['n_dca_after_tp_msg']} DCA(s) abiertos tras TP HIT msg "
                                 "Y MT5 no confirmó TP — investigar"))
            # Si MT5 sí confirmó TPs, no es anomalía: el canal anunció antes
            # que el broker tocara, y el bot correctamente esperó al precio real.

        # Mgmt ignorado: el bot ya cerró antes de aplicar la acción
        if m["n_mgmt_ignored"] > 0:
            out.append((sid, f"ℹ {m['n_mgmt_ignored']} mensaje(s) de gestión ignorados "
                             "(señal ya cerrada cuando llegaron)"))

        # Latencia muy alta
        if m["fill_latency_ms"] and m["fill_latency_ms"] > 500:
            out.append((sid, f"⚠ fill latency alto: {m['fill_latency_ms']}ms"))

        # Range delay muy alto (canal2 normalmente <90s, canal1 <300s)
        if m["range_delay_s"]:
            limit = 300 if m["channel"] == "canal1" else 90
            if m["range_delay_s"] > limit:
                out.append((sid, f"⚠ range delay alto: {m['range_delay_s']}s (>{limit}s para {m['channel']})"))

        # Layered C adverso: muchos en sesión = posible drift de timing
        if m["layered_case"] == "C_adverse":
            out.append((sid, f"ℹ caso C_adverse — entry fuera de rango contra (acción: {m['layered_action']})"))

    return out


# ─── Distribuciones ───────────────────────────────────────────────────────────

def _hist(values, bins, label):
    """Histograma simple en consola."""
    if not values:
        print(f"  {label}: sin datos")
        return
    counts = [0] * len(bins)
    for v in values:
        for i, edge in enumerate(bins):
            if v <= edge:
                counts[i] += 1
                break
        else:
            counts[-1] += 1
    print(f"  {label}  n={len(values)}  med={statistics.median(values):.1f}  "
          f"p90={sorted(values)[int(len(values)*0.9)]:.1f}  max={max(values):.1f}")
    for edge, c in zip(bins, counts):
        bar = "█" * int(c / max(counts) * 30) if max(counts) else ""
        print(f"     ≤{edge:>6}: {c:>3}  {bar}")


# ─── Render reporte ───────────────────────────────────────────────────────────

def render_report(metrics: list[dict], heartbeats: list[dict],
                  date_label: str):
    print(f"\n{'='*78}")
    print(f"  ANÁLISIS DE PATRONES — {date_label}")
    print(f"  {len(metrics)} señales | {len(heartbeats)} heartbeats")
    print(f"{'='*78}\n")

    if not metrics:
        print("  Sin señales en el rango pedido.\n")
        return

    # ── 1) Tabla por señal ──
    print("──────────────────── TABLA POR SEÑAL ────────────────────\n")
    hdr = (f"{'sig_id':<18} {'dir':<5} {'h':<3} {'fill':<8} "
           f"{'lat':<5} {'rng_d':<6} {'case':<12} {'dca':<4} "
           f"{'mgmt':<10} {'tag':<14} {'pnl':<8} {'dur':<5}")
    print(hdr)
    print("-" * len(hdr))
    for m in metrics:
        hour = f"{m['received_hour']:02d}" if m['received_hour'] is not None else "??"
        fill = f"{m['fill_price']:.2f}" if m['fill_price'] else "?"
        lat = f"{m['fill_latency_ms']}" if m['fill_latency_ms'] else "?"
        rd = f"{m['range_delay_s']:.0f}" if m['range_delay_s'] else "?"
        case = (m['layered_case'] or "?")[:11]
        dca_skip = f"+{m['n_dca_skipped']}sk" if m['n_dca_skipped'] else ""
        dca = f"{m['n_dca']}{dca_skip}{'!' if m['dca_dupes'] else ''}"
        mgmt = f"{m['n_mgmt_applied']}/{m['n_mgmt']}"
        tag = (m['final_tag'] or "OPEN")[:14]
        pnl = f"{m['pnl']:+.2f}" if m['pnl'] is not None else "?"
        dur = f"{m['duration_min']:.0f}m" if m['duration_min'] is not None else "?"
        print(f"{m['sig_id']:<18} {m['direction']:<5} {hour:<3} {fill:<8} "
              f"{lat:<5} {rd:<6} {case:<12} {dca:<4} "
              f"{mgmt:<10} {tag:<14} {pnl:<8} {dur:<5}")

    # ── 2) Agregados por canal ──
    print("\n──────────────────── AGREGADOS POR CANAL ────────────────────\n")
    by_ch = defaultdict(list)
    for m in metrics:
        by_ch[m["channel"]].append(m)
    for ch, ms in sorted(by_ch.items()):
        closed = [m for m in ms if m["is_closed"] and m["pnl"] is not None]
        wins = [m for m in closed if m["pnl"] > 0]
        losses = [m for m in closed if m["pnl"] < 0]
        total_pnl = sum(m["pnl"] for m in closed)
        wr = len(wins) / len(closed) * 100 if closed else 0
        print(f"  {ch}:  {len(ms)} señales  |  cerradas {len(closed)}  "
              f"|  WR {wr:.0f}%  ({len(wins)}W/{len(losses)}L)  |  P&L total {total_pnl:+.2f}")
        # Distribución de tags finales
        tags = Counter(m["final_tag"] for m in ms if m["final_tag"])
        if tags:
            print(f"      tags: {dict(tags)}")
        # Distribución layered
        cases = Counter(m["layered_case"] for m in ms if m["layered_case"])
        if cases:
            print(f"      layered: {dict(cases)}")

    # ── 2.5) BOT EXECUTION QUALITY (no juzga outcome, solo ejecución) ──
    print("\n──────────────────── BOT EXECUTION QUALITY ────────────────────")
    print("  (mide solo lo que el bot controla. P&L es responsabilidad del canal.)\n")
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        from bot_execution_quality import (aggregate_execution_stats,
                                           find_urgent_cases,
                                           find_pattern_correlations)

        for ch, ms in sorted(by_ch.items()):
            execs = [m["execution"] for m in ms if m.get("execution")]
            agg = aggregate_execution_stats(execs)
            if agg.get("n", 0) == 0:
                continue
            print(f"  {ch}:  {agg['n']} señales analizadas")
            print(f"    Avg execution score: {agg['avg_execution_score']}/100")
            print(f"    PERFECT: {agg['perfect_pct']}%   "
                  f"FAULTY: {agg['faulty_pct']}%")
            cats = agg.get("categories", {})
            cat_str = " | ".join(f"{cat}:{n}" for cat, n in cats.items())
            print(f"    Distribución: {cat_str}")
            # Tabla detallada por señal
            print(f"    Por señal:")
            for m in ms:
                exec_q = m.get("execution") or {}
                score = exec_q.get("execution_score")
                cat = exec_q.get("category", "?")
                emoji = (
                    "✅" if cat == "PERFECT_EXECUTION"
                    else "🟢" if cat == "GOOD_EXECUTION"
                    else "🟡" if cat == "DEGRADED_EXECUTION"
                    else "❌" if cat == "FAULTY_EXECUTION"
                    else "⚪"
                )
                pnl = m.get("pnl")
                pnl_s = f"{pnl:+.2f}" if pnl is not None else "OPEN"
                issues_str = ""
                if exec_q.get("issues"):
                    issues_str = f"  ← {len(exec_q['issues'])} issues"
                print(f"      {emoji} {m['sig_id']:<18} {cat:<20} "
                      f"score={score}/100  P&L={pnl_s}{issues_str}")

        # ── CASOS URGENTES: bot falló + outcome negativo ──
        print("\n──────────────────── 🚨 CASOS URGENTES ────────────────────")
        print("  (FAULTY/DEGRADED execution + P&L negativo = bot probablemente costó dinero)\n")
        urgent = find_urgent_cases(metrics)
        if not urgent:
            print("  ✓ Sin casos urgentes. Las pérdidas (si hubo) fueron por dirección"
                  "\n    del canal, no por ejecución del bot.")
        else:
            total_loss = sum(u["pnl"] for u in urgent)
            print(f"  ⚠️  {len(urgent)} casos para investigar (pérdida total atribuible "
                  f"al bot: ${total_loss:+.2f})\n")
            for u in urgent:
                print(f"    {u['sig_id']:<18} {u['category']:<20} score={u['execution_score']}/100  "
                      f"P&L=${u['pnl']:+.2f}")
                for issue in u["issues"]:
                    print(f"        • {issue}")

        # ── PATRONES DETECTADOS ──
        findings = find_pattern_correlations(metrics)
        if findings:
            print("\n──────────────────── 🔍 PATRONES DETECTADOS ────────────────────\n")
            for f in findings:
                print(f"  • {f}")
    except Exception as e:
        import traceback
        print(f"  ERROR computando execution quality: {e}")
        traceback.print_exc()

    # ── 3) Distribuciones ──
    print("\n──────────────────── DISTRIBUCIONES ────────────────────\n")
    lat_vals = [m["fill_latency_ms"] for m in metrics if m["fill_latency_ms"]]
    _hist(lat_vals, [100, 150, 200, 300, 500, 1000], "fill latency (ms)")
    print()
    rd_vals = [m["range_delay_s"] for m in metrics if m["range_delay_s"]]
    _hist(rd_vals, [10, 30, 60, 90, 120, 300], "range delay (s)")
    print()
    dur_vals = [m["duration_min"] for m in metrics if m["duration_min"]]
    _hist(dur_vals, [5, 15, 30, 60, 120, 300], "trade duration (min)")
    print()
    dca_vals = [m["n_dca"] for m in metrics]
    if dca_vals:
        c = Counter(dca_vals)
        print(f"  DCAs por señal: {dict(sorted(c.items()))}")

    # ── 4) Acciones de gestión más comunes ──
    print("\n──────────────────── MGMT ACTIONS ────────────────────\n")
    all_actions = Counter()
    for m in metrics:
        for action, n in m["mgmt_actions"].items():
            all_actions[action] += n
    for action, n in all_actions.most_common():
        print(f"  {action:<22} {n}")

    # ── 5) Anomalías ──
    print("\n──────────────────── ANOMALÍAS ────────────────────\n")
    anomalies = detect_anomalies(metrics)
    if not anomalies:
        print("  ✓ Sin anomalías. Buen día.")
    else:
        # Agrupa por sig_id para legibilidad
        by_sig = defaultdict(list)
        for sid, msg in anomalies:
            by_sig[sid].append(msg)
        for sid in sorted(by_sig.keys()):
            print(f"  {sid}:")
            for m in by_sig[sid]:
                print(f"     {m}")

    # ── 6) Heartbeat sanity ──
    print("\n──────────────────── HEARTBEATS ────────────────────\n")
    if heartbeats:
        first_hb = heartbeats[0]["ts"][:19]
        last_hb = heartbeats[-1]["ts"][:19]
        max_open = max(h.get("open_signals", 0) for h in heartbeats)
        print(f"  {len(heartbeats)} heartbeats  |  {first_hb} → {last_hb}")
        print(f"  Máx open_signals visto en algún beat: {max_open}")
        # Detecta si open_signals > 0 al final (= ghost signals al cerrar bot)
        last_open = heartbeats[-1].get("open_signals", 0)
        if last_open > 0:
            print(f"  ⚠ Último heartbeat tenía {last_open} señal(es) abiertas — "
                  f"posibles ghosts (Bug 2 fix las debe finalizar en próxima sesión)")
    else:
        print("  Sin heartbeats en el rango (¿sesión muy corta?)")

    print()


# ─── CSV export ───────────────────────────────────────────────────────────────

def export_csv(metrics: list[dict], path: Path):
    if not metrics:
        return
    # Aplanamos dicts anidados
    rows = []
    for m in metrics:
        r = {k: v for k, v in m.items()
             if not isinstance(v, (dict, list))}
        r["mgmt_actions"] = json.dumps(m["mgmt_actions"])
        r["closure_tags"] = json.dumps(m["closure_tags"])
        rows.append(r)
    fields = list(rows[0].keys())
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"\n  CSV exportado: {path}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    path = Path(args.file)
    if not path.exists():
        print(f"ERROR: no existe {path}")
        sys.exit(1)

    events = load_events(path)
    signals, heartbeats = group_by_signal(
        events, date=args.date, date_from=args.date_from, date_to=args.date_to,
        channel=args.channel,
    )

    metrics = sorted(
        (extract_metrics(sid, evs) for sid, evs in signals.items()),
        key=lambda m: m["received_ts"] or "",
    )

    date_label = args.date or f"{args.date_from or 'inicio'} → {args.date_to or 'hoy'}"
    label = f"{date_label} | canal={args.channel}" if args.channel else date_label
    render_report(metrics, heartbeats, label)

    if args.csv:
        out = ROOT / "analysis" / "patterns_signals.csv"
        export_csv(metrics, out)


if __name__ == "__main__":
    main()
