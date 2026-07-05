# Replay Validator Exacto

**Fecha:** 2026-07-04
**Proyecto:** telegram-signal-copier
**Estado:** pendiente de aprobacion para implementacion

## Objetivo

Construir la base verificable para simulaciones de estrategia. Antes de buscar
la estrategia mas rentable, cada senal robusta debe poder reproducirse contra
la verdad MT5 con diferencia exacta de `0.00` en la moneda de la cuenta.

La prioridad no es optimizar todavia. La prioridad es separar:

- verdad contable MT5,
- replay exacto de lo que realmente paso,
- simulacion tick-a-tick de la estrategia aplicada,
- simulaciones alternativas posteriores.

## Principios centrales

### 1. Ninguna senal se abandona

El sistema debe intentar reconstruir y simular todas las senales. Una senal no
debe desaparecer del analisis por tener huecos, cierres tardios, reinicios,
ediciones raras o datos incompletos. Si falta informacion, el replay debe
explicarlo y usar la mejor reconstruccion posible.

La diferencia importante no es "simulable o no simulable", sino:

- con que fuente se reconstruyo,
- que supuestos se tuvieron que aplicar,
- cuanta confianza merece,
- y si sirve para optimizacion estricta.

### 2. Optimizacion solo sobre base validada

Todas las senales deben tener una salida de replay, pero no todas deben pesar
igual al optimizar estrategias. Para buscar edge sin contaminarnos, un trade
solo es apto para optimizacion estricta si:

- tiene tickets reales MT5 completos,
- tiene entrada, cierre, volumen y PnL neto por posicion,
- tiene costes/conversion suficientes para cuadrar al centimo,
- tiene timeline de mensajes y gestion sin mirar el futuro,
- tiene ticks bid/ask suficientes para reconstruir el recorrido,
- y el replay de la estrategia aplicada coincide con MT5.

## Capas del sistema

### 1. Replay contable exacto

Esta capa no simula mercado. Reproduce el resultado economico desde los deals
MT5 reales.

Input:

- `data/ledger.jsonl`
- posiciones y deals MT5 reconciliados

Output propuesto:

- `data/accounting_replay_audit.jsonl`

Cada fila debe incluir:

```json
{
  "sig_id": "canal1_20700",
  "stage": "accounting_replay",
  "real_pnl_mt5": 9.90,
  "replayed_pnl": 9.90,
  "diff": 0.00,
  "status": "exact",
  "blockers": []
}
```

Estados:

- `exact`: diferencia `0.00`.
- `mismatch`: hay datos suficientes, pero no cuadra.
- `reconstructed`: se pudo reconstruir usando MT5 como fuente principal, pero
  falta algun evento interno del bot.
- `estimated`: se simulo con supuestos explicitos porque faltan datos finos.
- `blocked`: no se puede producir un resultado responsable todavia; necesita
  backfill de datos, ticks o deals.

### 2. Replay temporal de la operativa real

Esta capa reproduce lo que hizo el bot usando solo informacion disponible en
cada momento.

Reglas:

- Una senal solo puede usar niveles despues de que el mensaje o edicion llego.
- Un `MOVE_SL_TO_BE` solo se aplica desde su timestamp real.
- Un TP/SL provisional solo existe desde el evento que lo puso en MT5.
- Si el bot estaba reiniciando, sin conexion o con un hueco de logs, el trade
  debe quedar marcado como bloqueado o degradado.

El objetivo de esta fase es comprobar que el motor tick-a-tick puede llegar al
mismo cierre que MT5 para la estrategia realmente aplicada.

### 3. Simulacion de estrategias alternativas

Esta capa queda bloqueada hasta que las dos anteriores funcionen.

Cuando una senal pase replay exacto, se podran probar variantes como:

- distinto numero de posiciones,
- scale out distinto,
- mover BE antes o despues,
- no mover BE,
- cerrar parcial,
- TP ladder distinto,
- SL dinamico,
- time stop,
- DCA activado o desactivado,
- lotaje por tramo.

Estas simulaciones no tienen PnL real contra el que comparar, asi que se deben
evaluar con metricas agregadas y control de sobreajuste.

## Datos obligatorios

### Por senal

- `sig_id`
- canal
- direccion
- timestamp UTC de llegada del mensaje
- mensaje original y ediciones relevantes
- snapshot de estrategia/config activa
- estado de conexion Telegram/MT5 si hubo incidencia

### Por posicion

- ticket/position id MT5
- rol de la posicion (`market_a`, `scale_out_leg`, etc.)
- volumen
- precio y hora de apertura con precision suficiente
- precio y hora de cierre con precision suficiente
- motivo de cierre MT5
- PnL neto real
- profit, swap, commission y fee separados cuando MT5 los exponga
- moneda de cuenta y, si aplica, conversion usada por MT5
- historial confirmado de SL/TP por ticket

### Por mercado

- ticks bid/ask entre apertura y cierre
- cache diaria de ticks
- evidencia de gaps si faltan ticks
- offset horario MT5 vs UTC aplicado de forma explicita

## Bloqueos y degradaciones conocidas actuales

Investigacion del 2026-07-04 sobre `origin/main`:

- `simulation_ready=True` es demasiado laxo: las 136 operaciones aparecen como
  listas aunque eso no garantiza replay al centimo.
- Hay 3 trades cerrados en MT5 sin `signal_closed` en journal.
- Hay 1 trade con discrepancia journal vs MT5 (`canal1_20637`).
- No existe cache local de ticks en `data/ticks_cache`.
- El PnL simple `precio * volumen` no cuadra con MT5 en muchos tickets porque
  la cuenta esta en EUR y XAUUSD cotiza en USD. El replay exacto debe usar PnL
  neto MT5 o capturar la conversion/costes necesarios.
- `effective_tps` y `effective_sl` son niveles finales; el simulador no puede
  usarlos desde el inicio si llegaron mas tarde.

## Cambios de modelo necesarios

1. Introducir `exact_replay_ready`, separado de `simulation_ready`.
2. Crear `accounting_replay_audit.jsonl` como artefacto de auditoria.
3. Extender el ledger con componentes de deal cuando sea posible:
   `profit`, `swap`, `commission`, `fee`, `time_msc`.
4. Intentar replay para todos los trades, incluso si faltan ticks o eventos.
5. Marcar cada trade con `confidence` y `assumptions`, no descartarlo en
   silencio.
6. Marcar como `blocked` solo cuando ni siquiera se pueda producir una
   reconstruccion responsable; ese estado debe indicar que dato hay que
   recuperar.
7. Marcar como no apto para optimizacion estricta cualquier trade sin ticks
   suficientes para el periodo que se quiere simular.
8. Marcar como no apto para optimizacion estricta cualquier trade donde los
   niveles usados no puedan ordenarse temporalmente sin mirar el futuro.
9. Derivar cierres desde MT5 cuando el journal no tenga `signal_closed`, pero
   conservar la marca de que el journal fallo.

## Criterios de aceptacion de la primera implementacion

La primera version del Replay Validator debe:

- leer `data/replay_trades.jsonl`,
- producir `data/accounting_replay_audit.jsonl`,
- calcular replay contable por posicion,
- comparar contra `pnl_real_mt5`,
- exigir `diff == 0.00` para estado `exact`,
- generar una fila para cada senal,
- listar `assumptions`, `confidence` y `blockers` humanos por senal,
- generar un resumen por consola,
- tener tests para casos `exact`, `mismatch`, `reconstructed`,
  `estimated` y `blocked`,
- no cambiar ninguna decision live del bot.

## No objetivos de la primera implementacion

- No optimizar estrategia.
- No cambiar lotaje ni gestion live.
- No cerrar operaciones automaticamente.
- No crear dashboard.
- No declarar edge.
- No usar datos futuros para simular.

## Resultado esperado

El proyecto debe pasar de "tenemos logs" a "tenemos una base verificable".

Cada senal debe terminar con una reconstruccion o con una peticion concreta de
datos faltantes. Cuando una senal sea marcada como `exact`, podremos confiar en
que su verdad economica esta reconstruida al centimo. Las senales reconstruidas
o estimadas tambien se conservan, pero no deben mezclarse con las exactas al
optimizar sin que el informe lo muestre explicitamente.
