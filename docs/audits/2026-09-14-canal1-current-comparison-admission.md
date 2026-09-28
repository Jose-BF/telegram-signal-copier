# Periodo Y Referencia Dubai

## Fechas Comprobadas

Los ultimos resultados de entradas, filtro R05 y break-even no cubren enero.
La muestra retenida tiene 258 identidades, del 05/06/2026 al 07/09/2026.
Recepciones iniciales extremas UTC: 05/06 12:33:34.054 y 07/09 15:18:22.545.
De ellas, 158 rangos disponen de cobertura suficiente para el estudio de una
hora. Distribucion: junio 48, julio 44, agosto 52 y septiembre 14.

En el perfil desfavorable, el control amplio ejecuta 152 operaciones y seis
casos quedan sin entrada. Se retienen 36 bloqueos y 64 precios unicos que no
pertenecen a esta familia. No se interpreta ninguno como beneficio cero de
una operacion desconocida.

El filtro R05 conserva doce operaciones, desde el 11/06/2026 a las
14:16:40.183 UTC hasta el 07/09/2026 a las 13:47:59.041 UTC.
Su saldo hipotetico previo sin BE es +911.63 EUR, de los cuales +899.48
proceden de una sola operacion. No es rentabilidad de una cuenta ni OOS nuevo.

## Control Actual Identificado

`dubai_balanced_v1` coincide entre la politica local y el contrato canonico:

- Entrada al aviso inicial y hasta dos anadidos adversos a 4 y 8 XAU;
  0.01/0.04/0.04 lotes y ventana de 15 minutos.
- Stop monetario de cesta de 25 EUR; proteccion de beneficio armada en
  10 EUR con retroceso de 2 EUR.
- A partir de 40 minutos, cierre si el saldo no es positivo. No hay TP fijo
  ni BE propio y no se cierra necesariamente una cesta positiva a los 60 min.
- Gestion de cierres del proveedor segun el contrato exacto. Las entradas
  pendientes pueden seguir elegibles tras quedar plana la cesta.

Fingerprint de estrategia:
`32cb5c0fe8205ad00a0c655bacd5446c6cc219d1ad7338967212c71781860631`.
Fingerprint completo de ejecucion local:
`8566fa1e9b0b383a997570680fdbc7e8a81b0ed002e8587f7abea2638972b52c`.
La auditoria historica del 07/09 identifica esta politica en la VM en esa fecha.
El estado remoto del 14/09 no se ha consultado ni modificado en este bloque.

## Resultado De La Preparacion

Se fija la [comparacion y sus ramas](../development/2026-09-14-canal1-current-comparison.md)
con dos lecturas: tamanos originales y presupuesto nominal comun de 25 EUR,
recalculando entradas y costes. R05 anterior usaba hasta 200 USD nominales;
sus euros no se deben enfrentar directamente a los de Dubai a 25 EUR.

El perfil del estudio anterior solo admite las protecciones absolutas propias.
En ambos escenarios rechaza la politica de cesta con
`absolute_level_policy_contract` y `protection_extension_requires_own_rule_market`.
Esta es una carencia del alcance del modelo usado, no un resultado negativo
de Dubai. No se han eliminado esas comprobaciones ni simulado una sustitucion.

El siguiente trabajo tecnico queda delimitado: extension opt-in para stop de
cesta, ciclo de cierre a mercado y gestion causal en los tres motores, con
conservacion de fuentes y regresiones antes de calcular el contraste. El
contraste conserva cada reloj de entrada, lotes, cobertura propia, comparacion
en interseccion y cestas abiertas sin cierres artificiales.

Archivo local inmutable `current_comparison_admission_v1`, identidad
`997ddc82f3a1fd0282dca1e2e13758e5b865d7ddc741541c9a165211eed00018`.
258 IDs conservadas; no hay euros nuevos calculados para Dubai.
Se verificaron fuentes/resultados previos, archivo nuevo y 45 pruebas
focalizadas (siete nuevas, 17 de politica y 21 del contrato canonico).
Los motores compartidos no cambiaron; no se repitio la suite completa.

Automatizacion de 30 minutos pausada. Sin commit, push, MT5, VM, cambios live
ni ordenes. Preparacion terminada; comparacion economica pendiente.
