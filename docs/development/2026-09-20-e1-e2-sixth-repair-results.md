# Sexta Reparacion E1-E2: Identidad Global De Eventos

Fecha: 20/09/2026. Estado: el unico hallazgo de la quinta revision esta
reparado y verificado localmente. Pendiente de contraste final con Astra antes
de aprobar el conjunto E1-E2 o disenar E3. Sin commit, push, VM, reinicio,
configuracion ni orden real. No se modifica ninguna estrategia.

Revision posterior: [cierre offline E1-E2](2026-09-20-e1-e2-sixth-repair-review.md).
Astra no encuentra defectos materiales en la frontera revisada y confirma 38
pruebas, incluyendo ocho controles nuevos. Base aceptada para avanzar al
[plan E3](2026-09-20-e3-integration-plan.md); pruebas operativas aun pendientes.

Base: rama `feature/gold-555-live-trial`, HEAD
`7415941a410d18288fa4e08da76809f70b125efa` y cambios locales conservados.

## Correccion

`execution_latency.summarize_events` construye ahora el conjunto global de
event_id usados por decisiones y por intentos. Una identidad presente en ambos
tipos pone en cuarentena los grupos afectados antes de calcular duraciones.

La colision incrementa los contadores existentes de identidad conflictiva para
la decision y el intento. No cambia el schema 4 ni anade campos. La semantica
queda completa: event_id identifica un unico evento dentro del lote, con
independencia del tipo y del orden de entrada.

Dos regresiones canonicas fijan el limite:

- Treinta intentos reutilizando event_id de otras decisiones producen cero
  muestras y estado `diagnostic_only`, en orden original e inverso.
- Una sola colision elimina el intento conflictivo y el reloj de decision
  afectado: quedan 29 intentos diagnosticos y 28 escenarios validos. Las otras
  observaciones permanecen intactas.

## Verificacion

- Latencia canonica: 18 passed en 0,13 s.
- Probe independiente de Astra: 12 passed; la reproduccion anterior pasa en
  ambos ordenes y declara 30 conflictos de decision, 30 de intento y muestra 0.
- Protocolo, registro durable, gateway, latencia, inventario y matrices de las
  rondas cuarta/quinta: 229 passed en 25,03 s.
- Suite completa: 6.004 passed, 638 avisos existentes, 370,30 s.
- Compilacion de modulo y regresiones: correcta.
- `git diff --check`: sin errores; solo avisos CRLF del checkout.
- Entorno local: Windows, Python 3.14.2.

Hashes de esta frontera:

| Archivo | SHA256 |
| --- | --- |
| execution_latency.py | 20338143f942477c8cd3e65f73c7e91ed705a9b0d68eb865486a3e607f808d55 |
| tests/test_execution_latency.py | d779305f62d07b88cba0252f8a03345a86489ad491d1ca6c4d5d06f4118aa7ca |
| probe independiente | 40d3332e99f79d19ad568fc657a9efa10830ba89bf6f87854fee06db6e6ae145 |
| JUnit focal | 0802363e2070f8cb3ff9df1ae20bfcbdeab8fd8d2a9b3d7516b955cb9f20d25d |

## Inventario Conservado

No se regenera el inventario. El v6 conserva SHA256
`06c79ed4f8251c0445c44b5bccb965c4bf11554f5b7975d996181ba7b1380b01`
y se reconstruye exactamente desde el prefijo sellado: 267 senales, 253
recepciones, 53 grupos y 5.937 intentos. Tras las pruebas locales, el diario
tiene 111 filas/115.446 bytes posteriores al corte; todas quedan fuera del
prefijo. Las cinco fuentes auxiliares y el generador coinciden con sus hashes.

## Siguiente Gate

Astra/alto debe revisar solo esta frontera: deteccion global entre tipos,
cuarentena parcial, independencia del orden y ausencia de regresiones. Los
cierres F1, F2 y F4 de la quinta revision se conservan y no requieren otra
reimplementacion.

Si Astra no reproduce nuevos defectos materiales, se aprueban E1-E2 offline y
el siguiente trabajo sera disenar E3: integrar el trabajador aislado en todas
las llamadas live, adaptar consumidores y preservar las politicas actuales.
Siguen pendientes para fases posteriores Python 3.11, bloqueo nativo de 60 s,
carga sostenida, propietario unico tras reinicios y conciliacion contra broker.
Nada de este bloque demuestra rentabilidad ni cambia produccion.
