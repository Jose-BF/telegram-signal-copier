# Reparaciones De Observacion Y Contraste Nativo

## Alcance Y Estado

Peticion del 09/09: corregir lo necesario tras la revision de las 14:00 y
continuar la preparacion. Este bloque modifica codigo local, ejecuta pruebas
offline y obtiene un diagnostico retrospectivo de solo lectura. No publica,
reinicia, envia ordenes ni cambia estrategias, parametros o tolerancias.

Base local y VM: `ed9ede1f1f8d228791e09d5b10bbdd2645c1793c`;
rama local `feature/gold-555-live-trial`. Se conservan los cambios previos de
investigacion y documentacion. Estos cambios siguen sin commit ni push.

## Conversion Monetaria

La consulta historica de EURUSD terminaba junto con la de XAUUSD. Cuando la
ultima cotizacion FX superaba los 5 segundos, podia faltar la siguiente marca
temporal que demuestra su intervalo, aunque ya estuviera disponible en MT5.
Si aun no existia esa marca, la observacion consumia el tick sin evidencia
monetaria y conservaba `money_contract_missing` como bloqueo irreversible.

La correccion de `main.py` consulta la prueba de intervalo FX hasta el reloj
de observacion, acotada por los 60 segundos que ya establece el contrato.
Rechaza filas futuras devueltas fuera de la consulta. Usa siempre el Bid/Ask
anterior al tick XAU; la siguiente cotizacion solo aporta la marca temporal
de cierre del intervalo, que ahora se incluye en la identidad de evidencia.

Retiene el tick no resuelto y los posteriores sin adelantar el cursor;
entrega los ticks completos anteriores. Reintenta con pausa y deja rastro de
espera/reanudacion. Una falta real o un intervalo vencido sigue bloqueando.

Contraste offline de funciones antiguas y nuevas contra ticks nativos,
limitados al reloj de cada observacion y enlazados con los eventos originales:

| Incidente UTC | Senal y perfiles | Edad FX (ms) | Intervalo FX (ms) | Resultado |
| --- | --- | ---: | ---: | --- |
| 09:46:53.543 | Gold `canal2_2662`, b210/c490 | 5219 | 7652 | Antiguo sin dinero; nuevo espera a la prueba desde 09:46:55.976 y recupera el mismo tick |
| 11:59:47.920 | Dubai `canal1_22347`, tres perfiles | 5144 | 5416 | Antiguo sin dinero; nuevo resuelve con prueba ya disponible al observar a las 11:59:48.384 |

No se amplian limites ni se sustituye el precio anterior por uno futuro.
Gold 555 no tenia este bloqueo observado en los casos revisados; el primer
incidente corresponde a perfiles alternativos. No se cambia su politica live.

## Revisiones Y Recepciones

La nueva herramienta `tools/audit_raw_observations.py` separa contenido de una
revision de sus recepciones, reutilizando la validacion causal existente.
Conserva todas las observaciones con identidad, contenido, primera recepcion
y huellas; bloquea cambios de texto, medios, reply o revision y transporte o
cronologia invalidos. Su salida es inmutable, sus lecturas estan acotadas,
rechaza JSON ambiguo y comprueba hashes antes/despues.

Sobre 58683 filas: **68 observaciones, 37 revisiones, 31 recepciones repetidas
compatibles, cero conflictos de contenido y cero observaciones invalidas**.
De las 31 diferencias de la proyeccion antigua, 27 eran solo el instante de
recepcion y cuatro tambien la marca de callback `is_edit`.

No reemplaza la validacion completa de linaje o medios. No se modifica el
colector congelado ni se borran sus 31 conflictos originales: el bloque de la
manana sigue incompleto e invalidado como validacion prospectiva. El compilador
causal existente ya separaba las recepciones de esta manera.

## Conciliacion Nativa

Siete solicitudes y siete resultados coinciden con sus ordenes y deals MT5
en identidad, direccion, volumen y precio. Son cinco posiciones de Gold
`canal2_2668` y dos de Dubai `canal1_22347`, no siete senales independientes.

| Posicion | Solicitud a fill nativo (ms) | Fill a acuse cliente (ms) | Neto observado EUR |
| --- | ---: | ---: | ---: |
| 1968881295 | 36 | 93 | 1,72 |
| 1969009339 | 1440 | 1303 | 2,58 |
| 1969012639 | 765 | 1982 | 3,86 |
| 1969028684 | 6838 | 312 | 5,15 |
| 1969031673 | 566 | 665 | 6,44 |
| 1969126637 | 34 | 116 | -7,90 |
| 1969175471 | 43 | 350 | -16,86 |

Gold suma **19,75 EUR**, conciliados tambien con sus cinco cierres del diario.
Dubai suma **-24,76 EUR** en MT5, con cierres por SL posteriores al corte del
diario descargado, que termina a las 12:05:45.800 UTC. No se presentan esos
cierres como conciliados contra el diario. Comision, swap y fee observados
son cero en ambas caras de estas siete posiciones; no se generaliza a otras.

La latencia solicitud-acuse no es toda retraso de ejecucion del broker. Los
deals y ordenes adicionales se conservan fuera del alcance del control;
la captura nativa contiene 56 deals y 56 ordenes. Esta contabilidad no demuestra
fills alternativos ni valida entradas simuladas independientes.

## Verificacion

Entorno: Windows, Python 3.14, pytest 9.0.3, pandas 3.0.2.

- Regresiones iniciales: tres fallos reproducidos con el comportamiento
  anterior; la nueva variante de espera tambien detecto la falta de pausa.
- `py -3.14 -m pytest -q tests/test_strategy_shadow_main.py`: 54 aprobadas.
- Pruebas del nuevo auditor: 18 aprobadas, incluidos JSON ambiguo y limites.
- `py -3.14 -m pytest -q --junitxml=runtime_data/afternoon_repairs_20260909/full_suite_v1.xml`:
  **4465 aprobadas, 634 avisos, 217,73 segundos**, salida 0. Los avisos incluyen
  APIs deprecadas; no se extiende este bloque a una limpieza general.
- `py -3.14 runtime_data/afternoon_repairs_20260909/verify_repairs.py`:
  dos incidentes verificados, siete aperturas conciliadas y cinco cierres
  cotejados con el diario, salida 0. Sin llamadas MT5 ni simulacion integral.

Evidencia: `runtime_data/afternoon_repairs_20260909/`. El verificador conserva
hashes de codigo, datos y pruebas; valida los ocho ficheros fuente contra el
manifiesto y comprueba que no cambian durante la ejecucion.

| Artefacto vigente | SHA-256 |
| --- | --- |
| `repair_verification_v2.json` | `ccf6ecb73db09ceca0f0c4465fdc40120617802a4dea07c3e075e9b8a043b534` |
| `raw_observations_v2.json` | `190fb22661d8fc01915dc303385af86972ad31c785536e3c93a1b9d72a37bec6` |
| `full_suite_v1.xml` | `751ade7fcef8be2dbb235cfb09370ae70391364bc7b4ab9c0f264a51afc4f5f5` |
| Manifiesto nativo | `2d494434a3a4d8a77e8c4e0998a635aea10871591deffc4e3d45a0d83be05675` |

`repair_verification_v1.json` se conserva pero queda sustituido: su etiqueta
manual identificaba mal la senal Dubai. v2 obtiene y verifica esa identidad
de los eventos originales; no altera los datos fuente.

La captura nativa de las 14:23 UTC usa un nuevo helper identificado aparte del
colector original. Conserva el ancla y el sufijo 1456441364..1561098946 del diario,
sin reescribirlo ni descargarlo completo. No certifica continuidad total de ticks.

## Estado Real Y Continuacion

Lectura VM del **09/09 a las 14:44:58 UTC**: HEAD `ed9ede1`, arbol limpio,
PID 9412; heartbeat de las 14:44:44.847, cero posiciones del bot, senales
abiertas y entradas pendientes. Telemetria publicada correctamente a las
14:43:17, cola vacia y sin bloqueo de indice. Esa publicacion automatica es
de datos, no el despliegue de estas correcciones. No hubo reinicio por este trabajo.

Se cierran los defectos diagnosticados y la conciliacion del control acotado,
no los puntos 1-3 del mapa compartido. Sigue pendiente la reproduccion continua
con entradas/estado propios, primera divergencia y admision por capacidad,
dataset y horizonte, seguida de una muestra nueva congelada tras las reparaciones.
Se puede continuar localmente; nueva telemetria no es requisito universal
para todos los controles historicos.

Activar estas reparaciones requiere autorizacion explicita de publicacion y
comprobacion de exposicion/reinicio en ese momento. El presupuesto de nuevas
candidatas sigue en cero y se mantiene la revision conjunta antes de la busqueda.
