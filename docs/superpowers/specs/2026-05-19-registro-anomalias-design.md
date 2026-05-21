# Registro de anomalías y expediente completo por trade

**Fecha:** 2026-05-19
**Proyecto:** telegram-signal-copier (v1)
**Estado:** diseño aprobado, pendiente de plan e implementación

## Contexto y motivación

El bot opera señales de XAUUSD de dos canales de Telegram. Hoy hay tres
artefactos de log:

- `data/trade_events.jsonl` — stream JSON-lines de eventos atómicos (~1000+
  por sesión). Excelente para el forensics de UNA señal; pésimo para
  detectar PATRONES porque hay que reconstruir cada vista a mano.
- `data/trade_journal.csv` — una fila por trade con `tag` semántico
  (WIN_CLEAN / LOSS_REVERSAL…). Solo la vista del bot, sin verificar MT5.
- `data/ledger.jsonl` — una fila por trade, reconciliada contra MT5, con
  un campo `flags`. Es la fuente de verdad **pero está incompleta**: le
  falta la gestión cruzada del canal, la calidad de entrada, el contexto
  de mercado y una lista de anomalías categorizadas.

**Problema observado (2026-05-13 → 2026-05-19):** cuando el usuario pide
"investiga", la asistencia escribe scripts ad-hoc (`_d19_audit.py`,
`_mgmt_audit.py`, `_verify_canal1_filter.py`…) para reconstruir vistas
que deberían estar pre-computadas. Esto es lento, frágil, y patrones
quedan sin detectar — el bug `canal1_19778` (−$129.24, 4 posiciones
naked sobre un mensaje "GOLD UPDATE" que no era señal) habría aparecido
en segundos con datos consolidados.

**Adicional:** las alertas en tiempo real (`notify()`) están en ~6 sitios
hardcodeados con texto ad-hoc; no llevan severidad ni categoría; el
evento `notify_sent` registra el texto pero no es filtrable. Las
anomalías están dispersas en eventos sueltos (`naked_signal_detected`,
`mt5_action_failed`, `levels_rejected_inconsistent`, `msg_dropped`…) sin
esquema común.

## Objetivos

1. Capturar de forma estructurada todo lo relevante de cada trade.
2. Consolidar la lista de anomalías en un esquema único con `category` y
   `severity`, queryable por categoría/severidad y atribuible por trade.
3. **El ledger pasa a ser el expediente completo por trade** — una fila
   se lee sola y se carga en pandas para detección de patrones en una
   línea.
4. Mantener las alertas en tiempo real como **subproducto** de las
   anomalías críticas (sin duplicar lógica).
5. Trazabilidad de versión del bot (commit git) en cada sesión, para
   análisis antes/después de cada deploy.

## No-objetivos

- **NO** construir un dashboard ni un analizador estable adicional. La
  consolidación de los scripts ad-hoc en tooling fijo es un follow-up;
  con el ledger completo, `pandas` ad-hoc es fiable.
- **NO** pipeline de features para ML.
- **NO** simulación contrafactual nueva (existe `mt5_tick_simulator.py`).
- **NO** redesign de la estrategia (scale_out se mantiene; aquí solo
  se mejora la observabilidad).

## Diseño

### Capa 1 — Captura sin huecos

#### 1.a. `anomaly()` helper en `journal.py`

```python
def anomaly(signal_id: str, category: str, severity: str,
            detail: str, **ctx):
    """Registra una anomalía estructurada. severity='critical' dispara notify()."""
```

- Escribe al journal existente (`trade_events.jsonl`) con `ev="anomaly"`
  y campos `{category, severity, detail, ...ctx}`. **Un solo almacén.**
- Mismo `_file_lock` que `event()`. Nunca lanza al caller (try/except).
- `severity="critical"` invoca `notify()` automáticamente con un mensaje
  formateado: `🚨 [CRITICAL] {category} — {sig_id}\n{detail}\n{ctx…}`.

**Severidades (enum cerrado):**

| Severidad | Significado |
|---|---|
| `info` | Notable, no es problema (ej. abierta sin sticker). |
| `warning` | Subóptimo, el bot lo gestionó (ej. BE imposible → trailing). |
| `critical` | Fallo real, riesgo o dinero evitable (ej. naked, fill fallido). |

**Categorías (enum cerrado, v1):**
`naked`, `sl_be`, `fill`, `channel_msg`, `levels`, `mt5`.

#### 1.b. Migración de los puntos actuales

Los siguientes eventos pasan a llamar también a `anomaly()` (mantienen su
evento descriptivo original por compatibilidad de logs históricos):

| Evento actual | category | severity |
|---|---|---|
| `naked_signal_detected`, `canal1_text_processed_but_naked` | `naked` | `critical` |
| BE imposible → trailing aplicado | `sl_be` | `warning` |
| `mt5_action_failed` retcode 10036 (position closed) | `mt5` | `info` |
| `mt5_action_failed` retcode 10016 estructural | `mt5` | `critical` |
| `levels_rejected_inconsistent` | `levels` | `warning` |
| `msg_dropped reply_to_unknown_signal` (señal cerrada) | `channel_msg` | `info` |
| `msg_dropped` accionable ambiguo | `channel_msg` | `warning` |
| `market_fill_failed` | `fill` | `critical` |

#### 1.c. Eventos nuevos

- **`session_started`** (en `main.py` al arrancar): `{git_commit,
  git_branch, git_dirty, started_utc}`. Permite slice de métricas por
  versión del código (análisis antes/después de cada deploy). `git_dirty`
  marca si la sesión corrió con cambios no committeados — crítico para
  saber qué código se ejecutó realmente.
- **`session_closed`** (al cierre del watcher): `{ended_utc, n_trades,
  anomaly_summary}`.
- **`market_context`** (justo tras `signal_received`): `{atr_m5_14,
  recent_5m_range: [low, high], current_price_at_signal}`. ~5-10ms extra
  por señal. Permite separar fallos de ejecución vs régimen de mercado.

### Capa 2 — El ledger como expediente completo por trade

`reconcile.py` se extiende para que cada fila del ledger lleve todo lo
necesario para diagnosticar el trade sin reconstruir nada externo.

#### Esquema de la fila del ledger (campos nuevos en negrita conceptual)

```jsonc
{
  // Identidad — existe hoy
  "sig_id": "canal1_19778",
  "channel": "canal1",
  "direction": "BUY",
  "signal_dt_utc": "2026-05-19T12:54:45",
  "open_dt_utc": "...",
  "close_dt_utc": "...",
  "duration_min": 47.0,
  "status": "open" | "closed" | "no_position" | "partial",

  // === NUEVO: señal ===
  "signal_text": "**GOLD UPDATE — XAUUSD** Gold is still holding…",
  "trigger": "sticker" | "text_only" | "channel_msg",
  "levels_source": "predicted" | "channel" | "channel_overrode_predicted",
  "tps": [4548, 4550, 4552, 4554],
  "sl": null,
  "range": null,

  // === NUEVO: entrada y calidad ===
  "entry_price": 4540.37,
  "fill_latency_ms": 119,
  "signal_to_fill_ms": 1281,
  "fill_to_range_sec": null,
  "entry_quality": {
    "case": "A_inside" | "B_favorable" | "C_adverse" | null,
    "distance_to_zone_usd": 0.0
  },

  // === NUEVO: contexto de mercado al entrar ===
  "market_context": {
    "atr_m5_14": 1.85,
    "recent_5m_range": [4538.0, 4542.0],
    "current_price_at_fill": 4540.37
  } | null,

  // === EXTENDIDO: posiciones con ciclo de vida ===
  "n_positions": 4,
  "positions": [
    {
      "ticket": 1320899180,
      "role": "market_a" | "scale_out_leg" | "market_b" | "dca" | "rescue",
      "open_dt_utc": "...",
      "open_price": 4540.37,
      "volume": 0.01,
      "sl_history": [
        {"ts": "...", "sl": null, "source": "open"},
        {"ts": "...", "sl": 4525.0, "source": "mgmt:MOVE_SL_TO_PRICE"}
      ],
      "tp_history": [
        {"ts": "...", "tp": 4548.0, "source": "scale_out_assignment"}
      ],
      "close_dt_utc": "...",
      "close_price": 4508.06,
      "close_reason": "TP1" | "SL" | "LOSS_BE" | "MANUAL" | "CLOSE_FIRST" | …,
      "pnl_net": -32.31
    }
    // … resto de legs
  ],

  // === NUEVO: gestión del canal — cronología completa ===
  "management": [
    {
      "ts": "...",
      "raw_text": "GOLD UPDATE…",
      "classified": "INFORMATIONAL" | "MOVE_SL_TO_BE" | …,
      "confidence": 0.95,
      "applied": false,
      "applied_to_tickets": [],
      "skip_reason": "informational" | "ambiguous_target" | "no_open_signal" | …
    }
  ],

  // === NUEVO: anomalías + veredicto ===
  "anomalies": [
    {"ts": "...", "category": "channel_msg", "severity": "critical",
     "detail": "signal opened on non-signal text"},
    {"ts": "...", "category": "naked", "severity": "critical",
     "detail": "position opened without TPs/SL"}
  ],
  "health": "ok" | "degraded" | "failed",

  // === Resultado — existe + extendido ===
  "pnl_real_mt5": -141.24,
  "pnl_journal": null,
  "pnl_discrepancy": null,
  "reconciled_ok": null,
  "pnl_mt5_complete": true,
  "mfe_usd": 1.2,
  "mae_usd": -141.24,
  "tag": "MANUAL",
  "max_tp_idx_touched": null,

  // === NUEVO: cronología de hitos ===
  "timeline": [
    {"ts": "...", "event": "signal_received"},
    {"ts": "...", "event": "market_filled"},
    {"ts": "...", "event": "canal1_parser_incomplete"},
    {"ts": "...", "event": "naked_signal_detected"},
    {"ts": "...", "event": "signal_closed"}
  ],

  // === NUEVO: trazabilidad de versión del bot ===
  "bot_version": {
    "git_commit": "0d285e0",
    "git_branch": "main",
    "git_dirty": false,
    "session_started_utc": "..."
  },

  // Existe hoy — se mantiene por compatibilidad
  "flags": [],
  "reconciled_at": "..."
}
```

#### Cálculo de `health`

Función pura, testeable:

```python
def health_verdict(anomalies: list[dict]) -> str:
    if any(a["severity"] == "critical" for a in anomalies):
        return "failed"
    if any(a["severity"] == "warning" for a in anomalies):
        return "degraded"
    return "ok"
```

#### Cómo se construye cada campo nuevo en `reconcile.py`

`reconcile.py` ya itera por `signal_id` y junta journal+MT5. Se extiende:

1. `anomalies[]` ← eventos `ev="anomaly"` del journal filtrados por sig.
2. `management[]` ← eventos `mgmt_msg` del journal cross-ref con eventos
   de acción posterior (`be_armed_classifier`, `close_first_executed`,
   etc.) en una ventana corta.
3. `positions[i].sl_history` y `.tp_history` ← derivados de los eventos
   de modificación que ya genera el bot tras el fix `12a6cfc`
   (`sl_by_ticket` se rellena en `pending_actions._run` al confirmar).
4. `entry_quality` ← evento `layered_decision`.
5. `market_context` ← evento `market_context` (capturado en Capa 1).
6. `bot_version` ← evento `session_started` cuya ventana cubre el
   `signal_dt_utc`.
7. `timeline` ← lista filtrada de eventos clave por sig.

### Flujo de datos

```
Bot: anomaly() ─┬→ trade_events.jsonl (ev="anomaly", esquema fijo)
                └→ notify() si severity=critical → notify_sent (con sev+cat)
Bot: event(otros) ──→ trade_events.jsonl
MT5: history_deals_get ─┐
                        ├──→ reconcile.py ──→ ledger.jsonl (case file por trade)
trade_events.jsonl ─────┘
```

## Manejo de errores

- `anomaly()` y `event()`: nunca lanzan al caller. I/O fallida → `print`
  a stderr, continúa.
- Cómputo de `market_context`: si MT5 está desconectado o falla la query
  → captura excepción, `market_context: null`. No bloquea la apertura.
- `reconcile.py` enriquecido: cada nuevo cross-reference es defensivo
  (try/except por sección). Una sección que falla no rompe la fila —
  solo la deja con `null` o `[]` en ese campo.

## Tests

1. `anomaly()`: serializa esquema correcto; `critical` dispara `notify()`;
   `warning`/`info` NO disparan.
2. `health_verdict` (función pura): combinaciones de anomalías → ok /
   degraded / failed. Edge cases (lista vacía, mezcla).
3. `reconcile_signal` extendido: dado un journal sintético con
   anomalías + mgmt_msgs + decisions, produce la fila con todos los
   campos nuevos correctamente poblados.
4. `market_context`: mock de MT5 → snapshot correcto.
5. Migración: para cada uno de los 6 puntos migrados, comprobar que el
   evento descriptivo SIGUE emitiéndose Y que se emite el `anomaly` con
   la categoría/severidad esperada.

## Fuera de alcance

- Tooling de análisis estable (consolidación de `analysis/_d19_*.py` en
  un módulo) — follow-up si con el ledger completo no basta.
- Dashboard visual / UI.
- Pipeline de features para ML.
- Simulación contrafactual nueva.
- Cambios de estrategia.

## Notas de implementación

- **Compatibilidad:** los campos existentes del ledger se mantienen
  (incluido `flags`). Los nuevos campos se añaden — consumidores
  existentes (tests, scripts) no se rompen.
- **Idempotencia:** `reconcile.py` regenera el ledger entero — el
  enriquecimiento hereda esa propiedad.
- **Rendimiento:** `market_context` añade ~5-10ms por `signal_received`
  (1 llamada extra a MT5) — aceptable.
- **Migración progresiva:** los 6 puntos pueden migrarse en una sola
  PR; el ledger enriquecido puede ir por trozos (primero anomalies +
  health, luego management, luego market_context, etc.) — cada paso
  produce valor incremental.
