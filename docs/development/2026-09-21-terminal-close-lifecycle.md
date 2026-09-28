# Cierre Terminal Durante Esperas

## Alcance

Continuacion del contrato de rejilla de riesgo. Implementacion local opt-in
`single_basket_terminal_v2` en `ClientProfile`; v1 sigue siendo el valor por
defecto y conserva sus controles congelados. No se modifica estrategia live,
VM, watcher, configuracion de riesgo ni reglas de dinero.

Una espera del cliente no debe borrar el momento en que recibe un cierre
del proveedor. La intencion debe abarcar entradas ya enviadas que terminen
ejecutandose despues. Esto corrige una diferencia de trayectoria, no ajusta
el beneficio final ni una latencia para que encaje con un dia concreto.

## Evidencia De La Correccion

Antes del cambio del motor, cinco regresiones fallaron por comportamiento:
cuatro controles BUY/SELL, aislados/compartidos, registraban la decision de
cierre en el segundo 5 aunque estaba disponible en el segundo 1; otra prueba
enviaba la tercera entrada de una tanda despues del cierre del proveedor.
El round-trip del contrato ya pasaba, por lo que esos cinco fallos no eran
fallos de importacion ni de configuracion del nuevo nombre.

La version nueva conserva una primera intencion terminal con razon, ordinal
y reloj. La registra antes de procesar confirmaciones o despachar entradas,
incluso si el precio de ese ordinal no es usable. Cancela entradas de la
cola que no se han enviado, incluidas ofertas al transporte compartido.
No revoca una entrada comprometida ni libera el recurso antes de su respuesta.

Al conocerse un fill posterior, conserva su contabilidad y proteccion y crea
el cierre con el reloj de decision original. El envio usa el momento y precio
actuales, cuando el recurso queda disponible. Las solicitudes de cierre ya
enviadas no se repiten mientras se espera su confirmacion. Un SL/TP nativo
puede cerrar antes; el cierre de cliente restante drena su rechazo sin una
segunda realizacion de dinero.

Persistente significa aqui conservada entre cotizaciones, concesiones y
respuestas de la misma simulacion. No se afirma almacenamiento durable de
esta intencion ni recuperacion del simulador tras reiniciar su proceso.

## Semantica Del Proveedor

Se usa la funcion canonica `is_strategy_close_action`, no una interpretacion
nueva comun a los canales. El contrato local de Dubai usa `exact`: convierte
tambien determinadas acciones cuyo nombre contiene CLOSE en cierre completo.
Gold555 usa `explicit_close_only`: solo CLOSE_ALL, EXIT y CERRAR. Son reglas
del codigo local, no una comprobacion de la version instalada en la VM.

Una primera restriccion de v2 a explicit_close_only fue retirada tras leer
`listener._execute_one_action` y ambos helpers live: habria excluido Dubai
innecesariamente. Un mensaje llamado CLOSE_PARTIAL que la politica convierte
en cierre completo no habilita la ejecucion de cierres parciales del broker.
Los gates anteriores de `partial_runner`, basket guard y niveles absolutos
con cliente permanecen intactos.

El primer ensayo general se interrumpio intencionadamente antes de editar
las fuentes para ese ajuste; se conserva como ensayo interrumpido, no como
suite aprobada ni como fallo del motor. La segunda general usa las fuentes
definitivas congeladas y un runtime temporal exclusivo.

## Verificacion

145 casos especificos: entradas iniciales y DCA tardias, cancelacion de
tandas, SL durante espera, modificacion ocupada, contencion entre cestas,
corte de datos, limites de traza, cotizacion invalida, timestamps repetidos,
mensajes no elegibles y sufijos futuros. Incluyen 72 combinaciones de relojes
contrastadas con la formula independiente de despacho serial
`max(cierre_disponible, fill + espera_de_confirmacion)` y ejecucion en la
siguiente cotizacion, para el perfil especifico del ensayo.

La matriz de acciones tiene expectativas explicitas y, por separado,
comparaciones con los helpers locales de los dos canales. Los mensajes
mixtos retienen el primer cierre elegible segun cada politica.

535 pruebas focalizadas aprobadas, incluyendo referencias v1 completas,
transporte compartido, rejilla de riesgo, contratos y busqueda por perfiles.
Revision independiente final del codigo sin hallazgos materiales en este
alcance; 145 casos ejecutados por el revisor mediante invocacion directa en
memoria. Esa revision no sustituye pytest ni el contraste con MT5.

Evidencia en `reports/e5-terminal-close-20260921/`: controles BUY/SELL con
entradas congeladas, identidad de fuentes y ensayo inicial interrumpido.
`final/` conserva la segunda invocacion normal `python -m pytest -q`, runtime
temporal, hashes de fuentes, salida, JUnit y comprobacion posterior de cambios.
General final terminada: **6.837 aprobadas**, 460,51 s, diez advertencias
(nueve de record_property/xunit2 y una previa de pandas). Salida cero, hashes
de fuentes e identidad de implementacion sin cambios. El JUnit incluye los
145 casos especificos. Ningun ensayo propio queda en ejecucion.

Esta general limpia sustituye la necesidad de combinar ensayos anteriores
para verificar las fuentes actuales. No convierte los controles sinteticos
ni sus demoras elegidas en evidencia de fills o tiempos reales del broker.

## Pendiente Para El Objetivo Completo

- La decision se observa en el primer ordinal de la cinta posterior a la
  disponibilidad del mensaje; no se afirma temporizacion sub-tick nativa.
- La gestion monetaria todavia no puede observar cada precio interno mientras
  MT5 esta ocupado. Tick, posiciones e historiales son lecturas separadas en
  el runtime; no fabricar una instantanea atomica ni alimentar decisiones
  desde la rejilla de riesgo diagnostica.
- Completar esa disponibilidad de lecturas y conectar las reglas monetarias
  reales Dubai y la gestion Gold555 sin retirar gates por conveniencia.
- Contrastar trayectorias de varias cohortes historicas completas, preservar
  huecos e incertidumbre y verificar preparacion operativa de la VM con la
  autorizacion correspondiente. No hay certificacion live ni despliegue.

El objetivo sigue abierto. No hay commit, push, reinicio ni ordenes reales.
