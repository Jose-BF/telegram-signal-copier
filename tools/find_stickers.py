"""
Identifica automáticamente qué sticker es BUY y cuál es SELL
mirando el mensaje de texto que llega tras cada sticker.
Actualiza el .env directamente.
"""
import asyncio
from telethon import TelegramClient
from dotenv import load_dotenv, set_key
import os

load_dotenv()
API_ID     = int(os.getenv("TELEGRAM_API_ID"))
API_HASH   = os.getenv("TELEGRAM_API_HASH")
CANAL_1_ID = int(os.getenv("CANAL_1_ID"))
ENV_FILE   = os.path.join(os.path.dirname(__file__), ".env")

async def main():
    client = TelegramClient("signal_session", API_ID, API_HASH)
    await client.connect()

    print("\nAnalizando historial de Canal 1...\n")

    # Carga todos los mensajes (más antiguo primero para analizar secuencia)
    messages = []
    async for msg in client.iter_messages(CANAL_1_ID, limit=300):
        messages.append(msg)
    messages.reverse()  # cronológico

    sticker_direction = {}  # {sticker_id: "BUY" | "SELL"}

    for i, msg in enumerate(messages):
        if not msg.sticker:
            continue
        sid = msg.sticker.id
        if sid in sticker_direction:
            continue

        # Busca en los siguientes 5 mensajes el texto de seguimiento
        for j in range(i + 1, min(i + 6, len(messages))):
            text = (messages[j].text or "").upper()
            if "BUY GOLD NOW" in text or "BUY GOLD" in text:
                sticker_direction[sid] = "BUY"
                break
            elif "SELL GOLD NOW" in text or "SELL GOLD" in text:
                sticker_direction[sid] = "SELL"
                break

    print("Resultado:")
    buy_id = sell_id = None
    for sid, direction in sticker_direction.items():
        print(f"  {direction}: {sid}")
        if direction == "BUY":
            buy_id = sid
        else:
            sell_id = sid

    if buy_id and sell_id:
        set_key(ENV_FILE, "CANAL1_BUY_STICKER_ID",  str(buy_id))
        set_key(ENV_FILE, "CANAL1_SELL_STICKER_ID", str(sell_id))
        print("\n✓ .env actualizado automáticamente.")
        print("  Reinicia el bot para que Canal 1 quede operativo.")
    else:
        print("\n⚠ No se pudo determinar la dirección de algún sticker.")
        print("  Revisa el historial manualmente.")

    await client.disconnect()

asyncio.run(main())
