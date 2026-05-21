"""Muestra los últimos 5 usos de cada sticker con el texto que vino después."""
import asyncio
from telethon import TelegramClient
from dotenv import load_dotenv
import os

load_dotenv()
API_ID     = int(os.getenv("TELEGRAM_API_ID"))
API_HASH   = os.getenv("TELEGRAM_API_HASH")
CANAL_1_ID = int(os.getenv("CANAL_1_ID"))
BUY_ID     = int(os.getenv("CANAL1_BUY_STICKER_ID").strip("'"))
SELL_ID    = int(os.getenv("CANAL1_SELL_STICKER_ID").strip("'"))

async def main():
    client = TelegramClient("signal_session", API_ID, API_HASH)
    await client.connect()

    messages = []
    async for msg in client.iter_messages(CANAL_1_ID, limit=500):
        messages.append(msg)
    # Mantener orden: newest first (como devuelve Telethon)
    # Para el texto siguiente necesitamos el mensaje cronológicamente posterior = índice mayor en lista invertida
    messages_chrono = list(reversed(messages))  # oldest first, para buscar "siguiente"

    for label, sid in [("BUY", BUY_ID), ("SELL", SELL_ID)]:
        print(f"\n── Sticker {label} (ID={sid}) ──────────────")
        count = 0
        for i, msg in enumerate(messages_chrono):
            if not msg.sticker or msg.sticker.id != sid:
                continue
            count += 1
            siguiente = ""
            for j in range(i+1, min(i+4, len(messages_chrono))):
                if messages_chrono[j].text:
                    siguiente = messages_chrono[j].text[:60]
                    break
            hora = msg.date.strftime("%Y-%m-%d %H:%M UTC")
            print(f"  {hora} → {siguiente or '(sin texto siguiente)'}")

        print(f"  (total en ventana de 300 msgs: {count})")

    await client.disconnect()

asyncio.run(main())
