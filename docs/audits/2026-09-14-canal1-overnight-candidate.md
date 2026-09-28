# Canal 1: Prueba Nocturna Y Frontera Operativa

Estado cerrado: ninguna de las 106 reglas supera el cribado predeclarado.
No hay candidata admitida para observacion ni produccion, configuracion
publicada o nueva politica ejecutandose. No se modifica el filtro para nominar.

## Alcance Congelado

- 106 reglas de 16 familias existentes; maximo 128 permitido, sin busqueda
  adaptativa ni cambio de parametros despues de ver resultados.
- 881 activaciones hipoteticas desde version conocida y 240 desde mensajes
  iniciales sin editar, enero-septiembre. No son 1121 senales independientes.
- 293 pares jornada/escenario. Ventanas 56/14 dias y periodo reciente de 56
  dias; no separacion rigida enero contra septiembre ni afirmacion de OOS nuevo.
- Dos ejecuciones hipoteticas: referencia y desfavorable. Coste adicional
  predeclarado de 10 EUR por lote de ida y vuelta, ademas del efecto de precios.
- Exposicion de referencia, no capital del usuario: 0.02 lotes por regla,
  maximo dos reservas, 0.04 lotes y 40 USD de perdida configurada conjunta.
  Reserva fija 20 minutos, incluso sin fill o tras cierre anticipado.
- Solo dias completos admitidos para resultados; dias desconocidos conservados,
  nunca cero. Sin cuenta anual continua, margen, overnight ni riesgo con canal 2.

Una finalista necesita superar todos los criterios congelados en ambos relojes
y perfiles: muestra, cobertura, meses positivos, concentracion, ventanas de
comprobacion y periodo reciente. Aun asi solo seria una hipotesis retrospectiva
para observacion, no rentabilidad certificada ni garantia de fills futuros.

## Incidente Preservado

`paper_campaign_v1` se detuvo sin ranking despues de 93 pares jornada/escenario,
61692 evaluaciones y 862973348 visitas de cotizacion. Una senal afectada:
`dubai_stream:revision_time:18185`, 19/03/2026 14:00:32 UTC, 114 filas incidentes.
Los primeros dos ticks tenian igual hora pero precios distintos. Oracle
admitia una peticion que scalar y fast bloqueaban como ambigua.

No se borra ni se sustituye ese caso. Se conservaron 65 fuentes con SHA256
verificado en `implementation_before_request_quote_binding_v1` antes de reparar.
El modo por defecto `timestamp_only` conserva el bloqueo en los tres motores.
El modo opt-in `timestamp_and_ordinal` exige hora e indice exacto de la peticion
en el orden original. No se ordenan ni deduplican precios ni se inventan tiempos
submilisegundo. Es un contrato hipotetico de secuencia, no recepcion observada.

El caso real coincide en los tres motores: bloqueado en el modo anterior,
una entrada y una salida en el nuevo, para ambos perfiles. La campana v2
declara el modo nuevo y mantiene datos, reglas, criterios y presupuesto de v1.
Comparacion estructurada de protocolos: fuentes, datos, candidatos, criterios,
presupuestos y ventanas identicos; inventario difiere solo en identidad del
codigo y los perfiles solo en el campo explicito de vinculo hora+indice.

Verificacion ampliada durante v2, sin tocar su codigo ni seleccion:
los 93 pares ya calculados en v1 conservan 68998 filas identicas salvo
`result_digest`; las 114 filas incidentes pasan a simuladas, sin eliminarlas.
El mismo caso del 19/03 se verifica para las 106 reglas y ambos perfiles:
212 combinaciones, 636 evaluaciones de tres motores y 11233032 visitas de
cotizacion, sin diferencias entre motores ni con el resultado rapido de v2.

## Evidencia Del Codigo Actual

- `py -3.14 -m pytest -q`: 5602 pruebas pasan, 634 avisos, 344.01 s.
- Regresiones focalizadas de vinculo, mercado, BE/parcial, campana y finalistas:
  391 pasan, 12.74 s. Incluyen BUY/SELL, peticion en primer/segundo tick, latencias,
  escalera, fill ausente, volumen rechazado y 90 recorridos combinados sembrados.
- `shared_family_lab_v4` verificado contra codigo y fuentes actuales. Identidad
  `d7569e59987088ed27362c8d12878bcc28ee08ef48255a36c0f5feb62e2e9d09`.
- Nueve fechas mensuales, 61 casos, 448 combinaciones y 1952 filas. 5472
  evaluaciones de tres motores, 73674528 visitas, cero incidencias de motores
  o cartera. 416 combinaciones completas y 32 bloqueadas por cobertura de junio.
- Comparacion completa v3-v4: iguales las 448 combinaciones, 1952 filas y 5472
  resultados, excluyendo exclusivamente `behavior_digest`. v3 queda historico,
  no se reetiqueta como ejecucion del codigo reparado.
- Codigo de produccion no modificado en este bloque. La suite local no prueba
  version remota, cuenta actual, ausencia de exposicion o arranque en VM.

## Condiciones Operativas Antes De Una Prueba Con Broker

| Condicion | Estado de este bloque |
| --- | --- |
| Regla exacta congelada y candidata que supera el cribado | 106 reglas fijadas, ninguna supera el filtro |
| Tres motores y cartera sobre todos los casos de la finalista | No aplica: no hay finalista admitida; no se afirma esa verificacion anual |
| Misma identidad y parametros entre investigacion y consumidor operativo | No hay adaptador auditado |
| Mismo instante causal de mensaje/edicion y tratamiento de duplicados | Historico condicionado a snapshots; recepcion original desconocida |
| Misma decision de entrada, anclaje del stop y politica de reservas | No representados por la politica live actual |
| Lotaje, pasos/minimos, redondeo y limites del simbolo real | Requiere comprobacion actual del destino |
| Peticion, respuesta, fill y confirmacion de proteccion diferenciados | Simulados; falta evidencia prospectiva de la candidata |
| Parcial y remanente conciliados, sin cierre duplicado | Simulados; pendiente si aplica a finalista |
| Recuperacion tras reinicio, entradas pendientes y accion idempotente | Politica nueva aun sin integracion operativa |
| Cuenta demo actual verificada y ninguna operacion con dinero real | No verificado; informe demo del 08/09 no basta |
| Posiciones, senales, ordenes y entradas pendientes verificadas antes de reiniciar | No verificado; desconocido no equivale a cero |
| Entorno efectivo de VM y fuentes desplegadas iguales a las probadas | No verificado en este bloque |
| Observacion futura sin reutilizarla para declarar OOS pasado | Pendiente |

`DubaiLivePolicy` actual es otra politica schema1: escalera adversa de tres
patas, gestion del proveedor y tiempos propios. Las candidatas son schema2
de reglas propias. Publicar parametros no convierte una en otra.

El watcher de `origin/main` puede reiniciar y activar codigo. No se ha hecho
commit, push, cambio de flags, reinicio, conexion MT5 ni orden en este bloque.
No se autoriza activacion automatica desde resultados de investigacion.

## Resultado Anual

`paper_campaign_v2` completa, protocolo fijado antes de evaluar precios.
Identidad `b5ada0a49ebefe6448feed65a85efa488b1ca999f365a28f67d59b128a9890ef`.
Verificador ejecutado de nuevo al terminar: `verified_paper_screen_only`,
106 reglas, 209668 evaluaciones, cero finalistas, activacion live falsa.
1052 fuentes y 295 archivos vinculados; 293 pares jornada/escenario,
2722025728 visitas de cotizacion y 1581.76 segundos de presupuesto consumido.

Se conservan las 237652 filas: 168123 simuladas, 41545 sin entrada, 8904 no
admitidas por reservas y 19080 bloqueadas por cobertura. No hay incidencia de
motor en v2. Cobertura de jornadas completas: 800/881 casos del reloj principal
y 231/240 de publicacion inicial, sin sustituir los 81/9 desconocidos por cero.
Las sumas siguientes son hipoteticas sobre dias conocidos, tras el coste
adicional declarado; no son beneficio ni drawdown de una cuenta anual completa.

| Escenario | Reglas con suma positiva | Cero | Negativa |
| --- | ---: | ---: | ---: |
| Version conocida, referencia | 0 | 1 | 105 |
| Version conocida, desfavorable | 0 | 1 | 105 |
| Publicacion inicial sin editar, referencia | 6 | 1 | 99 |
| Publicacion inicial sin editar, desfavorable | 1 | 1 | 104 |

El cero corresponde al control sin entrada. Ninguna regla es positiva en los
cuatro grupos, incluso antes de exigir estabilidad mensual y de ventanas.
No se ejecuta el verificador de finalistas ni se crea manifiesto de candidata:
su entrada requiere una nominacion valida, ausente en este archivo.

## Pista Diagnostica Rechazada

La regla con menos motivos de rechazo (10), no una seleccion operativa, es
`eeafc8f2bdd5f0a01e0d165abe3bc6b3a5ded3bc0e3f342dfa7c4a8317b6c13b`:
entrada por impulso favorable de 5 unidades de precio XAUUSD, caducidad 3 min,
una posicion de 0.02 lotes, stop y objetivo de 10 unidades, salida forzosa
a 15 min, sin BE ni gestion del proveedor. No son 5/10 EUR de beneficio.

| Grupo | Casos ejecutados | Suma hipotetica EUR | Caida entre cierres diarios EUR | Ventanas positivas/utilizables |
| --- | ---: | ---: | ---: | ---: |
| Version conocida, referencia | 176 | -105.64 | 389.71 | 3/12 |
| Version conocida, desfavorable | 176 | -260.17 | 480.86 | 2/12 |
| Inicial sin editar, referencia | 64 | 90.77 | 145.63 | 1/6 |
| Inicial sin editar, desfavorable | 64 | 42.16 | 154.21 | 0/6 |

Solo 2/9 meses positivos en el reloj principal y 3/9 en el inicial. Los 56 dias
recientes son positivos en cuatro grupos, pero solo contienen 24 ejecuciones
principales y 4 iniciales. Ni ese tramo reciente ni la suma inicial positiva
compensan el rechazo del conjunto; no se usa para relajar criterios.

La eleccion movil usando exclusivamente cada desarrollo de 56 dias tambien
queda visible: reloj principal nomina en 4/14 ventanas y ninguna comprobacion
posterior es positiva en ambos perfiles; reloj inicial nomina en 13/14 y siete
comprobaciones posteriores son positivas en ambos. Son diagnosticos
retrospectivos con fuentes comunes, no periodos nuevos e independientes.

## Interpretacion Y Siguiente Bloque

Este rechazo se refiere a la rejilla fijada, no demuestra que todas las
estrategias del canal 1 sean perdedoras. Stop de 10, horizonte intradia corto,
direccion de la senal y ausencia de contexto previo son limites sustantivos;
no se han explorado todas las politicas imaginables.

El reloj de version conocida usa snapshots disponibles, no la hora observada
de recepcion original. El subconjunto sin editar es retrospectivo: al recibir
un mensaje no se sabe si se editara mas tarde. No se convierte ese filtro en
una regla live que requiera conocer el futuro. Los dos escenarios no se suman
como muestras independientes ni se elige solo el que produce mejor resultado.

Siguiente bloque separado, antes de otra busqueda: revisar entradas con
recepcion prospectiva y contexto causal previo (volatilidad/tendencia), declarar
una hipotesis nueva y congelar de nuevo datos, criterios y presupuesto. No se
ha iniciado esa ampliacion ni se ha ampliado esta campana para forzar ganadora.
La integracion operativa, cuenta demo actual y prueba futura siguen abiertas.
Resultado local, sin commit ni push; ninguna activacion en produccion.
