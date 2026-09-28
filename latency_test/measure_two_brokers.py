"""Mide los dos brokers con UN solo MT5, alternando de cuenta en cada ronda (SOLO demo).

Usa las contrasenas que MT5 ya tiene guardadas ("Guardar contrasena"): el script
no pide ni escribe contrasenas. En cada ronda entra en cada cuenta, abre 0,01
lotes, los cierra y apunta los tiempos en un CSV. Se niega si una cuenta no es demo.

Uso:  python measure_two_brokers.py --symbol BTCUSD
"""
import argparse, csv, datetime as dt, os, sys, time

import MetaTrader5 as mt5

ACCOUNTS = [(1344177, "VTMarkets-Demo"), (24767476, "VantageMarkets-Demo")]
MAGIC = 7700101  # el bot solo gestiona sus magics (20260421/20260422)
VOLUME = 0.01


def now():
    return dt.datetime.now(dt.timezone.utc)


def send(req):
    t0 = time.perf_counter()
    res = mt5.order_send(req)
    return res, (time.perf_counter() - t0) * 1000


def round_trip(symbol, w, base):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return "sin precio"
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": VOLUME,
           "type": mt5.ORDER_TYPE_BUY, "price": tick.ask, "deviation": 50, "magic": MAGIC,
           "comment": "latency_test", "type_filling": mt5.ORDER_FILLING_IOC}
    res, ms = send(req)
    if res is not None and res.retcode == mt5.TRADE_RETCODE_INVALID_FILL:
        req["type_filling"] = mt5.ORDER_FILLING_FOK
        res, ms = send(req)
    w.writerow({**base, "utc": now().isoformat(), "leg": "open", "retcode": getattr(res, "retcode", None),
                "roundtrip_ms": round(ms, 1), "price_requested": tick.ask,
                "price_filled": getattr(res, "price", None)})
    if res is None or res.retcode != mt5.TRADE_RETCODE_DONE:
        return f"abrir fallo retcode {getattr(res, 'retcode', None)}"
    time.sleep(2)
    for p in [p for p in (mt5.positions_get(symbol=symbol) or []) if p.magic == MAGIC]:
        t2 = mt5.symbol_info_tick(symbol)
        r2, ms2 = send({"action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": p.volume,
                        "type": mt5.ORDER_TYPE_SELL, "position": p.ticket, "price": t2.bid,
                        "deviation": 50, "magic": MAGIC, "comment": "latency_test",
                        "type_filling": req["type_filling"]})
        w.writerow({**base, "utc": now().isoformat(), "leg": "close", "retcode": getattr(r2, "retcode", None),
                    "roundtrip_ms": round(ms2, 1), "price_requested": t2.bid,
                    "price_filled": getattr(r2, "price", None)})
    return f"abrir {ms:.0f} ms"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--interval-min", type=float, default=10)
    ap.add_argument("--max-rounds", type=int, default=1000)
    args = ap.parse_args()
    if not mt5.initialize():
        sys.exit(f"No conecta con MT5: {mt5.last_error()}")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       f"latencia_2brokers_{args.symbol}_{now():%Y%m%d_%H%M}.csv")
    print(f"Guardando en {out}")
    fields = ["utc", "server", "login", "symbol", "leg", "retcode", "roundtrip_ms",
              "price_requested", "price_filled", "ping_ms"]
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for i in range(args.max_rounds):
            for login, server in ACCOUNTS:
                if not mt5.login(login, server=server, timeout=30000):
                    print(f"{now():%H:%M:%S} no pude entrar en {login}@{server}: {mt5.last_error()}")
                    continue
                acc = mt5.account_info()
                if acc is None or acc.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
                    mt5.shutdown()
                    sys.exit(f"PARADO: {login} no es demo.")
                time.sleep(5)  # que termine de sincronizar
                mt5.symbol_select(args.symbol, True)
                term = mt5.terminal_info()
                base = {"server": server, "login": login, "symbol": args.symbol,
                        "ping_ms": round(term.ping_last / 1000, 1) if term else None}
                msg = round_trip(args.symbol, w, base)
                fh.flush()
                print(f"{now():%H:%M:%S} ronda {i + 1} {server}: {msg}")
                if "10027" in msg:
                    print("Activa 'Trading algoritmico' en MT5 (en verde) y relanza.")
                    mt5.shutdown()
                    return
            time.sleep(args.interval_min * 60)
    mt5.shutdown()


if __name__ == "__main__":
    main()
