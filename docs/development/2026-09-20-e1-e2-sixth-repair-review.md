# Cierre De Revision Offline E1-E2

Fecha: 20/09/2026. Astra revisa directamente la ultima frontera modificada por
Sol. Sin nuevos defectos materiales en el alcance revisado. F3 queda cerrado;
se conservan los cierres F1/F2/F4 de la quinta revision. Base offline E1-E2
aceptada para avanzar al diseno E3; no equivale a aprobacion operativa de E2,
validacion de simulaciones economicas ni autorizacion de despliegue.

## Evidencia

- Hash de execution_latency.py verificado:
  `20338143f942477c8cd3e65f73c7e91ed705a9b0d68eb865486a3e607f808d55`.
- Hash de tests/test_execution_latency.py verificado:
  `d779305f62d07b88cba0252f8a03345a86489ad491d1ca6c4d5d06f4118aa7ca`.
- execution_intents.py y mt5_gateway.py conservan los hashes aprobados en la
  quinta revision. No se reabren sus contratos sin evidencia nueva.
- 38 pruebas pasan en 0,33 s: 18 canonicas, 12 del revisor anterior y ocho
  controles nuevos. Ejecucion con pytest e import-mode=importlib.
- Probes nuevos en runtime_data/e1_e2_sixth_review_20260920/test_identity_review.py;
  resultado en identity_review.xml. SQLite/MT5 no se usan en estas pruebas.
- Se reutiliza la evidencia de Sol: 6.004 pruebas globales en 370,30 s y 229
  focales. El XML focal conserva su hash y tiene 229 elementos testcase, cero
  errores/fallos/omitidos; su atributo agregado tests dice 257. No se presentan
  esas 257 como pruebas independientes adicionales.
- Inventario v6 y generador conservan exactamente sus hashes publicados:
  `06c79ed4f8251c0445c44b5bccb965c4bf11554f5b7975d996181ba7b1380b01`
  y `a09ef960cb647f1eafcd54cb00a55900e65cc80b2877bcb45c485b04b85e101e`.
  Se conserva la reconstruccion exacta verificada por Sol; no se regenera.
- Entorno local Windows/Python 3.14.2; HEAD
  `7415941a410d18288fa4e08da76809f70b125efa`, rama feature/gold-555-live-trial.

## Limites Revisados

El conjunto compartido detecta la colision antes de construir duraciones. La
cuarentena se propaga al grupo de decision y de intento, incluyendo aliases
y duplicados. La falta del inicio invalida la duracion dependiente, sin borrar
el roundtrip de otro intento cuya identidad siga siendo valida.

Se comprueban ambos tipos de inicio (Telegram e interno), orden invertido,
iterador, copias duplicadas, colision con la propia decision y con otra sesion.
En la cohorte mixta de 40 pares quedan 38 muestras validas y estado ready;
esto acredita que no se rechaza innecesariamente todo el lote. En la cohorte
totalmente contradictoria quedan cero muestras. Los controles limpios mantienen
30 muestras y sus tiempos originales.

El contrato revisado abarca decisiones e intentos usados para estas duraciones;
no es un validador universal de cualquier evento del diario. Los resultados
son controles sinteticos, no incidentes constatados en produccion.

## Siguiente Paso

Se pasa al [plan E3](2026-09-20-e3-integration-plan.md). Permanecen abiertos los
ensayos operativos de Python 3.11/Windows equivalente a VM, bloqueo nativo de
60 s, carga sostenida, propietario unico, recuperacion y conciliacion real.
No se reducen esos requisitos por aceptar el prototipo offline. La coincidencia
de decisiones, exposicion y drawdown se contrastara despues de la integracion.

Esta revision solo anade pruebas y documentacion. Sin cambios de estrategia,
commit, push, consulta VM, reinicio ni ordenes reales.
