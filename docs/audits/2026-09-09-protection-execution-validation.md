# Protecciones Pendientes: Implementacion Y Contraste MT5

## Resultado

Se integro un perfil explicito de ejecucion de SL/TP en el motor escalar, el
motor Numba y la referencia independiente. **No esta autorizada ni preparada
la busqueda masiva.** El incremento tiene verificacion determinista y un
contraste retrospectivo concreto, no una certificacion integral de MT5.

- 4.041 pruebas aprobadas, cero fallos, errores u omisiones; 633 warnings.
- 382 fuentes verificadas sin cambios durante la bateria final.
- 132 evaluaciones fijas: 11 senales Gold x 2 perfiles x 3 motores x 2 universos.
- 3 evaluaciones adicionales de un unico baseline idealizado, con presupuesto
  propio congelado, para aislar la discrepancia conocida de `canal2_2640`.
- Cero discrepancias entre motores en esas 135 evaluaciones. Cero candidatas
  nuevas y ninguna publicacion, reinicio u orden real.

## Que Cambio

El perfil opt-in separa intencion, solicitud pendiente, nivel instalado y acuse.
Un rechazo conserva ambos niveles anteriores; una aceptacion puede proteger
antes de que llegue su respuesta al cliente. La validacion usa la cotizacion
de procesamiento. El SL/TP ya instalado tiene prioridad sobre cambios pendientes.
El reintento espera el acuse y utiliza la intencion causal mas reciente.

Se conservaron los modos anteriores. El nuevo perfil bloquea, sin fabricar
fills o cierres, las capacidades no modeladas y los limites de evidencia o traza.
La referencia no llama al ciclo escalar; el motor rapido ejecuta su propio
estado compilado y reutiliza los contratos de dinero existentes.

La revision y los controles encontraron y corrigieron tres defectos adicionales:
una apertura bloqueada ocultaba un SL previo alcanzado en el mismo tick;
una escalera hipotetica combinada con protecciones de entradas reales provocaba
una excepcion; y el oracle conservaba una etiqueta de cierre con otra pierna
todavia abierta. Se anadieron regresiones y pruebas sinteticas reproducibles.

## Controles Congelados

Los dos perfiles usan procesamiento/acuse/reintento de `0/0/0 ms` y
`250/250/1000 ms`. Son hipotesis no ajustadas al resultado. Procesar requiere
una cotizacion posterior al indice solicitante; cero no significa instalacion
instantanea dentro del mismo tick. No hay tolerancias aprobadas para MT5.

El universo independiente consume mensajes y cotizaciones, manteniendo sus
propias entradas y estado. El condicionado recibe solo aperturas reales y
SL/TP de sus solicitudes iniciales, ligados a los deals nativos. No recibe
historial de protecciones modificadas, snapshots de gestion ni salidas reales.
Los outcomes se leen despues para comparar. Los 1.138 intentos retenidos
se contrastaron fila a fila con el registro causal original y su hash completo.

Los resultados siguientes se repiten en ambos perfiles:

| Control | Aperturas simuladas / reales | Precios de salida iguales | Tiempos de salida iguales |
| --- | --- | --- | --- |
| Entradas propias | 46 / 45 | 1 / 45 emparejadas; una entrada extra | 0 / 45 |
| Aperturas MT5 fijadas | 45 / 45 | 44 / 45 | 0 / 45 |

Cada universo conserva sus 22 comparaciones: 20 diferentes y 2 bloqueadas
por un cierre observado que no fue SL/TP nativo. Las siete senales Dubai
permanecen identificadas como fuera de este perfil, no se eliminan del inventario.
Las coincidencias condicionadas de precio no validan fills alternativos: sus
targets ya estan anclados a entradas reales suministradas.

## Incidente 2640

Para la pierna `1961398996`, con TP 4394,20 y apertura real fijada:

| Ejecucion | Salida UTC del 08/09 |
| --- | --- |
| MT5 observado | 16:29:59.817 |
| Baseline idealizado | 16:25:46.434 |
| Perfil de proteccion 250/250/1000 | 16:29:59.811 |

El adelanto pasa de 253.383 ms a 6 ms en este control. La siguiente observacion
tras el primer rechazo conserva SL 4423,28 y TP ausente, como exige el modelo.
Esto contrasta el defecto de tratar un TP solicitado como instalado; no demuestra
igualdad de toda la cola ni fija una tolerancia general de seis milisegundos.

El conjunto completo verificado contiene 248 respuestas 10016 en esta senal.
El perfil 250/250/1000 genera 139 rechazos, frente a 248 observados: la secuencia
del cliente y del servidor aun no esta reproducida. No mezclar este denominador
con subconjuntos de incidentes revisados anteriormente.

La primera pierna `1961396288` sigue discrepando: MT5 la cerro a mercado en
4393,18 a las 16:52:29.929; el perfil la conserva hasta un TP hipotetico en
4390,71 a las 16:56:18.671. Esta diferencia no se da por resuelta.

## Limites Y Siguiente Trabajo

Quedan por cerrar la generacion de fills y la bifurcacion 46/45; los acuses de
apertura y la cola real; cierres a mercado y la gestion de la primera pierna
2640; el perfil monetario de Dubai; cartera/margen y capacidades no admitidas.
Siguen faltando 19 cotizaciones live en la cinta archivada y no se conocen
los precios exactos de procesamiento del servidor. La validacion posterior
debera usar datos no reutilizados para reparar, con supuestos congelados.

No se habilito promocion para este perfil. Los puntos 1-3 del plan general
siguen incompletos y se mantiene la revision conjunta antes del punto 4.

## Evidencia Retenida

Raiz: `runtime_data/simulation_foundation_20260909/`.

- `verification_v4/`: bateria final y hashes de fuentes/entorno.
- `protection_independent_v2/` y `protection_actual_entries_v2/`: protocolos,
  inputs, resultados completos, trazas y comparaciones.
- `protection_2640_idealized_baseline_v1/`: tres evaluaciones adicionales.
- `protection_checkpoint_v1.json`: resumen verificable y hashes de artefactos;
  SHA-256 `4d58177d3b1262e1d2bc0b53238706de9897dd89829fef63c35190fd93e075f9`.
- Implementacion: `413a0be7db3b8634b32207862cfddaa9816ca3e31dab3300703ca026a4e0f7c9`.

Los estudios de proteccion v1 quedaron preparados sin simulaciones: el primero
fallo al releer una tupla como lista JSON. Se corrigio con regresion y se crearon
los v2, preservando los anteriores. `independent_v3` y las verificaciones
historicas tambien se conservan. Sin commit, push ni comprobacion nueva de la VM.
