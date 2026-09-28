# Prueba Retrospectiva De Las Dos Primeras Gold

Completada el 11/09/2026, 09:19 Madrid. Peticion explicita del usuario:
explicar los datos obtenidos y ejecutar el siguiente contraste retrospectivo.
Es un control acotado ya conocido, no datos reservados ni una busqueda.

## Conclusion

El supuesto base reproduce independientemente los dos disparadores de entrada,
una posicion de 0.04 por senal y dos salidas por TP: **3.44 EUR**, como MT5.
Los tres motores coinciden en sus resultados completos, salvo el digest de
comportamiento especifico del motor que el comparador existente excluye.
No se han introducido fills, tickets, protecciones confirmadas o P/L reales
como entradas de la simulacion. El comparador accede a esos datos despues.

No se detecta un fallo que requiera cambiar el motor. Si hay sensibilidad
material al escenario de ejecucion: el mismo beneficio puede ocultar mas
riesgo y tiempo en mercado; un retraso mayor puede cambiar el numero de patas.
Esto cierra el control retrospectivo de estos dos casos, no la certificacion
universal de MT5 ni la admision de cualquier historico o estrategia.

## Datos Obtenidos

- 68 observaciones de Telegram, correspondientes a 34 revisiones unicas de
  mensajes. Incluyen redeliveries y actualizaciones; NO son 68 senales.
- Dos disparadores Gold NOW: 2816 BUY y 2824 SELL. Parser sin incidencias en
  este tramo; identidad, fecha publicada, primera recepcion y revisiones
  retenidas. Se conservan seis actualizaciones de niveles del proveedor,
  pero no sustituyen los niveles propios del perfil 555.
- Historial observado: dos posiciones, cuatro deals y cuatro ordenes;
  precios, volumen, causa de cierre, beneficio, comision, fee y swap.
- Dos solicitudes/respuestas de apertura: 719 / 1500 ms cliente-cliente;
  diez modificaciones confirmadas y dos intentos de trailing que encontraron
  la posicion ya cerrada. Ver la auditoria observada, sin repetir su captura.
- Serie completa para esta prueba: 36.041 cotizaciones Bid/Ask XAUUSD y
  7.107 EURUSD; offset +10800 s y metadatos nativos contrastados.

Las posiciones nativas ganaron 1.72 EUR cada una. Cada ciclo emite un unico
cierre contable, caducan cuatro entradas no ejecutadas y el observador queda
sin flotante residual. El aviso transitorio de conciliacion de 2824 sigue
registrado en [la revision observada](2026-09-11-first-two-gold.md).

## Diseno E Independencia

Se usa `gold_555_genome()` sin cambios: fingerprint
`9b8a08950d6bd319b06f495a9844628c6e9d65bf49770b733f9c123f397ccc91`,
vinculado a estrategia live `555124a24b534aa2abda53ddaaa2ee35fd3afd07e61d05937eb14c80ad0676f0`.
Entradas adverso/reversion 1.0/1.5, caducidad 30 minutos, patas
0.04/0.03/0.03/0.03/0.03 y TP propios 0.5/1/1.5/2/2.5, entre las reglas
completas retenidas. No se convierte el estudio en otra interpretacion 555.

Preparacion, simulacion, reproduccion y comparacion son invocaciones separadas.
La preparacion verifica dos tramos del diario y proyecta solo `RAW_FIELDS`.
El ejecutor consume mensajes proyectados, precios y protocolo; comprueba sus
hashes y rechaza lecturas Python de datos runtime no declarados y conexion
de red. Sus seis rutas de lectura registradas son el propio ejecutor, tres
archivos de entrada y las dos series de precios, nunca el historial nativo.
No se presenta esa guarda Python como un aislamiento completo del sistema.

No se ejecuta ni se retima el protocolo prospectivo cancelado. Se copian solo
sus reglas y supuestos existentes a un nuevo protocolo retrospectivo,
fijado antes de ejecutar los motores. `input/` conserva una preparacion
preliminar sin ejecuciones; `input_v2/` es la vigente tras corregir la escritura
del auxiliar para que no necesite releer un archivo de salida no declarado.
No se cambio estrategia, dato o parametro en funcion de los resultados.

Universo: primeras dos Gold del dia local, derivadas de mensajes; ninguna
senal desaparece. Corte simulado 06:40 UTC, posterior a ambas caducidades.
Precios nuevos de 05:40 a 06:41 UTC, con cinco segundos previos de EURUSD.
Solapes con la captura original: 33.281 filas XAUUSD y 6.485 EURUSD, iguales
en todas las columnas y su orden. No se mezclan series ni se rellenan huecos.

## Resultado Base

El disparador simulado coincide exactamente con la cotizacion que confirmo
cada entrada live, comprobado despues: 4341.45 a 05:45:40.572 UTC y 4359.42 a
06:14:24.746 UTC. Una decision en el reloj de cotizaciones no es el instante
posterior en que el bot solicita o confirma una orden.

| Senal | Entrada simulada / MT5 | Salida simulada / MT5 | Tiempo fill simulado menos MT5 | Neto simulado / MT5 |
| --- | --- | --- | --- | --- |
| 2816 BUY | 4341.46 / 4341.46 | 4341.96 / 4341.96 | +83 ms | 1.72 / 1.72 EUR |
| 2824 SELL | 4359.32 / 4359.31 | 4358.82 / 4358.81 | -1030 ms | 1.72 / 1.72 EUR |

Las salidas simuladas se producen tres milisegundos antes de cada deal
nativo. No se considera eso prueba de precision submilisegundo ni una
tolerancia aprobada: se conservan las diferencias y los dominios de reloj.

## Sensibilidad Fijada Antes De Ejecutar

Se reutilizan los cuatro escenarios ya empleados en el trabajo anterior.
Son hipotesis nominales; cada accion se ejecuta en la primera cotizacion
elegible, no necesariamente exactamente X ms despues de solicitarla.
Procesamiento/acuse de SL/TP y cierres a mercado: 250/250 ms; retry 1000 ms,
stops 20 puntos, freeze cero. Sin slippage extra ni spread sintetico.

| Fill / acuse supuesto | Posiciones totales | Neto de las dos senales | Peor flotante de la segunda senal | Ultima salida de la segunda, UTC |
| --- | ---: | ---: | ---: | --- |
| 250 / 250 ms | 2 | +3.44 EUR | -0.76 EUR | 06:14:29.449 |
| 1000 / 250 ms | 2 | +3.44 EUR | -5.89 EUR | 06:16:44.689 |
| 5000 / 250 ms | 3 | +6.02 EUR | -9.79 EUR | 06:17:46.534 |
| 250 / 1000 ms | 2 | +3.44 EUR | -0.76 EUR | 06:14:29.449 |

La columna de peor flotante es el maximo resultado adverso de esa cesta,
no drawdown agregado de cartera. Los drawdowns flotantes de la segunda
son 0.24 / 7.30 / 9.88 / 0.24 EUR; ambos conceptos se conservan separados.

Con fill 5 s, la segunda senal entra a 4358.42 y despues activa una pata
adversa de 0.03 a 4360.07. Esta cierra en 4359.07 (+2.58 EUR); la primera
en 4357.92 (+1.72 EUR). No es una mejora seleccionada ni una recomendacion:
mas demora altera la trayectoria y tambien eleva la exposicion adversa.
No se ajustan supuestos para obtener el beneficio mayor o igualar MT5.

## Verificacion Y Limites

- 24 evaluaciones: dos senales x cuatro escenarios x tres motores. Cero
  bloqueos y desacuerdos; otras 24 reproducen exactamente las salidas.
- Verificador separado, sin motores: precio Bid/Ask de cada fill, primera
  cotizacion elegible por retraso, orden de acuses, TP instalado y tocado,
  volumen y conversion causal al centimo de cada salida simulada.
- FX estricto de 5000 ms sin ampliar tolerancias; hay cotizaciones antiguas
  en partes inactivas del recorrido, no se convierten en dinero verificado.
  Todas las salidas monetarias verificadas usan FX previo dentro del limite.
- 437 fuentes previamente preservadas siguen iguales. No se ha modificado
  codigo de producto; no era necesaria otra suite completa por este control
  de datos. Se reutiliza la verificacion previa del motor bajo fuentes iguales.
- Costes cero son una hipotesis intradia existente, aunque coincidan aqui con
  los nativos. No se valida rollover, margen, capital o fills parciales.
- `approved_tolerances=null` y `full_live_parity_verified=false` siguen
  intactos. No hay aprobacion cuantitativa universal, datos OOS nuevos,
  autorizacion de busqueda masiva ni estrategia seleccionada.

La tarea puntual de precios `Codex-Retro-Prices-20260911-b7e18a36a936` se
ejecuto sin disparadores futuros, termino con codigo cero y fue eliminada.
Terminal 6312/sesion 2 conservado; cuenta y reloj verificados antes/despues.
VM limpia en `d9037c5c1ac3b91814ec73a00278e6d6c7b52aad`. Sin ordenes,
reinicios, nueva programacion, commit o push. Informe y estudio locales.

## Archivos

Directorio: `runtime_data/gold_first_two_retro_20260911/`.
`retrospective.py prepare`, `run`, `verify`, `compare` finalizaron correctamente;
`verify_evidence.py` tambien. Ejecucion inicial de motores: 5.195 s.

| Archivo | SHA256 |
| --- | --- |
| input_v2/protocol.json | 257bb22f1d76231e9e66ac0a33184a4b2ecd21cf0196489c1dab71c5b779c046 |
| simulation_results.json | a593166b5142a7d834ff6a8e1fd420ed8fb800044d8569e579ddab7f18f68daf |
| comparison.json | 3af049f486fad3f31cc2f2f24c7747c6c5f3808f85ff14ea0c4fffd588e7649e |
| evidence_verification.json | 9e033e7bc11f1d10b5fde0fca5b34dbdaa76e94cdb9d49b5c5c8a92534f92472 |

`simulation_reproduction.json`, `input_v2/input_diagnostics.json`,
`input_v2/projection_provenance.json` y las series originales completan
la evidencia. El estudio no necesita ejecutarse otra vez solo para leerlo.
