# Confianza Del Simulador: Primera Verificacion

Nota de continuidad: este informe conserva la evidencia de la primera fase.
La revision posterior detecto que el contrato v2 aun confiaba en copias del
path para ciertos hechos por ticket; no equivale al contrato v3 vigente.
La correccion temporal tambien invalida la suite antigua como prueba del
codigo actual. Ver `2026-09-08-simulator-causal-hardening.md` en este directorio.

## Veredicto

Hay evidencia reproducible util, pero **todavia no paridad integral con MT5**.
Esta fase refuerza la validacion; no busca estrategias ni selecciona una
candidata. Trabajo local sobre `feature/gold-555-live-trial`, HEAD `08551e9a8`,
con los cambios previos preservados. Sin commit, push, despliegue, reinicio,
consulta a MT5 ni orden. No cambia capital, politica, volumen ni cuenta.

## Fallo Detectado Y Corregido

El certificador de gestion Gold comparaba el total y el numero de entradas
de cada cesta, pero no exigia igualdad por ticket. Una prueba controlada con
dos operaciones reales representadas por 1/5 EUR y dos resultados simulados
de 2/4 EUR daba antes `exact`: ambos conjuntos sumaban 6 EUR. Los tres motores
coincidian entre si, lo que tampoco detectaba esa discrepancia frente al control.

La regresion fallo antes de corregir el certificador. Ahora el resultado es
`mismatch`, con diferencias de +1/-1 EUR asociadas a cada ticket y sin permiso
de reutilizarlo como control aprobado. **Es un defecto demostrado del control,
no evidencia de que esas perdidas ocurrieran en una cuenta real.**

El contrato v2 comprueba tambien identidad de entrada, hora/precio/volumen
observados, volumen cerrado por ticket, importes no finitos, duplicados,
operaciones observadas sin cierre e inconsistencias entre sumas y totales.
Los cierres parciales se agregan solo dentro de su ticket. No se han ajustado
precios ni tolerancias para forzar coincidencia.

El informe de conjunto rechaza los certificados v1 como prueba del nuevo
control. Otra regresion demuestra que antes podia habilitar la extension
integral al coincidir importes y entradas, sin verificar la secuencia de
decisiones y deals de salida. Ahora esa evidencia ausente se declara como tal
y mantiene cerrada la barrera integral. No se inventa un validador de salidas.

Los certificados historicos no se sobrescriben: conservan su contrato antiguo.
Para usarlos con la exigencia nueva deben recalcularse desde sus fuentes.

## Piloto Reproducido

Se repitio el estudio independiente congelado del 07/09 hasta las 15:02:33 UTC,
no el dia completo. Los mensajes/precios, los registros del broker y las
interpretaciones reales conservan sus funciones separadas.

- Ocho senales, 20 aperturas identificadas frente a resultados de orden y
  20 cierres del broker. No se elimina ninguna senal del piloto.
- Las 24 simulaciones ya fijadas (ocho senales por 0/250/1000 ms) reproducen
  el resultado anterior y coinciden con el verificador escalar independiente.
- Tambien se reproducen los ocho controles condicionados a la interpretacion
  original del bot, separados de la simulacion independiente desde texto.
- Los cinco archivos de resultado reproducidos son semanticamente identicos
  a sus originales. Las ocho fuentes de la captura y los demas inputs
  utilizados conservan sus hashes antes y despues. No se sobrescribe el piloto.
- Los 20 cierres reproducen aritmeticamente al centimo el beneficio registrado;
  uno conserva conversion EURUSD de 5.638 ms, superior al limite de 5.000 ms.
  Que el importe coincida no da por valida esa conversion para alternativas.

Siguen presentes las limitaciones conocidas: diferencia de interpretacion de
Gold 2571, conversion hipotetica antigua en Gold 2576, ventana de nuevas entradas
todavia abierta de Gold 2580 y diferencias de fills/stops respecto a MT5.
No hay un total simulado certificado comparable para todo el piloto.

Esto no amplifica la muestra: repetir el mismo piloto valida reproducibilidad,
no aporta ocho senales nuevas, un dia completo ni datos OOS intactos.

## Pendiente

1. Contrastar decisiones y deals de salida completos: actualizaciones de stop,
   parciales, orden temporal y motivos, conservando la politica/version original.
2. Ampliar a jornadas completas con fuentes monetarias y temporales admisibles;
   mantener los casos incompletos visibles y las diferencias de ejecucion medidas.
3. Verificar despues con datos nuevos no utilizados para corregir o ajustar.
   Las reparaciones retrospectivas no son evidencia prospectiva nueva.

La contabilidad observada no acredita fills hipoteticos exactos. Tampoco la
igualdad de motores prueba que una estrategia sera rentable. Las pruebas de
perdidas extremas comprueban comportamiento, no estiman por si solas frecuencia.

## Evidencia Local

Directorio: `runtime_data/simulator_trust_20260908/`.

- `pytest_offsetting_red.xml`: falsa coincidencia por compensacion reproducida.
- `pytest_pipeline_red.xml`: dos falsas habilitaciones de barreras reproducidas.
- `pytest_controls.xml`: 28 controles y regresiones aprobados.
- `pytest_pilot.xml`: nueve pruebas del piloto aprobadas.
- `pilot_revalidation.json` y `reproduced_*.json`: comparacion con las fuentes
  originales, 24 ejecuciones y ocho controles semanticos.
- `pytest_full.xml`: 3.081 pruebas aprobadas, cero fallos/errores/omisiones,
  624 avisos, 150,55 s. Python 3.14.2 local; no prueba integracion con el
  terminal o servicios externos reales ni la version Python de la VM.
- `verification_final.json`: comprobacion estructurada de pruebas, archivos,
  hashes y limites; no sustituye las barreras de datos/ejecucion pendientes.
