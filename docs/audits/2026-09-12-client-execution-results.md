# Cliente Simulado: Correccion Mecanica Y Limite Historico

## Conclusion

Se implemento localmente el primer bloque del modelo de cliente, pero **no
resuelve por si solo la divergencia con la ejecucion real**. La cola y las
decisiones pendientes ya se pueden representar y comprobar. No basta con
anadirlas conservando las demoras fijas anteriores de 250 ms.

La cesta 2774 sigue teniendo un minimo simulado de -237.88 EUR frente a
-147.50 EUR con las operaciones observadas. Son minimos por cesta,
realizado mas flotante, no drawdown maximo de cuenta. Ambos recorridos
terminan en +19.81 EUR: coincidir en el neto no prueba igual riesgo ni
igual secuencia de operaciones.

No se ha ajustado ningun parametro al dia, elegido una variante por su
beneficio, publicado codigo ni modificado el bot. Fases 2 y 3 abiertas;
busqueda masiva no habilitada.

## Cambio Local

Nuevo perfil opcional `single_basket_serial_v1` en el motor de referencia:

- Entradas de escalera decididas en linea, despues de conocer el fill inicial.
- Cola FIFO compartida por aperturas, modificaciones y cierres; la llamada
  ocupa el recurso hasta que su respuesta esta disponible.
- Modo `snapshot_batch`: conserva la observacion que activo las patas de una
  tanda y su SL provisional; no pierde patas por el rebote posterior.
- Modo `fresh_quote`: exige volver a observar el cruce para cada pata nueva.
- SL y TP instalados siguen actuando mientras espera el cliente. Una respuesta
  pendiente se drena incluso si la posicion ya se cerro en el broker.
- Reloj del precio separado del reloj del fill. Por defecto se usa la
  cotizacion de procesamiento; una demora de precio explicita permite otra
  cotizacion causal posterior al envio y nunca posterior al fill.
- Traza de decision, cola, envio y liberacion del recurso; ordinal del tick de
  precio. Datos incompletos y presupuesto de traza agotado no inventan cierres.
- Perfil tipado en JSON y vinculado a la identidad de implementacion. El
  comportamiento previo permanece como predeterminado.

La regresion inicial reprodujo tres fallos con el perfil aun sin implementar:
entrada observada durante una modificacion, perdida de la tercera pata de una
tanda y precio inseparable del instante de fill. Las 29 pruebas nuevas pasan,
incluidas BUY/SELL, timestamps repetidos, caducidad de tandas comprometidas,
rechazos, cierres pasivos, respuestas tardias y serializacion de cierres.

## Comparacion Fija

[Protocolo previo](../development/2026-09-12-client-execution-repair.md):
19 senales, incluidas las cinco sin entrada; diez del 10/09, siete del 09/09 y
solo las primeras dos del 11/09. Son datos retrospectivos ya examinados, no
una muestra OOS intacta. Los fills nativos se usan para comparar, no como
entrada del motor.

Cuatro escenarios fijados antes de ejecutar, sin seleccion posterior. Se
mantienen los 250 ms de procesamiento y respuesta anteriores. No se interpreta
esta demora como una medicion representativa del puente real.

| Escenario | Error medio entrada 10/09 (s) | 09/09 (s) | Primeras dos 11/09 (s) |
| --- | ---: | ---: | ---: |
| Anterior | 11.252727 | 2.527875 | 0.556500 |
| Cola y tanda comprometida | 11.353500 | 2.527875 | 0.556500 |
| Cola y observacion nueva por pata | 11.255409 | 2.527875 | 0.556500 |
| Cola, tanda y precio al enviar | 10.790955 | 2.644625 | 0.556500 |

Las 76 evaluaciones conservan cantidades y volumen por senal. No hay posiciones
sin emparejar en esta prueba; el error temporal anterior usa los 22, 16 y 2
emparejamientos respectivos, no las senales sin entrada.

Precio medio absoluto de entrada, en unidades de precio XAUUSD, no EUR:

| Escenario | 10/09 | 09/09 | Primeras dos 11/09 |
| --- | ---: | ---: | ---: |
| Anterior | 0.937727 | 0.266875 | 0.005000 |
| Cola y tanda comprometida | 0.986364 | 0.266875 | 0.005000 |
| Cola y observacion nueva por pata | 0.959091 | 0.266875 | 0.005000 |
| Cola, tanda y precio al enviar | 0.910909 | 0.256875 | 0.060000 |

Todos mantienen el mismo neto por senal y por dia: 78.77, 48.96 y 3.44 EUR.
El precio al enviar mejora parcialmente un dia y empeora otras medidas: no
se adopta como correccion. El error de hora de salida de las primeras dos del
11/09 pasa de 0.003 a 0.616 s con esa variante.

## Cesta 2774

| Recorrido | Minimo EUR |
| --- | ---: |
| Posiciones observadas, marcado independiente | -147.50 |
| Modelo anterior | -237.88 |
| Cola y tanda comprometida | -237.88 |
| Cola y observacion nueva por pata | -237.88 |
| Cola, tanda y precio al enviar | -232.86 |

Con 250/250 ms, la cola de esta cesta tiene llamadas de hasta 677 ms y espera
decision-envio de hasta 602 ms. No reconstruye la espera observada de 56.857 s
en el refuerzo B3. La variante de precio al enviar tampoco reproduce su
entrada: aun difiere 61.874 s y 5.01 unidades de precio.

El [control anterior de la salida real](2026-09-12-timing-transfer-results.md)
ya verifico que el TP estaba instalado antes del cruce y que el cierre sucedio
46 ms despues. Copiar la duracion real de esa espera tampoco reproducia su
precio ejecutado. El resultado nuevo no altera esa evidencia ni atribuye toda
la diferencia al broker.

## Limites De Admision

- Es un recurso serial de una cesta aislada, no un planificador de todas las
  tareas del proceso, varias cestas ni una reproduccion certificada del GIL.
- Conserva el contrato anterior de modificacion atomica SL/TP. Produccion
  encola SL y TP por separado; ese detalle y la duracion por tipo de llamada
  siguen pendientes antes de afirmar equivalencia operativa.
- Los tiempos y precios son hipotesis declaradas, no fills empiricamente
  calibrados ni una distribucion de incertidumbre validada.
- El motor rapido, el oraculo independiente y la reconstruccion de cartera
  rechazan expresamente este perfil. No se oculta la falta de implementacion
  independiente reutilizando el motor de referencia como oraculo.
- El error monetario/cobertura de fase 2 no se ha reparado en este bloque.
  En 2801 hay once ticks FX invalidos tanto en nativo como en los cuatro
  escenarios. En 2668 hay tres nativos y uno en la variante precio-al-enviar.
  Se conservan y se etiquetan minimos parciales, sin ampliar la tolerancia FX.
- Cero bloqueos del motor en los 76 controles no significa riesgo certificado.
  El marcado independiente conserva esos huecos aunque el motor no los eleve.

El siguiente bloque necesario es contrastar la duracion y contencion de las
llamadas por mecanismo, incluyendo interacciones entre tareas y SL/TP separados.
Solo despues tiene sentido habilitar el perfil en motores independientes y
cartera. No se propone otro retraso constante elegido para acercar la cesta
2774. Los defectos monetarios y la revision conjunta previa a buscar estrategias
siguen vigentes.

## Verificacion Y Artefactos

- 19 escenarios base reproducen todos los resultados archivados anteriores.
- 76 evaluaciones repetidas identicamente, con invariantes causales de la cola.
- 95 recorridos marcados por un calculo monetario independiente: 19 nativos y
  76 simulados. Esto comprueba el marcado, no es un oraculo del cliente.
- Suite completa final: 5122 aprobadas, cero fallos, errores u omisiones en
  281.53 s. Conserva 643 avisos. La primera pasada tuvo 5115 aprobadas y un
  fallo de expectativa de metadatos; ambas pasadas quedan guardadas.
- Fuentes de produccion, configuracion live y evidencias originales sin editar.
  Cambios solo locales, sin commit, push, despliegue ni consulta nueva a la VM.

Directorio inmutable del experimento:
`runtime_data/client_execution_20260912/`. Incluye `protocol.json`,
`plan_frozen.md`, `results.jsonl.gz`, `summary.json`, `verification.json`,
`risk_comparison.json`, las copias anteriores y los resultados de pruebas.
Los fuentes y entradas se identifican por sus hashes; los resultados previos
permanecen intactos.
