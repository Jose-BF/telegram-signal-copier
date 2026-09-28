# Primeras Dos Senales Gold Del 11/09

Revision puntual solicitada a las 08:27 Madrid. Solo lectura de la VM y
analisis local; sin ordenes, cambio de estrategia, reinicio ni publicacion.
No reactiva las revisiones programadas que el usuario cancelo.

## Resultado Observado

Son las primeras dos senales recibidas y ejecutadas de Gold desde las 00:00
Madrid del 11/09 (10/09 22:00 UTC). Coinciden diario y orden nativo. Una sola
posicion de 0.04 lotes por senal; las cuatro entradas restantes caducaron.
Horas de la tabla: Madrid. Precios XAUUSD, no pips ni euros.

| Senal | Direccion | Entrada MT5 | Precio | Salida MT5 | Precio | Neto EUR |
| --- | --- | --- | ---: | --- | ---: | ---: |
| 2816 | BUY | 07:45:40.839 | 4341.46 | 07:46:00.943 | 4341.96 | +1.72 |
| 2824 | SELL | 08:14:26.078 | 4359.31 | 08:14:29.452 | 4358.81 | +1.72 |

Ambas salidas son TP nativo (reason 5): +0.50 de movimiento favorable por
posicion, 2 USD brutos con contrato 100 y 0.04 lotes, convertidos a 1.72 EUR.
Comision, fee y swap observados: cero en los cuatro deals. No se asumen cero
para otras operaciones. Conversion causal EURUSD Ask 1.16122 / 1.16124,
cotizaciones previas de 42 / 178 ms; redondeo por salida al centimo.
Total nativo y recalculado: **3.44 EUR**, diferencia **0.00 EUR**.

Identidades: posiciones 1989801556 / 1990135183; deals de entrada
1606992159 / 1607212976, de salida 1606993915 / 1607213811. Cuatro ordenes
nativas vinculadas por ticket, posicion, volumen, tipo, precio y tiempo.

## Tiempos Y Reglas

Recepcion de las senales: 07:44:32.962 / 08:08:07.175 Madrid. Antes de pedir
entrada, la regla 555 esperaba movimiento adverso y recuperacion; esa espera
de unos 68 / 378 segundos no es latencia de envio. Las confirmaciones
registran extremos 4339.90 / 4360.92 y precios 4341.45 / 4359.42.

Solicitud a respuesta del cliente, reloj monotono: **719 / 1500 ms**.
Solicitud a fill nativo, cruzando relojes normalizados: 127 / 1184 ms;
fill a respuesta: 592 / 323 ms. Estas ultimas cifras son descriptivas: no
certifican sincronizacion submilisegundo ni retraso interno puro del servidor.
Offset broker contrastado antes/despues: +10800 segundos.

Las dos aperturas terminan 10009. Hay diez modificaciones confirmadas
(6 / 4), incluidos ajustes de proteccion y trailing. Cada senal termina
con un intento de trailing 10036 cuando MT5 ya habia cerrado por TP;
no son dos rechazos de entrada ni cierres adicionales.

## Gestion, Observador Y Limites

Auditor existente: 58.692 decisiones de gestion, 12 con acciones y cero
bloqueadas; 117.676 filas causales completas en la captura inicial. Las
decisiones sin accion no son operaciones. Las comprobaciones del guard de
cesta fueron sin accion: no se valida una salida por guard disparado.

Cada senal emite un unico `signal_closed`, conciliado a 1.72 EUR. El ciclo
puede seguir esperando entradas aunque su primera posicion ya haya cerrado.
2816 finaliza contablemente a las 08:14:33.369; 2824 a las 08:38:38.991.
Estos tiempos de ciclo no son los tiempos de cierre de las posiciones MT5.

En 2824 hubo aviso `signal_without_mt5_position` a las 08:38:11.961,
tras caducar entradas; el reconciliador lo resuelve a las 08:38:38.488.
Se conserva este aviso transitorio de 26.527 s. No hay doble contabilizacion
en estos dos casos; no se declara ausencia universal de incidencias.

El observador 555 registra 1.72 EUR y flotante cero para cada senal,
con cadenas de estado verificadas. Sus precios entrada/salida son
4341.45/4341.95 y 4359.42/4358.92, distintos de MT5 por 0.01 / 0.11.
La coincidencia monetaria no demuestra ejecuciones identicas: el objetivo
se desplaza con la entrada. Es diagnostico del observador retenido, no una
nueva simulacion independiente ni certificacion del comparador publico.

Captura nativa 08:36:12: cero posiciones y ordenes pendientes. Seguimiento
posterior solo del diario, hasta 08:40:15.943, confirma las caducidades sin
nuevas aperturas de estas senales. Heartbeat 08:43:09: cero posiciones,
senales y entradas pendientes. Son observaciones fechadas de fuentes distintas.

## Que Significa Para La Calculadora

El nombre adecuado del trabajo es **validacion del modelo de simulacion**:
reglas, ejecucion, gestion y contabilidad. La latencia es una variable de
ejecucion, junto con Bid/Ask, precio, costes, rechazos y orden temporal.

No hay que conocer una latencia real del bot en febrero si no existio esa
ejecucion. El historico contrafactual necesita senales/precios causales y
supuestos declarados de ejecucion, con sensibilidad e incertidumbre visibles.
Los casos actuales contrastan mecanismos; no trasladan sus milisegundos a
febrero ni garantizan que esa distribucion de tiempos sea representativa.
Esta revision no rellena `approved_tolerances`, no cambia experimentos
congelados, no admite nuevos datos historicos ni autoriza busqueda masiva.

## Evidencia Y Verificacion

Archivos locales: `runtime_data/gold_first_two_20260911/`.
Captura inicial: 119.068 filas nuevas (204.282.841 bytes), sin releer todo
el diario; 54.747 ticks XAUUSD y 9.763 EURUSD. Corte 06:35:56.035538 UTC.
Manifiesto SHA256 `88241ba3198a0c9c7ba447bb04ccb014164ddb9664ba53e166a9602ca873527c`.
Archivo SHA256 `f29e414db0916a333a47eb2b20195382e2d8137106023700bc1697f9d862a1a1`.

`review.py` reconstruye y conserva `observed_review.json` y fuentes por
linea/hash. `audit_and_verify.py` ejecuta el auditor existente y recalcula
dinero y tiempos de forma separada: ver `verification.json`.
Seguimiento contiguo: 4.298 filas adicionales, SHA256
`0f23101b13bfb7e6a5f54c9f70996937ef899a890e02f9c09a61b0f1ebe3d4d6`.
`verify_finalization.py` verifica cierres, cadenas del observador y nuevas
filas causales; resultado vigente `finalization_verified_v2.json`.
V2 precisa el hash del diario concatenado; V1 se conserva, no se sobrescribe.

El recolector nativo termino con codigo cero. Su envoltorio fallo despues
por comparar barras de rutas como texto; archivo/manifiesto se verificaron
independientemente. Tarea auxiliar inmediata, sin disparadores, eliminada y
ausencia comprobada. No se repitio ni se programo otra captura nativa.

VM limpia en `d9037c5c1ac3b91814ec73a00278e6d6c7b52aad`; terminal original
6312, sesion 2, identidad igual antes/despues. Investigacion y este informe
siguen locales: ningun commit, push ni cambio de producto en esta revision.
