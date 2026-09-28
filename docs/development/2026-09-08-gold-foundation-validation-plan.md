# Base causal y monetaria Gold

## Autorizacion y limites

El usuario autoriza proceder tras acordar: datos causales, validacion de
ejecucion/dinero y solo despues investigacion de estrategias. Astra dirige y
revisa; Sol realiza tareas independientes acotadas. No se cambia la version
activa `08551e9a8`, la cuenta, los lotes o la estrategia. No se autoriza un push
incidental ni se inicia una busqueda antes de fijar sus limites.

## Hitos

- [x] Generar catalogo Gold actual desde el diario congelado, aislando chat,
  recepcion, revisiones y corte temporal; conservar el resultado de cada mensaje.
- [x] Contrastar las seis alternativas EURUSD y buscar el 24/07 solo en las
  fuentes conocidas. Conservar los bloqueos que no puedan resolverse.
- [x] Comprobar por pruebas la direccion causal catalogo -> dataset Gold;
  corregir un defecto demostrado sin cambiar la politica viva.
- [x] Cruzar cada senal NOW con requisitos de cobertura y registrar las
  dependencias monetarias/de ejecucion que impiden admitir un estudio concreto.
- [x] Revisar cambios, ejecutar pruebas proporcionadas y producir un informe
  final que separe resuelto, incertidumbre historica e informacion requerida.

## Criterios de cierre

Una senal no puede usar una revision recibida despues de su decision. Los chats
Gold historicos no pueden colisionar por numero de mensaje. Ningun mensaje,
rechazo, ausencia de original o captura discrepante se elimina silenciosamente.
Las diferencias son incidentes que se resuelven o se mantienen bloqueados.
La igualdad del motor con su referencia no certifica un fill hipotetico.

El capital, riesgo admisible y presupuesto de busqueda se fijan antes de una
busqueda. Se ha preguntado al usuario por capital/caida tolerable; mientras,
continua el trabajo de datos y las comprobaciones independientes del capital.

## Evidencia

Entrada: manifiesto `study_preparation_20260908_0044/data_inventory`, identidad
`2c30f5c51a41318b955fb621db78843596b24cf2b4868486d2b67809f996af52`.
Resultados nuevos: `runtime_data/gold_foundation_20260908_0150/`.
Los resultados anteriores y sus hashes no se sobrescriben.

Progreso: catalog_v2 contiene 242 NOW y conserva 2.159 mensajes del inventario.
Las seis discrepancias EURUSD son solo de procedencia/formato, no de precios;
siguen sin recuperarse EURUSD 24/07 y sin resolverse determinados huecos de
cotizacion. Las 63 fuentes pasan los validadores de sus contratos, lo que no
certifica continuidad util en cada horizonte. La version final supera 3.064
pruebas completas y 48 focalizadas. Revision independiente cerrada, fuentes
recomprobadas y correspondencia de las 242 senales verificada en
`runtime_data/gold_foundation_20260908_0150/verification_final.json`.
Informe: `../audits/2026-09-08-gold-foundation.md`. Se cierra esta preparacion
diagnostica, no la admision monetaria ni la investigacion posterior.
