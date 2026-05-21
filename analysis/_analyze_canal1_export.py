"""
Análisis del export de Telegram del canal 1.

Objetivo: identificar todos los tipos de mensaje que manda el trader y
qué subset el bot está capturando vs ignorando.
"""
import io
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

EXPORT_PATH = Path(r"C:\Users\josea\Downloads\Telegram Desktop\ChatExport_2026-04-29\result.json")

print(f"Cargando {EXPORT_PATH}...")
with open(EXPORT_PATH, encoding="utf-8") as f:
    export = json.load(f)

print(f"\n=== METADATA DEL CANAL ===")
print(f"  Nombre: {export.get('name')}")
print(f"  Tipo:   {export.get('type')}")
print(f"  ID:     {export.get('id')}")
print(f"  Total mensajes: {len(export.get('messages', []))}")

messages = export.get("messages", [])

# Rango de fechas
if messages:
    first_date = messages[0].get("date", "")
    last_date  = messages[-1].get("date", "")
    print(f"  Rango: {first_date}  →  {last_date}")

# Filtra a últimos 14 días para empezar
CUTOFF = (datetime.now() - timedelta(days=14)).isoformat()
recent = [m for m in messages if m.get("date", "") >= CUTOFF]
print(f"\n  Mensajes últimos 14 días: {len(recent)}")

# ─── 1) TIPOS DE MENSAJE ─────────────────────────────────────────────────
print(f"\n=== 1) TIPOS DE MENSAJE (últimos 14 días) ===\n")

def classify_msg(m: dict) -> str:
    """Clasifica un mensaje según su contenido principal."""
    if m.get("type") == "service":
        return "service (join/leave/etc)"
    media_type = m.get("media_type")
    if media_type:
        if "photo" in str(m.get("file", "")) or m.get("photo"):
            return "photo+media"
        return f"media:{media_type}"
    if m.get("photo"):
        return "photo"
    if m.get("file"):
        return f"file:{m.get('mime_type', 'unknown')}"
    if m.get("sticker_emoji"):
        return f"sticker"
    text = m.get("text", "")
    if isinstance(text, list):
        text = "".join(t.get("text", t) if isinstance(t, dict) else str(t) for t in text)
    if not text.strip():
        return "empty"
    return "text"

types = Counter(classify_msg(m) for m in recent)
for t, c in types.most_common():
    print(f"  {t:<35} {c}")

# ─── 2) FOTOS CON CAPTION ────────────────────────────────────────────────
print(f"\n=== 2) FOTOS CON CAPTION (últimos 14 días) ===\n")

def get_text(m: dict) -> str:
    text = m.get("text", "")
    if isinstance(text, list):
        # text es array mixto de strings y dicts {type, text}
        return "".join(
            t.get("text", "") if isinstance(t, dict) else str(t)
            for t in text
        )
    return str(text) if text else ""

photos_with_caption = []
for m in recent:
    if m.get("photo") or "photo" in str(m.get("file", "")):
        caption = get_text(m).strip()
        if caption:
            photos_with_caption.append({
                "date": m.get("date", ""),
                "id": m.get("id"),
                "reply_to": m.get("reply_to_message_id"),
                "caption": caption,
            })

print(f"Fotos con caption: {len(photos_with_caption)}")
print(f"  De ellas reply: {sum(1 for p in photos_with_caption if p['reply_to'])}")
print(f"  De ellas standalone: {sum(1 for p in photos_with_caption if not p['reply_to'])}")

# Muestra ejemplos de captions agrupados por longitud típica
print(f"\nEjemplos de captions (15 más recientes):")
for p in photos_with_caption[-15:]:
    reply_tag = f"→reply#{p['reply_to']}" if p["reply_to"] else "[STANDALONE]"
    print(f"  {p['date']}  {reply_tag:<22}  {p['caption'][:100]!r}")

# ─── 3) MENSAJES DE TEXTO SIN MEDIA ──────────────────────────────────────
print(f"\n=== 3) MENSAJES TEXTO PURO (últimos 14 días) ===\n")

text_msgs = []
for m in recent:
    if m.get("photo") or m.get("media_type") or m.get("file") or m.get("sticker_emoji"):
        continue
    text = get_text(m).strip()
    if text:
        text_msgs.append({
            "date": m.get("date", ""),
            "id": m.get("id"),
            "reply_to": m.get("reply_to_message_id"),
            "text": text,
        })

print(f"Mensajes texto puro: {len(text_msgs)}")
print(f"  De ellos reply: {sum(1 for t in text_msgs if t['reply_to'])}")
print(f"  De ellos standalone: {sum(1 for t in text_msgs if not t['reply_to'])}")

# ─── 4) STICKERS ─────────────────────────────────────────────────────────
print(f"\n=== 4) STICKERS (últimos 14 días) ===\n")
stickers = [m for m in recent if m.get("sticker_emoji")]
print(f"Total stickers: {len(stickers)}")
sticker_emojis = Counter(s.get("sticker_emoji", "?") for s in stickers)
for emoji, c in sticker_emojis.most_common():
    print(f"  {emoji}  {c}")

# ─── 5) PATRONES EN CAPTIONS (lo más importante) ─────────────────────────
print(f"\n=== 5) ANÁLISIS DE CONTENIDO DE CAPTIONS ===\n")

PATTERNS = {
    "BUY GOLD NOW (entry)":  re.compile(r"\bbuy\s+gold\s+now\b", re.IGNORECASE),
    "SELL GOLD NOW (entry)": re.compile(r"\bsell\s+gold\s+now\b", re.IGNORECASE),
    "BUY/SELL NOW (otro fmt)": re.compile(r"\b(?:buy|sell)\s+now\b", re.IGNORECASE),
    "TPx hit":               re.compile(r"\btp\s*\d+\s+(?:hit|reached|secured|done)\b", re.IGNORECASE),
    "SL hit / SL already":   re.compile(r"\bsl\s+(?:was\s+|already\s+|just\s+|has\s+been\s+)?(?:hit|reached|triggered)\b", re.IGNORECASE),
    "Move SL to BE":         re.compile(r"move\s+(?:my\s+|your\s+|the\s+)?sl\s+to\s+(?:be|breakeven|entry|0)", re.IGNORECASE),
    "Move SL to PRICE":      re.compile(r"(?:move|adjust|set|put|change|moving)\s+(?:my\s+|your\s+|the\s+)?(?:sl|stop[- ]?loss)\s+(?:to|at)\s+\d", re.IGNORECASE),
    "Close all/positions":   re.compile(r"close\s+(?:all|the\s+rest|positions|everything|now|trade|this\s+trade)", re.IGNORECASE),
    "Close first/early":     re.compile(r"close\s+(?:the\s+|your\s+|my\s+)?(?:first|early|initial|oldest)\s+(?:entry|entries|position|positions)", re.IGNORECASE),
    "TP1 4xxx (level info)": re.compile(r"\btp\d+\s*[:=\s]\s*\d{3,5}", re.IGNORECASE),
    "Pips profit":           re.compile(r"\d+\s*pips?\s+(?:profit|secured|locked|gained)", re.IGNORECASE),
    "Risk-free / 0% risk":   re.compile(r"(?:risk[- ]?free|0\s*%?\s*risk)", re.IGNORECASE),
    "Range/Entry zone":      re.compile(r"\d{4}\s*[-–]\s*\d{2,4}", re.IGNORECASE),
    "TP/SL announcement":    re.compile(r"(?:tp\s*\d+|sl|sp)\s*[:=\s]\s*\d{3,5}", re.IGNORECASE),
}

# Aplica patrones a cada caption / texto puro y cuenta
all_msgs = []
for p in photos_with_caption:
    all_msgs.append({"kind": "photo", "id": p["id"], "date": p["date"],
                     "reply_to": p["reply_to"], "text": p["caption"]})
for t in text_msgs:
    all_msgs.append({"kind": "text", "id": t["id"], "date": t["date"],
                     "reply_to": t["reply_to"], "text": t["text"]})

pattern_hits = {p: 0 for p in PATTERNS}
unmatched = []
for m in all_msgs:
    matched_any = False
    for pname, prx in PATTERNS.items():
        if prx.search(m["text"]):
            pattern_hits[pname] += 1
            matched_any = True
    if not matched_any:
        unmatched.append(m)

print(f"Mensajes analizados (fotos+texto): {len(all_msgs)}")
print(f"\nPatrones detectados:")
for pname, c in sorted(pattern_hits.items(), key=lambda x: -x[1]):
    pct = c / len(all_msgs) * 100 if all_msgs else 0
    print(f"  {pname:<30}  {c:>5}  ({pct:>4.1f}%)")

# ─── 6) MENSAJES QUE EL BOT POSIBLEMENTE IGNORARÍA ───────────────────────
print(f"\n=== 6) MENSAJES POTENCIALMENTE IGNORADOS POR EL BOT ===\n")
print("Criterio: foto o texto STANDALONE (no reply) cuyo caption matchea")
print("un patrón de gestión (close, move SL, etc.) — no es entry de señal.\n")

mgmt_patterns = {k: v for k, v in PATTERNS.items()
                 if k in ["TPx hit", "SL hit / SL already", "Move SL to BE",
                          "Move SL to PRICE", "Close all/positions",
                          "Close first/early", "Pips profit",
                          "Risk-free / 0% risk"]}

ignored_potential = []
for m in all_msgs:
    if m["reply_to"]:
        continue  # reply → el bot SÍ lo procesa
    # Si caption matchea entry pattern, el bot lo procesa
    if any(p.search(m["text"]) for p in [PATTERNS["BUY GOLD NOW (entry)"],
                                          PATTERNS["SELL GOLD NOW (entry)"]]):
        continue
    # ¿Matchea algún patrón de gestión?
    for pname, prx in mgmt_patterns.items():
        if prx.search(m["text"]):
            ignored_potential.append({**m, "matched_pattern": pname})
            break

print(f"Total candidatos a IGNORADOS: {len(ignored_potential)}\n")
print(f"Por patrón:")
ignored_by_pattern = Counter(m["matched_pattern"] for m in ignored_potential)
for p, c in ignored_by_pattern.most_common():
    print(f"  {p:<30}  {c}")

print(f"\nEjemplos (15 más recientes):")
for m in ignored_potential[-15:]:
    print(f"  {m['date']}  [{m['kind']}#{m['id']}]  → {m['matched_pattern']}")
    print(f"     {m['text'][:120]!r}")

# ─── 7) MENSAJES NO MATCHEADOS POR NINGÚN PATRÓN ─────────────────────────
print(f"\n=== 7) MENSAJES SIN MATCH (curiosidad) ===\n")
print(f"Total no matcheados por nuestros patrones: {len(unmatched)}")
print(f"Ejemplos (15 más recientes):")
for m in unmatched[-15:]:
    reply_tag = f"→reply#{m['reply_to']}" if m["reply_to"] else "[STANDALONE]"
    text_short = m["text"][:100].replace("\n", " ")
    print(f"  {m['date']}  [{m['kind']}#{m['id']}] {reply_tag}")
    print(f"     {text_short!r}")
