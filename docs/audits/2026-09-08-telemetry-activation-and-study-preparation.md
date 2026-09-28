# Activacion de telemetria y preparacion de estudio

## Autorizacion y alcance

El usuario autoriza proceder con lo que puede completarse ahora y esperar
operaciones nuevas para las comprobaciones que las necesitan. Se publica
exclusivamente la correccion probada de `tools/runtime_telemetry.py` y sus pruebas.
No se cambia estrategia, volumen, cuenta, configuracion ni se ejecutan simulaciones.

## Publicado y verificado en la VM

- Commit/push: `08551e9a86aa716473e43b978c13a65f21003b7a`, dos archivos,
  sobre `9765758ed84bcdcb5e2ca690ec954a2dce154bc6`.
- Preflight aislado en Python 3.11.9: 1266 pruebas operativas pasan en 247.90 s,
  63 archivos, sin credenciales ni sesiones copiadas y con MT5/red bloqueados.
  Se reutiliza la prueba local sin cambios: 3035 pasan, 624 advertencias.
  No se afirma una suite completa de investigacion pasada en la VM.
- A las 00:52:41 UTC, lectura directa desde la sesion interactiva del terminal
  existente: misma cuenta demo en EUR, cero posiciones y ordenes. No se hizo login
  ni order_send; la tarea temporal de lectura se retiro al terminar.
- A las 00:52:56 UTC, checkout limpio, heartbeat v3 plano, sin entradas pendientes
  ni pausa. Se registraron huellas de configuracion y prefijos de diario/consola.
- Push ordinario autorizado. El supervisor existente aplico su pausa/drenaje y
  reinicio controlado. No se detuvo manualmente el bot ni el terminal MT5.
- Arranque confirmado a las 00:55:00 UTC: MT5 y Telegram conectados, shadow
  iniciado con 276 estados recuperados. Esto no certifica esas 276 cestas.
- Verificacion a las 00:56:52 UTC: VM en el commit nuevo, limpia; bot 3540,
  supervisor 8436, mismo terminal 8968. Un solo arranque nuevo, sin pausa,
  exposicion/entradas/senales abiertas a cero, configuracion y prefijos intactos.
- Ambos archivos desplegados coinciden con los probados, normalizando solo CRLF.
  La primera comprobacion auxiliar comparaba incorrectamente el hash de bytes
  originales con texto normalizado; se corrigio el auxiliar, no el codigo probado.
- Publicacion automatica observada a las 00:54:37 UTC: cuatro archivos publicados,
  cero pendientes y sin error, commit de telemetria `2da9d3f40a64cbb14d7c17d0d79f043728314482`.
- Segunda verificacion a las 00:59:58 UTC: misma sesion y procesos, prefijos y
  configuracion intactos, exposicion cero. Publicacion nativa posterior al arranque
  a las 00:59:41 UTC, cuatro archivos, cero pendientes y ningun error:
  `fb0fa1ad109e2acdf1fb21d84c4ce5873dbf6c09`. Main remoto coincide con la VM.

Pruebas locales y de VM conservadas en
`runtime_data/telemetry_activation_20260908_0044/`: manifiesto de fuentes,
XML operativo, entorno aislado, lectura de cuenta/historial y evidencias antes/despues.
Los registros anteriores no se sustituyen por informes derivados.

## Preparacion local terminada

El inventario delimita Gold actual del 22/07 al 02/09 por disponibilidad, sin
admitir todavia el dataset ni llamarlo anual/OOS. Hay 2159 mensajes y 63 archivos
XAU/EUR seleccionados con sus hashes verificados; faltan originales de Telegram,
EURUSD del 24/07 y comprobaciones causales por horizonte. Quedan visibles seis
grupos de conversion no identicos y el solapamiento de 27 dias con estudios previos.

La calibracion se amplia con una lectura independiente del historial del broker
del 3/09 al 8/09 00:52 UTC, sin operaciones. Se cruzan tickets, direccion, simbolo,
propietario, precio y volumen con el diario congelado. Resultado: 104 intentos
del 3 al 7/09; 76 con timing v1, 74 aperturas contrastadas (46 Gold, 28 Dubai),
dos rechazos retcode 10016 y 28 intentos sin campos historicos suficientes.
Los dos rechazos carecen de deal porque no fueron fills; no son fills perdidos.

Las 20 aperturas del piloto anterior siguen presentes. Las cuatro adicionales
del 7/09 ocurrieron entre 15:04 y 15:18 UTC, despues del corte anterior 15:02:33;
el nuevo total de ese dia es 24, no una discrepancia contable ni una correccion
retroactiva del piloto. El otro dia con mediciones es el 4/09 (50 aperturas).

Mediana decision-respuesta combinada: 265.5 ms; p90 620.2 ms; maximo 2906 ms.
Se conservan resultados separados por canal. No se confunde este tiempo con
latencia pura de red ni tiempo exacto de fill, ni se fija coste/slippage cero.
El umbral descriptivo de 30 muestras del modulo no habilita certificacion.

Los cinco tests del inventario y siete de contraste pasan juntos: 12 en 0.10 s.
El protocolo queda en `docs/development/2026-09-08-first-study-preparation.md`.
Estos auxiliares, informes y documentos permanecen locales y sin publicar.

## Pendientes reales

Nuevas senales demo para validar el comportamiento reparado. Para investigar:
catalogo NOW causal, resolver fuentes discrepantes/huecos, contrato monetario
historico y congelacion explicita de capital, exposicion y presupuesto. No se
ha seleccionado candidata ni creado seguimiento automatico.
