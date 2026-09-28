# Canal 1: Cuatro Referencias En La Muestra Ampliada

## Resultado Principal

La recuperacion 2+1 anterior no conserva una ventaja positiva en toda la
muestra ampliada. Agosto es favorable; junio es claramente desfavorable.
Esto no prueba ausencia de ventaja del canal ni invalida otras gestiones.
Si prueba que el resultado positivo del subconjunto anterior no basta para
adoptar esta regla fija como universal.

Las 84 identidades compartidas con el contraste anterior conservan exactamente
sus 672 filas: regla, perfil, entradas, salidas, eventos, tiempos, precios,
volumen, dinero, bloqueos y resultados de los tres motores. No es una
diferencia causada por modificar el simulador o sustituir sus fuentes.

## Matriz Y Supuestos

258 IDs retenidos, 240 primeras recepciones respaldadas, 230 con ventana
de 20 minutos evaluable. Dieciocho entradas sin respaldo original y diez
ventanas sin cobertura permanecen bloqueadas. Hay 2064 filas, 258 x 4 x 2:
1208 simuladas, 632 sin entrada y 224 bloqueadas. No entrada significa cero;
datos desconocidos no significan cero.

Cuatro reglas anteriores sin reajuste: mercado SL10/TP5/15min,
demora 90s/SL8/TP12/5min, recuperacion 2+1/SL15/TP8/15min e impulso5/SL10/TP10/15min.
Todas a 0.01 lotes, una posicion aislada, caducidad 3 minutos y horizonte
20 minutos, sin gestion del proveedor. Ejecucion y dinero iguales a los
controles previos, incluido `timestamp_and_ordinal`. Recargo descriptivo
adicional de 10 EUR/lote round-trip, es decir, 0.10 EUR por entrada de 0.01.

Importes siguientes: sumas hipoteticas de posiciones aisladas con datos,
no rentabilidad observada, cuenta completa ni capital del usuario. No hay
capital, margen, costes broker completos ni dinero certificado. Historico
reutilizado, no OOS nuevo. Cero estrategias nuevas y cero ordenes enviadas.

## Comparacion

| Regla | Entradas de 230 oportunidades | Neto referencia EUR | Neto desfavorable EUR | Caida diaria conocida, desfavorable EUR |
| --- | ---: | ---: | ---: | ---: |
| Mercado | 230 | -119.12 | -229.89 | 238.43 |
| Demora 90 s | 230 | +39.44 | -69.38 | 110.12 |
| Recuperacion 2+1 | 111 | -69.38 | -112.85 | 149.54 |
| Impulso 5 | 33 | -24.71 | -34.53 | 66.02 |

Los dos perfiles ejecutan el mismo numero de entradas por regla, no los
mismos precios. La caida diaria solo agrega cierres conocidos: no incluye
flotante conjunto, operaciones desconocidas ni efectos de simultaneidad.
Ninguna de estas cuatro reglas es positiva en ambos perfiles sobre el total.

| Mes parcial disponible | Mercado | Demora 90 s | Recuperacion 2+1 | Impulso 5 |
| --- | ---: | ---: | ---: | ---: |
| Junio desde 05/06 | -126.55 | -77.42 | -114.67 | -19.62 |
| Julio | -62.68 | -29.61 | -18.82 | -24.30 |
| Agosto | -23.46 | +31.12 | +41.69 | +11.82 |
| Septiembre hasta 07/09 | -17.20 | +6.53 | -21.05 | -2.43 |

Tabla mensual: ejecucion desfavorable y mismo recargo. Para recuperacion,
44/26/31/10 entradas, respectivamente. Sus 58 resultados positivos y 53
negativos suman perdida: porcentaje de aciertos no equivale a rentabilidad.

Niveles de evidencia, recuperacion desfavorable: +29.71 EUR en 99 ventanas
canonicas (45 entradas), -149.32 en 123 antiguas (65), +6.76 en ocho
intermedias (una). Esos niveles corresponden a fechas distintas. No atribuir
causalmente la diferencia al formato del registro ni inferir fraude del canal.

Eliminar las recepciones tardias tampoco rescata recuperacion: 228 ventanas
rapidas, las mismas 111 entradas y -112.85 EUR. Ese estrato se muestra como
diagnostico fijado, no se activa como filtro retroactivo de una ganadora.

## Jornadas Completas

De los 65 dias con originales respaldados, 59 tienen cobertura de todos esos
originales a 20 minutos. Al limitar el informe a esos dias, recuperacion sigue
en -48.33 EUR desfavorables y -14.59 de referencia. No se oculta la diferencia
con -112.85/-69.38 del conjunto conocido, ni se declara completo el historial.

Exigir todos los IDs retenidos, incluidos los originales sin respaldo, deja
54 dias completos de 66. En ese denominador recuperacion da -58.00 EUR
desfavorables. El archivo conserva por separado ambos conceptos de dia
completo; no se sustituyen entre si para mejorar el resumen.

## Riesgo De Ejecucion Visible

Caso `canal1_22140`, BUY recibido el 04/09 a 12:28:04.841 UTC. En el escenario
desfavorable de recuperacion entra a 4447.11 a 12:30:09.026. El primer ajuste
de SL/TP se rechaza como `invalid_stops`; el siguiente instala SL4432.11 y
TP4455.11 a 12:30:12.185. La salida por stop se simula a 4420.45 a 12:30:14.611:
-23.00 EUR, -23.10 con el recargo, con caida flotante individual de 33.61 EUR.

Los tres motores coinciden en la secuencia. Es una simulacion sobre los
precios retenidos y el perfil declarado, no una orden observada de la cuenta.
El stop de 15 unidades de precio no garantiza limitar la perdida a su valor
nominal; se conservan rechazos, reintentos y precio de salida. Tampoco se
convierte el maximo favorable previo en un beneficio que pudimos cobrar.
Este caso permanece en todas las estadisticas aplicables.

## Verificacion

Archivo local `runtime_data/canal1_history_20260913/receipt_controls_v1`, identidad:
`d998603acf34e60c3be6e7eac01f55e0e5c05814df33fc273492eb29f725ed3d`.

5520 evaluaciones de motor, 69687432 visitas de cotizacion y 102569008 filas
fuente decodificadas incluyendo la verificacion de cobertura. 423.26 segundos;
dentro de 7000 evaluaciones, 150 millones de visitas, 150 millones de filas
y una hora. Los protocolos se escriben antes de motores; artefactos inmutables.

26 pruebas propias y 95 focalizadas pasan. Las primeras pruebas de integracion
detectaron lista JSON frente a tupla en protecciones iniciales; normalizacion
al formato canonico sin variar valores, antes del primer archivo real.
No se modificaron motores, canonicalizador, listener o ruta de ordenes.
Suite completa posterior: **5713 pasan**, 634 avisos, 339.93 segundos,
Python 3.14.2. No quedan calculos ni pruebas pendientes de este contraste.

Verificador ejecutado en proceso nuevo: relee fuentes, recompone cobertura
y recorridos, exige matriz completa, acuerdo de motores, envolvente de
posicion y coincidencia exacta con las filas previas; recalcula estadisticas
y consumo de recursos. No se afirma que ese verificador repita todas las
evaluaciones de motor: estas se ejecutaron y guardaron en la corrida original.

```powershell
py -3.14 tools/run_dubai_receipt_controls.py verify --output runtime_data/canal1_history_20260913/receipt_controls_v1
py -3.14 -m pytest -q tests/test_dubai_receipt_controls.py tests/test_dubai_paired_clocks.py tests/test_dubai_receipt_inventory.py tests/test_dubai_receipt_coverage.py tests/test_dubai_annual_dataset.py --disable-warnings
py -3.14 -m pytest -q --disable-warnings
```

## Siguiente Bloque

La [especificacion amplia v1](../development/2026-09-14-canal1-discovery-spec-v1.md)
se escribio antes de leer el P/L del contraste ampliado. Copia fija en
`runtime_data/canal1_history_20260913/discovery_spec_v1.md`, SHA-256:
`ca351dfde9de3416c66b093cbcfd019bedb6cb0996345eeaac65ed8aad495ed2`.

Define un catalogo inicial previsto de 4217 reglas, siete valores de SL,
siete de TP, cinco duraciones, siete familias de gestion, tiempo y filtros,
con cuotas para otras entradas y sus interacciones. Rondas posteriores
acotadas, techo global 10000 reglas/cuatro horas/24 mil millones de visitas,
cronologia movil, criterios de participacion/riesgo y parada explicitos.
Esto es especificacion fijada, no 4217 genomas ya generados o probados.

Siguiente trabajo: generar y verificar el catalogo sin precios, concretar las
cohortes y sellar el archivo de ejecucion, verificar capacidad por familia y
horizonte y solo entonces ejecutar las rondas dentro del presupuesto. Toda
incompatibilidad exige registro/version previa, no ajuste tras ver P/L.
Contexto/regimen adaptativo, reentradas, cuenta y live tienen fronteras propias.
Seguimiento activo actualizado al catalogo y campana finita; no vuelve a
solicitar los cuatro controles ya verificados.

No hay politica seleccionada ni candidata nueva admitida. Trabajo local,
sin commit, push, VM, MT5, reinicios, flags ni cambios de canal 2.
