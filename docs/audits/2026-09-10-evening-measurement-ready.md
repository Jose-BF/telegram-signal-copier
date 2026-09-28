# Recogida De Tarde Preparada

El usuario pide preparar las comprobaciones pendientes y dejar el bot operando
normalmente para medir nuevas operaciones de hoy. Este bloque no publica ni
activa las reparaciones del bot, no modifica estrategias y no provoca ordenes.

## Estado Real

VM a 17:44:31 Madrid: `892bc33c8f6c19be7248401ec3cf6a27ba3d0563` limpio,
principal 5216 y terminal 6312 con sus arranques originales. Heartbeat de
17:44:29: cero posiciones, senales y entradas pendientes. A 17:33 habia una
posicion declarada; la precaptura nativa posterior encontro cero posiciones y
pendientes. No se interrumpio su gestion ni se uso ese estado para reiniciar.

La captura anterior SI se ejecuto a las 17:00:00, codigo de salida cero y
wrapper verificado. ZIP descargado y 12 archivos verificados; delta completo
de 177301 filas / 321228648 bytes rehasheado sin saltar registros.
Manifiesto `543eaf244e3fa8e9bcf6d593a45677a63af1a30c15659bb835e2da28c3d3b6ca`,
ZIP `bea205f8f0ac0146e442c870868e3f4d1d6c9ec8205b3c6c37d1577b848becf8`.
Conserva 97 entradas de conflicto del recolector anterior, sin borrarlas ni
admitir ese corte como contraste independiente. Hay 66 deals nativos, 26 dentro
de su ventana; son registros de ejecucion, no 66 operaciones completas.

Base anterior: `runtime_data/calculator_delivery_20260910/forward_thursday_v2/`:
`final_capture/`, `final_capture.zip`, `final_retention_verified.json`.

## Nuevas Recogidas

Ventana nueva 18:00-20:00 Madrid (16:00-18:00 UTC), congelada ANTES de empezar.
Usa el recolector corregido como auxiliar separado, fuera del checkout live.
El bot no ha recibido las reparaciones de cierre/observador ni un nuevo commit.

Base nueva: `runtime_data/calculator_delivery_20260910/forward_evening_v1/`.
Protocolo SHA256 `16708c87965ac38780cfdaf0b71f1a407bba47058652882ecd7b741af580a7ee`.
Copia ejecutable SHA256 `143a7f11e0c03a258892eded050685b8836ff712cb6d855300362c5b0423dde1`.
425 fuentes/wrappers siguen iguales a las 5093 pruebas completas aprobadas;
perfil, reglas y tolerancias sin calibracion. Preparacion publica sin bloqueos.

Precaptura nueva completada a 17:37, descargada y diez archivos verificados:
14326 filas / 25881483 bytes adicionales, sin saltos respecto a la final previa.
Manifiesto `c71ca29fea93cbf51cadd13645f0588ef475f0cb9bf0a65e656ee7a47cc45405`,
ZIP `889a965187d2773e055e800fbe75b1c8c8ddaeda711e4c09b20cdb1730ab233d`.
Que aun no tenga mensajes de la ventana futura no es evidencia de suficiencia.

Tareas propias verificadas Ready a 17:44 Madrid:

| Tarea | Hora Madrid | Alcance |
| --- | --- | --- |
| `Codex-Simulator-20260910-gap-prices-1500-1600` | 18:01 | Precios nativos XAUUSD/EURUSD de 17:00-18:00; suplemento retrospectivo, no validacion futura |
| `Codex-Simulator-20260910-1600-final-16708c87965a` | 20:00 | Captura de la nueva ventana 18:00-20:00 con el ancla anterior |

Ambas son Interactive/Limited, ventanas ocultas, sin ejecucion atrasada y con
limites de tiempo. La final admite hasta 512 MiB y un millon de filas de delta,
dentro de los limites existentes; no truncar ni ampliar el limite tras un fallo.
Los recibos estan en la base nueva. Se retiraron solo la final antigua y la
precaptura terminadas con codigo cero, tras preservar sus recibos/evidencias.
La tarea permanente del bot no se toco. La captura de las 20 puede fallar o
resultar insuficiente; no sustituir una ejecucion futura por su programacion.

## Comparacion Preparada

1. Verificar bytes, cuenta, reloj, Bid/Ask, mensajes/editados y enlaces de
   decisiones/solicitudes/resultados; conservar rechazos y ausencias de ejecucion.
2. Producir primero la simulacion solo desde mensajes/precios/metadatos con el
   protocolo congelado. Leer resultados MT5 despues, nunca como inputs del motor.
3. Comparar entradas, volumen, retrasos, gestion, cierres y conversion causal
   a EUR con costes nativos. Separar la contabilidad observada de fills hipoteticos.
4. Retener abiertas, incompletas, bloqueadas y anteriores a la ventana. El
   control independiente congelado es Gold NOW; Dubai y operaciones previas se
   conservan como evidencia observada, no se declaran simuladas por ese control.

No usar la suma ingenua de signal_closed: el defecto de duplicacion sigue en
la version live y las posiciones/tickets nativos son el control contable.
No hay busqueda masiva, seleccion ni certificado de rentabilidad.

El bot sigue despues de las 20. Operaciones posteriores necesitan otro corte
identificado, sin ampliar retrospectivamente este protocolo. En el siguiente
seguimiento recuperar la final y el suplemento, informar cambios relevantes
o fallos y completar la programacion de viernes antes de su ventana. Su
protocolo local `forward_friday_v1` sigue conservado y su captura sin programar.
