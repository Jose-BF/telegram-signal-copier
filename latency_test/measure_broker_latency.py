"""Mide cuanto tarda un broker en ejecutar ordenes (SOLO cuentas DEMO).

Cada INTERVAL_MIN minutos abre 0,01 lotes y los cierra en seguida, y apunta
en un CSV los tiempos: envio -> respuesta del broker, precio pedido vs precio
obtenido. Se niega a funcionar si la cuenta no es demo.

Uso (con el MT5 del broker abierto y con la sesion iniciada):
    python measure_broker_latency.py --symbol BTCUSD
Opcional: --terminal "C:\\ruta\\terminal64.exe" si hay varios MT5 abiertos.
Parar: cerrar la ventana o Ctrl+C.
"""
import argparse, csv, datetime as dt, os, sys, time

import MetaTrader5 as mt5

MAGIC = 7700101
VOLUME = 0.01
# Magic propio: el bot solo cuenta y gestiona sus magics (20260421/20260422) e ignora este.


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def send(req):
    t0 = time.perf_counter()
    res = mt5.order_send(req)
    ms = (time.perf_counter() - t0) * 1000
    return res, ms


def running_terminals():
    """Rutas de todos los terminal64.exe abiertos en este PC."""
    import subprocess
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-Process terminal64 -ErrorAction SilentlyContinue | "
                          "Select-Object -ExpandProperty Path"],
                         capture_output=True, text=True).stdout
    return sorted({line.strip() for line in out.splitlines() if line.strip()})


def attach_to_login(login):
    for path in running_terminals():
        if mt5.initialize(path, timeout=10000):
            acc = mt5.account_info()
            if acc is not None and acc.login == login:
                print(f"Usando el MT5 de {path}")
                return acc
            mt5.shutdown()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--terminal", default=None)
    ap.add_argument("--login", type=int, default=None,
                    help="busca entre los MT5 abiertos el que tenga esta cuenta conectada")
    ap.add_argument("--interval-min", type=float, default=15)
    ap.add_argument("--max-rounds", type=int, default=700)
    args = ap.parse_args()
    if args.login:
        acc = attach_to_login(args.login)
        if acc is None:
            sys.exit(f"No encuentro ningun MT5 abierto con la cuenta {args.login}.")
    else:
        ok = mt5.initialize(args.terminal) if args.terminal else mt5.initialize()
        if not ok:
            sys.exit(f"No conecta con MT5: {mt5.last_error()}")
        acc = mt5.account_info()
    if acc is None or acc.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO:
        mt5.shutdown()
        sys.exit("PARADO: la cuenta no es DEMO. Este script solo funciona en demo.")
    if not mt5.symbol_select(args.symbol, True):
        mt5.shutdown()
        sys.exit(f"No existe el simbolo {args.symbol} en este broker.")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       f"latencia_{acc.server}_{args.symbol}_{now_utc():%Y%m%d_%H%M}.csv")
    print(f"Cuenta demo {acc.login} en {acc.server}. Guardando en {out}")
    fields = ["utc", "server", "symbol", "leg", "retcode", "comment", "roundtrip_ms",
              "price_requested", "price_filled", "slippage", "spread_points", "ping_ms"]
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for i in range(args.max_rounds):
            tick = mt5.symbol_info_tick(args.symbol)
            info = mt5.symbol_info(args.symbol)
            term = mt5.terminal_info()
            if tick is None or info is None or not info.trade_mode:
                print(f"{now_utc():%H:%M:%S} mercado cerrado o sin precio; espero")
                time.sleep(args.interval_min * 60)
                continue
            base = {"server": acc.server, "symbol": args.symbol, "spread_points": info.spread,
                    "ping_ms": round(term.ping_last / 1000, 1) if term else None}
            req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": args.symbol, "volume": VOLUME,
                   "type": mt5.ORDER_TYPE_BUY, "price": tick.ask, "deviation": 50,
                   "magic": MAGIC, "comment": "latency_test", "type_filling": mt5.ORDER_FILLING_IOC}
            res, ms = send(req)
            if res is not None and res.retcode == mt5.TRADE_RETCODE_INVALID_FILL:
                req["type_filling"] = mt5.ORDER_FILLING_FOK
                res, ms = send(req)
            w.writerow({**base, "utc": now_utc().isoformat(), "leg": "open",
                        "retcode": getattr(res, "retcode", None), "comment": getattr(res, "comment", mt5.last_error()),
                        "roundtrip_ms": round(ms, 1), "price_requested": tick.ask,
                        "price_filled": getattr(res, "price", None),
                        "slippage": (round(res.price - tick.ask, 5) if res is not None and res.price else None)})
            fh.flush()
            if res is not None and res.retcode == 10027:
                print("PARADO: MT5 tiene desactivado 'Trading algoritmico'. Activa ese boton "
                      "de la barra de MT5 (debe quedar en verde) y vuelve a lanzar el .bat.")
                break
            if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
                time.sleep(2)
                pos = [p for p in (mt5.positions_get(symbol=args.symbol) or []) if p.magic == MAGIC]
                for p in pos:
                    t2 = mt5.symbol_info_tick(args.symbol)
                    close = {"action": mt5.TRADE_ACTION_DEAL, "symbol": args.symbol, "volume": p.volume,
                             "type": mt5.ORDER_TYPE_SELL, "position": p.ticket, "price": t2.bid,
                             "deviation": 50, "magic": MAGIC, "comment": "latency_test",
                             "type_filling": req["type_filling"]}
                    r2, ms2 = send(close)
                    w.writerow({**base, "utc": now_utc().isoformat(), "leg": "close",
                                "retcode": getattr(r2, "retcode", None), "comment": getattr(r2, "comment", mt5.last_error()),
                                "roundtrip_ms": round(ms2, 1), "price_requested": t2.bid,
                                "price_filled": getattr(r2, "price", None),
                                "slippage": (round(r2.price - t2.bid, 5) if r2 is not None and r2.price else None)})
                    fh.flush()
            print(f"{now_utc():%H:%M:%S} ronda {i + 1}: abrir {ms:.0f} ms (retcode {getattr(res, 'retcode', None)})")
            time.sleep(args.interval_min * 60)
    mt5.shutdown()


if __name__ == "__main__":
    main()
