"""
inspect_raw.py — Inspecciona la ESTRUCTURA cruda del JSON para detectar
                 patrones que no estamos extrayendo.

Mira:
- Ediciones de mensajes (¿cuántas, qué cambia?)
- Texto completo de replies (¿hay palabras clave que estoy ignorando?)
- Mensajes "tipo señal" que no encajan en el parser actual
- Formatos especiales (forwards, multimedia, encuestas)
- Mensajes raros del canal que no son señales (anuncios, recaps)
"""

import json
import sys
import io
from collections import Counter
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

CANAL_1_ID = 1642806869
CANAL_2_ID = 2614601304

JSON_PATH = r"C:\Users\josea\Downloads\Telegram Desktop\DataExport_2026-04-22\result.json"


def extract_text(field) -> str:
    if isinstance(field, str):
        return field
    if isinstance(field, list):
        out = []
        for p in field:
            if isinstance(p, str):
                out.append(p)
            elif isinstance(p, dict):
                out.append(p.get("text", ""))
        return "".join(out)
    return ""


def main():
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    chats = data.get("chats", {}).get("list", [])
    canal1 = next((c for c in chats if c.get("id") == CANAL_1_ID), None)
    canal2 = next((c for c in chats if c.get("id") == CANAL_2_ID), None)

    for name, canal in [("CANAL 1", canal1), ("CANAL 2", canal2)]:
        if not canal:
            continue
        msgs = canal.get("messages", [])
        print(f"\n{'='*70}")
        print(f"  {name} — {len(msgs)} mensajes brutos")
        print(f"{'='*70}\n")

        # 1. Tipos de mensaje
        types = Counter()
        with_edit = 0
        with_reply = 0
        with_media = 0
        with_sticker = 0
        with_photo = 0
        with_forward = 0
        only_emoji = 0
        empty = 0

        for m in msgs:
            t = m.get("type", "?")
            types[t] += 1
            if "edited" in m:
                with_edit += 1
            if "reply_to_message_id" in m:
                with_reply += 1
            if "media_type" in m:
                with_media += 1
            if "sticker_emoji" in m or m.get("media_type") == "sticker":
                with_sticker += 1
            if "photo" in m:
                with_photo += 1
            if "forwarded_from" in m:
                with_forward += 1
            txt = extract_text(m.get("text", "")).strip()
            if not txt:
                empty += 1
            elif len(txt) <= 4 and not any(c.isalnum() for c in txt):
                only_emoji += 1

        print(f"  Tipos: {dict(types)}")
        print(f"  Editados: {with_edit}")
        print(f"  Con reply_to: {with_reply}")
        print(f"  Con media: {with_media}")
        print(f"  Stickers: {with_sticker}")
        print(f"  Fotos: {with_photo}")
        print(f"  Forwards: {with_forward}")
        print(f"  Vacíos / solo emoji: {empty} / {only_emoji}")

        # 2. Palabras clave en los mensajes (top 50)
        print(f"\n  Top 30 palabras clave (>=4 chars, en mayúsculas):")
        words = Counter()
        for m in msgs:
            txt = extract_text(m.get("text", "")).upper()
            for w in txt.split():
                w = "".join(c for c in w if c.isalnum())
                if len(w) >= 4 and not w.isdigit():
                    words[w] += 1
        for w, c in words.most_common(30):
            print(f"    {w:20s} {c}")

        # 3. Buscar patrones de mensajes "raros" (no señal, no reply de TP)
        print(f"\n  Mensajes que NO son señal estándar (muestra de 10):")
        non_signals = []
        for m in msgs:
            txt = extract_text(m.get("text", "")).strip()
            if not txt:
                continue
            t_upper = txt.upper()
            # No es señal estándar si:
            is_signal = ("BUY NOW" in t_upper or "SELL NOW" in t_upper or
                        "BUY GOLD" in t_upper or "SELL GOLD" in t_upper)
            is_tp_reply = ("TP" in t_upper and ("HIT" in t_upper or "✅" in txt or "PIPS" in t_upper))
            is_sl_reply = "SL" in t_upper and ("HIT" in t_upper or "❌" in txt)
            is_be = "BE" in t_upper or "BREAK" in t_upper or "BREAKEVEN" in t_upper
            if not (is_signal or is_tp_reply or is_sl_reply or is_be):
                if 20 < len(txt) < 200:
                    non_signals.append(txt)

        seen_first_lines = set()
        unique_samples = []
        for txt in non_signals:
            first_line = txt.split("\n")[0][:80]
            if first_line not in seen_first_lines:
                seen_first_lines.add(first_line)
                unique_samples.append(txt)

        for txt in unique_samples[:15]:
            preview = txt.replace("\n", " | ")[:150]
            print(f"    • {preview}")

        # 4. Ediciones: ver qué cambia
        print(f"\n  Ediciones en mensajes (5 muestras):")
        edits_shown = 0
        for m in msgs:
            if "edited" not in m or edits_shown >= 5:
                continue
            txt = extract_text(m.get("text", "")).strip()
            if not txt or len(txt) > 300:
                continue
            print(f"    Mensaje {m.get('id')} (editado en {m['edited']}):")
            print(f"      {txt[:200]}")
            edits_shown += 1


if __name__ == "__main__":
    main()
