# Contraste Factual Del Motor Actual

Version final de este bloque: control `market_independent_v3_bound`, comparacion
`market_factual_comparison_v2.json`. Las referencias v2/v1 de las primeras
secciones conservan el paso anterior a la revision independiente.

## Alcance

Trabajo local dentro del frente paralelo autorizado el 10/09 a las 10:22
Madrid. Se compara el control Gold de entradas y estado propios con hechos
MT5 leidos despues. No es una reproduccion con fills reales como entrada,
validacion nueva OOS, ajuste de tolerancias ni busqueda de estrategias.
No se modifica el bot live, no hay commit, push, reinicio ni orden.

Se reutiliza el generador inmutable `market_controls.py` del 09/09, con sus
11 senales, politica y perfil ya definidos. El control anterior tenia una
version distinta de `runtime_paths.py`; los demas hashes y el entorno eran
iguales. Se repite el control bajo identidad nueva, sin modificar resultados
anteriores: `runtime_data/simulator_delivery_20260910/market_independent_v2/`.

Resultado: 33 evaluaciones en 14,09 s, cero desacuerdos entre los tres motores
y cero bloqueos de ejecucion. No equivale a igualdad con MT5.

## Comparador

`tools/compare_market_controls.py` verifica protocolo, fuentes, identidad
actual, metadatos causales congelados y denominador. Recalcula los desacuerdos
entre motores, sin confiar solo en la etiqueta guardada. Reutiliza la prueba
de las 59 aperturas y los 1.138 intentos contra el registro causal original,
los hashes de las capturas y el ledger conciliado. Compara entradas, salidas,
volumen, motivo y dinero por posicion logica; no empareja por precio cercano.

Resultado inmutable:
`runtime_data/simulator_delivery_20260910/market_factual_comparison_v1.json`,
SHA256 `12c83ae1706840b6f31a8adefb312f6aadde934c6ff4310f1988ce558ddc2c29`.

- 18 senales / 59 posiciones observadas siguen en el denominador.
- Gold: 11 controles, 45 aperturas reales frente a 46 simuladas.
- Los 11 difieren en algun hecho; siete Dubai quedan explicitamente fuera
  del control Gold, sin borrarlos ni presentarlos como aprobados.
- Dos senales tienen diferencias estructurales: 2590 y 2640. Esta categoria
  significa evento ausente, direccion, volumen o mecanismo distinto; no
  concede tolerancia a diferencias de precio, tiempo o dinero.

## Primeras Causas

1. `canal2_2590`: cuarta entrada simulada sin equivalente observado, a
   06:37:20.656 UTC. Es la bifurcacion conocida del ladder anclado al fill:
   una diferencia en la primera ejecucion cambia los niveles posteriores.
   El perfil de 250 ms no es una latencia calibrada. No se ajusta al caso.
2. `canal2_2628`: primera entrada simulada 346.494 ms despues de la real.
   La recepcion raw es 13:28:15.241; el modelo usa el primer tick posterior,
   13:28:15.325, Ask 4400,28, y requiere bajar a 4399,28. El minimo antes de
   la entrada real fue 4399,29, por lo que no arma esa primera oportunidad.
   El bot registro referencia Ask 4400,41 del tick 13:28:15.085, anterior a
   la recepcion, y armo a 4399,33. La discrepancia nace en la seleccion de
   cotizacion de referencia, no en seis minutos de retraso de ejecucion.
   Datos comprobados con el archivo de ticks y `gold_555_entry_watch_started`,
   `gold_555_entry_watch_state` del mismo registro causal verificado.
3. `canal2_2640`, primera pierna: cierre experto real a 16:52:29.929 frente
   a TP simulado a 16:56:18.671. El mensaje 2642 dice "Be ready to close
   overall profit"; el compilador congelado lo clasifico como
   `MOVE_SL_TO_BE`, y la politica `explicit_close_only` lo ignora. La
   interpretacion de gestion no coincide con la accion live. No se inventa
   una orden de cierre para igualar el resultado. La semantica del proveedor
   exige revision separada si se pretende reproducir esa politica live;
   no es una dependencia universal del estudio de direccion/hora y reglas
   propias solicitado por el usuario.

## Que Falta

La comparacion de hechos ya esta disponible. Sigue sin demostrar igualdad
de decisiones, solicitudes, rechazos y acuses, ni precios alternativos
realizables. La seleccion de cotizacion, el procesamiento y la latencia son
supuestos que deben formar parte del contrato declarado de cada estudio.
Las limitaciones heredadas del protocolo antiguo se conservan con nombre
explicito, no se utilizan como lista actual de funciones sin revisar.

No se cambia el motor para reproducir a medida un dia historico. El siguiente
paso es consolidar la lista de capacidades actuales y la admision de los JSON,
separando las capacidades de reglas propias de la paridad con el bot live.

## Verificacion

Pruebas focalizadas iniciales: 13 aprobadas para el comparador. Se cubren
universo incompleto/duplicado, conteos, cambio de identidad, lectura de
desacuerdos, senales sin direccion y casos observados fuera del control.
El archivo fuente se conserva; no se reemplazan datos ni etiquetas de los
controles anteriores.

## Revision Independiente Y Cierre

Se reprodujeron y corrigieron dos hallazgos P2:

- El protocolo podia cambiar de genoma o perfil mientras los resultados
  conservaban la misma identidad de codigo. `tools/run_market_controls.py`
  deriva del generador inmutable previo, que no se modifica. El contrato
  `fixed_market_regression_v2` guarda el SHA256 del protocolo antes de ejecutar,
  lo reverifica y lo vincula en `results.protocol_sha256`. El comparador
  rechaza protocolos cambiados y controles v1 sin ese vinculo. No se retoca
  un resultado viejo para hacerlo pasar; se ejecuta otro control congelado.
- `initial_sl` e `initial_tp` no se traducian a mecanismos nativos SL/TP.
  El helper comun ahora los reconoce. Las pruebas reproducen ambos falsos
  bloqueos. No cambia el calculo de dinero ni la ejecucion del motor.

Control final: 33 evaluaciones en 11,17 s, cero discrepancias entre motores y
cero bloqueos. Sus resultados por senal son **identicos** a los del primer
control de hoy. Protocolo:
`6e69cb82e1401a8de1b12f038f0fd88c2ae5fc1d9dd68d8713fcfa9487ed1d52`.
Comparacion final `runtime_data/simulator_delivery_20260910/market_factual_comparison_v2.json`,
SHA256 `ca00c71d771c936682fb47f036ee31d874a9947fe1ddd5bd464c16b55752ade8`.
Mantiene exactamente 11 diferencias Gold, siete Dubai fuera del control y
45/46 entradas, con las tres familias explicadas arriba. No certifica paridad.

Verificacion de la entrega:

- Suite completa antes de estos dos ajustes: **4511 PASS**, cero fallos,
  errores u omitidos, 634 warnings; 256,86 s. **402 fuentes sin cambios**
  durante la ejecucion. XML SHA256
  `686b9a4a60eb827f4a3bc8f539405a851d7a28f639642cd87bd8c5088f464ef9`.
- Despues de los ajustes: **101 PASS** en las cinco suites afectadas, 16,03 s;
  incluye 22 casos del comparador nuevo y las regresiones de mezcla de perfil,
  genoma, protocolo antiguo y etiquetas iniciales. El nuevo productor se
  verifica ademas con la ejecucion real de los 33 controles y la comparacion.
- `verification_v1/post_review_sources.json` conserva 403 fuentes y confirma
  que desde la suite completa solo cambiaron las dos herramientas de
  comparacion, sus pruebas y el productor nuevo. El resto de la evidencia
  sigue siendo aplicable; no se afirma que se repitieran todos los tests
  despues de los ajustes finales.

Todo queda local y sin publicar. D1, recepcion del adaptador, se cierra tras
revisar codigo, hashes originales, archivo retenido y puente al constructor.
La lista consolidada sigue separando 15 requisitos de entrega abiertos de
estas reparaciones del comparador. No son 15 fallos del motor.
