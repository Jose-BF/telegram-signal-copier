# Prueba Retrospectiva De Gold Del 10 De Septiembre

Completada el 11/09/2026, 09:50 Madrid. Peticion: extender a la jornada de
ayer el contraste independiente realizado con las dos primeras Gold de hoy.
Es un control retrospectivo de la calculadora, no una busqueda de estrategias.

Nota posterior del 11/09: [investigacion de las causas](2026-09-11-execution-divergence-root-cause.md).
El cero de bloqueos indicado abajo es el resultado original del motor, no
garantia de cobertura completa: se descubren once ticks con FX invalido y
exposicion en 2801, aceptados sin aviso. Sus estadisticas de riesgo deben
considerarse parciales. La conciliacion de los deals y los archivos originales
se conservan. La divergencia de 2774 tiene causas de ejecucion/estado
demostradas; en su exposicion no aparece ese hueco de conversion.

## Conclusion

El caso base produce las mismas 22 posiciones, volumen por pata e importe
final por senal que MT5: **+78.77 EUR**. Cuatro senales sin entrada se
conservan tambien y coinciden en no operar. Los tres motores coinciden en
los 40 controles y una segunda ejecucion reproduce las 120 evaluaciones.

**La ejecucion completa NO coincide.** En particular, varios refuerzos de
2774 se producen antes y a otros precios. La mayor diferencia temporal es
76.734 segundos y la mayor diferencia de precio es 6.51 XAUUSD. El importe
final igual no elimina esa discrepancia: objetivos propios a distancia fija
pueden producir el mismo beneficio con entradas y trayectorias diferentes.
No se certifica fidelidad universal, no se aprueban tolerancias y no se
declara cerrado el modelo de ejecucion. Incidencia abierta para revision conjunta.

## Universo Y Datos

- Jornada civil de Madrid: 10/09 00:00 a 11/09 00:00, equivalente a
  09/09 22:00 a 10/09 22:00 UTC. Se seleccionan Gold BUY/SELL NOW,
  no zonas limitadas ni operaciones Dubai.
- Seis piezas consecutivas del diario, con hashes y limites de bytes
  enlazados, cubren antes y despues del dia. No se suman reentregas como
  nuevas senales ni se sustituye la recepcion por la publicacion.
- 573 observaciones crudas de ambos canales, 295 revisiones; Gold aporta
  306 observaciones y 177 revisiones. Diez disparadores Gold NOW del dia.
- 2723, 2729 y 2738 constan por primera vez despues de su caducidad.
  2747 tambien llega con retraso y no genera entrada bajo las reglas
  declaradas. Los cuatro casos permanecen en el denominador.
- 2716 corresponde a publicacion anterior a la jornada. El diagnostico
  de 2734 es un comentario de cierre de vela; 2805 ofrece alternativas de
  gestion y aparece despues del ultimo cierre simulado; 2808 es una idea
  de orden limitada. Se conservan los seis avisos Gold del compilador, sin
  afirmar cobertura universal de mensajes o transformar zonas en NOW.
- Precios nativos nuevos, solo lectura: 380703 ticks XAUUSD y 82036 EURUSD,
  desde 07:50 hasta 18:01 UTC, con cinco segundos previos de FX. Seis
  fragmentos acotados por simbolo; solapes EURUSD identicos en todas las
  columnas antes de retirar exclusivamente la repeticion de frontera.
- Desfase nativo 10800 segundos, Bid/Ask, cuenta demo EUR, identidad y
  terminal comprobados antes/despues. Sin sustitucion de precios por velas.
- Maximo intervalo XAUUSD 2651 ms. EURUSD tiene 152 intervalos mayores de
  cinco segundos; se conserva la regla FX causal de cinco segundos, sin
  ampliarla para admitir resultados. Ningun control final queda bloqueado.
- Horizonte de precios fijado a 18:00 UTC antes de los resultados. Todas
  las posiciones simuladas estan cerradas a 14:43:07.106 UTC; no hay cierre
  artificial a medianoche ni exposicion pendiente ocultada por el corte.

## Reglas Y Separacion

555 canonica sin modificar, fingerprint
`9b8a08950d6bd319b06f495a9844628c6e9d65bf49770b733f9c123f397ccc91`,
conservado en `input_v3/protocol.json`.
El control verifica igualdad estructural exacta del genoma con el protocolo
anterior y conserva las cuatro hipotesis de ejecucion ya utilizadas.

La simulacion recibe exclusivamente mensajes proyectados, precios, reglas
y sus metadatos. No recibe fills, beneficios, estados de gestion ni
decisiones reales. La comparacion nativa se ejecuta despues de guardar y
reproducir los resultados independientes. Una guarda Python comprueba los
archivos de trabajo leidos y prohibe conexiones; no se presenta como un
aislamiento del sistema operativo.

No se retima ni ejecuta el antiguo protocolo prospectivo. No hay candidatos,
calibracion a resultados, cambio de lotes, comision alternativa ni hipotesis
de capital ilimitado. Costes cero como hipotesis intradia; las 22 posiciones
nativas tienen comision, fee y swap cero. No se mide margen ni riesgo conjunto
de cartera. Datos ya vistos: no son validacion futura ni OOS intacto.

## Caso Base

Fill 250 ms y acuse de entrada 250 ms; procesamiento/acuse de proteccion
250/250 ms y reintento 1000 ms, con los limites originales.

| Senal | Posiciones MT5 / simuladas | Neto MT5 | Neto simulado |
| --- | --- | --- | --- |
| 2723 | 0 / 0 | 0.00 | 0.00 |
| 2729 | 0 / 0 | 0.00 | 0.00 |
| 2738 | 0 / 0 | 0.00 | 0.00 |
| 2747 | 0 / 0 | 0.00 | 0.00 |
| 2756 | 2 / 2 | 4.30 | 4.30 |
| 2774 | 5 / 5 | 19.81 | 19.81 |
| 2777 | 5 / 5 | 19.82 | 19.82 |
| 2786 | 1 / 1 | 1.72 | 1.72 |
| 2795 | 4 / 4 | 13.33 | 13.33 |
| 2801 | 5 / 5 | 19.79 | 19.79 |
| Total EUR | 22 / 22 | 78.77 | 78.77 |

Los 44 deals nativos se concilian con sus ordenes y se recalcula su dinero
con conversion causal, sin diferencias al centimo. Sus 22 identidades e
importes coinciden tambien con la auditoria nativa retenida de anoche.

## Sensibilidad Y Riesgo

| Hipotesis | Posiciones simuladas | Neto EUR | Peor saldo de una cesta EUR |
| --- | --- | --- | --- |
| Base: fill 250 ms / acuse 250 ms | 22 | 78.77 | -237.88 |
| Fill 1000 ms / acuse 250 ms | 23 | 85.23 | -237.00 |
| Fill 5000 ms / acuse 250 ms | 23 | 81.38 | -177.87 |
| Fill 250 ms / acuse 1000 ms | 22 | 107.38 | -237.88 |

Peor saldo significa minimo de beneficio realizado mas flotante de una
cesta respecto a su inicio, no perdida flotante pura ni drawdown de cartera.
En 2774 el caso base alcanza -237.88 EUR y una caida desde su maximo de
239.64 EUR, aunque termina en +19.81 EUR. El riesgo no queda descrito por
el beneficio final y no se afirma que ese riesgo hipotetico fuera el real.

Con fill de un segundo, 2795 incorpora una quinta pata. Con cinco segundos,
2786 pasa a dos posiciones. Con acuse de un segundo, el objetivo inicial
de 2786 es rechazado por distancia y el cierre acaba siendo por proteccion
de beneficio: +30.33 EUR en esa senal. Es sensibilidad del supuesto, no una
recomendacion de demorar ordenes ni una seleccion del resultado mas alto.

## Discrepancia Abierta

En 2774, diferencias de cada entrada simulada menos nativa:

| Pata | Diferencia temporal ms | Diferencia de precio XAUUSD |
| --- | --- | --- |
| Inicial | -475 | +0.37 |
| B1 | -32065 | +2.47 |
| B2 | -34533 | +4.44 |
| B3 | -76734 | +6.51 |
| B4 | -63512 | +3.00 |

Se conservan las 22 diferencias, incluidas las de menor magnitud, sin
filtrarlas del resultado. El cribado descriptivo de un segundo o 0.5 en
precio no constituye una tolerancia aprobada. Tambien quedan senalados
2756, 2777 y 2795. No se atribuye aun una causa unica ni se ha ajustado el
motor para forzar la coincidencia.

Siguiente paso util: revisar los disparadores de refuerzos y la secuencia
solicitud/acuse/ejecucion de 2774, separando supuestos de latencia, recepcion
y logica de estrategia. Resolver la incidencia con un caso de regresion
antes de utilizar esta muestra para afirmar paridad de ejecucion. No hace
falta esperar otra jornada para examinar la evidencia ya conservada.

## Verificacion Y Correcciones Del Auxiliar

- 120 evaluaciones publicadas y 120 de reproduccion exacta, 40 controles,
  cero bloqueos monetarios y cero desacuerdos entre motores.
- Verificacion adicional sin ejecutar motores: 90 posiciones hipoteticas
  de los cuatro escenarios, primera cotizacion elegible, lado Bid/Ask,
  proteccion o cierre efectivo, FX causal e importes al centimo.
- Las 437 fuentes preservadas de producto/investigacion siguen intactas.
- Dos pruebas locales cubren todas las patas nativas y el rechazo de una
  identidad de pata duplicada. No se ejecuto de nuevo la bateria general:
  no se modifico codigo de producto ni motores compartidos.
- Primer preparador abortado por precision de timestamps; corregida la
  comprobacion explicita ms/ns, sin alterar datos. El intento v2 calculo
  120 evaluaciones y otras 120 al intentar reproducir, pero aborto antes
  de publicar resultados por una fuente omitida en la lista de lecturas
  permitidas. Se conserva el original; v3 declara esas fuentes, sin abrir
  acceso a resultados nativos ni variar el experimento.
- El primer comparador omitio las 16 patas adicionales al usar el filtro
  de primera entrada del control anterior. `comparison.json` conserva ese
  resultado incorrecto de 10.32 EUR, **sustituido y no utilizable**.
  `comparison_v3.json` reutiliza `observed_slot`, verifica todo el
  denominador nativo del dia y reconcilia las 22 posiciones: 78.77 EUR.
  Tambien se corrigio el resumen de peor perdida, que requiere el minimo
  de valores negativos. No se reescriben resultados antiguos para ocultarlo.

## Evidencia Y Estado

Base local: `runtime_data/gold_day_retro_20260910/`.

- `input_v3/protocol.json`: SHA256
  `a9ccc6d6b509d4f910060915c82bb8c2498724e28e275a5954adb0995517cec4`.
- `simulation_results.json`: SHA256
  `b4213900868c13f6e113470a3629168b73e45d27e81997367a16966e752f603e`.
- `comparison_v3.json`: SHA256
  `a76e59aeb972f58d04ee3e9a2ee70f58297c29b646c3fc38f90b0100ae9b5016`.
- `verification.json`: SHA256
  `55dc57d50ac19be02119567888ef6c0b8558e0e78a6258718a844bec3c396c18`.
- `raw_messages.json`, `inventory.json`, `provenance.json`, `prices/`,
  `simulation_reproduction.json`, `execution_discrepancies.json`,
  `attempts.json` y auxiliares versionados preservados.

Precio ZIP SHA256
`4d153cdbe1efc41839eb4869971f646dbae7852c8a98a5e2134b32fabbc83f75`;
manifiesto SHA256
`4f632d586a072a9dd53d3c8669da827465a4011926c729f8de447744ecf038ae`.
Consulta inmediata sin disparadores futuros, resultado cero y retirada
verificada de `Codex-Retro-Prices-20260911-d352cbfe1ae5`.

Solo analisis, auxiliares e informe locales. Sin commit, push, reinicio,
ordenes ni cambio de configuracion. VM comprobada durante la captura de
07:33 UTC: `d9037c5c` limpio y terminal 6312 sin cambio. Esa comprobacion
no se presenta como lectura nueva de exposicion actual. La programacion
cancelada sigue cancelada; no se ha creado seguimiento automatico.
