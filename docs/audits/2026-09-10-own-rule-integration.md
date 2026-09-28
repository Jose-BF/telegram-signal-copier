# Integracion De Reglas Propias

## Estado A Las 11:22 UTC

Entrega local del 10/09/2026. Sin commit, push, despliegue ni ordenes. Se
reutilizan los motores scalar, fast y oracle, la cartera y el buscador existentes.
No se construye otro motor ni se cambia el bot. Suite completa coordinada:
**5043 PASS**, cero fallos, errores u omisiones; 424 fuentes y wrappers estables.
`verification_v3/final.json` liga codigo, entorno y XML. Las comprobaciones
siguientes son controles tecnicos y reales de inputs, no certificacion total.

## Dominio Inicial

El coordinador fija este dominio para preparar la primera sesion, dentro de
la delegacion del usuario. El experimento y su busqueda requieren revision previa.
No se habilita toda la gramatica legacy por el hecho de conectar una CLI.

| Aspecto | Contrato inicial |
| --- | --- |
| Disparador | Gold NOW con snapshot causal recibido; los cuatro exports tienen escenarios y chats distintos |
| Entradas | signal_market, delay, pullback, momentum, adverse_reversal; BUY/SELL; controles de interaccion focalizados |
| Primer experimento | Una posicion de 0.01 lotes, simultanea, sin entradas pendientes adicionales ni gestion del proveedor |
| Salidas iniciales | SL fixed_move, TP per_leg_steps, be_mode none; tiempos none/loss_only/profit_only/non_negative |
| Filtros iniciales | none, max_spread y time_window; este ultimo es hora limite UTC, no una ventana local de inicio/fin |
| Precision | XAU 0.01, FX 0.00001, lotes multiplos de 0.01, EUR a dos decimales; contrato XAU 100 |
| Perfil de control | Fill 250 ms, ack entrada 250 ms; proceso/ack proteccion y cierre 250 ms; retry SL/TP 1000 ms |
| Restricciones | Point 0.01, digits 2, stops 20 points, freeze 0; volumen del perfil 0.01..1.00 |
| Cotizacion | Primera cotizacion disponible en/despues del disparador; no snapshot de mercado anterior inferido |
| Cobertura | Hueco maximo XAU 5 s, horizonte declarado y cola completa; todos los bloqueos retenidos |
| FX | Edad 5 s; default estricto. Intervalo historico 60 s solo explicito, precio previo y bracketing existente |
| Dinero | Intradia hipotetico en EUR, costes cero declarados; capital null no significa capital ilimitado |
| Presupuesto M7 | Hasta 40 senales, 120 evaluaciones y 600 s; techo duro 4 millones de quotes. Configs previos 2 millones; nuevo bloque de tres dias 3 millones, sin truncar fuentes |

El SL inicial se solicita desde el precio de solicitud y se valida al procesar;
el TP posterior por posicion se basa en su entrada simulada. Un TP cruzado antes
de instalarse no genera un beneficio retroactivo. SL puede ejecutarse peor ante
un salto. Los retardos son una hipotesis de reloj de quotes, no una calibracion
de latencia real ni garantia de precio. Una salida temporal sin ack al final
permanece incompleta: no se inventa su cierre.

No se ofrecen en este primer dominio BE personalizado/proveedor, parciales,
objetivos monetarios de cesta, entradas observadas bajo market, freeze no cero,
requotes/llenados parciales, overnight, comisiones no soportadas o margen/stop-out.
Los modos adicionales que ya existen conservan su inventario y pruebas; no se
eliminan ni se anuncian integrados sin su evidencia aplicable.

## Evidencia Integrada

- Helper estricto de perfiles, con rechazo de campos desconocidos y tipos no
  representables. Interaccion de reglas propias: 46 pruebas, resultados conocidos
  BUY/SELL, fill/ack, filtros, signo de salida y rechazos de SL/TP sin dinero.
- M7 + bridge + FX: 128 pruebas aprobadas; revision independiente repite 128.
  Dos P2 corregidos con regresiones intactas: mutacion de cintas/evidencia en
  memoria y codigo M7 importado antes de cambiar su fuente.
- S1 integrado en Gold y Dubai: 134 pruebas aprobadas. Perfil y fuente M7 se
  conservan en seis mundos, oracle, cartera, archivo y reanudacion. Tres dias
  sinteticos, una evaluacion fija por control; ninguna busqueda real.
- El modo conectado exige --study-config, --execution-profile, --own-rules-config
  y horizonte explicito compatible. No admite flags legacy contradictorios,
  semillas fuera del dominio ni checkpoints de otras fuentes/perfiles.

Detalles: `2026-09-10-own-rule-study-runner.md`,
`2026-09-10-search-execution-profile.md` y `2026-09-10-study-fx-interval.md`.

## Controles Reales Sin Busqueda

Raiz de artefactos: `runtime_data/calculator_delivery_20260910/`.

Los archivos actuales siguientes se generaron tras el cambio causal y la
ampliacion acotada de fuentes. Los anteriores se conservan sin reescribirlos;
la verificacion actual no se arrastra por etiqueta.

| Control | Denominador completo | Resultado |
| --- | --- | --- |
| Sep8 estricto, v3 | 117 mensajes, 11 triggers; 49 sin admision y 57 fuera del universo | 9 simuladas, 2 bloqueadas FX; 27 evaluaciones, 0 desacuerdos |
| Jul28 historico, v2 | 219 receipts, 44 mensajes, 10 triggers; 34 sin trigger | 10 simuladas; 30 evaluaciones, 0 desacuerdos, cartera canonica sin bloqueos |
| Jul28-30 raw, v1 | 1094 receipts, 239 mensajes, 28 triggers (10/12/6); 211 sin trigger | 28 simuladas; 84 evaluaciones, 0 desacuerdos, cartera canonica sin bloqueos |

Los diez triggers Jul28 se eligieron por calidad de datos antes de resultados:
original conocido y revision canonica. Se conservan las 9288 filas del origen y
la contabilidad de las 9069 fuera del dia. 809927 quotes leidas, 347378 quotes de
paths para el horizonte predeclarado de 60 minutos. No se cambiaron regla,
periodo, costes o tolerancias tras calcular. Los diagnosticos de mensajes de
gestion se conservan aunque esta politica los ignore.

Sep8 se repite con la configuracion original intacta: filas, cartera, estados y
contadores son iguales a v1. Sus dos bloqueos FX siguen siendo 2612 y 2640.
Jul28 tiene su propia configuracion que declara el intervalo historico; no se
aplica retroactivamente al experimento estricto.

`fixed_controls_verification_v2.json` registra ejecucion y reproduccion de los
tres archivos: identidad actual aprobada y bytes identicos, 141 evaluaciones
mas 141 de repeticion. Tiempos iniciales/repeticion: Sep8 0.965/0.574 s,
Jul28 3.357/3.244 s, tres dias 17.101/17.206 s. Son medidas de estos controles,
no estimaciones lineales de una busqueda anual. SHA-256:
`6ab37003125328c2ee66f8cd02326857c1d3e724602e562c7b206c2f7fcb1ebf`.
El informe anterior v1 se conserva como evidencia de su codigo original.

Tres dias: 2609968 quotes de seis fuentes completas, 1012184 quotes de paths;
un config congelado antes del resultado, 28 triggers raw. 585 y 586 no son la
misma revision: se mantienen separados, sin afirmar que equivalgan a dos
cestas del catalogo. Todas las 9288 filas originales se contabilizan: 1094
en el periodo y 8194 fuera. El coordinador verifica el archivo con la CLI
publica; identidad `3052d968f1ba187ad07c395906b50a612d448dfc60409c9606b0d1ad25927a59`.

El bridge carga diez paths Jul28 y 28 de tres dias, cobertura completa en ambos.
Dinero verificado false, seleccion null y promocion false en todos los casos.
La ausencia de deals observados en este puente se reporta como desconocida,
no como beneficio observado cero. Cartera calcula equity abierta, drawdown y
exposicion bajo el mismo perfil, pero no acredita solvencia o margen.

## Pendientes Concretos

1. Acopio y contraste de la ventana futura congelada: Sep10 13:00-15:00 UTC;
   no forzar dos operaciones. Protocolo vigente `forward_thursday_v2/frozen/protocol.json`;
   precaptura verificada y cierre puntual registrado para las 17:00 Madrid.
2. Admision monetaria historica, separada del control hipotetico. Auditoria
   nativa: 2508 deals/1254 posiciones; comision y fee cero, 12 deals con swap.
   La unica diferencia intradia se explica por residuo binario inferior a un
   centimo, con la misma cotizacion; no se cambian los flags del archivo previo.
   No se puede demostrar binding de cuenta en el contrato Jul22 ni reloj
   historico universal. Los tres overnight diagnosticados siguen bloqueados.
3. Periodo final mayor que este control, folds, presupuesto aprobado y
   declaracion de uso previo. Tres dias retrospectivos no son OOS intacta.
4. Revision conjunta del experimento y autorizacion de busqueda: candidatas
   reales evaluadas por esta entrega, cero. La rentabilidad no esta establecida.
