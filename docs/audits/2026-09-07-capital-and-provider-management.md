# 800 EUR: margen, gestion del trader y resultados publicados

Fecha: 2026-09-07. Consulta de la VM: 19:35:35 UTC, 21:35:35 Madrid.
Version observada: `b4426e6cb5649dee10c3ca57eb95866203d0bd98`.
Estado: diagnostico local, sin cambios del bot ni estrategia seleccionada.

## 1. Decision y alcance

El usuario plantea 800 EUR de capital y acepta como referencia un dia de -150 EUR.
No se interpreta esa referencia como permiso de perdida ilimitada ni como stop ya
configurado. Se ha preguntado si quiere un limite diario conjunto, incluyendo
perdidas abiertas, o solo una referencia de tolerancia; respuesta pendiente.
El usuario acepta el resto del orden de reparacion y validacion de la auditoria
anterior. La reparacion de la base sigue pendiente y precede a la seleccion.

Con 1:500, varias cestas caben por margen. Eso no significa que 800 EUR permitan
soportar su riesgo. No hay evidencia para afirmar que perder esos 800 EUR sea
muy improbable: la frecuencia de dias ganadores no determina la perdida extrema,
y aun existen defectos y limitaciones en la evidencia prospectiva.

## 2. Consulta real de margen, sin enviar ordenes

Se consulto el terminal MT5 ya abierto, sin cambiar cuenta, apalancamiento ni
parametros. Se usaron `account_info`, `symbol_info`, `positions_get`,
`orders_get`, `order_calc_margin` y `order_calc_profit`. Las dos ultimas calculan;
no abren operaciones. El calculo de margen aislado no incorpora posiciones ni
ordenes pendientes existentes. [Referencia MT5](https://www.mql5.com/en/docs/python_metatrader5/mt5ordercalcmargin_py).

Datos observados, no condiciones garantizadas de una futura cuenta real:

- Cuenta demo EUR, apalancamiento 1:500 y modalidad hedging.
- Equity de la demo: 9.521,16 EUR; no es una prueba con solo 800 EUR disponibles.
- Cero posiciones y cero ordenes pendientes al consultar, a las 19:35:39 UTC.
- XAUUSD: contrato de 100 onzas por lote; minimo y paso 0,01 lote.
- Margen call: 50%; stop-out: 20%, ambos en modo porcentual.
- Limite de ordenes pendientes comunicado: 1.000. No es un limite de cestas ni
  prueba de que se permitan 1.000 posiciones abiertas. `volume_limit=0` no
  comunica un tope direccional adicional en esa propiedad.

Ultimo precio XAUUSD disponible: Bid 4406,20 / Ask 4406,46, a las 18:29:59,779 UTC
segun la normalizacion horaria previamente comprobada. Era una cotizacion antigua
al consultar, no un precio ejecutable actual. EURUSD rondaba 1,1622. Por eso las
cifras siguientes son aproximaciones con el ultimo precio, no una certificacion
de conversion historica ni una garantia de margen cuando vuelva a cotizar.

### Cestas completas

Se suman los margenes de todos los tramos previstos. No se presupone compensacion
por tener compras y ventas opuestas. Cesta = una senal con todos sus tramos.

| Cestas llenas | Posiciones | Lotes totales | Margen aprox. EUR | Libre con 800 EUR y P/L cero | Libre tras -150 EUR |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 Dubai | 3 | 0,09 | 68 | 732 | 582 |
| 1 Gold | 5 | 0,16 | 121 | 679 | 529 |
| 1 Dubai + 1 Gold | 8 | 0,25 | 190 | 610 | 460 |
| 2 Gold | 10 | 0,32 | 243 | 557 | 407 |
| 2 Dubai + 2 Gold | 16 | 0,50 | 379 | 421 | 271 |
| 4 Gold | 20 | 0,64 | 485 | 315 | 165 |

Son escenarios aritmeticos, NO una recomendacion de abrir ese numero de cestas.
No incluyen el spread inicial, comisiones, swap ni cambios futuros de margen.
El redondeo por tramo puede diferir algunos centimos de calcular todo el volumen
como una sola orden. La apertura real sigue sujeta a mercado y controles del broker.

### Que puede impedir seguir abriendo

El margen libre es equity menos margen usado. Las perdidas abiertas reducen la
equity aunque el saldo no haya cambiado. Una orden nueva necesita margen libre
suficiente; puede rechazarse antes de llegar al stop-out. Con DCA puede caber la
primera entrada y faltar capacidad para un tramo posterior.

El nivel de margen es `100 * equity / margen usado`. El 20% de stop-out NO significa
que solo se pueda perder el 20% del deposito. Por ejemplo, con 243 EUR de margen
constante, el 20% corresponde a unos 49 EUR de equity restante. Es un ejemplo
aislado: cierres y cambios de margen alteran el proceso. No utilizar el stop-out
como proteccion de los 800 EUR. [Propiedades de cuenta](https://www.mql5.com/en/docs/constants/environment_state/accountinformation).

El broker puede modificar requisitos segun cuenta, entidad e instrumento. Mas
apalancamiento reduce margen exigido para los mismos lotes, no su perdida por
movimiento del oro. [Guia del broker](https://www.vantagemarkets.com/en/academy/xauusd-risk-management/).
Antes de una cuenta real hay que confirmar entidad, apalancamiento efectivo de
XAUUSD, stop-out y proteccion de saldo negativo; la demo no garantiza esos terminos.

### Riesgo nominal de las reglas actuales

- Gold completo: 0,16 lotes cambian aproximadamente 13,77 EUR por cada 1,00 USD de
  movimiento del precio del oro, si todos los tramos siguen abiertos en el mismo
  sentido. Un movimiento adverso de unos 11 USD equivale a unos 151 EUR antes de
  salidas, costes y cambios del tipo de cambio.
- Cada tramo Gold tiene un stop inicial a 30 USD de su propio fill. La suma
  nominal para los cinco tramos ronda 413 EUR, el 51,6% de 800 EUR. Dos cestas
  suman unos 826 EUR de perdida nominal hasta esos stops: no es necesario asumir
  un fallo informatico para que la exposicion pueda agotar una cuenta de 800 EUR.
- Esa suma NO es una prediccion ni una perdida maxima garantizada. TP, trailing y
  cierres previos pueden reducirla; gaps, deslizamiento y costes pueden empeorarla,
  y un stop-out podria intervenir antes. No se asigna una probabilidad sin datos.
- Dubai tiene un presupuesto de stop de cesta de 25 EUR. No sumar su sensibilidad
  lineal durante 30 USD e ignorar ese stop para describir su politica real.
- 150 EUR son el 18,75% de 800 EUR. El bot no incorpora por esta consulta un limite
  diario global de ese importe.

La revision de codigo encontro limites de volumen POR SENAL y manejo del rechazo
MT5 por falta de dinero (`mt5_errors.py:34`), pero no un presupuesto global que
reserve margen para todos los tramos futuros ni un limite diario conjunto de 150
EUR en las rutas revisadas. Las politicas activas tambien tienen proteccion de
cuenta demo; pasar a real requiere un cambio expresamente autorizado y validado.

Antes de real, probar con 800 EUR una cartera cronologica con reserva de margen
para el plan completo, exposicion conjunta, perdidas realizadas y abiertas,
rechazos y stop-out. No limitarse a restar un saldo inicial a una curva obtenida
con mucha mas capacidad. Lotaje minimo 0,01: reducir cinco tramos a menos de 0,05
lotes exige cambiar el numero de tramos, no un escalado continuo ficticio.

## 3. Gestion minima del trader que merece evaluacion

Su principal aportacion puede ser invalidar una idea antes de nuestros stops.
La propuesta no depende de que toda su gestion supere a la nuestra. Evaluar
incrementalmente cada grupo sobre las mismas senales, coste y riesgo:

| Instruccion inequivoca y vinculada a la senal | Tratamiento a comparar |
| --- | --- |
| Cerrar porque la idea ha fallado, incluso en perdida | Cerrar toda la cesta y cancelar sus entradas futuras |
| Cancelar la senal antes de entrar | Cancelar vigilancia/entradas de esa senal |
| No agregar mas posiciones | Detener DCA pendiente; no deducir cierre de las existentes |
| Cerrar y esperar nueva condicion | Cerrar y bloquear reentrada hasta una autorizacion causal nueva y definida |
| Reducir exposicion o apretar SL | Comparar parcial/proteccion explicitos, compatibles con nuestros fills y limites |
| Cerrar o pausar antes de noticia/cierre de mercado | Solo ejecutar cuando sea una instruccion, no por mencionar una noticia |

No adoptar automaticamente ampliar SL, aumentar lotaje, doblar para recuperar o
eliminar protecciones. Un cambio a break-even debe considerar nuestra cesta,
beneficio ya realizado y costes; el precio de entrada del trader puede no ser el
nuestro. Una nueva senal opuesta tampoco cancela por si sola todas las anteriores.

La diferencia entre orden directa, condicion, sugerencia y comentario se conserva.
Una negacion como "no cerramos" no es cierre. Los mensajes editados solo afectan
desde que su nueva version fue conocida. Todo cierre de invalidacion debe anular
el DCA asociado; de lo contrario el bot podria reabrir la idea que se acaba de retirar.

Esto se probara como politica de investigacion versionada. No se modifica aqui
el comportamiento actual de Dubai con cierres parciales ni la interpretacion de
frases ambiguas de Gold. Primero hay que reparar y congelar el contexto del modelo.

## 4. Resultado publicado por Gold: comprobacion concreta

Se recupero la imagen ya archivada del mensaje 2587, publicada el lunes 7 a las
16:30 UTC. Lectura visual: **6 Signals Sent, 6 Wins, Pips +420**. El recuerdo de
645 no coincide con ESTA imagen. Su hash coincide con el archivo de captura.

Hay seis identidades NOW distintas en la captura del dia: 2562, 2571, 2576, 2578,
2580 y 2584. Las revisiones no se cuentan como nuevas senales. Esto concuerda con
el numero anunciado, no demuestra seis beneficios netos ejecutables.

Tres senales tienen cifras explicitas de pips en mensajes asociados: sus maximos
son 100, 50 y 50; suman 200. No se suman anuncios sucesivos como +30 y luego +100
como si fueran operaciones distintas. Las otras tres contienen avisos de TP, no
un resultado monetario completo. Un anuncio de 100 pips ligado a un analisis de
grafico, no a una de esas senales, se conserva aparte sin atribucion inventada.

Se contrastaron doce convenciones contables delimitadas: pip de 0,1 o 0,01 USD,
entrada en extremo cercano/centro/extremo lejano de la zona y prioridad de cifra
explicita o mayor TP anunciado. Resultado: **ninguna reproduce exactamente 420**.
No prueba que el anuncio sea falso; faltan su convencion de entradas, parciales,
tramos restantes y pips. Son hipotesis sobre anuncios, no backtests de ejecucion.
No se cambiaron parametros para fabricar una coincidencia.

La infraestructura ya tiene hipotesis contables en
`research/gold_iterative/provider_accounting.py`, pero las variantes existentes
usan un factor candidato de 100 pips por unidad XAU y estan marcadas no verificadas.
No tomar esa convencion de codigo como prueba de la convencion del canal.

### Investigacion recursiva, sin ajustar al total a cualquier precio

1. **Explicar el recuento.** Crear un libro por senal con sus revisiones, zonas,
   TP, cierres y anuncios diarios/semanales. Mantener separados el avance maximo,
   resultado anunciado y resultado realizable. Buscar una convencion sencilla
   que explique senales individuales y varios dias, no solo un total agregado.
2. **Ver si era ejecutable.** Reproducir entradas causales, volumen por tramo,
   cierres/SL/BE y costes con Bid/Ask. Un maximo alcanzado posteriormente es una
   referencia retrospectiva, no una salida que pudieramos conocer de antemano.
3. **Comparar alternativas.** Gestion del trader definida por completo, gestion
   minima de invalidacion y gestion propia, todas con 800 EUR y el mismo riesgo.
   Si el trader resulta mejor, no hay motivo para inventar una alternativa peor.
4. **Validar la explicacion.** Congelar convencion y estrategia antes del siguiente
   bloque reservado. Si se cambia al ver ese bloque, pasa a ser descubrimiento y
   hace falta otra validacion intacta. Guardar tambien los intentos fallidos.

Un mismo total de 420 admite muchas secuencias de riesgo y beneficio diferentes.
Igualarlo no identifica una estrategia unica ni demuestra que se pueda cobrar.
Los mensajes de hoy tambien anuncian una llamada de trading; no se tiene aqui su
contenido, asi que no se presupone que Telegram incluya todas las decisiones.
La investigacion amplia queda detras de las reparaciones de base ya aceptadas.

## 5. Verificacion y archivos

Carpeta local: `runtime_data/capital_audit_20260907_1931/`.

- `snapshot/capital_snapshot.json`: calculos directos de MT5, posiciones y metadatos.
- `snapshot/today_events.jsonl`: 7.820 eventos del dia; hash verificado.
- `gold_2587_summary.jpg`: imagen inspeccionada; SHA-256
  `83289e30ba1a49aed2c8e242b579014c51964105ed82d193ac74b1eb9056b88f`.
- `protocol.json`: capital, alcance, doce convenciones y fuentes congeladas.
- `capital_and_claim_analysis.json`: calculos, seis raices y resultados completos;
  SHA-256 `a2d0d928d11a37c991c4509cd11c7e3accdd18594063dd9cd166005dc7aae9e5`.
- Ocho tests locales pasan: deduplicacion, disponibilidad causal de niveles,
  mensajes sin enlace, redondeo, margen frente a perdidas y limites de certificacion.

```powershell
python runtime_data/capital_audit_20260907_1931/analyze.py
python -m pytest -q --confcutdir=runtime_data/capital_audit_20260907_1931 runtime_data/capital_audit_20260907_1931/test_analysis.py
```

La consulta remota termino correctamente y su tarea temporal fue retirada.
MT5 y el bot conservaron sus procesos. No hubo ordenes, cambio de apalancamiento,
commit, push, despliegue ni reinicio. Ningun resultado de este documento certifica
la rentabilidad de una candidata ni resuelve los fallos de base anteriores.
