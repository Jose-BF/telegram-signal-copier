# Canal 1: Historial Anual Y Muestras

## Alcance

El usuario retoma expresamente el acuerdo JSON -> disparadores -> reglas
propias -> precios historicos, pide actualizar Telegram mediante Computer Use
y propone estudiar desde principios de 2026. La campana anterior de 2500
variantes uso solo 39 disparadores raw para desarrollo: no cubrio este
historial JSON. La falta de recepcion observada no impide formular otro
experimento con publicacion y retrasos declarados; no autoriza a llamarlo
reconstruccion de recepciones reales ni a omitir las ediciones.

Trabajo local de descarga, inventario y propuesta. Sin nuevas simulaciones,
cambios de motor, commit, push, acceso a VM, reinicios ni ordenes. Rama
observada: `feature/gold-555-live-trial`; cambios previos preservados.

## Descarga Comprobada

Telegram Desktop mostro la exportacion terminada el 13/09/2026: 33 archivos,
5.2 MB. Configuracion: desde 1 de enero a las 00:00 de la interfaz hasta
presente, JSON y stickers; otros medios desmarcados. El calendario de la
interfaz no se usa como reloj UTC: las fechas analizadas proceden de
`date_unixtime`. El archivo comienza el 02/01, sin ambiguedad en la frontera
anual. No se afirma que existan mensajes el 01/01 ni que se hayan recuperado
mensajes borrados o todas las revisiones intermedias.

- Fuente: `C:/Users/josea/Downloads/Telegram Desktop/ChatExport_2026-09-13/result.json`.
- Nombre exportado: DT Investing || V.I.P 2.0; ID `1642806869`, mismo canal 1
  Dubai, no un canal distinto por el cambio de nombre visible.
- SHA256: `3238bcc60fd82c686cbe860426d0978650f1cbc0e9a73b4921e27bbeb4628f01`.
- 5494 mensajes, 5494 IDs distintos.
- Primera publicacion: `2026-01-02T12:50:44Z`.
- Ultima publicacion: `2026-09-13T12:20:45Z`.
- 717 mensajes con sticker; los siete archivos distintos referenciados existen.

Los JSON anteriores de abril y julio conservaban 2934 IDs de 2026, pero no
tenian mayo. La nueva exportacion contiene esos 2934 y anade 2560 IDs. Se
conservan las fuentes anteriores, no se sobrescriben con snapshots recientes.

## Inventario Mensual

BUY/SELL son publicaciones con los dos stickers GOLD inspeccionados
visualmente, no operaciones ni senales admitidas por el motor. Reenvios es
un subconjunto de BUY+SELL. La presencia de mensajes en todos los meses no
certifica continuidad ni cobertura de precios.

| Mes 2026 | Mensajes | BUY GOLD | SELL GOLD | Reenvios GOLD |
| --- | ---: | ---: | ---: | ---: |
| Enero | 444 | 33 | 37 | 0 |
| Febrero | 485 | 26 | 45 | 0 |
| Marzo | 646 | 58 | 48 | 0 |
| Abril | 796 | 41 | 50 | 0 |
| Mayo | 680 | 31 | 54 | 0 |
| Junio | 633 | 35 | 62 | 2 |
| Julio | 531 | 16 | 56 | 0 |
| Agosto | 733 | 32 | 41 | 1 |
| Septiembre, hasta el 13 | 546 | 21 | 26 | 22 |
| Total | 5494 | 293 | 419 | 25 |

Evidencia visual y bytes, relativos a la carpeta nueva:

- `stickers/sticker.webp`: BUY GOLD, 293 publicaciones; SHA256
  `1ec854d5eb9faea569ca26dce4b7b6fcabcdaf4dddc4a458ee7930f062db4781`.
- `stickers/sticker (1).webp`: SELL GOLD, 419 publicaciones; SHA256
  `68b3008b47c42baeef4f00f29332ed4655b0ee2afb031679e29721a20c93a720`.

No se infiere direccion del emoji, nombre de archivo ni posicion en la
carpeta. Esta identificacion visual no modifica el adaptador del motor.
Los otros cinco mensajes se conservan por separado: tres imagenes no son
ordenes direccionales GOLD; una animacion TGS queda sin clasificar; el ID
22158 es un sticker BUY sin simbolo, reenviado de VIP 3.0, y no se admite
implicitamente como GOLD. Septiembre contiene reenvios de VIP 3.0: no se
mezclan silenciosamente con publicaciones propias del canal.

De las 712 publicaciones GOLD, 687 no tienen `forwarded_from` y 25 si.
480 tienen marca de edicion y 232 no. Una marca de edicion no demuestra
que cambiase la direccion, pero tampoco prueba que el sticker actual fuese
el inicial. Las alternativas deben mantener separado lo corroborado por
fuentes anteriores, lo desconocido y las hipotesis explicitas; no se debe
trasladar automaticamente todo contenido final a la hora de publicacion.

## Fuentes Unidas Y Verificacion

Se ejecuto el preparador existente `tools/prepare_telegram_exports.py`, sin
modificarlo, sobre los dos JSON antiguos de Dubai y el nuevo, comprobando
los tres hashes. Periodo de inventario/admisibilidad:
`[2026-01-01T00:00:00Z, 2026-09-14T00:00:00Z)`. La cota final no afirma
que se hayan descargado publicaciones posteriores al momento de captura.

Archivo de evidencia:
`runtime_data/canal1_history_20260913/export_admission_v1/`.
Identidad: `6fa8d6c0eecfdaa79d3979f3fa5e0e51fa230fcc4780ddbfe508bc6ee3270dbc`.

- 24674 ocurrencias fuente; 21740 identidades unicas, 5494 dentro de 2026
  y 16246 fuera del periodo, conservadas.
- 2934 ocurrencias repetidas por identidad; seis identidades con multiples
  revisiones conocidas. Ninguna con multiples textos normalizados distintos.
- El preparador actual solo admite texto: 38 disparadores en el escenario
  de publicacion inicial y 737 en el de ultima revision. Cero recepciones
  observadas. Esto no incorpora los stickers ni certifica ticks o entradas.
- Los 737 mensajes textuales no se suman a los 712 stickers como operaciones:
  pueden ser instrucciones complementarias y requieren conciliacion.

Comprobaciones independientes con el parser JSON de PowerShell: ID del canal,
5494 IDs unicos, extremos UTC, tabla mensual, subgrupos editados/reenviados,
existencia de todos los archivos de stickers y hashes. Los totales mensuales
reconcilian con el archivo completo. Se verifica tambien el archivo generado
mediante `research.telegram_export.load_admission`.

## Muestras Propuestas

Actualizacion posterior: el usuario rechaza esta particion fija y acuerda
preparar todo el listado anual, seguido de ventanas moviles cronologicas.
La tabla inferior queda como propuesta historica, no protocolo vigente.
Ver [catalogo anual y acuerdo actualizado](2026-09-13-canal1-annual-catalog.md).

Propuesta por calendario, previa a evaluar rentabilidad; no hay una nueva
busqueda ejecutada ni presupuesto heredado para otra campana.

| Uso | Periodo UTC 2026 | Publicaciones GOLD candidatas no reenviadas |
| --- | --- | ---: |
| Desarrollo, con bloques cronologicos internos | 1 enero-30 abril | 338 |
| Comprobacion posterior de reglas congeladas | 1 mayo-30 junio | 180 |
| Contraste reciente, retrospectivo | 1 julio-13 septiembre | 169 |

Los 25 reenvios GOLD quedan en una cohorte separada (2 en mayo-junio y 23 en
julio-septiembre); no desaparecen del inventario. Todos estos recuentos son
anteriores a conciliar disparadores, ediciones y cobertura de precios.
No se eliminan casos bloqueados para presentar una muestra aparentemente
completa. Ningun bloque se declara OOS intacto sin comprobar su uso previo;
julio-agosto ya se reutilizaron. Para validacion genuinamente nueva, reservar
datos posteriores a congelar las reglas, no asumir que hoy ya estan fijadas.

El manifiesto de precios local revisado selecciona XAUUSD y EURUSD desde
22/07 hasta 02/09 (32 y 31 dias respectivamente), sin dias de enero-junio.
Esto no prueba que el broker no pueda proporcionarlos ni es un escaneo de
todos los proyectos. No se ha consultado MT5 ni descargado precios nuevos.
Por tanto: **mensajes anuales disponibles; precios anuales aun no acreditados**.

Siguiente bloque: conciliar y preparar los disparadores del JSON (incluidos
stickers y mensajes complementarios), fijar el tratamiento temporal explicito
de ediciones/publicacion/retrasos y comprobar u obtener Bid/Ask y conversion
para los horizontes necesarios. Presentar cobertura y casos bloqueados antes
de congelar una nueva busqueda. No repetir mas optimizacion sobre solo 39
senales como sustituto de esta ampliacion del historial.
