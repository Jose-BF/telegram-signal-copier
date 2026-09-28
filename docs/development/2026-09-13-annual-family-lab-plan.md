# Laboratorio Anual De Familias

## Alcance Autorizado

El usuario pide la preparacion mas completa posible y proceder si esta lista.
Se implementa la conexion anual, inventario de familias y un control acotado
de integracion. No implica busqueda ilimitada, seleccion, capital supuesto,
modificacion live, ordenes, MT5, VM, commit ni push.

Entrada vigente: `entry_stream_v3`; 881 casos de version conocida y 240 sin
editar, escenarios independientes. Precios raw v2 y cobertura v1 conservados.
El control `entry_probe_v1` dio 60/61 simulados y 180 evaluaciones coincidentes.
No cambiar ni invalidar esos archivos o sus implementaciones para ampliar.

## Hitos

- [x] Catalogo ejecutable: todos los campos de StrategyGenome clasificados,
  familias propias congeladas, exclusiones y ampliaciones visibles; pruebas
  que detecten opciones nuevas sin clasificar. Sin afirmar cobertura infinita.
- [x] Entrada anual perezosa: comprobar hashes y conservar denominadores,
  cobertura 20 min/cuatro horas, ventanas moviles y dias completos; conectar
  paths y fuentes canonicas existentes en cargas de tamano limitado.
- [x] Exposicion compartida: dos reservas simultaneas como maximo, 0.04 lotes
  y 40 USD de perdida configurada antes de costes/gaps como referencias, no
  margen ni capital. Reservar todos los componentes de cada entrada desde
  el trigger hasta el fin de su horizonte, aunque cierre antes; politica
  conservadora y causal, distinta de liberar al cerrar (ampliacion pendiente).
  Lotes de referencia 0.02 por cesta; stop inicial fijo de 10 unidades XAUUSD.
  Coincidencias temporales atomicas: aceptar todo el grupo o rechazarlo entero.
- [x] Control por familias y ejecucion: mismas primeras jornadas completas
  mensuales que el control anterior, ambos relojes separados, perfiles base
  y adverso predeclarados, maximo 18 controles, 9000 evaluaciones, 30 minutos.
  Sin afinado ni clasificacion por beneficio. No reemplazar casos fallidos.
- [x] Verificacion: regresiones, integracion, suite completa y revision directa
  del cambio; informe y mapa compartido actualizados con evidencia y limites.

## Contratos

Reutilizar `make_path`, cobertura/normalizacion raw, tres motores, perfiles y
reconstruccion de cartera existentes. Nuevas piezas locales y acotadas; no
alterar motores para conseguir igualdad. El dataset anual no es el antiguo
input NOW ni una recepcion observada. Precios previos necesarios para filtros
de contexto no se inventan ni se sustituyen por cero volatilidad en el trigger.

La reserva de horizonte es una politica real de este experimento, no estimacion
de exposicion efectiva; se muestran ambas por separado. No se emplean P/L o
tiempos de cierre futuros para decidir admision. Una jornada con cobertura
incompleta se conserva bloqueada para todas las reglas del control; no se suma
el subconjunto sano como si fuera el resultado anual o de la muestra completa.
El corte de datos es retrospectivo; los finalistas necesitaran datos nuevos.

Los costes base siguen siendo hipotesis optimistas. El perfil adverso prueba
latencias y deslizamiento; comisiones/swap reales y margen no quedan certificados.
Capital y caida tolerable consultados al usuario sin bloquear el trabajo de
referencia. Dinero verificado, seleccion y promocion permanecen desactivados.

El usuario aclara que solo se trabaja con canal 1 y que canal 2 se tratara mas
adelante. Las reservas compartidas se aplican entre senales de canal 1, nunca
entre ambos canales. Capital/tolerancia no facilitados, no se presuponen.

Comprobacion sintetica: 25 pruebas nuevas pasan. El preflight de perfiles
identifica `break_even` y `partial_runner` como `protection_policy_unsupported`.
Se conservan entre los 16 controles pero no entran en motores ni se sustituyen;
14 controles admitidos para la prueba. Ampliar esa proteccion es un trabajo
explicito pendiente, no quitar una restriccion para forzar un aprobado.

Suite completa ejecutada tras los cambios: `py -3.14 -m pytest -q`, 5443
pruebas aprobadas en 563.03 s, 634 advertencias (incluidas deprecaciones y un
control deliberado de mutacion de pandas). Revision directa de los cuatro
modulos nuevos y de sus pruebas, sin revision por un agente independiente.
Control historico terminado en `shared_family_lab_v1`: protocolo congelado antes
de leer precios para los motores; nueve fechas, 61 casos, techo previo de 5856
evaluaciones y 78055104 visitas a cotizaciones. No cambiar codigo vinculado
durante el calculo ni sobrescribir un archivo de resultados existente.

Resultado: 448 combinaciones, 1952 filas conservadas; 364 combinaciones
completadas, 56 bloqueadas por perfil y 28 por la jornada incompleta del 01/06.
4788 evaluaciones sin discrepancias de motores ni incidencias de cartera;
maximo simultaneo dos senales y 0.04 lotes. Verificacion nueva contra 1052
fuentes, codigo y entorno actuales, salida 0. Identidad
`2f2f8e04ba57c43e3a5646fc8bac00f79cbd25b953b25deb8b104e8a108e9b78`.
[Informe y continuacion](../audits/2026-09-13-canal1-shared-family-lab.md).
Todos los hitos de este bloque cerrados; preparacion global para busqueda
masiva y barreras de dinero/seleccion siguen abiertas. No hay ganadora.

## Modulos Previstos

`research/dubai_family_catalog.py`, `research/dubai_annual_dataset.py`,
`research/dubai_shared_lab.py`, `tools/run_dubai_shared_lab.py` y pruebas
especificas. Evidencia inmutable bajo `runtime_data/canal1_history_20260913/`.
