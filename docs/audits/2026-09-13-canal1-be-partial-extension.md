# Canal 1: Break-Even Y Cierre Parcial

## Estado

Bloque terminado y verificado bajo el codigo definitivo: 5539 pruebas pasan y
laboratorio `shared_family_lab_v3` verificado, 5472 evaluaciones sin incidencias
de motores/cartera. Se habilitan estos controles BE/parciales, no todas las
variantes ni la busqueda masiva. Canal 2, capital y operativa real fuera del bloque.

## Cambio

La nueva capacidad `ProtectionProfile.policy_extension=own_rule_be_partial_v1`
es opt-in. Su valor por defecto es `none`; el nombre descriptivo del perfil no
activa comportamientos. El perfil anterior sigue rechazando BE/parciales.
El laboratorio y su CLI solo activan la extension al pedirla expresamente.

Se implementan por separado scalar, fast de punto fijo y oracle. El oraculo
no importa los otros motores ni sus funciones de ejecucion. Contratos, tipos y
costes comunes conservan la estructura existente; no hay motor sustitutivo.

## Semantica

Break-even por precio:

- El movimiento de activacion se compara de forma decimal exacta contra el
  precio de entrada, sin tolerancias. El beneficio economico puede seguir
  siendo negativo por spread, deslizamiento o costes al cerrar en ese stop.
- Un pico anterior a la confirmacion de entrada no activa retroactivamente BE.
- La activacion produce intencion/peticion, no un stop instalado instantaneo.
  Se respetan procesamiento, rechazo, confirmacion y espera de reintento.
- La proteccion anterior tiene prioridad en la cotizacion de procesamiento.
  Un stop instalado puede ejecutarse antes de recibirse su confirmacion.

Cierre parcial:

- Los umbrales existentes de `partial_runner` son dinero hipotetico de la cesta:
  realizado mas flotante convertido, no movimiento de XAUUSD. Se corrige esa
  descripcion en el catalogo; el archivo v1 permanece sin alteraciones.
- Se solicita una sola tanda de volumen parcial. Tanto ese volumen como el
  residual deben respetar min/max/paso; si no, bloqueo antes de abrir.
- La peticion se procesa en una cotizacion posterior. El volumen restante
  conserva sus protecciones; no se registra el ticket como totalmente cerrado.
- Se espera la confirmacion del parcial antes de decidir otra salida. El
  remanente puede cerrar por objetivo monetario runner, stop, BE o tiempo.
- Un stop puede ganar al parcial pendiente: cierre completo y rechazo de la
  peticion posterior sobre una posicion que ya no existe. No se duplica volumen.
- El swap devengado se reparte entre el volumen cerrado y el residual. Nuevos
  cargos solo se aplican al volumen abierto. Esto se prueba sinteticamente;
  no se admiten por ello posiciones nocturnas historicas sin sus datos/costes.

No se trata de una ejecucion parcial por falta de liquidez del broker. Se modela
una orden de cierre por un volumen elegido por la estrategia, con sus esperas.

## Barreras Conservadas

La extension requiere entradas hipoteticas schema2, mercado y proteccion
integrados, gestion propia sin proveedor y sin cliente serial. BE habilitado:
solo `price`. Otros modos de BE siguen bloqueados. Parciales: entradas
simultaneas, no escaleras con aperturas futuras. Freeze distinto de cero,
volumen incompatible, conversion desconocida, fin de datos con operaciones o
confirmaciones pendientes y presupuestos de eventos conservan sus bloqueos.

No se altera capital, tolerancia a perdidas, margen, stop-out o politica real.
El limite del laboratorio sigue siendo referencia de exposicion, no una cuenta
financiada: 0.02 lotes por entrada, dos reservas, 0.04 lotes y 40 USD de perdida
configurada antes de gaps/costes. Se reserva hasta el horizonte de 20 minutos,
aunque cierre antes. No se usa el cierre futuro para admitir otra senal.

## Pruebas

- Contrato publico nuevo: fallo por campo no reconocido antes de implementarlo.
- Con solo el contrato: 24 fallos de capacidad y dos aprobados. Tras implementar
  la ejecucion: 26 aprobados.
- Ampliacion a conversion, swap, combinaciones de perfiles y 240 recorridos
  sinteticos BUY/SELL con latencias/costes, varias posiciones y reglas mezcladas.
- Una regresion de umbral 0.3 encontro cuatro discrepancias de precision binaria
  entre fast y scalar/oracle. Se reparo con aritmetica decimal en el nuevo camino
  integrado; los 16 controles de umbrales/escala de precio pasan. El BE legacy
  sin perfil queda fuera de esta nueva evidencia, no se revalida implicitamente.
- 551 pruebas focalizadas aprobadas en 15.98 s. Incluyen 89 pruebas nuevas de
  extension y dos de laboratorio, ademas de compatibilidad con perfiles previos.
- `py -3.14 -m pytest -q`: 5534 aprobadas en 401.59 s, 634 advertencias de
  dependencias/deprecaciones y del control deliberado de mutacion de pandas.
- Revision directa del cambio y de sus limites; no se presenta como revision
  realizada por un agente independiente.

La revision posterior encontro `decimal.InvalidOperation` para NaN en proporcion
o volumen parcial. Se mantiene el rechazo canonico de valores no finitos mediante
ValueError, sin serializar NaN/Infinity ni inventar identidad para una regla
invalida. La validacion de lotes ahora se omite si el genoma ya es invalido; los
valores finitos fuera de limites siguen produciendo resultados bloqueados.
Cinco pruebas adicionales conservan esos dos contratos. El fixture de escalera
se corrigio a dos patas para comprobar la barrera sobre un genoma valido.

Verificacion del codigo definitivo, sin nuevos cambios durante su ejecucion:

- `py -3.14 -m pytest -q tests/test_own_rule_protection_extension.py tests/test_dubai_shared_lab.py tests/test_dubai_annual_dataset.py tests/test_dubai_family_catalog.py`:
  121 aprobadas en 12.85 s, incluidas las 94 de extension.
- `py -3.14 -m pytest -q`: 5539 aprobadas en 426.45 s, 634 advertencias;
  proceso terminado con salida 0. Python 3.14.2, pytest 9.0.3, Windows.

## Archivo Anterior

Antes de editar se volvio a verificar `shared_family_lab_v1`: salida 0,
identidad `2f2f8e04ba57c43e3a5646fc8bac00f79cbd25b953b25deb8b104e8a108e9b78`.
Se copiaron y verificaron 63 fuentes de codigo vinculadas en
`runtime_data/canal1_history_20260913/implementation_before_extensions_v1`.
Mensajes, precios y cobertura no se modifican. Tras cambiar implementacion,
v1 es evidencia historica de ese codigo conservado, no un resultado bajo el
motor nuevo. No se fuerza su verificador de codigo vigente para hacerlo pasar.

## Control Historico V2

Terminado y verificado antes de esa ultima correccion. Identidad
`1612c9f905a3afe20290403c0c8e746f68c74ada3289143dabcf7ca29bc19e3d`, 1052 fuentes.
5472 evaluaciones, 416 combinaciones completadas y 32 bloqueadas por la jornada
de junio; cero incidencias de motores/cartera. Sin bloqueos de capacidad para
estos 16 controles fijos. Se conservan las 1952 filas: 1506 simuladas, 318 sin
entrada, 96 de jornada incompleta y 32 no admitidas por reserva de exposicion.

Comparacion estructurada v1-v2: las 392 combinaciones de las 14 familias
anteriores, incluidas sus 1708 filas y 4788 resultados de motor, coinciden
exactamente excluyendo solo `behavior_digest`. La comparacion incluye carteras,
motivos, reservas, tiempos, precios, volumen, dinero hipotetico y trazas.
No se ha usado esa coincidencia para cambiar de etiqueta la version de codigo.

Se conserva v2 y una segunda copia de 63 fuentes en
`implementation_before_partial_validation_fix_v1`. v2 conserva su identidad
historica; la repeticion definitiva se registra por separado a continuacion.

## Control Historico Vigente V3

Archivo `runtime_data/canal1_history_20260913/shared_family_lab_v3`, identidad
`55cb522be3239178a23359a791f23e954065796fd4345171b3e22d2837f4b2be`.
Comprobacion nueva de sus cuatro resultados vinculados y 1052 fuentes, salida 0:

```text
py -3.14 tools/run_dubai_shared_lab.py verify --output runtime_data/canal1_history_20260913/shared_family_lab_v3
verified_current_hypothetical_family_lab
```

Ejecucion con `--policy-extension own_rule_be_partial_v1`; mismo `entry_stream_v3`,
`coverage_v1`, `raw_mt5_v2_audit.json` y parametros de v2. No optimizacion ni cambio
de fechas: 02/01, 02/02, 02/03, 01/04, 01/05, 01/06, 01/07, 03/08 y 01/09.
Los 45 casos de version conocida y los 16 sin editar son escenarios distintos,
no 61 senales originales independientes ni rentabilidad de una cuenta anual.

- 16 controles fijos, dos perfiles de ejecucion, 448 combinaciones y 1952 filas.
- 416 combinaciones completadas y 32 bloqueadas por cobertura de la jornada
  del 01/06. Cada familia conserva 26 completadas y dos bloqueadas.
- 5472 evaluaciones de motor y 73674528 visitas a cotizaciones, bajo los limites
  previstos de 5856 y 78055104; presupuesto duro de 9000, 100000000 y 30 minutos.
- Filas: 1506 simuladas, 318 sin entrada, 96 de jornada incompleta y 32 no
  admitidas por reserva de exposicion. Cero bloqueos de capacidad para estos
  controles; cero incidencias de motores o cartera.
- Maximos en estas simulaciones: dos senales y 0.04 lotes. No acreditan margen,
  capital suficiente ni una perdida maxima posible. Reserva hasta 20 minutos,
  tambien si la posicion no llega a abrirse o cierra antes.

Comparacion estructurada v2-v3: las 448 combinaciones, sus 1952 filas y 5472
resultados de motor coinciden exactamente excluyendo solo `behavior_digest`.
Incluye carteras, bloqueos, reservas y trazas, no solo cifras finales. Salida 0,
cero diferencias. La correccion de validacion no cambia los controles validos.

Inspeccion adicional de los resultados finales, sin modificar sus archivos:
684 recorridos de motor de las dos familias nuevas, cero errores. En cada ticket,
volumen cerrado igual al abierto y cierre de proteccion solo en la ultima salida.
Las 204 peticiones posteriores a parciales respetan su confirmacion; las 225
instalaciones de BE tienen peticion causal posterior a la confirmacion de entrada.
Hay 270 ejecuciones parciales entre los tres motores, 90 por motor.

En un solo motor son 114 filas simuladas por cada familia, incluyendo ambos
relojes/perfiles. Para parciales: 90 primeras salidas parciales y 114 salidas
finales (49 runner, 38 stop fijo, 27 tiempo). BE: 75 instalaciones y 114 cierres
(26 BE, 22 stop fijo, 46 objetivo, 20 tiempo). Estos recuentos comprueban rutas
ejercitadas; no comparan beneficios ni representan observaciones independientes.

`selected_policy=null`, cero candidatas optimizadas, `annual_portfolio=null` y
`sample_aggregate_profit=null`. Admision automatica, dinero verificado, paridad
real completa y busqueda masiva siguen en `false`. Comision/swap historicos cero
son supuestos de este control intradia, no costes reales certificados.

## Continuacion

El siguiente bloque es el contexto anterior a la entrada: cotizaciones de
calentamiento y filtros causales, sin rellenar volatilidad desconocida con cero.
Despues, revision del alcance y congelacion de una campana por familias y
ventanas moviles. Este bloque no busca una ganadora ni demuestra rentabilidad.

Sin MT5, VM, ordenes, cambio live, commit, push o despliegue. Todo queda local.
