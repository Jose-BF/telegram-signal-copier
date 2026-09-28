# Base operativa y plan de investigacion por canal

Fecha: 2026-09-07. Evidencia remota: 15:45:25 UTC, 17:45:25 Madrid.
Estado: auditoria completada; base NO validada para seleccionar ni promover estrategias.
Version comprobada en la VM: `b4426e6cb5649dee10c3ca57eb95866203d0bd98`.
Rama local: `feature/gold-555-live-trial`, con cambios pendientes anteriores conservados.

## Respuesta directa

El bot recibe las senales del trader, pero ejecuta estrategias propias congeladas
que admiten algunas instrucciones posteriores del trader. Es una gestion hibrida,
no una copia literal de toda su gestion. Tampoco consulta beneficios simulados para
cambiar su estrategia en directo. La investigacion y las sombras no eligen ni
publican automaticamente la politica activa.

No hay una candidata nueva certificada como ganadora. Los resultados anteriores
son evidencia con distintos alcances, no una garantia de rentabilidad futura.

## 1. Hallazgos que bloquean avanzar

### P1. Una discontinuidad bloquea el avance global de las sombras

La VM acumula 5.880 intentos consecutivos de espera en el corte examinado. Busca
el tick de las 14:03:59.826 UTC, que NO aparece en la descarga historica congelada
del mismo dia. El ultimo aviso examinado es de las 15:44:28.263 UTC: aproximadamente
100 minutos sin superar ese punto. No son 5.880 operaciones ni huecos distintos.

`main.py:4021` usa el cursor activo mas antiguo de `strategy_shadow_runtime.py:135`.
Si falta continuidad, espera y reintenta, sin aislar en ese recorrido la cohorte
afectada. Esto frena tambien las sombras posteriores. `main.py:3498` rechaza
correctamente una historia que no contiene el cursor; el defecto es la falta de
recuperacion acotada y aislamiento, no ese rechazo.

La coincidencia no se arregla omitiendo los flags: falta incluso la marca temporal.
No esta probado por que el historico del broker no conserva ese tick observado.
Reparacion propuesta: diario durable de ticks causales, recuperacion por solapes
verificados, cuarentena explicita de la cohorte incompleta y generaciones nuevas
independientes. Nunca saltar el hueco y llamar completa a la evidencia anterior.

### P1. Dos reglas de finalizacion diferentes tienen la misma identidad de ejecucion

`strategy_runtime_contract.py:118` convierte el contrato a sombra sin transmitir
`pending_entry_policy`, `automatic_flat_policy` ni `require_zero_positions`.
La identidad de ejecucion se calcula a partir de esa representacion incompleta.

Contraejemplo reproducido sobre copias inmutables de Gold 555: mantener entradas
pendientes hasta caducidad y finalizar al quedar plano producen el mismo hash
`32ece4caad7772a113c8833b0cf65d48d426731e219fe3b2658e8fabeb35bcf0`.
El plan live SI conserva esas diferencias, utilizadas por `signal_lifecycle.py`.
Por tanto, igualdad del identificador no demuestra igualdad de comportamiento.

Reparacion propuesta: contrato completo, conversion sin perdida a cada motor,
pruebas negativas por cada campo semantico y version nueva de identidad. Las
versiones antiguas deben conservarse como tales, no reinterpretarse como nuevas.
Revisar tambien `strategy_shadow_engine.py:671`, cuya finalizacion es implicita.

### P2. El contexto que recibe el clasificador no representa fielmente al bot

`classifier.py:81` etiqueta el beneficio como USD, pero `state.py:409` agrega el
beneficio de las posiciones en la moneda de cuenta, EUR en la evidencia actual.
`classifier.py:245` entrega los TP/SL del proveedor como niveles actuales, aunque
ya existen campos separados para los niveles efectivos del broker. El prompt
tambien dice que el bot no agrega posiciones autonomamente, pese al DCA activo.

Esto puede afectar a la interpretacion de instrucciones ambiguas. No se atribuye
a este hallazgo una orden concreta sin una evaluacion causal adicional.
Reparacion propuesta: moneda correcta, niveles efectivos y del proveedor separados,
politica activa explicita y conjunto etiquetado de mensajes con su contexto real.
Guardar version del modelo, prompt y contexto para poder reproducir cada decision.
No convertir automaticamente cualquier frase sobre recoger beneficio en cierre.

## 2. Que reglas estan activas

Fuente: contrato publicado por la VM y codigo correspondiente; cuenta observada demo.
Los umbrales XAU son variacion del precio del oro, no euros ni pips del proveedor.

| Regla | Canal 1: Dubai | Canal 2: Gold Signals |
| --- | --- | --- |
| Politica | Dubai balanced v1 | Gold NOW 555 v1 |
| Origen de direccion | Senal del trader | BUY/SELL GOLD NOW del trader |
| Primera entrada | Mercado | Espera movimiento adverso 1,0 y reversion 1,5 XAU |
| Entradas propias | Hasta 3; 0,01/0,04/0,04 lotes; escalon adverso 4 XAU | Hasta 5; 0,04/0,03/0,03/0,03/0,03 lotes; escalon adverso 1,5 XAU |
| Ventana de entradas | 15 minutos | 30 minutos |
| Objetivos propios | Sin TP fijo; proteccion de beneficio de cesta | TP por tramo: 0,5/1/1,5/2/2,5 XAU |
| Proteccion propia | Stop de cesta 25 EUR; arma beneficio en 10 EUR y retroceso 2 EUR | Trailing 30 XAU por posicion; arma beneficio de cesta 30 EUR y retroceso 1 EUR |
| Salida temporal | A partir de 40 min si esta en perdida | A partir de 180 min si no esta en perdida |
| TP, SL y BE posteriores del trader | No gobiernan estas protecciones | No gobiernan estas protecciones |
| Cierre del trader admitido | Acciones normalizadas CLOSE, incluso parcial, cierran toda la cesta | Solo cierre total explicito; no parcial |
| Cesta plana antes de caducar | Puede conservar entradas elegibles pendientes | Puede conservar entradas elegibles pendientes |

Las instrucciones pasan primero por asociacion a senal, interpretacion y controles
de modalidad/aplicabilidad. La tabla de cierres describe acciones YA normalizadas,
no una simple busqueda de la palabra CLOSE en cualquier mensaje. El comportamiento
amplio de Dubai esta cubierto por tests y no se cambia aqui por considerarlo raro.

Los riesgos actuales no son comparables por contar lotes o mirar euros ganados:
Gold no tiene el stop monetario fijo de cesta de Dubai. La busqueda debe comparar
politicas con el mismo presupuesto de riesgo y comprobar la exposicion conjunta
de ambos canales sobre XAUUSD. Falta fijar capital y caida maxima admisible.

## 3. Que evidencia sirve y que no

| Comprobacion | Resultado y limite |
| --- | --- |
| Suite local existente | 2.945 tests pasan; 622 warnings; no prueba de ausencia de defectos no cubiertos |
| Integracion con MT5 y modelo reales | No ejecutada en esta auditoria |
| Sombra prospectiva actual | Bloqueada; no valida para clasificar candidatas |
| Control independiente de hoy | Util para diagnostico; no certificacion integral |
| Primeras entradas Gold de hoy | Cinco de cinco coinciden en tick y precio de disparo con el control independiente |
| Contabilidad observada de hoy | 20 entradas y 20 cierres reconciliados; +17,71 EUR demo hasta 17:02:33 Madrid |
| Simulacion monetaria completa de hoy | No disponible: interpretacion, conversion y ventana abierta impiden un total comparable |
| Historial desde enero | Disponibilidad parcial comprobada; causalidad y cobertura completa no certificadas |
| Nueva ganadora por canal | Ninguna seleccionada |

El total observado anterior no se actualiza con eventos posteriores al corte.
Gold 2580 aun tenia ventana de entradas pendiente, aunque estaba plano entonces.
Las diferencias de fill y stop nativo del broker no equivalen por si solas a un
error de regla. La conversion monetaria alternativa debe conservar sus bloqueos:
no se amplio el limite de antiguedad del cambio de divisa para hacer pasar casos.

### Correccion de mi control de Dubai

El adaptador local de hoy omitio `pending_entry_policy=until_expiry` para Dubai.
Por ello no debio considerarse una transcripcion completa del contrato activo.
He repetido las tres senales con la regla correcta y latencias de 0/250/1.000 ms:
nueve ejecuciones, nueve coincidencias con el oraculo independiente, y ningun
cambio de resultado salvo la identidad de estrategia. Las salidas ocurren despues
de la ventana de entradas en estos casos. Esto limita el impacto de ESTA omision,
no certifica contratos o cestas diferentes. Se preservaron todos los originales.

## 4. Enero: condiciones para usar JSON y ticks

El sondeo anterior obtuvo ticks de nueve ventanas de cinco minutos, una por mes
de enero a septiembre: demuestra disponibilidad en esas ventanas, no continuidad
de todo el periodo. MT5 documenta ticks con Bid/Ask y manejo temporal UTC; el
desfase concreto de este broker debe verificarse sobre cada bloque descargado.
[Documentacion MT5](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py).

Los exports locales ya contienen enero, pero muchas senales fueron editadas.
El texto final y su fecha de publicacion no recuperan automaticamente el texto
que existia al entrar. Telegram distingue `date` y `edit_date`; eso no constituye
un historial completo de revisiones. [Esquema Telegram](https://core.telegram.org/constructor/message).

El importador actual `tools/parse_export.py` pierde informacion relevante de
edicion y enlaces. Gold de enero pertenece ademas a otra generacion de canal;
no debe mezclarse con el canal actual sin comprobar continuidad del proveedor.
Los stickers de Dubai podrian conservar direccion, pero su identidad y gestion
asociada necesitan prueba. Un JSON nuevo no resuelve por si solo estas limitaciones.

Construir tres cohortes explicitas: originales causales; recuperacion parcial con
restricciones declaradas; no reconstruibles. Todas cuentan en cobertura. Solo la
primera permite las afirmaciones causales que exija el experimento. La segunda
puede servir para preguntas mas limitadas, nunca hacerse pasar por la primera.

## 5. Plan con condiciones de avance

Estado de todas las fases siguientes: PENDIENTE. No hay busqueda nueva en marcha.

1. **Reparar y demostrar la base.** Resolver los tres hallazgos y sus regresiones.
   Modulos: contratos, lifecycle, shadow runtime/engine, clasificador y contexto.
   Comparar contabilidad observada, decisiones y ejecucion hipotetica por separado.
   Probar cierres parciales/totales, cesta plana con pendientes, reinicio, orden
   rechazada, mensaje duplicado/editado, conversion ausente y tick no recuperable.
   Condicion: ningun incidente duro inexplicado en los controles afectados; una
   sombra nueva avanza sin que una cohorte antigua incompleta la paralice.
2. **Construir datos causales.** Conservar canal/generacion, revisiones y primera
   disponibilidad; archivado inmutable de mensajes, ticks XAU/FX y costes.
   Descargar el periodo amplio solo tras un piloto de integridad. Fijar cohortes
   y semanas de validacion antes de probar parametros. Nada ya utilizado para
   descubrir o corregir se vuelve a llamar validacion intacta.
3. **Buscar por hipotesis y riesgo comun.** Comparar tres familias: gestion del
   trader operacionalmente definida; gestion propia; gestion hibrida. Incluir
   controles actuales y no operar. Empezar por un piloto acotado de 100 contratos
   distintos por canal; presupuesto inicial propuesto, aun no ejecutado: hasta
   10.000 contratos unicos por canal en lotes con checkpoint. Ampliar solo por una
   hipotesis nueva y evidencia discriminante, no por no haber encontrado ganador.
   Usar motores existentes y oraculo; registrar todos los intentos y descartes.
4. **Desafiar las finalistas.** Evaluacion cronologica por dias/semanas completos,
   periodos reservados sin retocar, costes, retrasos y ejecucion adversa; intervalos
   de incertidumbre, drawdown de cartera, perdidas extremas y concentracion de
   beneficio. Exigir estabilidad en parametros vecinos y penalizar complejidad y
   seleccion entre muchos ensayos. Fijar previamente umbrales segun capital y
   riesgo acordados; si falta muestra, declarar evidencia insuficiente.
5. **Sombra sana antes de promover.** Congelar finalistas, INCLUIDA la potencial
   ganadora, y observar senales nuevas con la misma version. Probar continuidad,
   recuperacion y comparacion diaria trazable. Solo despues, con autorizacion de
   publicacion y exposicion comprobada, despliegue gradual con limites y vuelta
   atras. Las restantes siguen en sombra mientras su evidencia sea valida.

La seleccion repetida de parametros puede inflar el resultado aparente. La
correccion por multiples ensayos y la forma de los retornos complementan, no
sustituyen, la validacion temporal. [Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).

Definicion del objetivo: mayor rentabilidad neta validada dentro del riesgo
admisible, entre las alternativas evaluadas. No se puede probar un maximo absoluto
entre todas las estrategias posibles ni prometer que exista una rentable.
La complejidad se acepta si mejora evidencia fuera de muestra y compensa su coste
operativo; no se premia ni se prohibe por principio.

### Ideas adicionales que merece la pena contrastar

- Medir cuanto aporta el trader: controles con tiempos/direcciones aleatorizados
  dentro de sesiones comparables, sin contaminar los bloques reservados.
- Comparar operar menos: abstencion cuando mensaje, spread o datos son deficientes;
  incluir el coste de oportunidades omitidas y todas las senales en el denominador.
- Normalizar distancias por volatilidad conocida en ese instante y comprobar
  estabilidad por regimen; no seleccionar filtros con el resultado futuro.
- Evaluar ambos canales como cartera: dos ganadoras aisladas pueden multiplicar
  la misma exposicion al oro y dejar de ser aceptables juntas.
- Separar ventaja de senal, ventaja de gestion y dependencia de fills ideales.
  Si el beneficio desaparece con retrasos plausibles, no promover la candidata.

## 6. Modelos y ahorro de tokens

Esta es mi propuesta de reparto para el proyecto, no una configuracion ya activada.
Los papeles siguientes son juicio de ingenieria, apoyado en las capacidades y
costes documentados en [modelos](https://learn.chatgpt.com/docs/models) y
[consumo de Codex](https://learn.chatgpt.com/docs/pricing).

| Tarea | Modelo propuesto | Esfuerzo |
| --- | --- | --- |
| Diseno causal, riesgos, auditoria final | GPT-6 Astra | Alto, en hitos |
| Codigo critico de motores, contratos y recuperacion | GPT-5.6 Sol | Alto |
| Importadores, tests y trabajo delimitado | GPT-5.6 Terra | Medio |
| Inventarios y resumenes comprobables | GPT-5.6 Luna | Bajo |
| Miles de simulaciones y estadisticas | Motores Python/Numba, sin LLM por candidato | No consume tokens de modelo |

Luna esta orientado a volumen y coste reducido; eso no demuestra que clasifique
correctamente nuestros mensajes. [Ficha oficial Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna).
El bot usa actualmente reglas deterministas y Gemini 2.5 Flash como respaldo.
No sustituir ese modelo sin comparar interpretacion, errores peligrosos, latencia
y coste en un corpus congelado con contexto; no dejar al LLM calcular dinero.

Ahorrar con resultados cacheados por datos/contrato/codigo, muestras y resumenes
compactos, lectura focalizada y escalado solo de problemas no resueltos. No enviar
ticks o tablas completas a la IA en cada iteracion. El consumo de Codex depende
tambien de modelo, contexto, razonamiento y herramientas; precios API y limites
del plan no son lo mismo. No se ha activado ninguna API de pago ni cambiado modelo.

## 7. Evidencia y reproduccion

Carpeta nueva: `runtime_data/base_audit_20260907_1540/`.
Los datos runtime son locales y no quedan publicados por crear este documento.

- `runtime_snapshot.json`: copia de eventos y version remota, con hora de corte.
- `foundation_audit.json`: contraejemplos, matriz de gestion y hashes de fuentes.
  SHA-256: `021abff209b7fd66b8ce8e87652f49e030587b58208ea7b84866632d5583dac5`.
- `dubai_terminal_addendum.json`: nueve controles repetidos sin alterar originales.
  SHA-256: `d448076dfa424fdb3675bf9563ac6cff287d2464899df5c060128231a67c40b1`.
- Diagnostico de hoy: `runtime_data/today_parity_20260907_1458/RESUMEN.md` y
  `comparison_v2.json`; `comparison.json` anterior NO es la comparacion valida.
- Analisis historico previo: `docs/audits/2026-09-07-research-feasibility.md`.

Comandos ejecutados en el proyecto:

```powershell
python -m pytest -q -m 'not integration'
python runtime_data/base_audit_20260907_1540/audit.py
python runtime_data/base_audit_20260907_1540/dubai_addendum.py
```

Suite: 2.945 passed, 622 warnings, 113,05 s, Python local 3.14.2; pruebas sin
integracion real de broker/modelo. VM observada Python 3.11, no probada mediante
esa suite. Auditoria: salida correcta, hallazgos fallidos conservados. Addendum:
9/9 motor-oraculo, resultados anteriores sin cambio salvo fingerprint.

Trabajo de este pedido: analisis, reproducciones locales y este documento.
No se modifico codigo de produccion, no hubo commit, push, despliegue, reinicio
del bot ni orden enviada. La version live indicada es la observada al corte,
no una comprobacion de exposicion abierta en este instante.

Decision pendiente del usuario: capital previsto y caida maxima de cuenta
aceptable, en EUR o porcentaje. La iteracion tecnica la asume el agente; esos
limites personales no deben inventarse a partir de la cuenta demo.
