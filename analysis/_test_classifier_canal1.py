"""Mide qué tan bien el classifier actual maneja mensajes reales de canal 1."""
import io, json, os, sys
sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from classifier import _regex_classify_all
from pathlib import Path

EXPORT = Path(r"C:\Users\josea\Downloads\Telegram Desktop\ChatExport_2026-04-29\result.json")
with open(EXPORT, encoding="utf-8") as f:
    export = json.load(f)

def get_text(m):
    text = m.get("text", "")
    if isinstance(text, list):
        return "".join(t.get("text", "") if isinstance(t, dict) else str(t) for t in text)
    return str(text) if text else ""

# Filtrar últimos 14 días, mensajes con caption o texto puro (no entry)
from datetime import datetime, timedelta
CUTOFF = (datetime.now() - timedelta(days=14)).isoformat()

mgmt_candidates = []
for m in export.get("messages", []):
    if m.get("date", "") < CUTOFF:
        continue
    if m.get("media_type") == "sticker":
        continue
    text = get_text(m).strip()
    if not text:
        continue
    if "BUY GOLD NOW" in text.upper() or "SELL GOLD NOW" in text.upper():
        continue  # son entries, no mgmt
    mgmt_candidates.append({
        "id": m.get("id"),
        "date": m.get("date"),
        "is_reply": bool(m.get("reply_to_message_id")),
        "text": text,
    })

print(f"Mgmt candidates en últimos 14 días: {len(mgmt_candidates)}\n")

# Clasifica cada uno con el regex actual
results = []
for m in mgmt_candidates:
    actions = _regex_classify_all(m["text"])
    if not actions:
        category = "EMPTY (caería a Gemini)"
    elif len(actions) == 1 and actions[0]["action"] == "INFORMATIONAL":
        category = "INFO (regex)"
    else:
        action_str = ",".join(a["action"] for a in actions)
        category = f"ACCION: {action_str}"
    results.append({**m, "category": category, "actions": actions})

# Stats
from collections import Counter
cats = Counter(r["category"] for r in results)
print("Distribución de clasificación regex:")
for cat, n in cats.most_common():
    print(f"  {cat:<35} {n}")

# Ejemplos de cada categoría
print("\n\n=== EJEMPLOS DE 'EMPTY' (caen a Gemini) ===")
for r in [x for x in results if "EMPTY" in x["category"]][:10]:
    short = r["text"][:200].replace("\n", " | ")
    print(f"\n  [{r['date']}] {short!r}")

print("\n\n=== EJEMPLOS DE 'INFO regex' (mensajes que regex marca como info) ===")
for r in [x for x in results if x["category"] == "INFO (regex)"][:5]:
    short = r["text"][:200].replace("\n", " | ")
    print(f"\n  [{r['date']}] {short!r}")

print("\n\n=== EJEMPLOS DE 'ACCION' (regex detectó acción) ===")
for r in [x for x in results if "ACCION" in x["category"]][:5]:
    short = r["text"][:200].replace("\n", " | ")
    print(f"\n  [{r['date']}] {r['category']}")
    print(f"     {short!r}")
