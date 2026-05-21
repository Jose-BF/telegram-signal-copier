"""Cruza export Telegram + JSONL del bot para ver qué hizo Gemini con
mensajes contextuales de canal 1, y dónde podría haber fallado."""
import io, json, sys, os
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.getcwd())

EXPORT = Path(r"C:\Users\josea\Downloads\Telegram Desktop\ChatExport_2026-04-29\result.json")
with open(EXPORT, encoding="utf-8") as f:
    export = json.load(f)

# Carga eventos del bot
events = []
with open("data/trade_events.jsonl", encoding="utf-8") as f:
    for line in f:
        if line.strip():
            events.append(json.loads(line))

def get_text(m):
    text = m.get("text", "")
    if isinstance(text, list):
        return "".join(t.get("text", "") if isinstance(t, dict) else str(t) for t in text)
    return str(text) if text else ""

# Mensajes recientes con texto/caption del canal 1 (no entries)
CUTOFF = "2026-04-25"
canal1_msgs = []
for m in export.get("messages", []):
    if m.get("date", "") < CUTOFF:
        continue
    if m.get("media_type") == "sticker":
        continue
    text = get_text(m).strip()
    if not text:
        continue
    if "BUY GOLD NOW" in text.upper() or "SELL GOLD NOW" in text.upper():
        continue  # entry text
    canal1_msgs.append({
        "id": m.get("id"),
        "date": m.get("date"),
        "is_reply": bool(m.get("reply_to_message_id")),
        "reply_to": m.get("reply_to_message_id"),
        "text": text,
    })

print(f"Total mgmt candidates canal 1 desde {CUTOFF}: {len(canal1_msgs)}")

# Cómo el classifier actual los maneja (regex local)
from classifier import _regex_classify_all
buckets = {"REGEX→action": [], "REGEX→info": [], "REGEX→empty (a Gemini)": []}
for m in canal1_msgs:
    actions = _regex_classify_all(m["text"])
    if not actions:
        buckets["REGEX→empty (a Gemini)"].append(m)
    elif len(actions) == 1 and actions[0]["action"] == "INFORMATIONAL":
        buckets["REGEX→info"].append(m)
    else:
        m["_actions"] = actions
        buckets["REGEX→action"].append(m)

for name, items in buckets.items():
    print(f"  {name}:  {len(items)}")

# ═══ Lo importante: qué mensajes van a Gemini y qué dijo Gemini ═══
print("\n" + "="*78)
print("MENSAJES QUE FUERON A GEMINI (canal 1, ejemplos típicos)")
print("="*78)

# De los mensajes empty regex, ver los que más sentido tendría que Gemini
# entendiera bien (no spam, no ads, no chitchat)
def is_substantive(text):
    """Heurística: el mensaje habla de gold/trade/SL/TP/profit, no es ad/chitchat."""
    t = text.lower()
    if "https://" in t and len(text) < 50:  # link spam
        return False
    if "giveaway" in t or "vip" in t and "trad" not in t:
        return False
    keywords = ("gold", "trade", "tp", "sl", "profit", "loss", "entry", "be ",
                "breakeven", "buy ", "sell ", "running", "secure", "close",
                "support", "resistance", "target", "drop", "push", "stop", "hold")
    return any(k in t for k in keywords)

substantive_to_gemini = [m for m in buckets["REGEX→empty (a Gemini)"]
                         if is_substantive(m["text"])]
print(f"\nDe los {len(buckets['REGEX→empty (a Gemini)'])} a Gemini, sustantivos: {len(substantive_to_gemini)}")
print(f"Sample (10 ejemplos típicos):\n")

for i, m in enumerate(substantive_to_gemini[:10], 1):
    short = m["text"][:300].replace("\n", " | ")
    reply_tag = f"REPLY→#{m['reply_to']}" if m["is_reply"] else "STANDALONE"
    print(f"\n{i}. [{m['date'][:16]}] [{reply_tag}]")
    print(f"   {short!r}")

# ═══ Casos que CASI matchea pero escapa el regex ═══
print("\n" + "="*78)
print("CASOS PROBLEMÁTICOS — mensajes contextuales que el clasificador genérico")
print("podría malinterpretar:")
print("="*78)

# Ejemplo categories interesantes
categories = {
    "Sugerencia condicional (if X then Y)": [],
    "Acción implícita ('secure profits')": [],
    "Cancela/desaconseja entrada nueva": [],
    "Comentario direccional (price/zone)": [],
    "Cierre parcial / 'last entries'": [],
}

for m in substantive_to_gemini:
    t = m["text"].lower()
    if " if " in t and (" close" in t or " move sl" in t or " be " in t):
        categories["Sugerencia condicional (if X then Y)"].append(m)
    elif "secure" in t and ("profit" in t or "pip" in t):
        categories["Acción implícita ('secure profits')"].append(m)
    elif "don't open" in t or "do not open" in t or "no extra" in t or "stay out" in t:
        categories["Cancela/desaconseja entrada nueva"].append(m)
    elif any(p in t for p in ("zone", "support", "resistance", "we expect", "watching", "patience")):
        categories["Comentario direccional (price/zone)"].append(m)
    elif "last entries" in t or "closing our" in t or "last entry" in t:
        categories["Cierre parcial / 'last entries'"].append(m)

for cat, msgs in categories.items():
    if msgs:
        print(f"\n— {cat} ({len(msgs)} casos) —")
        for m in msgs[:3]:
            short = m["text"][:200].replace("\n", " | ")
            print(f"   [{m['date'][:16]}] {short!r}")
