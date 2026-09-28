# Canal 1: Resultado De Las Tres Gestiones De Break-Even

## Decision

El bloque esta terminado y verificado. **No anadir BE a R05-60 por estos
resultados.** Sigue siendo una hipotesis pequena y concentrada, no una
estrategia certificada ni una prueba en papel activada.

Mover el stop no ha resuelto la dependencia de la gran ganadora. El BE del
proveedor empeora el conjunto amplio; el automatico a mitad de TP1 evita
perdidas y tambien corta ganancias, dejando una mejora neta insuficiente.
No se han optimizado mas umbrales despues de ver estos resultados.

## Comparacion Economica

Mismas 158 senales con rango y cobertura de una hora. 258 identidades retenidas
en todas las filas, incluidos bloqueos y formatos no aplicables. Una entrada
inmediata al recibir niveles, mismos lotes y SL/TP1 absolutos. Dos perfiles,
riesgo nominal de referencia hasta 200 USD por senal aislada y recargo de
10 EUR por lote ejecutado, ademas de spread/FX y ejecucion modelada.

EUR netos hipoteticos, no cuenta real ni retorno sobre capital:

| Gestion | Amplio referencia | Amplio desfavorable | R05 referencia | R05 desfavorable |
| --- | ---: | ---: | ---: | ---: |
| Sin BE | +25.53 | -561.99 | +995.30 | +911.63 |
| Primera instruccion directa recibida | -310.23 | -965.93 | +976.80 | +897.38 |
| BE al avanzar la mitad del TP1 | -180.21 | -524.88 | +915.42 | +887.58 |

El conjunto amplio ejecuta 153 operaciones en referencia y 152 en desfavorable;
R05 ejecuta las mismas doce en ambas. No cambian entradas ni lotes entre las
tres gestiones. R05 sigue teniendo +899.48 EUR de su gran ganadora en el perfil
desfavorable. Sin el mejor dia queda +12.15 sin BE, -2.10 con el aviso del
proveedor y -11.90 con el BE automatico. La concentracion no mejora.

La caida por cierres diarios de R05 desfavorable baja de 328.57 a 202.03 EUR
con BE automatico, pero su beneficio tambien baja y la dependencia de una sola
operacion persiste. No se elige solo porque el factor de beneficio suba de
2.30 a 3.13: esa razon no recoge por si sola el beneficio perdido ni la muestra
reducida. La peor operacion sigue en -193.01 EUR en los tres casos.
Estas caidas no son equity de una cuenta con senales superpuestas.

## Que Cambia En Las Operaciones

Sobre las 158 senales del conjunto amplio desfavorable:

- Aviso del proveedor: ocho resultados mejoran en total 463.53 EUR; 19
  empeoran en 867.47 EUR; 131 no cambian. Saldo diferencial -403.94 EUR.
  Ocho perdedoras mejoran, pero 17 antiguas ganadoras pierden beneficio.
- BE automatico: 18 resultados mejoran en 1678.43 EUR; 45 empeoran en
  1641.32 EUR; 95 no cambian. Saldo diferencial +37.11 EUR.
  Mejora 18 perdedoras y perjudica 43 antiguas ganadoras.

No todos los resultados negativos son stops grandes: una salida en el precio
de entrada puede quedar ligeramente negativa por costes y deslizamiento.
Por eso el BE automatico pasa de 105 a 62 resultados positivos, sin que eso
signifique que todas las salidas restantes sean perdidas de riesgo completo.

El diagnostico temporal previo encontro 42 avisos directos de BE antes del
cierre base, 53 despues y 57 sin aviso posterior a los niveles entre las 152
operaciones desfavorables. Catorce perdedoras recibian aviso antes de cerrar.
Eso no equivalia a catorce perdidas evitables: el SL debe poder instalarse.

Se instalo BE en 38 operaciones con el aviso del proveedor y en 114 con la
regla automatica. Hubo modificaciones rechazadas en 13 y tres operaciones,
respectivamente. El proveedor termina en 27 cierres BE; el automatico, en 63.
Los rechazos, reintentos y protecciones iniciales siguen visibles en el archivo.
No se contabiliza BE desde la solicitud ni se supone salida neta exactamente cero.

## Reglas Y Causalidad

- Solo la primera instruccion MOVE_SL_TO_BE de modalidad directa recibida
  despues del primer mensaje con niveles. Las condicionales, opcionales e
  informativas no se convierten en ordenes. Se conserva texto, fuente, revision
  recibida, evidencia y asociacion; no se inventa una revision canonica.
- El BE automatico usa la mitad de la distancia a TP1 cotizada al decidir la
  entrada, redondeada hacia arriba a 0.01 y con minimo 0.01. El avance se mide
  desde el fill del escenario. Es una regla propia, no el BE del trader.
- SL en el precio de entrada solo tras la aceptacion modelada. El stop anterior
  sigue activo mientras la modificacion esta pendiente o rechazada. Se respetan
  demora, acuse, reintento, restricciones y prioridad de protecciones existentes.
- Mismo horizonte de 3910 segundos, cobertura Bid/Ask/FX e intradia que el
  control anterior. Sin swaps inferidos, margen ni eliminacion de bloqueos.
- Todas las filas calculables se ejecutaron de nuevo en los tres motores.
  El control sin BE iguala los resultados anteriores, exceptuando el digest
  de comportamiento que puede incorporar identidad de perfil/inputs.

## Evidencia

Archivo completo: `runtime_data/canal1_history_20260913/break_even_study_v2`.
Identidad: `abbbfc0bfd5e19b691265e4458ee1626c2095403f665e95cdec73497fd06c524`.
1548 filas, 2844 evaluaciones nuevas, 948 contrastes completos entre motores,
46691197 cotizaciones fuente y 117361764 visitas de cotizacion. 511.53 segundos.
Fuentes, archivos, matriz, resumen y paridad retenida verificados.

Se preservaron 61 fuentes anteriores con hashes antes de ampliar el perfil
`absolute_levels_be_v1`, en `sources_before_absolute_be_v1`. Los estudios
anteriores pertenecen a esa implementacion congelada; no se afirma que todo
su catalogo se haya revalidado bajo el motor nuevo. El control correspondiente
de una hora si fue contrastado nuevamente en todas sus filas calculables.

`break_even_study_v1` conserva la incidencia del primer umbral 1.835, que el
motor rapido no representa con paso 0.01. Se anadio una prueba roja y se
concreto el redondeo causal hacia arriba a 1.84; no se amplio la tolerancia ni
se descarto la senal. v2 se ejecuto completo desde las fuentes.

Verificacion:

- `python -m pytest -q --tb=short`: 5822 pasan, 634 avisos, 383.28 s.
- `tests/test_dubai_break_even_study.py`: siete pruebas adicionales pasan.
- Las 19 pruebas de BE absoluto, incluidas en la suite, cubren ambos sentidos,
  aviso recibido, BE por precio, prioridades, rechazos/reintentos, modalidad,
  aviso anterior/tardio y trayectorias aleatorias. El perfil anterior no activa BE.
- `python tools/run_dubai_break_even_study.py verify --output runtime_data/canal1_history_20260913/break_even_study_v2`
  verifica 1548 filas. La verificacion de archivo no vuelve a ejecutar motores.
- `git diff --check`: sin errores; avisos de conversion CRLF existentes.

## Siguiente Paso

Mantener R05-60 sin cambios y comprobar si existe evidencia local adicional,
fuera de las senales usadas para seleccionarla. Primero inventariar recepciones
y ticks posteriores al 07/09, identificando cuales ya se usaron en diagnosticos
o calibracion. No llamarlos OOS nuevo sin esa comprobacion. Si hay cobertura
adecuada, contrastar la regla fija y su control sin filtro, sin ajustar parametros.
Si falta evidencia local, detener ese bloque y concretar que captura haria falta;
no sustituirla por mas variantes sobre los mismos doce casos.

Solo trabajo local. Sin commit, push, MT5, VM, ordenes, configuracion live ni
reinicios. No hay prueba en papel activa ni nueva version de produccion verificada.
Seleccion certificada, contabilidad verificada y validacion prospectiva pendientes.
