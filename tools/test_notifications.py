"""
test_notifications.py — Prueba los 3 tipos de notificación al usuario.

Uso:
    python tools/test_notifications.py

Envía por Telegram (a NOTIFY_CHAT_ID, default "me" = Mensajes Guardados):
  1. Notificación genérica simple
  2. Time-stop notify (con señal fake realista)
  3. Ambiguous decision notify (con señal fake + clasificación Gemini simulada)

No abre ninguna posición real. No modifica nada en MT5.
"""

import asyncio
import sys
import os

# Añadir el directorio raíz del proyecto al path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config
import executor
from state import Signal
from telethon import TelegramClient
from datetime import datetime, timedelta


# ─── Señal fake realista ─────────────────────────────────────────────────────

def _make_fake_signal() -> Signal:
    """Construye una Signal de prueba con datos plausibles."""
    sig = Signal(
        channel="canal2",
        message_id=99999,
        direction="BUY",
        timestamp=datetime.utcnow() - timedelta(minutes=67),
    )
    sig.market_ticket     = None          # sin ticket real
    sig.market_fill_price = 4571.50
    sig.tps  = [4574.0, 4577.0, 4580.0, 4583.0, 4587.0]
    sig.sl   = 4562.0
    sig.be_armed = False
    sig.time_stop_at = datetime.utcnow() - timedelta(minutes=7)  # ya expirado
    sig.be_at_tp_index = 0
    sig.entry_mode = "intra_dca"
    sig.dca_tickets = []
    return sig


# ─── Notify 1: genérico ──────────────────────────────────────────────────────

async def test_notify_generic(client, notify_fn):
    print("\n[TEST 1] Notificación genérica...")
    await notify_fn(
        "🔔 TEST NOTIFICACIÓN GENÉRICA\n"
        "\n"
        "El bot puede enviarte mensajes directamente a Telegram.\n"
        "Este es el canal que recibirá:\n"
        "  • Avisos de time-stop\n"
        "  • Decisiones ambiguas (Gemini no seguro)\n"
        "  • Eventos críticos futuros\n"
        "\n"
        "Si ves este mensaje, la notificación genérica funciona ✅"
    )
    print("[TEST 1] Enviado.")


# ─── Notify 2: time-stop ─────────────────────────────────────────────────────

async def test_notify_timestop(notify_fn):
    print("\n[TEST 2] Time-stop notify...")

    # Importar la función interna del dca_monitor
    from dca_monitor import _notify_time_stop, _floating_pl_summary

    sig = _make_fake_signal()
    elapsed_min = (datetime.utcnow() - sig.timestamp).total_seconds() / 60

    # _notify_time_stop necesita el `notify` importado desde listener.
    # Como estamos fuera del bot, lo monkey-patching temporalmente.
    import dca_monitor as _dm
    import listener as _ln
    _ln._notify_peer = None   # fuerza re-resolución del peer

    # Ejecutar la notificación directamente (sin señal real en MT5)
    # Parcheamos _floating_pl_summary para que devuelva datos fake
    _real_fpl = _dm._floating_pl_summary

    def _fake_fpl(signal):
        return {
            "pl": -4.32,
            "n_open": 1,
            "avg_entry": 4571.50,
            "current_price": 4569.80,
            "lots_total": 0.01,
        }

    _dm._floating_pl_summary = _fake_fpl
    try:
        await _notify_time_stop(sig, elapsed_min)
        print("[TEST 2] Enviado.")
    finally:
        _dm._floating_pl_summary = _real_fpl


# ─── Notify 3: decisión ambigua ──────────────────────────────────────────────

async def test_notify_ambiguous(notify_fn):
    print("\n[TEST 3] Ambiguous decision notify...")

    from listener import notify_ambiguous_decision
    from state import TradeContext

    sig = _make_fake_signal()
    sig.market_ticket = 9999999   # ticket fake para build_context

    # Monkey-patch Signal.build_context para devolver datos fake sin MT5
    fake_ctx = TradeContext(
        channel="canal2",
        signal_id="canal2_99999",
        direction="BUY",
        entry_price=4571.50,
        tps=[4574.0, 4577.0, 4580.0, 4583.0, 4587.0],
        sl=4562.0,
        n_initial=1,
        n_open=1,
        open_tickets_pnl=[(9999999, -4.32)],
        floating_pnl_total=-4.32,
        elapsed_min=67.3,
        current_price=4569.80,
        be_armed=False,
    )

    original_bc = Signal.build_context
    Signal.build_context = lambda self: fake_ctx

    fake_classification = {
        "action": "MOVE_SL_TO_BE",
        "confidence": 0.62,
        "reasoning": "El trader dice 'protect capital' pero no especifica precio. "
                     "Podría ser BE o podría ser CLOSE_FIRST. Confianza media.",
    }
    raw_text = (
        "Gold traders, the market is moving fast.\n"
        "Please protect your capital now.\n"
        "Move to breakeven if possible."
    )

    try:
        await notify_ambiguous_decision(sig, fake_classification, raw_text)
        print("[TEST 3] Enviado.")
    finally:
        Signal.build_context = original_bc


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 55)
    print("  Test de Notificaciones")
    print("=" * 55)
    print(f"  Destino: NOTIFY_CHAT_ID = {config.NOTIFY_CHAT_ID!r}")
    print()

    # Iniciar cliente Telethon (usa la misma sesión del bot)
    client = TelegramClient(
        "../signal_session",
        config.TELEGRAM_API_ID,
        config.TELEGRAM_API_HASH,
    )
    await client.start(phone=config.TELEGRAM_PHONE)
    me = await client.get_me()
    print(f"Conectado como: {me.first_name} (@{me.username})")

    # Inyectar el cliente en listener para que notify() funcione
    import listener as _ln
    _ln.client = client
    _ln._notify_peer = None

    async def notify_fn(text: str):
        await _ln.notify(text)

    # Ejecutar los 3 tests
    await test_notify_generic(client, notify_fn)
    await asyncio.sleep(1)
    await test_notify_timestop(notify_fn)
    await asyncio.sleep(1)
    await test_notify_ambiguous(notify_fn)

    print("\n" + "=" * 55)
    print("  Tests completados. Revisa Telegram.")
    print("=" * 55)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
