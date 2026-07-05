# Replay Validator v1

## Objetivo

Construir una primera validacion contable que intente reconstruir todas las
senales desde `data/replay_trades.jsonl` y compare el resultado contra la
verdad MT5 al centimo.

Esta version no decide estrategia. Solo responde: "para esta senal, el PnL
reconstruido desde tickets MT5 cuadra con el PnL real, y con que confianza".

## Alcance

1. Crear `accounting_replay_validator.py`.
2. Leer `data/replay_trades.jsonl`.
3. Escribir `data/accounting_replay_audit.jsonl`.
4. Generar una fila por senal, sin descartar ninguna en silencio.
5. Clasificar cada senal como:
   - `exact`: tickets completos y diferencia `0.00`.
   - `reconstructed`: diferencia `0.00`, pero faltan eventos internos del bot.
   - `estimated`: resultado producido con supuestos explicitos.
   - `mismatch`: hay datos suficientes, pero no cuadra al centimo.
   - `blocked`: falta informacion minima para reconstruir responsablemente.
6. Incluir `confidence`, `assumptions`, `blockers` y `optimization_bucket`.
7. Cubrir los estados principales con tests.

## Reglas

- No tocar la operativa live.
- No optimizar parametros.
- No usar ticks todavia.
- No marcar una senal como perdida: toda entrada produce una salida de auditoria.
- Para optimizacion estricta solo entra `exact`.

## Verificacion

1. Tests unitarios del validador.
2. Ejecucion del CLI sobre los datos actuales.
3. Suite completa de pytest antes de push.
