# Reparaciones Locales E1-E2

Fecha: 19/09/2026. Estado: reparaciones implementadas y verificadas localmente;
pendientes de revision independiente. Sin commit, push, VM, reinicio, cambio de
configuracion ni orden real. E3 no esta integrado ni activado.

Actualizacion: la [revision de estas reparaciones](2026-09-19-e1-e2-repair-review.md)
reproduce ventanas de fallo pendientes y NO aprueba la integracion E3. Los
resultados siguientes conservan lo comprobado en la corrida de implementacion;
el encabezado de cierre no cubre los nuevos contraejemplos.

Actualizacion posterior: esos contraejemplos se corrigen en la
[segunda ronda de reparaciones](2026-09-19-e1-e2-second-repair-results.md), con
schema 4 y 5.968 pruebas. Sigue pendiente la revision independiente final.

## Que Se Ha Cerrado

Los cinco defectos del inventario tienen regresiones especificas y una nueva
salida schema 3. El inventario conserva todos los contratos usados por una senal
tras reinicios, registra tramos de sesion sin contrato, da prioridad al dia UTC
observado en eventos, mantiene por separado las fechas auxiliares, descubre
raices nativas antes de releer el diario, deduplica event_id, excluye identidades
conflictivas y solo certifica timing schema 1 validado.

Los seis defectos del prototipo MT5 tambien tienen regresiones. Peticion y
payload quedan congelados profundamente; cada intento conserva request/action,
sesion y PID del trabajador y reloj monotono. Las respuestas tardias se
correlacionan sin romper la siguiente peticion. Un resultado conocido que no
puede escribirse permanece en el sobre para reintentar solo la persistencia,
nunca la llamada al broker. Errores tardios pasan a UNKNOWN. El plazo incluye
admision y lock, y una cancelacion asincrona no libera capacidad mientras su
hilo sigue ejecutandose. Retcodes futuros quedan UNKNOWN con respuesta cruda;
los rechazos terminales documentados usan allowlist.

## Inventario Schema 3

Salida nueva, sin reemplazar schema 1 ni schema 2:
`runtime_data/runtime_simulation_inventory_20260919_v3/inventory.json`.

- SHA256: `162f8c28171e0ba4d7ae09aa880431d77e8cd72623e013da325a01d5e1d52cad`.
- 1.129.796 bytes; 192.410 lineas de eventos leidas; 267 senales,
  29 fechas UTC unicas y 53 grupos dia/canal. Recuento aclarado en la revision:
  la version inicial de este informe llamaba dias a esos 53 grupos.
- 253 recepciones observadas; 5.937 intentos, 5.927 enviados y 10 no enviados.
- Cero event_id duplicados o conflictivos en estas fuentes concretas.
- 20 senales utilizaron mas de un contrato y 103 tienen al menos un tramo de
  sesion sin contrato precedente.
- Las 5.937 duraciones son legado observado; cero cumplen el nuevo contrato de
  timing certificado y ninguna se etiqueta ya como tal.
- 118 filas auxiliares siguen no colocables; no se eliminan del inventario.
- No contiene `raw_text` ni `message_text`.

El diario crecio 37 lineas desde la corrida schema 2. El generador comprobo que
cada fuente permanecio estable durante su lectura. El periodo sigue siendo
2026-07-27T00:00:00Z hasta 2026-09-19T00:00:00Z exclusivo.

## Verificacion

- 59 pruebas focalizadas superadas en 10,08 s.
- Suite completa: 5.949 pruebas superadas, 638 avisos existentes, 617,69 s.
- Compilacion de los modulos afectados: correcta.
- Entorno comprobado: Windows, Python 3.14.2.
- Fuente oficial contrastada para retcodes MT5: MetaQuotes, tabla vigente de
  codigos 10004-10046.

## Lo Que No Demuestra

No hay Python 3.11 instalado en este host, por lo que esa compatibilidad sigue
pendiente. Tampoco se han realizado todavia la prueba nativa de 60 s, carga
sostenida, propietario unico entre reinicios, conciliacion con MT5 real ni la
migracion E3 de todas las lecturas y escrituras. La suite demuestra que estas
reparaciones no rompen los contratos existentes; no demuestra fidelidad total
simulacion-real ni rentabilidad de una estrategia.

El siguiente gate es revision independiente con Astra de codigo, regresiones y
salida schema 3. Solo tras resolver sus hallazgos se decide si E1-E2 se aprueban
y se plantea E3. Publicacion y activacion requieren autorizacion separada y las
guardas de exposicion del proyecto.
