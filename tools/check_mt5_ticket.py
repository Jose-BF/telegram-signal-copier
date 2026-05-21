"""
check_mt5_ticket.py — Verifica qué dice MT5 realmente sobre un ticket.

Uso:
    python tools/check_mt5_ticket.py 1266500881

Cuando el bot loguea `market_filled ticket=X` pero en la GUI de MT5 no
aparece la posición, este script ayuda a saber dónde está la verdad:

  1. Conecta a MT5 con la MISMA cuenta que el bot (.env MT5_LOGIN).
  2. Imprime info de la cuenta para que verifiques que es la correcta.
  3. Busca el ticket en posiciones abiertas, en ordenes pendientes,
     y en TODO el historial reciente (24h, deals + orders).
  4. Si lo encuentra, muestra todos los detalles. Si no, lo confirma.

Resultado típico:
  - "Encontrado en positions_get" → la posición existe; revisar GUI.
  - "Encontrado en history_deals" → abrió y cerró (mira el deal_out).
  - "NO existe en MT5" → bug crítico: el bot logueó un ticket fantasma.
"""

import os
import sys
from pathlib import Path

# Permitir importar config desde la raíz del proyecto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
from datetime import datetime, timedelta

import config


def main(ticket: int) -> int:
    print(f"\n=== check_mt5_ticket: buscando #{ticket} ===\n")

    # Conexión MT5 igual que el bot
    if not mt5.initialize(
        login=config.MT5_LOGIN,
        password=config.MT5_PASSWORD,
        server=config.MT5_SERVER,
    ):
        print(f"[!] mt5.initialize falló: {mt5.last_error()}")
        return 1

    try:
        # Info de la cuenta para verificar que es la que esperamos
        info = mt5.account_info()
        term = mt5.terminal_info()
        if info:
            print(f"Cuenta MT5: login={info.login} server={info.server!r} "
                  f"name={info.name!r} balance={info.balance} equity={info.equity}")
        if term:
            print(f"Terminal:   path={term.path!r} connected={term.connected} "
                  f"trade_allowed={term.trade_allowed}")
        print(f"Símbolo configurado: {config.MT5_SYMBOL}")
        print()

        found_anywhere = False

        # 1) Posiciones abiertas
        pos = mt5.positions_get(ticket=ticket)
        if pos:
            found_anywhere = True
            for p in pos:
                print(f"[ABIERTA] ticket={p.ticket} {p.symbol} "
                      f"{'BUY' if p.type==0 else 'SELL'} {p.volume} "
                      f"@{p.price_open} sl={p.sl} tp={p.tp} profit={p.profit} "
                      f"magic={p.magic} comment={p.comment!r} "
                      f"opened={datetime.utcfromtimestamp(p.time).isoformat()}")
        else:
            print(f"[ABIERTA] No se encuentra como posición abierta.")

        # 2) Órdenes pendientes
        ord_pend = mt5.orders_get(ticket=ticket)
        if ord_pend:
            found_anywhere = True
            for o in ord_pend:
                print(f"[PENDIENTE] ticket={o.ticket} {o.symbol} type={o.type} "
                      f"volume={o.volume_initial} price={o.price_open} "
                      f"magic={o.magic} comment={o.comment!r}")
        else:
            print(f"[PENDIENTE] No se encuentra como orden pendiente.")

        # 3) Historial — ventana de 7 días
        date_from = datetime.now() - timedelta(days=7)
        date_to = datetime.now() + timedelta(hours=1)

        # 3a) Deals por position id
        deals_by_pos = mt5.history_deals_get(position=ticket)
        if deals_by_pos:
            found_anywhere = True
            print(f"\n[HISTORIAL DEALS por position={ticket}]")
            for d in deals_by_pos:
                ts = datetime.utcfromtimestamp(d.time).isoformat()
                print(f"  deal={d.ticket} order={d.order} {d.symbol} "
                      f"type={d.type} entry={d.entry} volume={d.volume} "
                      f"price={d.price} profit={d.profit} comm={d.commission} "
                      f"swap={d.swap} magic={d.magic} comment={d.comment!r} "
                      f"time={ts}")
        else:
            print(f"\n[HISTORIAL DEALS por position={ticket}] No hay deals.")

        # 3b) Order history por ticket
        orders_hist = mt5.history_orders_get(ticket=ticket)
        if orders_hist:
            found_anywhere = True
            print(f"\n[HISTORIAL ORDERS ticket={ticket}]")
            for o in orders_hist:
                ts_setup = datetime.utcfromtimestamp(o.time_setup).isoformat()
                ts_done = datetime.utcfromtimestamp(o.time_done).isoformat() if o.time_done else "(no completado)"
                print(f"  order={o.ticket} {o.symbol} type={o.type} state={o.state} "
                      f"volume={o.volume_initial}/{o.volume_current} "
                      f"price={o.price_open} magic={o.magic} "
                      f"reason={o.reason} comment={o.comment!r} "
                      f"setup={ts_setup} done={ts_done}")
        else:
            print(f"\n[HISTORIAL ORDERS ticket={ticket}] No hay orders.")

        # 3c) Búsqueda en deals/orders en ventana temporal por si el ticket
        #     que recibió el bot se "transformó" en otro id en historial
        print(f"\n=== Deals últimas 24h del símbolo {config.MT5_SYMBOL} ===")
        deals_window = mt5.history_deals_get(date_from, date_to)
        if deals_window:
            recent = [d for d in deals_window if d.symbol == config.MT5_SYMBOL][-15:]
            for d in recent:
                ts = datetime.utcfromtimestamp(d.time).isoformat()
                print(f"  [{ts}] deal={d.ticket} order={d.order} pos={d.position_id} "
                      f"type={d.type} entry={d.entry} vol={d.volume} price={d.price} "
                      f"profit={d.profit} magic={d.magic} comment={d.comment!r}")
        else:
            print("  (sin deals)")

        print(f"\n=== Resumen ===")
        if found_anywhere:
            print(f"✓ Ticket {ticket} EXISTE en MT5 (mira arriba dónde).")
        else:
            print(f"✗ Ticket {ticket} NO EXISTE en MT5 en ningún sitio.")
            print(f"  Esto significa que mt5.order_send devolvió DONE pero la")
            print(f"  orden no llegó al broker, o el bot está conectado a una")
            print(f"  cuenta DISTINTA de la que estás revisando.")
            print(f"  Verifica en la cuenta de arriba (login={info.login if info else '?'}) "
                  f"que es la misma que ves en tu MT5 GUI.")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python tools/check_mt5_ticket.py <ticket>")
        sys.exit(1)
    try:
        ticket = int(sys.argv[1])
    except ValueError:
        print(f"Ticket debe ser numérico, recibido: {sys.argv[1]!r}")
        sys.exit(1)
    sys.exit(main(ticket))
