# Canal 1: Laboratorio Anual De Familias

Nota posterior: la etiqueta de unidades de `partial_runner` en el catalogo v1
era incorrecta; sus umbrales en el motor son EUR hipoteticos de la cesta, no
unidades de precio XAUUSD. Esa familia estaba bloqueada y no se ejecuto en v1.
La [ampliacion opt-in y correccion](2026-09-13-canal1-be-partial-extension.md)
conserva este archivo de resultados y 63 fuentes de su codigo anterior. Despues
de esa ampliacion, v1 es evidencia historica, no codigo vigente reetiquetado.
La version vigente v3 admite los 16 controles, con 5472 evaluaciones verificadas
y 5539 pruebas aprobadas. Ver el informe enlazado para resultados y limites;
los recuentos y barreras originales de v1 se conservan debajo como historia.

## Alcance

Preparacion y control de integracion de reglas propias desde los mensajes de
canal 1. Canal 2 queda fuera por confirmacion del usuario. No hay optimizacion,
ganadora, capital supuesto, cambio de politica real ni busqueda masiva.

Este bloque amplia el [control mensual de entradas](2026-09-13-canal1-entry-probe.md).
Las mismas nueve fechas y 61 casos se congelan antes de evaluar precios: no
se eligen dias por beneficio, por cobertura favorable ni por concordancia.
El muestreo mensual es un control de software, no la muestra definitiva para
valorar estrategias ni un sustituto de las ventanas moviles acordadas.

## Entrada Anual

Se enlaza `entry_stream_v3` con precios raw v2, cobertura v1 y los tipos de
entrada/cartera existentes. Se verifican bytes de las fuentes; la cobertura
raw se recalcula al cargar cada jornada. No se afirma haber ejecutado los
1121 casos anuales ni haber decodificado todos sus recorridos en este bloque.

| Reloj hipotetico | Casos | Dias con casos | Pasan 20 min | Dias completos 20 min | Pasan 4 h | Dias completos 4 h |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Primera version conocida | 881 | 184 | 863 | 166 | 724 | 101 |
| Publicacion sin editar | 240 | 109 | 235 | 104 | 204 | 80 |

Al exigir jornadas completas, en el primer reloj quedan 800 casos en dias
completos a 20 minutos y 442 a cuatro horas. En el segundo quedan 231 y 179.
Los restantes no desaparecen: 81/439 y 9/61 siguen en jornadas incompletas.
No se suman beneficios del subconjunto completo como rentabilidad del ano.

Cada reloj conserva 15 ventanas: 56 dias de desarrollo, 14 de comprobacion,
avance quincenal y purga de cuatro horas, con la ultima ventana parcial.
Son datos retrospectivos, no validacion nueva. Las ediciones no se convierten
en publicaciones iniciales ni se inventa una hora de recepcion observada.
Ambos escenarios se evaluan separados; no son dos canales ni dos muestras
independientes que puedan sumarse para aumentar la evidencia.

## Mapa De Familias

El catalogo clasifica los 32 campos actuales del motor y comprueba sus opciones
categoricas contra la validacion del propio motor. Una opcion nueva sin
clasificar hace fallar el control del catalogo. Eso evita omisiones del motor
conocido; no demuestra cobertura de toda estrategia imaginable.

| Control fijo | Variacion |
| --- | --- |
| Mercado | Entrada inmediata, objetivo a 5 unidades de precio |
| Demora | Espera de 30 segundos |
| Retroceso | Entrada tras 2 unidades adversas |
| Continuacion | Entrada tras 2 unidades favorables |
| Recuperacion | 2 unidades adversas y recuperacion de 1 |
| Objetivos separados | Dos posiciones, objetivos 3 y 8 |
| Escalera adversa | Dos posiciones separadas 2 unidades en contra |
| Escalera favorable | Dos posiciones separadas 2 unidades a favor |
| Break-even | A 3 unidades; bloqueado por perfil de proteccion |
| Trailing | Distancia 3, sin objetivo fijo |
| Proteccion de beneficio | Activacion hipotetica a 4 EUR, devolucion 2 EUR |
| Salida por tiempo | Cinco minutos, sin objetivo fijo |
| Parcial y runner | Media posicion a 3, resto a 8; bloqueado por perfil |
| Filtro de spread | Maximo 0.6 unidades de precio |
| Corte horario | Hora UTC <=12; no es un rango de sesiones |
| Sin entrada | Control negativo |

Hay 16 controles, incluidos dos bloqueados. `break_even` y `partial_runner`
reciben `protection_policy_unsupported` antes de cargar sus recorridos en los
motores. Se mantiene esa barrera; no se desactiva proteccion ni se cambia de
perfil para obtener un aprobado. Cerrar posiciones separadas con distintos
objetivos no equivale a cerrar parcialmente una misma posicion.

Los rangos futuros de parametros quedan documentados, no ejecutados. El
catalogo diferencia opciones presentes pero no comprobadas y ampliaciones:
direccion contraria, reentradas, contexto previo, volatilidad adaptativa,
tendencia/regimen causal, noticias con hora de publicacion, gestion entre
cestas, posiciones nocturnas, margen/stop-out y ejecuciones parciales del broker.
Los filtros de volatilidad quedan bloqueados sin historia anterior al trigger;
no se alimentan con volatilidad cero fabricada. Recuperacion de riesgo ilimitado
queda fuera. Canal 2 y otros activos no pertenecen a este universo.

## Exposicion Y Ejecucion

- Referencia por caso: 0.02 lotes totales y stop inicial a 10 unidades XAUUSD.
  Con contrato 100, perdida configurada de 20 USD antes de gaps y costes.
- Limite conjunto del canal 1: dos reservas, 0.04 lotes y 40 USD configurados.
  No son capital, margen, perdida maxima posible ni limites propuestos para real.
- Cada caso reserva toda su capacidad durante 20 minutos desde el trigger,
  aunque no entre o cierre antes. Solo se libera estrictamente despues de su
  vencimiento. Es una regla conservadora del experimento, no exposicion real.
- Casos con la misma hora se admiten todos o ninguno. La reserva no lee precios,
  beneficio, cobertura futura ni tiempos de cierre para decidir la entrada.
- Salvo el control de cinco minutos, salida a 15 minutos; expiracion de entrada
  a tres minutos. Ambos caben dentro de los 20 minutos con cola de ejecucion.
- Cada jornada y reloj es una simulacion separada sin posiciones iniciales.
  No se reconstruye una cuenta continua anual ni posiciones heredadas de otros
  dias. Liberar capacidad con confirmaciones de cierre requiere otro contrato.

Perfil de referencia: 250 ms en entrada, tramitacion y confirmaciones del
modelo, sin deslizamiento adicional. Perfil de contraste: entrada 1000 ms,
tramitacion/confirmaciones de 500 ms, 0.1 unidades de deslizamiento en entrada
y salida, y 0.1 de spread adicional. No son costes calibrados del broker ni
una cota garantizada de peor resultado: una demora puede cambiar el recorrido.

Dinero hipotetico: USD convertido a EUR con Bid/Ask causal, contrato 100,
precision dos decimales, comision y swap cero. No se certifican capital,
apalancamiento, margen, stop-out, costes reales, cliente serial global ni
contencion entre cestas. Capital y caida tolerable quedan pendientes; no se
extrapola el saldo de una cuenta a partir de estos tamanos de referencia.

## Resultado Historico

Control terminado y verificado en un proceso nuevo. Nueve fechas: 02/01,
02/02, 02/03, 01/04, 01/05, 01/06, 01/07, 03/08 y 01/09 de 2026. Son 45 casos
del reloj de version conocida y 16 sin editar. El archivo conserva exactamente
448 combinaciones de jornada/reloj/familia/perfil y 1952 filas de casos.

| Estado de combinacion | Cantidad |
| --- | ---: |
| Diagnostico completado con cartera hipotetica | 364 |
| Bloqueo del perfil: break-even/cierre parcial | 56 |
| Bloqueo de jornada por datos | 28 |

| Estado de fila | Cantidad |
| --- | ---: |
| Operacion simulada | 1278 |
| Sin entrada ejecutada | 318 |
| No admitida por reserva de exposicion | 28 |
| Jornada incompleta | 84 |
| Regla bloqueada por perfil | 244 |

Los 1596 casos evaluados producen 4788 evaluaciones scalar/fast/oracle sin
discrepancias en los campos comparados (se excluye solo `behavior_digest`).
No aparecen incidencias de cartera ni operaciones fuera de la reserva.
Maximo simultaneo en las carteras hipoteticas: dos senales y 0.04 lotes;
maximo reservado: 0.04 lotes. Son limites de este experimento, no de una cuenta
financiada. Las 1952 filas no son otras tantas senales independientes.

La entrada BUY `dubai_stream:revision_time:17866`, 02/03 a las 16:15:41 UTC,
no se admite por capacidad en los 28 controles/perfiles ejecutables. Mantiene
su identidad y motivo, sin usar sus resultados para decidir la reserva.

El 01/06 queda bloqueado entero en el reloj principal: `20016`, `20025` y
`20030`. El ultimo conserva las tres pausas XAUUSD superiores a cinco segundos,
maximo 11.05 s, ya documentadas en el control anterior. Las otras dos entradas
no se rescatan para sumar solo la parte favorable de la jornada. Los controles
de perfil bloqueado conservan tambien la cobertura original en cada fila.

Presupuesto previo: 5856 evaluaciones y 78055104 visitas completas a recorridos
como cota. Consumo contabilizado: 4788 y 64465212; el segundo cuenta el recorrido
completo por motor, no afirma que cada motor haya inspeccionado cada cotizacion
despues de cerrar. Limites duros: 9000 evaluaciones, 100 millones de visitas y
30 minutos; sin truncar la matriz para encajar en ellos.

Archivo inmutable: `runtime_data/canal1_history_20260913/shared_family_lab_v1`.
Cinco archivos exactos y 1052 fuentes vinculadas verificados contra codigo y
entorno vigentes (Python 3.14.2, Numba 0.65.0, NumPy 2.4.4, pandas 3.0.2).

- Identidad: `2f2f8e04ba57c43e3a5646fc8bac00f79cbd25b953b25deb8b104e8a108e9b78`.
- SHA protocolo: `52c09f9e73f0b981e04070d0deb4d2eb3ae27b283a19930897350b1e4a3428e2`.
- SHA resultados: `49a40b165bfdce35ec6f6f31f590f1a21fc23ff6055fc531dd70b48012d3f037`.

Seleccion nula; cero candidatas de busqueda. `ready_for_massive_search`,
`money_contract_verified`, `account_currency_money_verified` y admision
automatica siguen en falso. No hay beneficio agregado de muestra ni cartera
anual. Pasar este control no certifica cada combinacion de parametros de las
14 familias, su rentabilidad o sus fills reales.

## Verificacion

- `py -3.14 -m pytest -q tests/test_dubai_shared_lab.py tests/test_dubai_annual_dataset.py tests/test_dubai_family_catalog.py`:
  25 pruebas aprobadas en 12.16 s.
- `py -3.14 -m pytest -q`: 5443 pruebas aprobadas en 563.03 s, 634 advertencias.
  Incluyen deprecaciones y el control deliberado de mutacion de pandas.
- `py -3.14 tools/run_dubai_shared_lab.py run --stream runtime_data/canal1_history_20260913/entry_stream_v3 --coverage runtime_data/canal1_history_20260913/coverage_v1 --raw-audit runtime_data/canal1_history_20260913/raw_mt5_v2_audit.json --output runtime_data/canal1_history_20260913/shared_family_lab_v1`:
  terminado, salida 0, sin incidencias de motores/cartera.
- `py -3.14 tools/run_dubai_shared_lab.py verify --output runtime_data/canal1_history_20260913/shared_family_lab_v1`:
  `verified_current_hypothetical_family_lab`, identidad y denominadores intactos.
- Relectura estructurada adicional: 448 combinaciones, 1952 filas, 364 carteras,
  exposicion maxima y barreras de dinero/seleccion comprobadas.
- Revision directa del catalogo, carga anual, reservas, CLI y pruebas; no se
  presenta como revision realizada por un agente independiente.

Se prueba causalidad de reservas, simultaneidad atomica, cada limite, dias
incompletos, presupuestos previos a lectura, perfiles bloqueados, discrepancia
de motores, integracion con cartera, fuentes alteradas e inmutabilidad.

## Continuacion

1. Este control no revela discrepancias de motores/cartera. Mantener los casos
   de datos incompletos y sus motivos, sin ampliar tolerancias o sustituirlos
   para mejorar el aspecto de una comparacion.
2. Ampliar y verificar break-even/cierre parcial y contexto previo. Incorporar
   cada extension con un contrato y pruebas propios, no como bandera de acceso.
3. Congelar una campana acotada por familias, parametros, horizonte, exposicion
   y escenarios. Repartir exploracion entre familias y registrar todos los
   intentos; no concentrarla solo en la primera que resulte atractiva.
4. Evaluar con ventanas moviles y dias completos, separando relojes y cobertura.
   Comparar estabilidad entre meses, sensibilidad de parametros, perdida de
   cola, exposicion y drawdown de equity; no seleccionar solo por beneficio.
5. Refinar zonas estables y combinaciones justificadas, contabilizando los
   ensayos multiples y los bloques ya reutilizados. Mantener costes y dinero
   no verificados como restricciones, y reservar datos nuevos para finalistas.

La implementacion y las pruebas son locales. Sin commit, push, despliegue,
acceso a VM, arranque de MT5, operaciones ni modificacion de la politica real.
