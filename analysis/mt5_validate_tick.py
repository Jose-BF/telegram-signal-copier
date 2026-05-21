"""
mt5_validate_tick.py — Paso 1 de validación con MT5 real.

Solo LEE de MT5. No abre órdenes, no modifica nada.

Objetivo: para una señal concreta (canal 1 SELL 12:24 UTC 2026-04-22),
mostrar qué pasa REALMENTE en el mercado tick a tick:

  1. Specs del símbolo (digits, point, contract size, spread actual)
  2. Zona horaria del servidor MT5
  3. Ticks bid/ask en el segundo exacto del mensaje
  4. Fill que MT5 haría para SELL market (= bid en ese instante)
  5. Comparativa vs lo que asumió mi simulador (M1 open 4761.33)
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import MetaTrader5 as mt5
except ImportError:
    print("ERROR: MetaTrader5 no instalado. Instala con: pip install MetaTrader5")
    sys.exit(1)

DATA_DIR = Path(__file__).parent / "data"
SYMBOL = "XAUUSD"

# Señal a validar
TARGET_DT = datetime(2026, 4, 22, 12, 24, tzinfo=timezone.utc)


def main():
    print("="*80)
    print("  VALIDACIÓN MT5 — paso 1 (solo lectura)")
    print("="*80)

    # 1. Inicializar MT5
    print(f"\n[1] Inicializando MT5...")
    if not mt5.initialize():
        print(f"  ERROR: {mt5.last_error()}")
        print(f"  Asegúrate de que el terminal MT5 está abierto.")
        return
    print(f"  OK")

    # 2. Info terminal y zona horaria
    ti = mt5.terminal_info()
    ai = mt5.account_info()
    print(f"\n[2] Terminal:")
    print(f"  Path:       {ti.path}")
    print(f"  Connected:  {ti.connected}")
    print(f"  Trade allow:{ti.trade_allowed}")
    if ai:
        print(f"  Cuenta:     #{ai.login} {ai.server} (balance ${ai.balance:.2f})")

    # 3. Symbol info
    print(f"\n[3] Symbol info para {SYMBOL}:")
    si = mt5.symbol_info(SYMBOL)
    if si is None:
        print(f"  ERROR: {SYMBOL} no encontrado.")
        print(f"  Símbolos disponibles que contienen 'XAU':")
        for s in mt5.symbols_get():
            if "XAU" in s.name.upper():
                print(f"    - {s.name}")
        mt5.shutdown()
        return

    if not si.visible:
        print(f"  {SYMBOL} no visible en Market Watch, lo añado...")
        mt5.symbol_select(SYMBOL, True)
        si = mt5.symbol_info(SYMBOL)

    print(f"  Name:           {si.name}")
    print(f"  Description:    {si.description}")
    print(f"  Digits:         {si.digits}")
    print(f"  Point:          {si.point}")
    print(f"  Contract size:  {si.trade_contract_size}")
    print(f"  Tick value:     {si.trade_tick_value}")
    print(f"  Tick size:      {si.trade_tick_size}")
    print(f"  Spread current: {si.spread} pts = ${si.spread * si.point:.2f}")
    print(f"  Filling mode:   {si.filling_mode}")

    # 4. Zona horaria del servidor
    cur_tick = mt5.symbol_info_tick(SYMBOL)
    if cur_tick:
        srv_dt = datetime.fromtimestamp(cur_tick.time, tz=timezone.utc)
        utc_now = datetime.now(timezone.utc)
        offset_h = round((srv_dt - utc_now).total_seconds() / 3600)
        print(f"\n[4] Zona horaria del servidor:")
        print(f"  Tick actual server time: {srv_dt}  (epoch={cur_tick.time})")
        print(f"  UTC ahora:               {utc_now}")
        print(f"  Offset estimado:         UTC{offset_h:+d}h")
        print(f"  Bid={cur_tick.bid:.2f}  Ask={cur_tick.ask:.2f}  "
              f"Spread=${cur_tick.ask-cur_tick.bid:.2f}")
    else:
        offset_h = 0
        print(f"  No tick actual disponible.")

    # 5. Buscar la señal exacta en el parquet
    print(f"\n[5] Buscando señal canal 1 cerca de {TARGET_DT}...")
    df = pd.read_parquet(DATA_DIR / "canal1_signals_2026.parquet")
    df["dt"] = pd.to_datetime(df["dt"], utc=True)
    candidates = df[(df["dt"] >= TARGET_DT) &
                    (df["dt"] < TARGET_DT + timedelta(minutes=2))]
    if len(candidates) == 0:
        print(f"  No hay señal entre {TARGET_DT} y +2min.")
        # mostrar últimas señales del día
        same_day = df[df["dt"].dt.date == TARGET_DT.date()].sort_values("dt")
        print(f"  Señales del 2026-04-22 disponibles:")
        for _, r in same_day.iterrows():
            print(f"    {r['dt']}  {r['direction']}  "
                  f"range={r['range_low']}-{r['range_high']}")
        mt5.shutdown()
        return

    sig = candidates.iloc[0]
    sig_dt = sig["dt"].to_pydatetime()
    print(f"  Encontrada: id={sig['id']}")
    print(f"  DT exacto:  {sig_dt}  (segundo {sig_dt.second})")
    print(f"  Direction:  {sig['direction']}")
    print(f"  Range:      {sig['range_low']}-{sig['range_high']}")

    # 6. Pull ticks reales en una ventana ±2 min alrededor del mensaje
    # El servidor está en UTC+offset_h, así que para pedir ticks tenemos
    # que pasar tiempos en zona del servidor (no UTC).
    # NOTA: copy_ticks_range espera datetime que se interpreta como server time.
    sig_srv = sig_dt + timedelta(hours=offset_h)
    t_from = sig_srv - timedelta(seconds=60)
    t_to   = sig_srv + timedelta(seconds=120)

    print(f"\n[6] Pidiendo ticks reales:")
    print(f"  Ventana (server time): {t_from} → {t_to}")
    ticks = mt5.copy_ticks_range(SYMBOL, t_from, t_to, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        print(f"  ERROR: no hay ticks. last_error={mt5.last_error()}")
        print(f"  Posibles causas:")
        print(f"    - History de ticks no descargada (Tools→Options→Charts→Max bars)")
        print(f"    - Fechas fuera de rango disponible")
        print(f"    - Símbolo no operable en ese momento (fin de semana, etc.)")
        mt5.shutdown()
        return

    print(f"  OK: {len(ticks)} ticks recibidos")

    ticks_df = pd.DataFrame(ticks)
    ticks_df["time_utc"] = pd.to_datetime(ticks_df["time_msc"], unit="ms", utc=True)
    # corregir de server-time a UTC restando el offset
    ticks_df["time_utc"] = ticks_df["time_utc"] - pd.Timedelta(hours=offset_h)

    # 7. Tick exacto en el segundo del mensaje
    print(f"\n[7] Ticks alrededor del mensaje ({sig_dt}):")
    print(f"  {'tick UTC':<24} {'bid':>9} {'ask':>9} {'spread':>7} {'flags':>5}")
    for _, t in ticks_df.iterrows():
        if abs((t["time_utc"] - sig_dt).total_seconds()) < 5:
            mark = " ← mensaje" if abs((t["time_utc"] - sig_dt).total_seconds()) < 1 else ""
            print(f"  {str(t['time_utc']):<24} {t['bid']:>9.2f} {t['ask']:>9.2f} "
                  f"{t['ask']-t['bid']:>7.2f} {int(t['flags']):>5}{mark}")

    # 8. ¿Qué fill haría MT5?
    after = ticks_df[ticks_df["time_utc"] >= sig_dt]
    if len(after) > 0:
        fill = after.iloc[0]
        sell_fill = fill["bid"]   # SELL market fills at bid
        buy_fill  = fill["ask"]   # BUY market fills at ask
        print(f"\n[8] Fill que MT5 haría:")
        print(f"  Primer tick ≥ mensaje: {fill['time_utc']}")
        print(f"  Bid={fill['bid']:.2f}  Ask={fill['ask']:.2f}  "
              f"Spread=${fill['ask']-fill['bid']:.2f}")
        print(f"  → SELL market fillaría @ {sell_fill:.2f}")
        print(f"  → BUY  market fillaría @ {buy_fill:.2f}")

        # Comparativa con mi simulador
        m1_open_assumption = 4761.33
        print(f"\n[9] Delta vs mi simulador:")
        print(f"  Mi sim asumía: ${m1_open_assumption:.2f} (M1 open + slippage 0.50)")
        print(f"  MT5 real SELL: ${sell_fill:.2f}")
        print(f"  Delta: ${sell_fill - m1_open_assumption:+.2f}")
        in_range = sig['range_low'] <= sell_fill <= sig['range_high']
        print(f"  ¿Fill REAL dentro del rango {sig['range_low']}-{sig['range_high']}? "
              f"{'SÍ' if in_range else 'NO → safety check cancelaría'}")

    # 9. Movimiento posterior (slippage si hubiera latencia)
    print(f"\n[10] Comportamiento del precio en los siguientes 30s:")
    follow = ticks_df[(ticks_df["time_utc"] >= sig_dt) &
                      (ticks_df["time_utc"] <= sig_dt + timedelta(seconds=30))]
    if len(follow) > 0:
        print(f"  Bid min:  {follow['bid'].min():.2f}")
        print(f"  Bid max:  {follow['bid'].max():.2f}")
        print(f"  Bid last: {follow['bid'].iloc[-1]:.2f}")
        print(f"  N ticks:  {len(follow)}")

    mt5.shutdown()
    print(f"\n{'='*80}\n  Listo. MT5 desconectado.\n{'='*80}")


if __name__ == "__main__":
    main()
