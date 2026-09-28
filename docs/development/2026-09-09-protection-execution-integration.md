# Integracion De Protecciones Con Ejecucion

## Objetivo Y Limites

Continuacion autorizada por el usuario el 09/09: comprobar el simulador contra
datos reales MT5 antes de cualquier busqueda masiva. No se publica ni se toca el
bot live. Los controles actuales de 3.905 pruebas y `independent_v3` se conservan
con su identidad; cualquier cambio del motor requiere un experimento nuevo.

El siguiente incremento debe integrar la separacion entre **nivel deseado**,
**solicitud pendiente**, **nivel instalado en broker** y **confirmacion recibida**
en los motores existentes. El kernel SL/TP aislado aporta regresiones, pero no
permite declarar terminada esa integracion.

## Contrato De Ejecucion

- La ejecucion de protecciones sera un perfil opt-in e identificado. El modelo
  actual de tick idealizado queda disponible para comparar la diferencia.
- El perfil declara punto/digitos, distancia minima, restriccion de congelacion,
  retraso de procesamiento, retraso de confirmacion y espera para reintentar.
  Ninguno se interpreta como dato broker verificado sin su evidencia de origen.
- Un nivel deseado no provoca un cierre pasivo mientras no este instalado.
  Un rechazo conserva ambos niveles anteriores, sin aplicar parcialmente la
  peticion. La instalacion puede preceder al acuse que recibe el cliente.
- La proteccion ya instalada se evalua antes de procesar cambios sobre la misma
  cotizacion. La peticion se valida al procesarse, no sobre el precio antiguo
  que la motivo. Se conservan solicitudes y respuestas sin modificar el pasado.
- Una solicitud nueva espera al acuse de la anterior; el reintento usa el estado
  y la intencion causal mas reciente. Los supuestos de cola/coalescencia quedan
  explicitados; no se deducen de que una peticion finalmente haya tenido exito.
- Las protecciones iniciales de una entrada deben tener origen declarado. Un
  control condicionado puede usar las del request de apertura MT5 reconciliado;
  una entrada hipotetica debe obtenerlas de su propia politica y cotizacion.
  No trasladar niveles reales de otras posiciones a entradas simuladas.
- Las trazas deben identificar posicion, instante, solicitud, niveles y resultado.
  Los presupuestos limitan la traza sin perder silenciosamente evidencia; una
  interrupcion debe conservar el prefijo y marcarlo como incompleto.

## Secuencia De Trabajo

1. Escribir regresiones de TP cruzado antes de instalarse, SL antiguo durante un
   cambio pendiente, rechazo atomico, reintento, acuse tardio y cierre mientras
   un cambio sigue pendiente. Incluir BUY/SELL, igualdad temporal y gaps.
2. Integrar el contrato en el motor escalar. Mantener propia la implementacion
   de referencia y portar la misma semantica al motor rapido; no simular tres
   validadores cuando en realidad todos llaman al mismo kernel.
3. Repetir un control con aperturas reales fijadas para aislar protecciones,
   incluyendo el incidente 10016 de `canal2_2640`. Comparar con requests,
   resultados, niveles y deals reconciliados de MT5. Es un control condicionado,
   no una demostracion de que se predicen aperturas alternativas.
4. Repetir el control independiente desde mensajes/precios con perfil declarado.
   Mantener las divergencias de apertura y datos; una mejora de TP/SL no las
   cierra automaticamente. Separar tiempo de fill y disponibilidad del acuse al
   estudiar las solicitudes posteriores.
5. Verificar coherencia del motor/oracle, dinero y casos adversos; ejecutar la
   bateria completa sobre fuentes congeladas. Emitir un informe de diferencias
   que no convierta muestras retrospectivas en validacion nueva.

## Criterio De Parada

Cero candidatas nuevas. Comparaciones finitas de los controles existentes y de
regresiones dirigidas. No calibrar un retraso individual para borrar un fallo.
Si el contraste necesita una cinta nueva o instrumentacion publicada, consultar
al usuario antes de activar esa captura. La busqueda masiva sigue detenida y
requiere la revision conjunta del alcance, evidencia y presupuesto.

## Incremento Implementado Y Pendiente

- Integracion escalar, referencia independiente y Numba completada para el
  perfil acotado. Bateria final: 4.041 pruebas aprobadas sobre fuentes congeladas.
- Contraste MT5 ejecutado: 132 evaluaciones de dos universos y tres adicionales
  de un baseline; cero discrepancias entre motores, pero sigue sin haber paridad
  integral con MT5. 44/45 precios de salida coinciden con aperturas reales fijadas;
  el control de entradas propias conserva la bifurcacion 46/45.
- El episodio B1 de 2640 pasa de 253.383 ms de adelanto a 6 ms en el control,
  sin reproducir todos los reintentos ni el cierre a mercado de la primera pierna.
- Informe y evidencia: `../audits/2026-09-09-protection-execution-validation.md`.
  La preparacion general sigue abierta; no se inicia la busqueda masiva.

### Alcance Del Perfil

El primer perfil admite objetivos por tramo o ninguno, SL fijo por movimiento o
ninguno, y trailing. El nivel de congelacion debe ser cero: no se supone conocida
la semantica de un nivel distinto. Las salidas virtuales por proveedor, tiempo,
hard stop o profit lock conservan su condicion de activacion, pero se bloquean
al activarse mientras no se modele su solicitud y fill a mercado. La politica
Gold 555 conserva sus parametros; no se desactiva su guardia para obtener casos
aparentemente completos. El SL monetario de Dubai todavia no esta admitido.

Las solicitudes se procesan sobre la primera cotizacion disponible posterior al
indice solicitante y no anterior al plazo declarado. Dos cotizaciones con igual
timestamp conservan su orden. Si el timestamp de una solicitud de apertura
hipotetica identifica varias cotizaciones, el perfil bloquea esa apertura: el
contrato de entradas actual no conserva ese indice y no se adivina el precio.

El TP mantiene el convenio previo de fill en el nivel limite; el SL utiliza el
precio disponible con los costes declarados. Son hipotesis explicitas, no una
garantia de precio MT5. La latencia del acuse de apertura, la cola real del cliente,
el precio exacto que vio el servidor y ticks no retenidos siguen siendo limites
del contraste. Un fin de datos o un presupuesto agotado conserva el prefijo y
deja el P/L total desconocido; este perfil no fabrica un cierre `data_end`.
