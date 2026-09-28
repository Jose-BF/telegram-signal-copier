# Plan maestro: de las señales a una cuenta de fondeo rentable (28/09/2026)

**Puerta de entrada para ejecutar.** Resume la intención y el orden, y remite al detalle:
- `plan-verificacion-reto.md` (verificación, capa de examen, bot);
- `diseno-bloque-O.md` (descubrimiento señal a señal);
- `mapa-de-fallos.md` (dónde puede fallar todo, con su estado).

No es una receta para seguir a rajatabla: es una **brújula**. Quien ejecute debe entender la
intención y usar su criterio. Si una tarea resulta inútil, se cambia o se salta, dejando escrito el porqué.

## 1. Brújula (intención y principios)
1. **Objetivo único:** ganar dinero de forma sostenida con las señales de los dos canales. Camino
   preferido: reto de FTMO de 2 fases (10k para empezar) y luego cuentas fondeadas. Sin perder
   dinero en retos por fallos evitables.
2. **Explorar con libertad, juzgar con rigor.** Mente abierta para buscar (mecanismos, indicadores,
   comportamiento de los traders, recursión), pero toda conclusión pasa por el juez honesto:
   walk-forward, placebo del procedimiento entero, 3 brokers, costes de FTMO, régimen, estrés.
3. **Nada heredado es verdad por defecto.** La 555, el buscador recursivo, el motor y las
   conclusiones previas (de modelos anteriores y de la búsqueda 2) se ponen a prueba. Lo que no
   aguante, se rehace o se descarta sin pena.
4. **Primero los fallos, luego el trabajo.** Cada tarea empieza diciendo qué filas del mapa de
   fallos ataca, y termina con su evidencia. Nada cuenta como hecho sin evidencia.
5. **Dinero solo tras las puertas.** Ningún euro en retos hasta pasar la puerta de decisión (§5).
   El bot en vivo no se toca sin el OK de Jose para ese cambio concreto (protocolo de despliegue de `estado-y-plan.md` §6).
6. **Realismo sobre el mercado:** 2026 fue caótico en el primer trimestre (rango diario 137–166 $)
   y más tranquilo después (78–89 $). Todo se mira por régimen, con algo más de peso a lo reciente.
7. **Hablar con Jose en llano:** informes cortos con "haciendo esto → pasa esto → dinero",
   y cualquier duda se le pregunta.

## 2. Qué tenemos (activos) y qué es sospechoso
- **Activos con evidencia:**
  - motor exacto validado contra operaciones reales en la 555 y Dubai balanced (55/55 cestas);
  - recorridos por señal (`research/signal_paths.py`), calculadora rápida (`bracket_grid`, `basket_grid`);
  - walk-forward (`walkforward.py`), PBO (`folds.py`), rasgos v2 (`signal_features_v2.py`);
  - simulador de reto (`prop_sim.py`, `prop_rules.py`);
  - recetas congeladas R1/R2/R3 (`search2/frozen_v1.json`).
- **Sospechoso o sin probar:**
  - la ejecución tipo R1 nunca examinada en vivo;
  - costes de FTMO sin medir;
  - sesgo de selección de la búsqueda 2;
  - el buscador recursivo heredado;
  - la sombra de la VM (parada desde el 30/08).

## 3. Bloques (qué pregunta responde cada uno)
| Bloque | Pregunta | Hecho cuando |
|---|---|---|
| **0 · Auditoría y mapa** | ¿Qué puede estar mal en lo que ya tenemos? | Mapa revisado; piezas heredadas clasificadas (usable / auditar / descartar) |
| **V · Verificación de R1/R2** | ¿Son reales o suerte, y aguantan costes y rachas? | V1–V4 de `plan-verificacion-reto.md` con veredicto por receta |
| **P · Entender a los traders** (nuevo) | ¿Cómo y cuándo publica cada proveedor, y cuándo acierta? | Un "perfil del trader" por canal, con lo que implica para operar |
| **O · Descubrimiento** | ¿Cómo sacar el máximo a **cada** operación con la información disponible en cada momento? | Una **política adaptativa** (reglas CUANDO → HACER por fase de la operación y por contexto) juzgada con el juez honesto, o la conclusión, con números, de que no mejora a R1/R2 |
| **M · Capa de examen** | ¿Con qué lote y qué frenos se pasa el reto con suspenso ≤ 5 %, lo más rápido posible? | Lote y reglas por tamaño de cuenta, con costes reales |
| **F · Bot y pruebas en vivo sin riesgo** | ¿El bot hace exactamente lo que dice el simulador? | Entorno de examen en la demo de 10k, sombra en tiempo real con comprobación nocturna, 4 semanas de examen, 2 semanas en la demo de FTMO |
| **G · Puerta** | ¿Compramos el reto de 10k? | Criterios de §5 cumplidos |

### Bloque P: entender a los traders (pedido por Jose, 28/09)
**Idea:** si sabemos qué mira cada proveedor para publicar, sabremos cuándo sus señales son buenas y
cuándo no, y podremos "leerlas" con más luz. En la búsqueda 2 ya asoman dos perfiles distintos:
Gold funciona cuando la señal va a favor del impulso (ruptura del rango asiático), y en Dubai R2
funciona tras una caída fuerte en contra. Preguntas:
- **Cuándo publican:** horas, sesiones, días, cerca de noticias (hay el doble de señales reales
  que de placebo a menos de 30 min de una noticia), rachas.
- **En qué situación de mercado:** comparar los rasgos en el momento real de la señal con momentos
  al azar del mismo día (tras subidas o bajadas, en extremos de rango, con RSI extremo, en
  rupturas…). Un árbol pequeño que distinga "momento de señal" de "momento al azar" enseña su regla aproximada.
- **Qué niveles dan** (Gold publica zona, TP y SL): dónde los ponen respecto a la estructura del
  precio, y qué parte se cumple.
- **Hipótesis de Jose: canal 2 opera con soportes y resistencias.** Se construyen rasgos de niveles
  y se mide si sus señales, zonas, TP y SL caen sobre ellos más que al azar:
  - máximos y mínimos de giro en varias escalas (5 min, 15 min, 1 h, 4 h, diario), con cuántas veces se han tocado;
  - máximo y mínimo de ayer, de la semana y del rango asiático;
  - números redondos; pivotes diarios;
  - zonas de volumen de ticks;
  - rupturas y "barridos" de nivel.

  Se mira también qué indicadores (medias, RSI, Bollinger, MACD, ADX, VWAP…) explican mejor sus
  momentos de publicación.
- **Cómo gestionan** (mensajes de gestión del canal, ya reconstruidos en `research/gold_channel_*.py`).
- **Cuándo aciertan y cuándo fallan:** acierto por contexto y régimen.
- **Fuentes:** `runtime_data/signal_universe_v1/`, textos del canal (`gold_export_texts.json`, diario
  del bot), rasgos v2 y los estudios de las secciones 17–18 de `estado-y-plan.md`.
- **Salida:** `docs/development/perfil-traders.md`, con consecuencias concretas (filtros, cuándo no
  operar, qué gestión encaja con su estilo), que alimentan el bloque O. Todo filtro que salga de
  aquí pasa por el juez igual que los demás.

### Qué busca de verdad el bloque O (aclaración de Jose, 28/09)
No se trata de encontrar "otra estrategia fija" (una 666), sino de **sacar el máximo a cada
operación**: una política que, señal a señal y momento a momento, decide si operar, cómo entrar,
cuánto, cuándo añadir, cómo proteger el beneficio y cuándo cortar, según el contexto y lo que va
pasando en la operación. "666" o "H6668" son solo nombres para lo que salga.
Límite honesto: el máximo de cada operación mirando el futuro no se puede alcanzar. Lo alcanzable
es **la mejor decisión con la información que existe en cada momento**, y eso es lo que se construye.
Detalle completo: `diseno-bloque-O.md` (9 puntos de decisión, información dentro de la operación,
arquetipos de recorrido, probabilidades de giro, arrepentimiento por decisión, punto común y
mejora recursiva).

## Estado
- **Bloque 0 — hecho (29/09).** `auditoria-bloque0.md`:
  - piezas heredadas clasificadas;
  - fallo de causalidad en los rasgos v1 corregido y con test (R1/R2 no cambian);
  - buscador recursivo con diseño correcto, pero mal usado: se reutilizan sus operadores, no sus resultados;
  - 63 tests previos rotos clasificados (no los causa esta sesión);
  - motor rápido = lento en R1 (78/78).
- **Bloque V — en curso:**
  - V1 (placebo de toda la búsqueda) calculándose en el motor; pre-registro en `search2/v1_preregistro.json`.
  - **V2 superado** (`out/search2/v2/v2_robustez.json`):
    - R1: 8/9 vecinos positivos (solo falla con el tope en 30 €); sin su 5 % mejor +390 €; positiva en los 3 regímenes y en T2/T3 (3/3 y 3/3);
    - R2: 13/14 vecinos positivos (solo falla sin objetivo); sin su 5 % mejor +509 €; positiva en los 3 regímenes; T3 el mejor trimestre.
  - **V3 superado** (`out/search2/v3/v3_costes.json`):
    - R1 +553 € sin costes → +435 € con la comisión alta (22,5 $/lote); peor combinación (spread +0,2 y comisión alta) +279 €; retraso de 2 min y comisión alta +224 €;
    - R2 casi no se inmuta (+401 € en el peor caso).
  - **V4 superado** (`out/search2/v4/v4_dia_ftmo.json`): peor día idéntico con el día del broker y el de FTMO (R1 −92 €, R2 −72 €, juntas −92 €). R2 deja 4/142 operaciones abiertas al corte: el bot necesita cerrar antes del fin de día.

- **Bloque P — avanzado (29/09)**, en `perfil-traders.md`:
  - los dos publican "comprando la caída" a corto plazo;
  - los niveles de estructura no se confirman, pero sí prefieren números redondos;
  - ninguno acierta la dirección mejor que el azar en ninguna situación, así que la ventaja está en la
    forma del recorrido más la gestión, y eso orienta el bloque O.

## 4. Orden y dependencias
0 → V (V1 primero: su listón de suerte sirve para todo) ∥ P → O → M → F → G.
P puede ir en paralelo a V porque es descriptivo. O usa lo que salga de P. Tras cada bloque:
informe corto a Jose y actualización del mapa de fallos.

## 5. Puerta de decisión (fijada ya)
Comprar el reto de 10k solo si:
1. las candidatas pasan V (o su equivalente de O);
2. 4 semanas en papel con resultado ≥ 0 y ningún día que habría roto la regla diaria;
3. la demo de FTMO ejecuta igual que el simulador, sin errores, y con costes medidos dentro de lo supuesto;
4. la probabilidad simulada de suspender es ≤ 5 % con costes reales, topes en racha y freno diario;
5. el mapa de fallos no tiene filas relevantes en rojo sin aceptar por Jose.

## 6. Decisiones ya tomadas por Jose
- Suspenso ≤ 5 % y lo más rápido posible dentro de eso.
- FTMO: 2 fases, Standard, EUR, empezar por 10k. Las señales están permitidas (según su respuesta;
  guardarla por escrito).
- Producción = entorno de examen en una demo de Vantage de 10.000 € (la abre Jose).
- Sombra en tiempo real con el motor validado y comprobación nocturna.
- Demo de FTMO: cambiar el bot 2 semanas.

## 7. Pendiente de Jose
- Guardar la respuesta escrita de FTMO.
- Abrir la demo de Vantage de 10k (sin conectar hasta F).
- Subir el esfuerzo del modelo a máximo al llegar al bloque O (se le avisará).
