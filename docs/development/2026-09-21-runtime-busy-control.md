# Monitor Ocupado Y Valoracion Explicita

Estado: 484 pruebas focalizadas y 7346 globales aprobadas, sin fallos,
errores ni omisiones. Fuentes e identidad sin cambios durante la ejecucion.
Paquete congelado `A:/cb-xaihqvzq/e`; no admision de estrategia completa.

## Avance Funcional

El monitor real Gold555 y la cola operan sobre el mismo libro mientras las
cotizaciones avanzan durante preparacion o espera de una respuesta. Se retiene
la respuesta en una frontera identificada, sin sustituir decisiones ni fills.
Los controles verifican riesgo monetario y exposicion por cotizacion, identidad
de tickets, peticiones efectivas y finalizacion, no solo resultado final.

Catorce controles BUY/SELL cubren:

- DCA ejecutada y stop nativo antes de entregar su acuse.
- Precio cambiado durante preparacion: fill y TP exacto se anclan al fill.
- Cesta plana tras stop que conserva entradas aun vigentes. Se abren solo las
  patas pendientes con IDs nuevos; no reaparecen las cerradas.
- Trailing preparado o aplicado cuando el stop cierra la posicion. Un envio
  preparado puede ser rechazado 10036; no se presenta como instalado.
- Cierre temporal preparado o aplicado mientras cambia el mercado. Si el stop
  se adelanta, no hay segundo cierre efectivo; la causa final es SL.

Dos controles adicionales ejercitan profit lock con dinero realizado y flotante.
Usan contract_size=200 declarado para alcanzar el umbral congelado, sin alterar
la politica. Es una hipotesis de contrato, no la especificacion de produccion.
Comprueban tres TP, cierre activo de dos patas y la negativa del finalizador a
terminar mientras queda una posicion abierta. Ese bloqueo observado no se oculta.

## Defecto Corregido

`_classify_closures` podia atribuir a una intencion de cierre del bot una salida
que realmente habia ejecutado SL/TP/BE. Ahora esas causas nativas prevalecen
sobre las marcas de guard, rescate BE, close-first y cierre risk-free. No se
modifica la estrategia ni el fill. Doce regresiones directas y dos controles
integrados de carrera con SL demuestran la diferencia.

Los rechazos informativos esperados se verifican por operacion, ticket, retcode
y numero de intentos. Las otras anomalias siguen haciendo fallar los controles.

## Capacidad Para Dubai

El perfil opt-in `linear_contract_fx_unrounded_v1` habilita `calc_profit` en el
contrato privado del broker simulado. Reutiliza `linear_profit_value`, devuelve
un escalar sin redondear a centimos y usa FX causal de cada llamada. No muta el
libro. Parametros invalidos se rechazan; valoracion desconocida devuelve None,
incluido el caso de beneficio bruto cero sin FX valido.

El mailbox y ReadWorker reales preservan FOUND/UNKNOWN y registran perfil,
cotizacion y `native_valuation_verified=False`. Sin perfil no hay callback.
Cincuenta y cinco pruebas cubren ambas direcciones, orientaciones FX, signos,
cero, precision subcentimo, datos invalidos, reloj y protocolo.

## Evidencia

- `A:/cp-b01/result.xml`: cuatro fallos de expectativa/fixture. El fin automatico
  antes de los 30 minutos no correspondia al contrato de entradas vigentes;
  esperar vaciado de cola sin su siguiente tick agotaba el presupuesto del
  mailbox. Se conserva el fallo y se prueba ahora la continuidad del plan,
  ademas de permitir avanzar el tick con cola pendiente. No se eleva el limite.
- `A:/cp-b04/red.xml`: 12 fallos de atribucion y dos carreras integradas con SL;
  otros dos eran la expectativa incorrecta del nombre del cierre temporal.
- `A:/cp-b06/green.xml`: 86 verdes, incluidas 14 carreras y 12 atribuciones.
- `A:/cp-b07` y `cp-b08`: expectativas del test de profit lock; se distingue
  negativa esperada a finalizar con posicion abierta de fallo no previsto.
  `A:/cp-b09/result.xml`: dos controles profit lock verdes.
- Valoracion: `A:/cp-f42dc1/red.xml`, 51 rojas; regresion mailbox en
  `A:/cp-8215eb/mailbox-red.xml`; `A:/cp-16b626/focused.xml`, 171 verdes.
- Congelacion conjunta: `A:/cb-xaihqvzq/e/focused.xml`, 484 verdes,
  cero fallos/errores/omisiones, Python 3.11.9. 52 trazas en `controls/`;
  comprobados sus hashes contra el manifiesto sin discrepancias.
- `A:/cb-xaihqvzq/e/full.xml`: 7346 verdes, proceso 443.125 s;
  SHA256 `f71cdd4763bebc73874cb13972dc47f387863abb46f0fb0d88566efbaa8f09dc`.
  `verification.json` confirma fuentes/implementacion sin cambios y mantiene
  en falso admision completa, paridad nativa y objetivo terminado.
- Runner: `reports/e5-runtime-busy-control-20260921/run_verification.py`.
  Fuentes, identidad de implementacion, entorno, salidas y XML se conservan.
- Revision independiente estatica sin defectos materiales nuevos confirmados.

## Limites Y Siguiente Bloque

Las carreras integradas usan SL; TP/BE frente a intencion perdedora tienen
regresion directa de atribucion, no todos los recorridos completos. El grid
compara dinero total y volumen post-evento; no todas las fases diagnosticas.
Reloj y cuenta son fixtures, transporte en proceso, costes cero declarados.
No valida latencia VM, aislamiento de proceso, ejecucion nativa, recuperacion
total ni contencion entre varias cestas. La valoracion sigue siendo hipotesis.

Siguiente recorrido Dubai: `_open_canal1_from_text`, mensaje/direccion/niveles
causales, admision explicita, entrada durable con presupuesto de perdida,
ancla al fill y expiracion desde recepcion; una DCA, stop de cesta confirmado,
profit lock, cierres, historial y finalizador. Mantener los calculos reales
del worker y de cesta, no inyectar un stop precalculado. Controlar los relojes
locales sin cambiar deadlines del transporte. BUY/SELL y casos adversos.

Solo despues se completa el contraste historico por capacidad/dataset y la
revision conjunta de admision. No hay busqueda masiva ni rentabilidad certificada.
Sin commit, push, despliegue, reinicio ni nueva comprobacion de la VM.
