"""
Extrae señales 2026 de canal 1 (DT Investing) y canal 2 (Gold Standard)
del JSON gigante (521MB) usando ijson en streaming, y las cachea a parquet
para que los simuladores carguen rápido sin OOM.

Output:
  data/canal1_signals_2026.parquet
  data/canal2_signals_2026.parquet
"""

import sys, re, json
from datetime import datetime
from pathlib import Path

import ijson
import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

JSON_PATH = r"C:\Users\josea\Downloads\Telegram Desktop\DataExport_2026-04-22\result.json"
OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True)

CANAL_1 = 1642806869
CANAL_2 = 2614601304


def extract_text(field):
    if isinstance(field, str): return field
    if isinstance(field, list):
        return "".join(p if isinstance(p, str) else p.get("text", "") for p in field)
    return ""


def stream_chats():
    """Stream cada chat (con sus mensajes) sin cargar todo el JSON."""
    with open(JSON_PATH, "rb") as f:
        # JSON estructura: {"chats": {"list": [chat1, chat2, ...]}}
        # Iterar sobre cada chat
        for chat in ijson.items(f, "chats.list.item"):
            yield chat


def parse_canal1(msgs, out_rows):
    """Empareja sticker -> texto siguiente con TPs/SL."""
    for i, m in enumerate(msgs):
        if m.get("media_type") != "sticker": continue
        date = m.get("date", "")
        if date < "2026-01-01": continue
        try: dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
        except: continue

        # Buscar siguiente texto BUY/SELL en 5 min
        for j in range(i+1, min(i+8, len(msgs))):
            nxt = msgs[j]
            ndate = nxt.get("date", "")
            if not ndate: continue
            try: ndt = datetime.fromisoformat(ndate.replace("Z", "+00:00"))
            except: continue
            if (ndt - dt).total_seconds() > 300: break
            txt = extract_text(nxt.get("text", ""))
            if not txt: continue
            up = txt.upper()
            if "BUY" not in up and "SELL" not in up: continue

            tps = [float(t.group(1)) for t in
                   re.finditer(r"TP\d+\s*[:\s]\s*(\d{3,5}(?:\.\d{1,3})?)\b",
                                txt, re.IGNORECASE)]
            if not tps: continue
            slm = re.search(r"SL\s*[:\s]\s*(\d{3,5}(?:\.\d{1,3})?)\b",
                             txt, re.IGNORECASE)
            if not slm: continue
            sl = float(slm.group(1))

            em = re.search(r"NOW\s*@?\s*(\d{3,5}(?:\.\d{1,3})?)",
                            txt, re.IGNORECASE)
            entry = float(em.group(1)) if em else None

            rng_lo, rng_hi = None, None
            rm = re.search(r"(\d{3,5}(?:\.\d+)?)\s*[-/\u2013]\s*(\d{2,5}(?:\.\d+)?)", txt)
            if rm:
                try:
                    a, b = float(rm.group(1)), float(rm.group(2))
                    bs = rm.group(2)
                    if b < 100 and "." not in bs:
                        b = int(a/100)*100 + b
                    if abs(a-b) <= 30 and 1500 <= a <= 5500 and 1500 <= b <= 5500:
                        rng_lo, rng_hi = min(a,b), max(a,b)
                except: pass

            direction = "BUY" if "BUY" in up else "SELL"
            # Sanity: el primer TP debe ir en dirección
            if entry:
                if direction == "BUY" and tps[0] <= entry: break
                if direction == "SELL" and tps[0] >= entry: break

            out_rows.append({
                "channel": "canal1",
                "id": m["id"],
                "dt": dt,
                "direction": direction,
                "entry": entry,
                "range_low": rng_lo, "range_high": rng_hi,
                "tp1": tps[0] if len(tps) > 0 else None,
                "tp2": tps[1] if len(tps) > 1 else None,
                "tp3": tps[2] if len(tps) > 2 else None,
                "tp4": tps[3] if len(tps) > 3 else None,
                "tp5": tps[4] if len(tps) > 4 else None,
                "sl": sl,
                "raw_text": txt[:300],
            })
            break


def parse_canal2(msgs, out_rows):
    for m in msgs:
        date = m.get("date", "")
        if date < "2026-01-01": continue
        txt = extract_text(m.get("text", ""))
        if not txt: continue
        up = txt.upper()
        if "BUY NOW" not in up and "SELL NOW" not in up: continue
        try: dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
        except: continue

        direction = "BUY" if "BUY" in up else "SELL"
        rng_lo, rng_hi = None, None
        rm = re.search(r"(\d{4}(?:\.\d+)?)\s*[-\u2013]\s*(\d{4}(?:\.\d+)?)", txt)
        if rm:
            try:
                lo, hi = float(rm.group(1)), float(rm.group(2))
                if lo > hi: lo, hi = hi, lo
                if abs(hi - lo) <= 30 and 1500 <= lo <= 5500 and 1500 <= hi <= 5500:
                    rng_lo, rng_hi = lo, hi
            except: pass
        if rng_lo is None: continue

        tps = [float(t.group(1)) for t in
               re.finditer(r"TP\d+\s*[:\s]\s*(\d{3,5}(?:\.\d{1,3})?)\b",
                            txt, re.IGNORECASE)]
        if not tps: continue
        slm = re.search(r"SL\s*[:\s]\s*(\d{3,5}(?:\.\d{1,3})?)\b",
                         txt, re.IGNORECASE)
        if not slm: continue
        sl = float(slm.group(1))

        out_rows.append({
            "channel": "canal2",
            "id": m["id"], "dt": dt, "direction": direction,
            "entry": None,
            "range_low": rng_lo, "range_high": rng_hi,
            "tp1": tps[0] if len(tps) > 0 else None,
            "tp2": tps[1] if len(tps) > 1 else None,
            "tp3": tps[2] if len(tps) > 2 else None,
            "tp4": tps[3] if len(tps) > 3 else None,
            "tp5": tps[4] if len(tps) > 4 else None,
            "sl": sl,
            "raw_text": txt[:300],
        })


def main():
    print("Streaming JSON 521MB...", flush=True)
    rows1, rows2 = [], []

    for chat in stream_chats():
        cid = chat.get("id")
        msgs = chat.get("messages", [])
        if cid == CANAL_1:
            print(f"  Canal 1 (DT Investing): {len(msgs)} mensajes")
            parse_canal1(msgs, rows1)
        elif cid == CANAL_2:
            print(f"  Canal 2 (Gold Standard): {len(msgs)} mensajes")
            parse_canal2(msgs, rows2)

    df1 = pd.DataFrame(rows1)
    df2 = pd.DataFrame(rows2)
    print(f"\nCanal 1 señales 2026: {len(df1)}")
    print(f"Canal 2 señales 2026: {len(df2)}")

    df1.to_parquet(OUT / "canal1_signals_2026.parquet", index=False)
    df2.to_parquet(OUT / "canal2_signals_2026.parquet", index=False)
    print(f"\nGuardado en {OUT}")


if __name__ == "__main__":
    main()
