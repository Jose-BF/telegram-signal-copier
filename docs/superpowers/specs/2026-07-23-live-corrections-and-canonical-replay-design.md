# Correcciones live y replay canónico

Fecha: 2026-07-23

## Objetivo

Corregir los fallos observados el 23 de julio sin descartar jornadas completas
ni borrar lo que ocurrió realmente. La operativa live debe impedir que el fallo
se repita; la simulación debe conservar la verdad observada y, por separado,
representar la intención canónica del proveedor con correcciones auditables.

## Principios

1. `trade_events.jsonl` y la evidencia MT5 son inmutables: una ejecución
   equivocada también forma parte de la verdad observada.
2. Ningún fallo aislado invalida automáticamente el día completo. La aptitud se
   decide por señal, acción y política simulada.
3. Una corrección canónica nunca elimina evidencia: añade motivo, evidencia
   origen y efecto exacto sobre la simulación.
4. Un cambio remoto no puede reiniciar el bot mientras existan posiciones
   gestionadas por él.
5. No se automatiza una semántica rara sin evidencia suficiente.

## Cambios

### 1. Apertura idempotente de mensajes de Telegram

La autorización para crear exposición será única por canal, identificador del
mensaje y revisión semántica, con independencia de que Telegram entregue el
contenido como `new`, `edit`, `poll_new` o recuperación de un edit huérfano.

Procesar una revisión y abrir posiciones serán operaciones separadas. Distintas
rutas podrán enriquecer niveles o texto, pero una misma revisión solo podrá
crear un bloque de posiciones. El intento repetido quedará registrado como
deduplicado.

### 2. Mensaje excepcional de entrada adicional

Frases explícitas como `I put more sell on 4055.00` se reconocerán como
`EXPLICIT_ADDITIONAL_ENTRY_REVIEW`. Por ahora no abrirán posiciones ni crearán
órdenes pendientes, porque solo existe un caso fiable y no hay contrato probado
sobre cantidad, vigencia o desviación de precio.

El aviso humano mostrará únicamente:

- proveedor y dirección;
- precio declarado y precio ejecutable actual;
- diferencia favorable o desfavorable;
- operación relacionada;
- motivo por el que requiere revisión.

El evento estructurado se conservará para detectar recurrencia. No bloqueará el
día ni las demás señales.

### 3. Despliegue sin reinicios con exposición abierta

El watcher podrá detectar y descargar información remota, pero si un cambio de
código requiere reinicio y existen posiciones del bot:

- mantendrá el proceso actual;
- registrará `code_update_deferred_open_positions`;
- notificará una sola vez que existe una actualización pendiente;
- aplicará el cambio cuando no queden posiciones o durante una intervención
  explícita por SSH.

Los cambios que solo contienen datos no provocarán reinicio. Ningún push desde
el entorno de desarrollo desplegará ni reiniciará la VM automáticamente.

### 4. Auditoría correcta de cambios SL/TP

El auditor conservará una ventana breve de acciones MT5 confirmadas y sus
niveles reales por ticket. Una modificación de BE confirmada seguirá siendo
atribuible aunque la cola pendiente ya la haya retirado.

Cada ticket se comparará con su TP y SL efectivos, no con un único TP supuesto
para toda la señal. Solo se emitirá `mt5_level_change_unattributed` cuando no
exista una acción pendiente o recientemente confirmada que explique el cambio.

### 5. Verdad observada y replay canónico

El catálogo distinguirá:

- lotes de ejecución reales, aunque compartan el mismo identificador de señal;
- intención del proveedor;
- correcciones canónicas explícitas.

Para el duplicado del 23 de julio, el replay observado conservará los diez
tickets. El replay canónico representará un solo bloque y añadirá una corrección
con la identidad del bloque excluido, el motivo `duplicate_delivery_execution`
y la evidencia Telegram/MT5 que lo demuestra.

La granja de estrategias usará la capa canónica. Los validadores de ejecución y
contabilidad seguirán usando la capa observada. Una corrección incompleta
afectará solo a la señal implicada y quedará visible; no invalidará por defecto
el resto de la jornada.

## Pruebas de aceptación

1. Un edit huérfano seguido por `poll_new` crea exactamente un bloque.
2. Una revisión posterior con niveles nuevos actualiza la señal sin duplicar
   posiciones.
3. `I put more sell on 4055.00` genera un único aviso estructurado y ninguna
   orden.
4. Un cambio de código remoto con posiciones abiertas queda aplazado y el bot
   continúa activo.
5. El mismo cambio se aplica cuando ya no existen posiciones.
6. Un BE confirmado no produce una falsa alerta y conserva el TP propio de cada
   ticket.
7. El catálogo cuenta dos lotes reales cuando hubo doble ejecución bajo una
   misma señal.
8. El replay observado reproduce ambos lotes; el canónico conserva uno y
   publica la corrección.
9. La jornada mantiene utilizables las señales no afectadas.

## Fuera de alcance

- Automatizar entradas adicionales sin señal formal.
- Elegir una estrategia o cambiar lotaje.
- Borrar o reescribir logs históricos.
- Hacer push, desplegar o reiniciar la VM sin confirmación del usuario.
