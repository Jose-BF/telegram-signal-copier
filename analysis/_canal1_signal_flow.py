"""Análisis del flujo completo señal-por-señal del canal 1.

Para cada sticker, muestra TODOS los mensajes en la siguiente hora con
la cadena de replies (sticker → text → photo replies → ...).
"""
import io, json, sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

EXPORT = Path(r"C:\Users\josea\Downloads\Telegram Desktop\ChatExport_2026-04-29\result.json")
with open(EXPORT, encoding="utf-8") as f:
    export = json.load(f)
messages = export.get("messages", [])

def get_text(m):
    text = m.get("text", "")
    if isinstance(text, list):
        return "".join(t.get("text", "") if isinstance(t, dict) else str(t) for t in text)
    return str(text) if text else ""

def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None

CUTOFF = (datetime.now() - timedelta(days=14)).isoformat()
recent = [m for m in messages if m.get("date", "") >= CUTOFF]
stickers = [m for m in recent if m.get("media_type") == "sticker"]

# Para cada sticker, recoge TODOS los mensajes en los siguientes 60 min
# (no filtramos por chain — vemos todo el contexto)
for sticker in stickers[-5:]:  # solo los 5 últimos
    sid = sticker.get("id")
    sdate = sticker.get("date", "")
    s_dt = parse_dt(sdate)
    print(f"\n{'='*100}")
    print(f"STICKER #{sid}  {sdate}  file={sticker.get('file', '?')[-40:]}")
    print(f"{'='*100}")
    if not s_dt: continue

    for m in messages:
        m_dt = parse_dt(m.get("date", ""))
        if not m_dt or m_dt < s_dt: continue
        if (m_dt - s_dt).total_seconds() > 3600: break  # 60 min ventana
        if m.get("id") == sid: continue

        delta_s = (m_dt - s_dt).total_seconds()
        delta_str = f"+{int(delta_s//60):>2}m{int(delta_s%60):>2}s"
        text = get_text(m).strip().replace("\n", " | ")
        kind = "STICK" if m.get("media_type") == "sticker" else \
               "PHOTO" if (m.get("photo") or "photo" in str(m.get("file",""))) else \
               "TEXT"
        rid = m.get("reply_to_message_id")
        reply_str = f"→#{rid}" if rid else "[STANDALONE]"
        # Quita el emoji ✅🔥 etc para output limpio
        clean = text.encode("ascii", errors="replace").decode("ascii")[:130]
        print(f"  [{delta_str}] [{kind}] [#{m.get('id'):>5}] [{reply_str:<12}]  {clean!r}")
