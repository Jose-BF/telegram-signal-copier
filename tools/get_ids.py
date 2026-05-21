import asyncio
from telethon import TelegramClient
from dotenv import load_dotenv
import os

load_dotenv()
API_ID   = int(os.getenv("TELEGRAM_API_ID"))
API_HASH = os.getenv("TELEGRAM_API_HASH")
PHONE    = os.getenv("TELEGRAM_PHONE")

async def main():
    client = TelegramClient("signal_session", API_ID, API_HASH)
    await client.start(phone=PHONE)

    print("\n── Tus canales y grupos ──────────────────")
    async for dialog in client.iter_dialogs():
        if dialog.is_channel or dialog.is_group:
            print(f"  ID: {dialog.id}  |  {dialog.name}")
    print("──────────────────────────────────────────\n")

    await client.disconnect()

asyncio.run(main())
