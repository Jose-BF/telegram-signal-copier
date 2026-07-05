# Caja Negra y Replay Forense

**Fecha:** 2026-05-21
**Proyecto:** telegram-signal-copier v1
**Estado:** aprobado para implementacion inicial

## Objetivo

Completar la observabilidad del bot sin cambiar ninguna decision de trading.
La meta es que cada operacion pueda reconstruirse desde tres fuentes:

- `data/trade_events.jsonl`: decisiones y acciones del bot, en orden.
- Historial MT5 via `reconcile_mt5_ledger.py`: fills, cierres y P&L real.
- Ticks MT5 via `copy_ticks_range`: replay de precio bid/ask entre entrada y cierre.

El resultado debe permitir responder, trade por trade, por que se gano o se
perdio, que hizo el canal, que hizo el bot, que confirmo MT5 y que hubiera
pasado con reglas alternativas.

## No-objetivos

- No cambiar lotaje, `scale_out`, time-stop, BE ni ninguna estrategia activa.
- No cerrar automaticamente operaciones que hoy solo notifican.
- No hacer dashboard web.
- No optimizar parametros con OOS.

## Diagnostico

`reconcile_mt5_ledger.py` ya es la base correcta: cruza journal con historial MT5 y genera
`data/ledger.jsonl`. El problema es que el ledger todavia no contiene toda la
cadena causal:

- No queda registrado el `order_send` completo para cada apertura.
- `sl_history` y `tp_history` estan vacios.
- Un mensaje de gestion puede quedar como `applied=True` aunque la modificacion
MT5 todavia este en cola, haya fallado o se haya confirmado despues.
- El time-stop notify-only no marca el desenlace posterior.
- Falta guardar por senal el snapshot de estrategia/config que estaba activo.

## Diseno

### 1. Eventos MT5 de caja negra

Emitir eventos estructurados sin bloquear el hot path:

- `mt5_order_requested`
- `mt5_order_result`
- `mt5_modify_requested`
- `mt5_modify_confirmed`
- `mt5_close_requested`
- `mt5_close_result`
- `mt5_cancel_requested`
- `mt5_cancel_result`

Cada evento debe llevar `ticket`, `retcode`, `new_sl`, `new_tp`, `label`,
`attempts`, `magic` y datos relevantes disponibles. Para aperturas, el
`sig_id` se deriva del `comment` (`c1_...`, `c2_...`, `DCA_c...`,
`..._rescue`, `..._B1`).

### 2. Snapshot de estrategia por senal

Emitir `strategy_snapshot` al crear cada `Signal`, con:

- `entry_mode`
- `num_entries`
- `target_tp_index`
- `be_at_tp_index`
- `time_stop_min`
- `time_stop_at`
- `adverse_action`
- `effective_lot`
- `magic`

Esto permite comparar resultados antes/despues de commits sin depender de la
memoria humana.

### 3. Historial SL/TP en ledger

`reconcile_mt5_ledger.py` debe consumir los eventos MT5 nuevos y rellenar por posicion:

```json
"sl_history": [
  {"ts": "...", "sl": 4525.0, "source": "SL #123", "status": "requested"},
  {"ts": "...", "sl": 4525.0, "source": "SL #123", "status": "confirmed"}
]
```

Lo mismo para `tp_history`. El estado confirmado es el que se usa para
forensics; los requests fallidos ayudan a explicar huecos.

### 4. Time-stop como outcome medible

Cuando salta `time_stop_notified`, registrar tambien una anomaly `outcome`
warning. Al reconciliar, derivar `post_time_stop_outcome`:

- `sl_after_time_stop`
- `profit_after_time_stop`
- `loss_after_time_stop`
- `manual_or_bot_close_after_time_stop`
- `open_after_time_stop`

Si una operacion acaba en SL despues del aviso, el ledger debe marcar una
anomaly derivada `outcome/warning`. Esto no cambia la operativa, solo lo hace
medible.

### 5. Replay forense posterior

Esta fase deja la base para un comando posterior:

```bash
python analysis/replay_trade.py canal1_19822
```

Ese comando no se implementa en el primer corte. Dependera de que el ledger ya
contenga lifecycle por ticket y strategy snapshot.

## Criterios de aceptacion

- No cambia ninguna decision de trading.
- Los eventos nuevos son append-only y best-effort.
- `reconcile_signal()` conserva compatibilidad con journals antiguos.
- `positions[*].sl_history` y `positions[*].tp_history` se rellenan cuando hay
  eventos nuevos.
- Trades con `time_stop_notified` tienen `post_time_stop_outcome`.
- Tests unitarios cubren parser de lifecycle, snapshot y outcome derivado.
