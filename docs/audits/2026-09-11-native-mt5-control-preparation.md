# Control Nativo MT5: Preparacion De Dos Jornadas

Estado al 11/09/2026, 18:51 Madrid: **preparacion parcial; las jornadas todavia
no se han simulado en el Probador de Estrategias nativo**. No hay un resultado
nuevo con el que afirmar que MT5 coincide mejor con la realidad.

## Peticion Y Comparacion

El usuario solicita repetir dentro de MT5 los dos controles retrospectivos
ya revisados, con la misma estrategia que opero, y compararlos tanto con las
operaciones reales como con nuestro simulador. No autoriza buscar parametros,
publicar, reiniciar el bot ni abrir operaciones de cuenta.

- 10/09: diez senales Gold NOW, incluidas las cuatro que no abrieron posiciones.
- 11/09: solamente las dos primeras senales Gold ya revisadas, no el dia completo.
- Politica fija: `Gold555Policy`, identidad live
  `555124a24b534aa2abda53ddaaa2ee35fd3afd07e61d05937eb14c80ad0676f0`.
- Las senales y cotizaciones son entradas. Los fills, beneficios y peores
  resultados observados se reservan para el comparador, no para decidir entradas.
- No ajustar tiempos o reglas para obtener una coincidencia deseada.

La discrepancia que motiva el control pertenece a `canal2_2774`: minimo de
resultado realizado mas flotante de la cesta de -237.88 EUR simulado, frente
a -147.50 EUR reconstruido con fills reales y las mismas cotizaciones. El
bot habia registrado -146.19 EUR en sus observaciones. No son tres medidas
de drawdown de la cuenta: hay diferencias de frecuencia y de ejecuciones.
La [investigacion previa](2026-09-11-execution-divergence-root-cause.md) explica
la influencia de los retrasos y las posiciones que seguian abiertas.

## Preparacion Verificada

Carpeta experimental local: `runtime_data/mt5_native_control_20260911/`.
Entradas vigentes: `inputs_v2/manifest.json`, SHA256
`7696cdfb10beba6d712cf4ffbbbe2227591eefcbd9c5fd21a7b1f32f8efb429c`.

| Control | Senales | Cotizaciones XAUUSD | Cotizaciones EURUSD |
| --- | ---: | ---: | ---: |
| 10/09 | 10 | 380703 | 82036 |
| Primeras dos del 11/09 | 2 | 36041 | 7107 |

Se comprobaron los hashes de los dos protocolos previos, mensajes y cuatro
series originales. La conversion conserva todas las filas y su orden,
incluidas cotizaciones con el mismo milisegundo. Las cuatro series CSV se
releyeron y compararon exactamente con los datos de origen. Se normaliza a
UTC restando una sola vez el desplazamiento documentado del reloj del broker.

Los CSV de senales contienen identidad, direccion, publicacion y recepcion.
Los cierres explicitos conservan su hora de disponibilidad. Los diagnosticos
del parser y las senales no ejecutadas no desaparecen del universo.
No se han resuelto ni ocultado las limitaciones FX ya identificadas.

Cuatro pruebas locales verifican precision temporal, conservacion de filas
con igual timestamp, rechazo de un doble desplazamiento horario y rechazo
de precios Bid/Ask invalidos. Resultado: **4 aprobadas**.

`inputs_v1/` y `prepare_inputs_attempt1.py` conservan un intento rechazado:
una prueba detecto unidades temporales incorrectas y la comprobacion exacta
de CSV detecto perdida de precision binaria en EURUSD. Se corrigio la
preparacion antes de generar `inputs_v2`; no hubo ejecucion de estrategia
con aquellas entradas. No se modificaron los estudios originales.

## Comprobacion Del Entorno

Se creo una copia separada de los ejecutables locales y de metadatos de
servidor/simbolos. No se copiaron bases de cuentas, contrasenas, perfiles de
robots, servicios o credenciales de la instalacion habitual.

El MetaTester build 6140 ejecuto `NativeEnvironmentProbe` en modo matematico:
el agente registro `NATIVE_ENVIRONMENT_PROBE_COMPLETED` y el resultado
271828. Es un control de ejecucion de codigo, **sin precios ni operaciones**.
El terminal tambien anoto `some error after pass finished`; no se presenta
este ensayo como un backtest financiero aprobado ni como un informe completo.

La prueba de importacion se cargo en un grafico EURUSD, pero no alcanzo su
marca de inicio/completado ni genero su archivo de resultado. La copia no
tiene una cuenta configurada; tambien se intento utilizar un historial local
EURUSD ya existente y habilitar ejecucion de programas, manteniendo
`AllowLiveTrading=0`. No se ha demostrado que la falta de cuenta sea la unica
causa de la inicializacion incompleta.

El MCP local respondio 401 a una solicitud sin credenciales. No se cambiaron
sus claves ni se desactivo autenticacion. No hay herramienta MT5 conectada
a esta sesion de Codex.

La copia experimental se actualizo automaticamente a build 6182 al volver
a arrancar. El actualizador sustituyo el proceso inicial, por lo que se
comprobo y detuvo expresamente el nuevo proceso de esa ruta. Las versiones
de cualquier ensayo posterior deberan volver a identificarse; no se mezclan
resultados de builds distintos como si fueran una sola ejecucion.

## Lo Pendiente

Se solicito permiso para utilizar el MT5 habitual de este ordenador con
operativa automatica desactivada durante el ensayo, sin tocar la VM. No se
ha arrancado esa instalacion ni aplicado cambios a su configuracion.

Una vez resuelto el acceso al entorno:

1. Cargar y comprobar las cotizaciones en MT5, incluyendo conversion EUR y
   correspondencia de velas/ticks. No suplir huecos con datos inventados.
2. Adaptar y verificar el robot de prueba con la politica fija completa.
   Los programas preparados hasta ahora son sondas del entorno, no ese robot.
3. Fijar condiciones de ejecucion y version antes del contraste, sin buscar
   la demora que acerque el resultado al historico.
4. Ejecutar ambos controles y comparar operaciones, precios, cierres, costes,
   exposicion abierta y minimos de cada cesta. Separar esos minimos del
   drawdown pico-valle y del riesgo global de la cuenta.

El probador nativo no reproduce automaticamente las pausas que sufrio el
puente Python en produccion. La comparacion debe distinguir una regla mal
implementada de una ejecucion distinta; que un resultado sea nativo no
garantiza que se acerque a -147.50 EUR.

## Alcance De Los Cambios

Solo archivos locales de este experimento y este informe. Ningun cambio de
codigo de producto, commit, push, orden, reinicio, captura programada o acceso
a la VM. No hay una busqueda masiva ni una estrategia seleccionada.

Referencias de las funciones utilizadas:
[arranque y configuracion de MT5](https://www.metatrader5.com/en/terminal/help/start_advanced/start)
y [simbolos personalizados](https://www.metatrader5.com/en/terminal/help/trading_advanced/custom_instruments).
