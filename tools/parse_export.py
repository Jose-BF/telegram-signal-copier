"""
parse_export.py — Analiza el JSON de exportación de Telegram Desktop.

Extrae señales de Canal 1 y Canal 2, las parsea con el parser existente,
genera estadísticas y un CSV con todas las señales y sus niveles.

Uso:
    # Primero, listar todos los chats del export para confirmar IDs:
    python parse_export.py --json "C:/ruta/result.json" --list-chats

    # Análisis completo + CSV:
    python parse_export.py --json "C:/ruta/result.json"

    # Guardar CSV en ruta concreta:
    python parse_export.py --json "C:/ruta/result.json" --output mi_analisis.csv
"""

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

# Importar parser del propio proyecto
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional helper dependency
    load_dotenv = None

if load_dotenv:
    load_dotenv(PROJECT_ROOT / ".env")

from parser import (
    is_canal2_entry,
    is_canal1_signal_text,
    parse_canal2,
    parse_canal1_text,
    predict_levels,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def extract_text(field) -> str:
    """Telegram puede exportar el texto como string o como lista de entidades."""
    if isinstance(field, str):
        return field
    if isinstance(field, list):
        return "".join(
            item if isinstance(item, str) else item.get("text", "")
            for item in field
        )
    return ""


def is_sticker(msg: dict) -> bool:
    return msg.get("media_type") == "sticker" or "sticker_emoji" in msg


def telegram_export_id_from_env(value: str) -> int:
    """Convert a Telethon channel ID into the chat ID used by Telegram exports."""
    raw = str(value).strip()
    if not raw:
        raise ValueError("empty Telegram channel ID")

    numeric = abs(int(raw))
    digits = str(numeric)
    if digits.startswith("100") and len(digits) > 10:
        return int(digits[3:])
    return numeric


def resolve_channel_id(label: str, cli_value: str | None, env_key: str) -> int | None:
    """Read a channel ID from CLI or .env without hardcoding private channel IDs."""
    raw = cli_value or os.getenv(env_key)
    if not raw:
        return None
    try:
        return telegram_export_id_from_env(raw)
    except ValueError as exc:
        raise SystemExit(f"{label}: invalid channel ID in {env_key}: {exc}") from exc


def load_channel(data: dict, channel_id: int) -> list[dict]:
    """Extrae los mensajes de un canal específico del export global."""
    for chat in data.get("chats", {}).get("list", []):
        if chat.get("id") == channel_id:
            msgs = chat.get("messages", [])
            print(f"  Encontrado: '{chat.get('name', '?')}' — {len(msgs)} mensajes")
            return msgs
    return []


# ─── Canal 2 ──────────────────────────────────────────────────────────────────

def extract_canal2_signals(messages: list[dict]) -> list[dict]:
    """
    Extrae señales de Canal 2.

    NOTA: el export de Telegram Desktop solo guarda la versión FINAL de cada
    mensaje editado, no el historial de edits. Por tanto el texto de cada señal
    ya contiene rango + TPs + SL en su forma definitiva cuando los tiene.
    """
    # Índice replies: message_id → [mensajes que responden]
    replies: dict[int, list[dict]] = {}
    for msg in messages:
        if msg.get("type") != "message":
            continue
        rid = msg.get("reply_to_message_id")
        if rid:
            replies.setdefault(rid, []).append(msg)

    signals = []
    for msg in messages:
        if msg.get("type") != "message":
            continue
        if msg.get("reply_to_message_id"):
            continue  # skip replies, son gestión

        text = extract_text(msg.get("text", ""))
        if not is_canal2_entry(text):
            continue

        parsed = parse_canal2(text)
        if "direction" not in parsed:
            continue

        rng = parsed.get("range")
        tps = parsed.get("tps", [])
        sl  = parsed.get("sl")

        # Predicción provisional si falta SL o TPs pero hay rango
        tps_pred = sl_pred = None
        if rng and (not sl or not tps):
            pred = predict_levels(parsed["direction"], rng[0], rng[1])
            if not tps:
                tps_pred = pred["tps"]
            if not sl:
                sl_pred = pred["sl"]

        reply_msgs = replies.get(msg["id"], [])

        signals.append({
            "channel":      "canal2",
            "message_id":   msg["id"],
            "date":         msg.get("date", ""),
            "was_edited":   "edited" in msg,
            "direction":    parsed["direction"],
            "range_low":    rng[0] if rng else None,
            "range_high":   rng[1] if rng else None,
            "tps":          tps,
            "sl":           sl,
            "tps_predicted": tps_pred,
            "sl_predicted":  sl_pred,
            "num_replies":  len(reply_msgs),
            "reply_texts":  [extract_text(r.get("text", "")) for r in reply_msgs],
            "raw_text":     text,
        })

    return signals


# ─── Canal 1 ──────────────────────────────────────────────────────────────────

def extract_canal1_signals(messages: list[dict]) -> list[dict]:
    """
    Extrae señales de Canal 1 emparejando stickers con el texto posterior.

    Flujo: sticker BUY/SELL → ~2 min después, mensaje de texto con rango+TPs+SL.
    El export no distingue BUY/SELL del sticker (file_id no se guarda), pero sí
    el emoji del sticker. La dirección la sacamos del texto posterior.
    """
    replies: dict[int, list[dict]] = {}
    for msg in messages:
        if msg.get("type") != "message":
            continue
        rid = msg.get("reply_to_message_id")
        if rid:
            replies.setdefault(rid, []).append(msg)

    signals = []
    pending: Optional[dict] = None  # sticker sin texto asociado aún

    for msg in sorted(messages, key=lambda m: m.get("id", 0)):
        if msg.get("type") != "message":
            continue
        if msg.get("reply_to_message_id"):
            continue  # skip replies

        text = extract_text(msg.get("text", ""))

        if is_sticker(msg):
            # Nuevo sticker → inicio de señal Canal 1
            pending = {
                "channel":      "canal1",
                "message_id":   msg["id"],
                "date":         msg.get("date", ""),
                "sticker_emoji": msg.get("sticker_emoji", "?"),
                "direction":    None,
                "range_low":    None,
                "range_high":   None,
                "tps":          [],
                "sl":           None,
                "tps_predicted": None,
                "sl_predicted":  None,
                "num_replies":  len(replies.get(msg["id"], [])),
                "reply_texts":  [extract_text(r.get("text", "")) for r in replies.get(msg["id"], [])],
                "raw_text":     "",
                "was_edited":   False,
            }
            signals.append(pending)

        elif is_canal1_signal_text(text) and pending is not None:
            # Texto que sigue al sticker → completa la señal
            parsed = parse_canal1_text(text)
            pending["direction"] = parsed.get("direction")
            pending["raw_text"]  = text
            rng = parsed.get("range")
            if rng:
                pending["range_low"], pending["range_high"] = rng
            pending["tps"] = parsed.get("tps", [])
            pending["sl"]  = parsed.get("sl")
            pending = None  # señal completada

    # Descartar señales sin dirección (stickers sin texto posterior)
    complete   = [s for s in signals if s["direction"]]
    incomplete = [s for s in signals if not s["direction"]]
    if incomplete:
        print(f"  [Canal1] {len(incomplete)} sticker(s) sin texto posterior (ignorados)")

    return complete


# ─── Estadísticas ─────────────────────────────────────────────────────────────

def print_stats(signals: list[dict], channel: str):
    total = len(signals)
    if not total:
        print(f"\n[{channel.upper()}] Sin señales encontradas.")
        return

    buys       = sum(1 for s in signals if s["direction"] == "BUY")
    sells      = sum(1 for s in signals if s["direction"] == "SELL")
    with_range = sum(1 for s in signals if s["range_low"])
    with_tps   = sum(1 for s in signals if s["tps"])
    with_sl    = sum(1 for s in signals if s["sl"])
    with_reply = sum(1 for s in signals if s["num_replies"] > 0)

    print(f"\n{'='*55}")
    print(f"  {channel.upper()} — {total} señales")
    print(f"{'='*55}")
    print(f"  BUY:  {buys:>3} ({buys/total*100:>4.0f}%)  |  SELL: {sells:>3} ({sells/total*100:>4.0f}%)")
    print(f"  Con rango:    {with_range:>3}/{total}")
    print(f"  Con TPs:      {with_tps:>3}/{total}")
    print(f"  Con SL:       {with_sl:>3}/{total}")
    print(f"  Con gestión:  {with_reply:>3}/{total}  (al menos 1 reply de gestión)")

    # Distribución por hora UTC
    hours: dict[int, int] = {}
    for s in signals:
        try:
            h = datetime.fromisoformat(s["date"]).hour
            hours[h] = hours.get(h, 0) + 1
        except Exception:
            pass

    if hours:
        print(f"\n  Distribución por hora UTC:")
        for h in sorted(hours):
            bar = "█" * hours[h]
            print(f"    {h:02d}h  {bar} ({hours[h]})")

    # Verificación del patrón TP/SL (solo Canal 2)
    if channel == "canal2":
        print(f"\n  Verificación patrón TP/SL (entry = edge del rango):")
        print(f"    BUY:  entry=range_high | TPs = entry+3, +5, +7, +9 | SL = range_low-4")
        print(f"    SELL: entry=range_low  | TPs = entry-3, -5, -7, -9 | SL = range_high+4")

        verifiable = [
            s for s in signals
            if s["range_low"] and s["tps"] and s["sl"]
        ]
        print(f"\n    Señales con rango+TPs+SL (verificables): {len(verifiable)}/{total}")

        if verifiable:
            tp1_hits = tp2_hits = sl_hits = 0
            tp1_diffs = []
            sl_diffs  = []

            for s in verifiable:
                direction = s["direction"]
                sign = 1 if direction == "BUY" else -1
                entry    = s["range_high"] if direction == "BUY" else s["range_low"]
                sl_edge  = s["range_low"]  if direction == "BUY" else s["range_high"]

                # TP1
                exp_tp1 = round(entry + sign * 3, 2)
                diff_tp1 = abs(s["tps"][0] - exp_tp1)
                tp1_diffs.append(diff_tp1)
                if diff_tp1 < 1.0:
                    tp1_hits += 1

                # TP2 (si existe)
                if len(s["tps"]) >= 2:
                    exp_tp2 = round(entry + sign * 5, 2)
                    if abs(s["tps"][1] - exp_tp2) < 1.0:
                        tp2_hits += 1

                # SL
                exp_sl = round(sl_edge - sign * 4, 2)
                diff_sl = abs(s["sl"] - exp_sl)
                sl_diffs.append(diff_sl)
                if diff_sl < 1.0:
                    sl_hits += 1

            n = len(verifiable)
            n2 = sum(1 for s in verifiable if len(s["tps"]) >= 2)
            avg_tp1 = sum(tp1_diffs) / len(tp1_diffs) if tp1_diffs else 0
            avg_sl  = sum(sl_diffs)  / len(sl_diffs)  if sl_diffs  else 0

            print(f"\n    TP1 ≈ entry±3:  {tp1_hits}/{n} ({tp1_hits/n*100:.0f}%)  — desviación media: {avg_tp1:.2f}$")
            if n2:
                print(f"    TP2 ≈ entry±5:  {tp2_hits}/{n2} ({tp2_hits/n2*100:.0f}%)")
            print(f"    SL  ≈ edge±4:   {sl_hits}/{n}  ({sl_hits/n*100:.0f}%)  — desviación media: {avg_sl:.2f}$")

            # Mostrar las que NO encajan para análisis manual
            outliers = [
                s for s in verifiable
                if abs((s["range_high"] if s["direction"]=="BUY" else s["range_low"])
                       + (1 if s["direction"]=="BUY" else -1) * 3 - s["tps"][0]) >= 1.0
            ]
            if outliers:
                print(f"\n    Señales donde TP1 NO encaja con entry±3 (revisar manualmente):")
                for s in outliers[:10]:
                    entry = s["range_high"] if s["direction"]=="BUY" else s["range_low"]
                    sign  = 1 if s["direction"]=="BUY" else -1
                    print(f"      msg={s['message_id']} {s['direction']} "
                          f"rango=[{s['range_low']},{s['range_high']}] "
                          f"entry={entry} "
                          f"TP1_real={s['tps'][0]} TP1_esperado={round(entry+sign*3,2)} "
                          f"diff={abs(s['tps'][0]-round(entry+sign*3,2)):.2f}")


# ─── CSV ──────────────────────────────────────────────────────────────────────

def save_csv(signals: list[dict], path: str):
    fieldnames = [
        "channel", "message_id", "date", "was_edited", "direction",
        "range_low", "range_high", "sl", "tps",
        "sl_predicted", "tps_predicted",
        "num_replies", "reply_texts", "raw_text",
    ]
    out = Path(path)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for s in signals:
            row = dict(s)
            row["tps"]           = "|".join(str(t) for t in s.get("tps", []))
            row["tps_predicted"] = "|".join(str(t) for t in s["tps_predicted"]) if s.get("tps_predicted") else ""
            row["reply_texts"]   = " /// ".join(s.get("reply_texts", []))
            w.writerow(row)
    print(f"\nCSV guardado: {out.resolve()}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Analiza export Telegram Desktop para señales de trading")
    ap.add_argument("--json",       required=True,                 help="Ruta al result.json")
    ap.add_argument("--output",     default="signals_export.csv",  help="Fichero CSV de salida")
    ap.add_argument("--list-chats", action="store_true",           help="Solo listar chats disponibles y salir")
    ap.add_argument("--canal1-id",   help="ID de Canal 1; si falta, usa CANAL_1_ID del .env")
    ap.add_argument("--canal2-id",   help="ID de Canal 2; si falta, usa CANAL_2_ID del .env")
    args = ap.parse_args()

    print(f"\nCargando {args.json} ...")
    with open(args.json, encoding="utf-8") as f:
        data = json.load(f)

    # Modo listado
    if args.list_chats:
        print("\nChats disponibles en el export:")
        print(f"  {'ID':>12}  {'Mensajes':>8}  Nombre")
        print(f"  {'-'*12}  {'-'*8}  {'-'*30}")
        for chat in data.get("chats", {}).get("list", []):
            n = len(chat.get("messages", []))
            print(f"  {chat['id']:>12}  {n:>8}  {chat.get('name', '?')}")
        return

    canal1_id = resolve_channel_id("Canal 1", args.canal1_id, "CANAL_1_ID")
    canal2_id = resolve_channel_id("Canal 2", args.canal2_id, "CANAL_2_ID")
    if canal1_id is None and canal2_id is None:
        print("\nNo hay IDs de canal configurados.")
        print("Define CANAL_1_ID/CANAL_2_ID en .env o usa --canal1-id/--canal2-id.")
        print("Ejecuta con --list-chats para ver los IDs disponibles en tu export.")
        return

    # Extraer mensajes por canal
    print(f"\nBuscando canales...")
    c1_msgs = []
    if canal1_id is not None:
        print(f"  Canal 1 (ID={canal1_id}):")
        c1_msgs = load_channel(data, canal1_id)
    else:
        print("  Canal 1: sin configurar")

    c2_msgs = []
    if canal2_id is not None:
        print(f"  Canal 2 (ID={canal2_id}):")
        c2_msgs = load_channel(data, canal2_id)
    else:
        print("  Canal 2: sin configurar")

    if not c1_msgs and not c2_msgs:
        print("\nNo se encontraron mensajes de Canal 1 ni Canal 2.")
        print("Ejecuta con --list-chats para ver los IDs disponibles en tu export.")
        return

    # Parsear señales
    print(f"\nParsando señales...")
    c1_signals = extract_canal1_signals(c1_msgs) if c1_msgs else []
    c2_signals = extract_canal2_signals(c2_msgs) if c2_msgs else []

    print(f"  Canal 1: {len(c1_signals)} señales válidas")
    print(f"  Canal 2: {len(c2_signals)} señales válidas")

    # Estadísticas
    if c1_signals:
        print_stats(c1_signals, "canal1")
    if c2_signals:
        print_stats(c2_signals, "canal2")

    # Guardar CSV
    all_signals = c1_signals + c2_signals
    if all_signals:
        save_csv(all_signals, args.output)
        print(f"Total exportado: {len(all_signals)} señales")
    else:
        print("\nSin señales para exportar.")


if __name__ == "__main__":
    main()
