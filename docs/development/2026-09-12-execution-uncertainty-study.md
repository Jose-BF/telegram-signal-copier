# Tiempos Observados Y Margen De Error Del Simulador

## Alcance Antes Del Analisis

Peticion: intentar explicar las diferencias de tiempos/precios y valorar,
como ultima alternativa, si una simulacion con error podria servir como
referencia conservadora. No cambiar el bot, buscar candidatas, ajustar una
cesta ni declarar cerrada una incidencia por una mediana favorable.

Se reutilizan los registros ya conservados del 09/09, 10/09 y primeras dos
senales del 11/09. El contraste de riesgo mantiene las mismas 19 senales,
40 posiciones observadas y cuatro escenarios fijados en el bloque anterior.
Todo es retrospectivo; no hay OOS nuevo ni dias independientes suficientes
para certificar colas extremas. No se solicitan operaciones nuevas.

## Preguntas Y Reglas Fijas

1. Medir antes, durante y despues de las llamadas usando los relojes
   monotonicos existentes. Separar por operacion, dia y version cuando se
   disponga de ella. Conservar rechazos, llamadas no enviadas y faltantes.
2. Relacionar intentos, decisiones, acciones y deals por sus identificadores;
   no unir solo por proximidad de hora. Calcular solapamiento como union de
   intervalos dentro de una misma sesion, nunca sumar dos veces las esperas.
   Solapamiento y silencio son observaciones, no prueba de una causa exclusiva.
3. Conciliar los 40 fills de entrada contra sus hechos nativos. Descomponer
   desviacion de precio en movimiento desde la cotizacion de solicitud y
   residuo respecto a la ultima cotizacion anterior al fill. El primer tick
   posterior solo puede ser diagnostico, nunca precio causal del simulador.
4. Para riesgo, definir L=max(0,-minimo_de_cesta) y error peligroso
   E=L_observado-L_simulado. E positivo significa que la simulacion subestima
   el riesgo. Comparar el signo exacto al centimo, no solo el error absoluto.
5. Separar cinco senales sin exposicion y las cestas con FX incompleto. Todas
   quedan en las tablas. Una pareja parcial no prueba el signo de su error.
6. Mantener el modelo anterior como comparacion primaria y mostrar los otros
   tres como sensibilidad, sin seleccionar el mejor. El umbral de 200 EUR es
   el ejemplo del usuario, no una asignacion de capital ni un limite autorizado.
7. Medianas y percentiles muestrales son descriptivos. La posible correccion
   futura de riesgo solo podria anadir un margen unilateral validado; no
   restar una mediana al drawdown para mejorar una estrategia. Folds y
   remuestreo deben mantener juntas las cestas de un mismo dia.

## Salida Esperada

- Tabla trazable de llamadas y componentes, distribuciones por mecanismo y
  evidencia de las esperas mayores, con limites de identificacion.
- Tabla de precios y cobertura de las 40 entradas, sin introducir fills reales
  en el motor ni ajustar el precio de cotizacion a posteriori.
- Comparacion de riesgo conservador/optimista con denominadores visibles.
- Logica de uso provisional: escenarios de ejecucion conjuntos, error adverso
  de cola, dominio de aplicacion, validacion fuera del ajuste y reglas de parada.
  No convertirla en permiso de busqueda ni en garantia de perdida maxima.

Presupuesto: una extraccion, hasta 2 millones de filas retenidas, 100000 intentos,
20 minutos de analisis local. Sin nueva rejilla de estrategias o latencias.
Implementacion del motor permanece congelada; las pruebas del analisis son
locales y focales, conservando la evidencia anterior de 5122 pruebas del motor.

## Progreso

- [x] Alcance, signo del error y limites fijados.
- [ ] Tiempos y precios extraidos y comprobados.
- [ ] Hipotesis conservadora contrastada sin excluir discrepancias.
- [ ] Logica final y siguiente paso documentados.
