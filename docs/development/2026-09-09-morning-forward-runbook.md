# Contraste Natural De La Manana Del 09/09

## Alcance Y Autorizacion

Preparar antes de las 08:30 Madrid y revisar operaciones normales del bot.
Ventana fija: 06:30 <= UTC < 08:30, equivalente a 08:30-10:30 Madrid.
Un Gold 555 original, un perfil no ajustado de 250 ms, tres motores, cero
candidatas nuevas. No alargar la ventana para obtener un resultado favorable.

No hay autorizacion de publicar, cambiar estrategia, reiniciar el bot ni
enviar ordenes de prueba. El arranque del bot esta pendiente de autorizacion
expresa y de nueva comprobacion nativa de posiciones y pendientes. Una captura
flat antigua no autoriza un arranque posterior. Los colectores nunca reinician.

## Estado Comprobado Antes De La Ventana

- VM: main fc8a4202ef44f8fab9db281cee2d7bfcf6bc79bd, checkout limpio.
- Terminal MT5 ya existente: PID 8968, sesion 2, conectado a la cuenta demo EUR
  identificada por hash. No se abre otro terminal ni se hace login.
- Dos capturas previas completadas en modo lectura. La segunda tambien prueba
  el lanzador local completo, tarea temporal, archivo, copia y hashes.
- Captura inicial: `runtime_data/causal_capture_today_20260909/sep9_pre_20260909t054755z_b5917253/`.
  SHA-256 del manifest: eb0186ab84fd6310daaf600821535093a87c4eaafeb78fb74924e7dde7e422f3.
- Captura adicional: `runtime_data/causal_capture_today_20260909/sep9_pre_20260909t055516z_f2ac077f/`.
  Ambas detectan bot sin proceso y heartbeat antiguo. Cero posiciones y cero
  ordenes pendientes en sus snapshots nativos. No implican estado actual eterno.
- El log del supervisor apunta a pausa del sistema, fallo de verificacion de
  codigo durante la recuperacion e interrupcion posterior. No se ha cambiado
  ni desactivado esa proteccion.
- Control retrospectivo nuevo: `runtime_data/simulation_foundation_20260909/market_independent_v1/`.
  Once Gold, 33 evaluaciones, acuerdo de todos los campos comparados de los tres
  motores y cero bloqueos del motor. No es evidencia futura ni admision de los
  huecos del archivo historico.

## Preparacion Final

El cierre local se identifica en `market_forward_v1/` dentro de
`runtime_data/simulation_foundation_20260909/`. Existen profile.json,
readiness.json y forward/protocol.json. Freeze realizado a las
06:14:03.102256 UTC, antes del inicio. SHA-256 del protocolo:
1edab5799897073fedfc9bf911c35e167b9c0444f03e5958748ea8dfd9284c25.
La verificacion posterior de protocolo, fuentes y 20 pruebas de procedencia
ha pasado. La prueba completa se conserva en `market_verification_v1/`:
4.416 pruebas, cero fallos/errores/omisiones y codigo estable, final 06:13:47 UTC.
Si una reparacion invalida esas fuentes, preparar identidad nueva, nunca
reescribir resultados ni presentar un freeze posterior como futuro.

## Captura

El lanzador local esta en
`runtime_data/causal_capture_today_20260909/operations/run_morning_capture.ps1`.
Su primera captura posterior a 06:30 puede usar el ancla inicial predeterminada.
Cada invocacion genera etiqueta nueva y recibos inmutables. Consultar el
recibo si se interrumpe la conexion; no lanzar un duplicado sin comprobar la
tarea remota identificada en invocation.json.

```powershell
& ./runtime_data/causal_capture_today_20260909/operations/run_morning_capture.ps1 -Phase checkpoint
```

Despues, usar como AnchorLabel la etiqueta de la captura anterior. La captura
final solo se ejecuta desde 08:30 UTC, preferentemente en sus primeros segundos:

```powershell
& ./runtime_data/causal_capture_today_20260909/operations/run_morning_capture.ps1 -Phase final -AnchorLabel <etiqueta_anterior>
```

El limite de 120 segundos despues del corte solo admite muestras de reloj y
metadatos tomadas al recogerlo. No extiende mensajes, ticks, deals ni resultados
del experimento. Si se llega tarde, se conserva el bloqueo; no se falsea la fecha.

El lanzador verifica hashes de los helpers ya instalados fuera del repo live,
ejecuta una tarea temporal limitada en la sesion interactiva existente y la
retira cuando termina. No hace pull, checkout, instalacion, restart ni trade.
El collector verifica cuenta, simbolos, terminal, codigo y ancla antes de leer.
Cada delta conserva su cadena, sin afirmar un rehash completo del log de 1,4 GB.

## Ejecucion Independiente Y Contraste

Usar carpetas nuevas para cada corte. Producir solo desde mensajes causales,
Bid/Ask, reloj y metadatos del contrato de captura:

```text
python tools/run_simulator_forward.py produce --protocol <freeze>/forward/protocol.json --capture <captura> --out <dataset_nuevo>
python tools/prepare_simulator_forward.py check --protocol <freeze>/forward/protocol.json --dataset <dataset_nuevo> --stage inputs
python tools/run_simulator_forward.py run --protocol <freeze>/forward/protocol.json --dataset <dataset_nuevo> --out <ejecucion_nueva>
python tools/compare_simulator_forward.py --protocol <freeze>/forward/protocol.json --dataset <dataset_nuevo> --results <ejecucion_nueva>/independent_results.json --capture-manifest <primera_captura>/manifest.json --capture-manifest <captura_final>/manifest.json --out <contraste_nuevo>
```

Para el comparador, pasar todas las capturas encadenadas que contengan la
ventana, ordenadas. No omitir un delta intermedio. No es necesario incluir las
capturas previas si la primera posterior contiene todo el delta desde su ancla
anterior a 06:30. No incorporar un bloqueo preventana como si fuera una incidencia
ocurrida dentro de la cohorte. Nunca descartar un bloqueo dentro de ella.

El comparador vuelve a ejecutar y verificar el resultado independiente en un
proceso limpio antes de leer evidencia nativa. Los hechos de dinero, volumen
e identidad de request/deal se concilian aparte de las diferencias hipoteticas
de precio y tiempo. La cartera usa las mismas cintas; no certifica margen,
stop-out o swap nocturno. Sin dos senales y operaciones suficientes, el corte
sigue incompleto. Una posicion abierta al corte no se fuerza a cerrar.

## Interpretacion Y Seguimiento

El acuerdo de motores demuestra coherencia bajo el perfil local. Un contraste
factual permite revisar que mecanismos aparecen y donde divergen respecto de
MT5. Ni esa revision ni un numero de pruebas certifican todas las capacidades.
La hora exacta de instalacion en el servidor no se deduce de un acuse local.
Las capacidades no observadas quedan pendientes, sin convertir la copia
milimetrica de un dia en el objetivo general. No se amplian tolerancias.

Hay comprobaciones programadas en esta tarea a las 08:30, 09:30 y 10:30 Madrid,
limitadas al 09/09. A las 08:30 comprobar primero protocolo y salud del bot; la
ventana acaba de comenzar, no hay dos horas de evidencia. Informar solo de
cambios relevantes, resultados, fallos o intervencion requerida. Si el bot
sigue parado, la captura puede diagnosticarlo pero no inventar operaciones.
La busqueda masiva y la promocion de estrategias siguen sin autorizar.
