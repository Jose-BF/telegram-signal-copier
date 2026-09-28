# Comparacion Con Dubai Actual

## Alcance

Peticion nueva: gestionar la investigacion y comparar tambien con la estrategia
actual de Dubai. La automatizacion de 30 minutos permanece pausada. Trabajo
local sin publicaciones, cambios de produccion ni operaciones.

Referencia local: `dubai_balanced_v1`, contrato puro de
`dubai_live_candidate.py` y `strategy_runtime_contract.py`. La auditoria de VM
del 07/09 confirma esa identidad en su fecha; no certifica el estado remoto hoy.
No confundir esta politica del bot con replicar discrecionalmente al trader.

## Comparacion Congelada

Dos politicas: contrato Dubai balanced v1 y R05-60 sin BE/DCA. No optimizar
parametros a partir de esta comparacion. Mantener tambien el control inmediato
sin filtro ya disponible para explicar abstenciones. Universo: las 258
identidades de `signal_anatomy_v2`, con cobertura propia para cada reloj.

- Dubai decide desde el aviso inicial recibido; R05 espera el primer rango.
- Dubai conserva 0.01/0.04/0.04 lotes, escalon adverso 4 XAU y ventana 15 min.
- Stop de cesta 25 EUR; arma beneficio en 10 EUR y permite retroceso 2 EUR.
- Tras 40 min sale si el saldo de cesta no es positivo; no forzar cierre a
  60 min a una cesta positiva para poder compararla con R05.
- Cierres del proveedor segun semantica y asociacion causales, incluidos
  cierres parciales ya normalizados que el contrato interpreta como cierre
  de cesta. Una clasificacion determinista no equivale al interprete live.
- Conservar `pending_entry_policy=until_expiry` y la finalizacion completa.

Dos lecturas economicas separadas: tamanos originales y presupuesto nominal
comun de 25 EUR por senal. Para R05, convertir ese presupuesto a USD con el
Bid/Ask de conversion causal de la solicitud, redondear lotes hacia abajo al
paso permitido y recalcular la ejecucion. No multiplicar el beneficio antiguo
por un factor. El presupuesto no garantiza perdida maxima bajo saltos o demora.
Costes, spread, divisa, retrasos y ventanas deben ser comparables y explicitos.
Sin capital supuesto ni rentabilidad de cartera. Conservar todas las ausencias.

Informar conjunto propio de cada politica, interseccion de cobertura y mismas
12 identidades seleccionadas retrospectivamente por R05 como diagnostico
secundario, nunca como filtro retrospectivo de las entradas de Dubai.

## Ramas

| Rama | Estado | Siguiente comprobacion |
| --- | --- | --- |
| Aviso, primer precio, rango y revisiones | Anatomia terminada | Asociacion y versiones en el control actual |
| Mercado al recibir niveles, toque de rango, medio, DCA 60/25/15 | 24 reglas calculadas | No repetir sin una hipotesis nueva |
| TP1, salida dividida, 60/240 min | Calculadas | Conservar sensibilidad a cobertura |
| Calidad cotizada TP1/SL | Cuatro umbrales calculados | R05 fijo, aun concentrado |
| Sin BE, aviso directo, medio TP1 | Bloque terminado | No anadir BE por estos resultados |
| Dubai balanced actual | Contrato identificado | Soporte de cesta con ciclo de cierre a mercado |
| Igual riesgo entre politicas | Pendiente | Presupuesto causal 25 EUR, lotes recalculados |
| Cierre parcial/total, modalidad, rechazo y reintento | Mecanismos parciales existentes | Paridad del control completo, no solo del fill |
| Cesta plana con entradas pendientes, ultima entrada y caducidad | Contrato identificado | Regresiones y trazas de los tres motores |
| Positiva tras 40/60 min, falta de datos, solapamiento | Pendiente para este contraste | Sin cierre artificial ni capital infinito |
| Datos nuevos y meses/regimenes | Pendiente | Separar reutilizacion de validacion nueva |

Las ramas se cierran por evidencia; no se promete cubrir infinitas combinaciones
ni encontrar beneficio por fuerza. No se reactiva el catalogo de 4217 reglas.

## Paso De Ejecucion

El perfil usado por el estudio de rangos es deliberadamente de SL/TP absolutos
y rechaza la politica de cesta y gestion del proveedor. Se conserva ese rechazo.
Para el contraste economico hace falta una extension explicita, independiente
en los tres motores, del stop monetario con solicitud, procesamiento y acuse
de cierre. No quitar validaciones, ignorar el stop ni sustituirlo por un SL por
posicion. Primero preservar las fuentes anteriores y probar la extension con
prioridad stop/proveedor/beneficio/tiempo, entradas pendientes y demoras.

Despues, congelar el archivo y ejecutar el contraste: maximo 300 senales,
20000 evaluaciones, 150 millones de cotizaciones fuente y cuatro horas.
Ventana inicial de hasta cuatro horas intradia; cestas sin cierre natural
siguen pendientes y visibles, sin atribuirles cero ni resultados realizados.
No hay una comparacion economica completada mientras esta capacidad falte.
