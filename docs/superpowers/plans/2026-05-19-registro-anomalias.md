# Registro de anomalías y expediente por trade — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convertir el sistema de logs en uno estructurado — `anomaly()` con categoría/severidad + el ledger como expediente completo por trade — para que la detección de patrones y anomalías sea una línea de pandas.

**Architecture:** Aditivo sobre infra existente. Un solo almacén (`trade_events.jsonl`); `reconcile.py` enriquecido como punto único de consolidación; la `notify()` actual se reusa, no se duplica.

**Tech Stack:** Python 3.14, pytest (con `monkeypatch`/`tmp_path` para aislar el journal), `MetaTrader5`. Pandas solo en consumo (analizar el ledger), no en runtime.

**Spec:** `docs/superpowers/specs/2026-05-19-registro-anomalias-design.md`

---

## File Structure

| Fichero | Acción | Responsabilidad |
|---|---|---|
| `journal.py` | MODIFY | añadir constantes `SEVERITIES`/`CATEGORIES`, funciones `anomaly()`, `_notify_critical()`, `health_verdict()` |
| `main.py` | MODIFY | emitir `session_started` al arrancar y `session_closed` al cerrar (con `git_commit`, `git_branch`, `git_dirty`); migrar el `_naked_signal_watchdog` para que use `anomaly()` |
| `listener.py` | MODIFY | migrar 3 puntos (`canal1_text_processed_but_naked`, BE-trailing, `msg_dropped` accionable) a `anomaly()`; emitir `market_context` justo tras `signal_received` en los 3 handlers de entrada |
| `pending_actions.py` | MODIFY | en `_log_failure`, emitir `anomaly()` con severidad según retcode (`10036`→`info`, estructural→`critical`) |
| `market_context.py` | CREATE | helper puro `compute_market_context(symbol) -> dict` (ATR M5×14, recent 5m range) — separado para testear con MT5 mocked |
| `reconcile.py` | MODIFY | extender `reconcile_signal` con helpers de rollup para cada campo nuevo del expediente |
| `tests/test_journal.py` | CREATE | tests de `anomaly()` y `health_verdict()` (con journal aislado en `tmp_path`) |
| `tests/test_market_context.py` | CREATE | tests del cómputo de market_context (MT5 mocked) |
| `tests/test_reconcile.py` | EXTEND | tests del enriquecimiento del ledger (anomalies/health/management/timeline/per-leg) |

`dca_monitor.py` no se toca (su único punto candidato es el auto-BE, que no es anomalía).

---

## Task 1: Primitivo de anomalía — `anomaly()` + `health_verdict()`

**Files:**
- Modify: `journal.py` (añadir al final de la sección "API: eventos atómicos")
- Create: `tests/test_journal.py`

**Diseño clave:**
- `anomaly()` valida los enums, llama a `event()` con `ev="anomaly"`, y si `severity=="critical"` dispara `notify()` vía un helper `_notify_critical()` para que sea testeable por monkeypatch.
- `health_verdict()` es función pura sobre `list[dict]` con `severity`.

- [ ] **Paso 1.1 — Test fallando: tests/test_journal.py**

```python
"""Tests de journal.anomaly() y health_verdict() — la capa de anomalias.

Aisla el journal en tmp_path con monkeypatch para no contaminar
data/trade_events.jsonl real (problema observado con test_pending_actions
que escribe en el journal de produccion).
"""
import json

import pytest

import journal


@pytest.fixture
def isolated_journal(tmp_path, monkeypatch):
    """Redirige EVENTS_FILE/JOURNAL_FILE a tmp_path."""
    monkeypatch.setattr(journal, "EVENTS_FILE", tmp_path / "events.jsonl")
    monkeypatch.setattr(journal, "JOURNAL_FILE", tmp_path / "journal.csv")
    return tmp_path / "events.jsonl"


def _events(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


class TestAnomaly:
    def test_writes_schema(self, isolated_journal):
        journal.anomaly("canal1_12345", "naked", "critical",
                        "position opened without SL", ticket=999)
        ev = _events(isolated_journal)[0]
        assert ev["ev"] == "anomaly"
        assert ev["sig"] == "canal1_12345"
        assert ev["category"] == "naked"
        assert ev["severity"] == "critical"
        assert ev["detail"] == "position opened without SL"
        assert ev["ticket"] == 999

    def test_rejects_invalid_severity(self, isolated_journal):
        with pytest.raises(ValueError, match="severity"):
            journal.anomaly("s1", "naked", "OOPS", "x")

    def test_rejects_invalid_category(self, isolated_journal):
        with pytest.raises(ValueError, match="category"):
            journal.anomaly("s1", "OOPS", "info", "x")

    def test_critical_triggers_notify(self, isolated_journal, monkeypatch):
        calls = []
        monkeypatch.setattr(
            journal, "_notify_critical",
            lambda sig, cat, det, ctx: calls.append((sig, cat, det, ctx)))
        journal.anomaly("s1", "naked", "critical", "no SL", ticket=42)
        assert calls == [("s1", "naked", "no SL", {"ticket": 42})]

    def test_warning_does_not_trigger_notify(self, isolated_journal, monkeypatch):
        calls = []
        monkeypatch.setattr(journal, "_notify_critical",
                            lambda *a: calls.append(a))
        journal.anomaly("s1", "sl_be", "warning", "BE imposible")
        assert calls == []


class TestHealthVerdict:
    def test_empty_is_ok(self):
        assert journal.health_verdict([]) == "ok"

    def test_only_info_is_ok(self):
        assert journal.health_verdict([{"severity": "info"}]) == "ok"

    def test_warning_is_degraded(self):
        assert journal.health_verdict([
            {"severity": "info"}, {"severity": "warning"}]) == "degraded"

    def test_critical_is_failed(self):
        assert journal.health_verdict([
            {"severity": "warning"}, {"severity": "critical"}]) == "failed"
```

- [ ] **Paso 1.2 — Correr tests (red)**

```
pytest tests/test_journal.py -q
```
Esperado: 8 fallos (`AttributeError: module 'journal' has no attribute 'anomaly'/...`).

- [ ] **Paso 1.3 — Implementar en `journal.py`** (añadir tras la función `event()`, antes de la sección "API: tracking acumulativo en memoria"):

```python
# ─── API: anomalias (capa estructurada sobre event()) ──────────────────────

SEVERITIES = ("info", "warning", "critical")
CATEGORIES = ("naked", "sl_be", "fill", "channel_msg", "levels", "mt5")


def anomaly(signal_id: str, category: str, severity: str,
            detail: str, **ctx):
    """Registra una anomalia estructurada (capa sobre event()).

    - Escribe ev='anomaly' al journal con esquema fijo {category, severity,
      detail, **ctx}.
    - Si severity='critical' dispara notify() automaticamente.

    Las alertas de Telegram dejan de estar en 6 sitios hardcodeados y
    pasan a ser un subproducto: cualquier anomalia critica notifica.
    Spec: docs/superpowers/specs/2026-05-19-registro-anomalias-design.md
    """
    if severity not in SEVERITIES:
        raise ValueError(f"severity '{severity}' invalida; debe ser una de {SEVERITIES}")
    if category not in CATEGORIES:
        raise ValueError(f"category '{category}' invalida; debe ser una de {CATEGORIES}")
    event(signal_id, "anomaly", category=category, severity=severity,
          detail=detail, **ctx)
    if severity == "critical":
        _notify_critical(signal_id, category, detail, ctx)


def _notify_critical(signal_id: str, category: str, detail: str, ctx: dict):
    """Dispara notify() para una anomalia critica. Defensivo —
    nunca lanza al caller (asyncio.create_task + import lazy)."""
    try:
        import asyncio
        from listener import notify
        lines = "\n".join(f"  {k}: {v}" for k, v in ctx.items()) if ctx else ""
        text = (f"🚨 [CRITICAL] {category} — {signal_id}\n"
                f"{detail}\n"
                f"{lines}".rstrip())
        asyncio.create_task(notify(text))
    except Exception as e:
        print(f"[journal.anomaly] notify failed: {e}")


def health_verdict(anomalies: list[dict]) -> str:
    """Veredicto de salud de un trade segun la severidad maxima de sus
    anomalias. Funcion pura."""
    has_critical = any(a.get("severity") == "critical" for a in anomalies)
    if has_critical:
        return "failed"
    has_warning = any(a.get("severity") == "warning" for a in anomalies)
    if has_warning:
        return "degraded"
    return "ok"
```

- [ ] **Paso 1.4 — Correr tests (green)**

```
pytest tests/test_journal.py -q
```
Esperado: 8 passed.

- [ ] **Paso 1.5 — Suite completa (no regresiones)**

```
pytest tests/ -q
```
Esperado: ≥389 + 8 = 397 passed.

- [ ] **Paso 1.6 — Commit**

```
git checkout -- data/trade_events.jsonl 2>/dev/null
git add journal.py tests/test_journal.py
git commit -m "feat(journal): primitivo anomaly() + health_verdict()

Capa estructurada sobre event(): severity ∈ {info,warning,critical} y
category ∈ {naked,sl_be,fill,channel_msg,levels,mt5}. severity='critical'
dispara notify() automaticamente — alertas como subproducto.

health_verdict([anomalies]) -> ok|degraded|failed: funcion pura para
el rollup por trade en el ledger.

Tests: 8 casos cubriendo esquema, validacion de enums, trigger de notify."
```

---

## Task 2: Eventos de sesión — `session_started`/`session_closed`

**Files:**
- Modify: `main.py` (añadir helper + emitir al arrancar y al cerrar)
- (Tests: cubierto indirectamente; el helper `_git_info()` se puede testear si crece)

**Diseño:** un helper `_git_info()` que lee `git rev-parse HEAD`, `git rev-parse --abbrev-ref HEAD`, y `git status --porcelain` (vacío = clean). Llamado una vez al inicio de `main()`, emite `journal.event("bot", "session_started", git_commit=..., git_branch=..., git_dirty=..., started_utc=...)`. Y un `atexit`/`finally` emite `session_closed`.

- [ ] **Paso 2.1 — Implementar `_git_info()` en `main.py`** (cerca del top, junto a otros helpers):

```python
def _git_info() -> dict:
    """Devuelve la version git de la sesion actual. Best-effort."""
    import subprocess
    def _run(args):
        try:
            return subprocess.check_output(args, cwd=Path(__file__).parent,
                                            stderr=subprocess.DEVNULL,
                                            text=True).strip()
        except Exception:
            return None
    commit = _run(["git", "rev-parse", "--short", "HEAD"])
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    dirty_out = _run(["git", "status", "--porcelain"])
    dirty = bool(dirty_out) if dirty_out is not None else None
    return {"git_commit": commit, "git_branch": branch, "git_dirty": dirty}
```

- [ ] **Paso 2.2 — Emitir `session_started` al arrancar**

Dentro de `async def main()` (o el equivalente bootstrap), tras inicializar el journal/logger:

```python
journal.event("bot", "session_started", **_git_info(),
              started_utc=datetime.utcnow().isoformat(timespec="seconds"))
```

- [ ] **Paso 2.3 — Emitir `session_closed` al cerrar**

En el bloque `finally` del main (o donde se hace el shutdown):

```python
journal.event("bot", "session_closed",
              ended_utc=datetime.utcnow().isoformat(timespec="seconds"))
```

- [ ] **Paso 2.4 — Smoke test manual + suite**

```
pytest tests/ -q
```
Esperado: sigue verde (no se rompió nada — solo añadimos llamadas).

- [ ] **Paso 2.5 — Commit**

```
git checkout -- data/trade_events.jsonl 2>/dev/null
git add main.py
git commit -m "feat(main): eventos session_started/closed con git info

Cada sesion emite session_started con git_commit/branch/dirty. Permite
slice de metricas por version del codigo: 'tras el deploy X mejoro Y%'.
git_dirty marca si la sesion corrio con cambios no committeados."
```

---

## Task 3: Migrar los 6 puntos hardcoded a `anomaly()`

**Files:**
- Modify: `listener.py` (3 puntos: `canal1_text_processed_but_naked`, BE-trailing, `msg_dropped` accionable)
- Modify: `main.py` (`_naked_signal_watchdog`)
- Modify: `pending_actions.py` (`_log_failure`)

**Diseño:** cada punto que hoy hace `journal.event(...)` + `asyncio.create_task(notify(...))` pasa a hacer `journal.anomaly(...)`. Los eventos descriptivos originales se MANTIENEN (compatibilidad de logs históricos) — sólo se añade el `anomaly()` paralelo. La llamada explícita a `notify()` se elimina (la dispara `anomaly()` cuando es critical).

**Tabla de mapeos:**

| Punto actual | Llamada nueva |
|---|---|
| `naked_signal_detected` (`main.py:_naked_signal_watchdog`) | `anomaly(sig, "naked", "critical", "posición abierta sin TPs/SL aplicados por el bot", ticket=..., entry=..., elapsed_s=...)` |
| `canal1_text_processed_but_naked` (`listener.py`) | `anomaly(sig, "naked", "critical", "texto canal1 procesado pero parser no extrajo niveles", ticket=..., text_preview=...)` |
| BE-trailing aplicado (`listener.py`, en MOVE_SL_TO_BE branch tras `be_armed_classifier`) | `anomaly(sig, "sl_be", "warning", "BE imposible — SL trailing aplicado", n_moved=...)` |
| `msg_dropped` con `reason="standalone_mgmt_ambiguous"` | `anomaly(sig, "channel_msg", "warning", "mensaje accionable con destino ambiguo", n_open=..., actions=...)` |
| `mt5_action_failed` retcode 10036 (`pending_actions._log_failure`) | `anomaly(sig, "mt5", "info", "posición ya cerrada al intentar modificar", ticket=..., retcode=10036)` |
| `mt5_action_failed` estructural | `anomaly(sig, "mt5", "critical", "MT5 bloqueado tras N intentos", ticket=..., retcode=..., age_s=...)` |
| `market_fill_failed` (`listener.py`) | `anomaly(sig, "fill", "critical", "executor.open_market devolvió None", trigger=...)` |

- [ ] **Paso 3.1 — Localizar cada punto**

Grep para confirmar los puntos exactos:

```
grep -n "naked_signal_detected\|canal1_text_processed_but_naked\|be_armed_classifier\|standalone_mgmt_ambiguous\|market_fill_failed" listener.py main.py
grep -n "_log_failure\|mt5_action_failed" pending_actions.py
```

- [ ] **Paso 3.2 — `main.py: _naked_signal_watchdog`** (el bloque tras `journal.event(...,"naked_signal_detected",...)` y la `asyncio.create_task(notify(...))`):

ANTES (resumido):
```python
journal.event(full_sig_id, "naked_signal_detected", channel=..., direction=...,
              ticket=..., entry=..., elapsed_s=...)
try:
    from listener import notify
    await notify("🚨 [URGENT] Posicion NAKED detectada — ...")
except Exception as e: ...
sig._naked_alerted = True
```

DESPUÉS:
```python
journal.event(full_sig_id, "naked_signal_detected", channel=...,
              direction=..., ticket=..., entry=..., elapsed_s=...)
journal.anomaly(full_sig_id, "naked", "critical",
                f"posicion abierta hace {elapsed/60:.0f} min sin TPs ni SL "
                f"aplicados por el bot",
                ticket=sig.market_ticket, entry=sig.market_fill_price,
                direction=sig.direction, elapsed_s=round(elapsed, 1))
# (eliminar el try/notify — anomaly() lo hace al ser critical)
sig._naked_alerted = True
```

- [ ] **Paso 3.3 — `listener.py`: las 3 migraciones**

Aplicar el mismo patrón en los 3 puntos del listener (ver tabla arriba). Mantener el `journal.event(..., <evento_descriptivo>, ...)` y AÑADIR debajo el `journal.anomaly(...)` con la categoría/severidad correspondiente. Eliminar las llamadas explícitas a `notify()` que hoy van junto a esos eventos (las críticas ya las dispara `anomaly()`).

- [ ] **Paso 3.4 — `pending_actions.py: _log_failure`** — añadir tras el `journal.event(..., "mt5_action_failed", ...)`:

```python
# Severidad segun naturaleza del fallo:
# - 10036 (POSITION_CLOSED): la pos ya estaba cerrada, benigno.
# - reason.startswith("stops_structural"): MT5 lleva bloqueado N segundos
#   sin poder aplicar el modify → critico, dinero en riesgo.
# - resto (timeout, permanent_error otros retcodes): warning por defecto.
if act.last_retcode == 10036:
    sev = "info"
elif reason.startswith("stops_structural"):
    sev = "critical"
else:
    sev = "warning"
journal.anomaly(sig_id, "mt5", sev,
                f"{act.kind} fallo: {reason}",
                ticket=act.ticket, retcode=act.last_retcode,
                attempts=act.attempts, label=act.label,
                age_seconds=round(time.time() - act.created_at, 1))
```

- [ ] **Paso 3.5 — Suite completa**

```
pytest tests/ -q
```
Esperado: sigue verde (cambios aditivos al log, no cambian lógica).

- [ ] **Paso 3.6 — Commit**

```
git checkout -- data/trade_events.jsonl 2>/dev/null
git add listener.py main.py pending_actions.py
git commit -m "refactor(logs): migrar 6 puntos hardcoded a journal.anomaly()

Naked watchdog, canal1_text_processed_but_naked, BE-trailing,
msg_dropped ambiguo, mt5_action_failed y market_fill_failed pasan a
emitir tambien una anomalia categorizada (severity+category). Los
eventos descriptivos originales se mantienen para compatibilidad de
logs historicos. notify() critica deja de ser explicita: la dispara
anomaly() cuando severity=critical.

Consecuencia: 'da me las alertas criticas del dia' deja de ser grep
de emojis y pasa a ser 'ev=anomaly && severity=critical'."
```

---

## Task 4: `market_context` al entrar

**Files:**
- Create: `market_context.py` (helper puro)
- Create: `tests/test_market_context.py`
- Modify: `listener.py` (llamar en los 3 handlers de entrada)

**Diseño:** un helper `compute_market_context(symbol)` que devuelve `{atr_m5_14, recent_5m_range, current_price_at_signal}` desde MT5. Defensivo — si falla, devuelve `None` y el listener lo loguea como `None`.

- [ ] **Paso 4.1 — Test fallando: `tests/test_market_context.py`**

```python
"""Tests de market_context.compute_market_context — el snapshot que se
captura al recibir senal."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import market_context


@pytest.fixture
def mt5_mock(monkeypatch):
    mt5 = MagicMock()
    monkeypatch.setattr(market_context, "mt5", mt5)
    return mt5


def _bar(high, low, close):
    return SimpleNamespace(high=high, low=low, close=close)


class TestComputeMarketContext:
    def test_happy_path(self, mt5_mock):
        # 14 barras M5 con TR ~1.0 cada una → ATR ~1.0
        bars = [_bar(2001.0, 2000.0, 2000.5) for _ in range(14)]
        mt5_mock.copy_rates_from_pos.return_value = bars
        mt5_mock.symbol_info_tick.return_value = SimpleNamespace(bid=2000.5, ask=2000.7)
        ctx = market_context.compute_market_context("XAUUSD")
        assert ctx is not None
        assert abs(ctx["atr_m5_14"] - 1.0) < 0.01
        assert ctx["recent_5m_range"] == [2000.0, 2001.0]
        assert ctx["current_price_at_signal"] == 2000.6  # mid bid/ask

    def test_mt5_failure_returns_none(self, mt5_mock):
        mt5_mock.copy_rates_from_pos.return_value = None
        assert market_context.compute_market_context("XAUUSD") is None

    def test_exception_returns_none(self, mt5_mock):
        mt5_mock.copy_rates_from_pos.side_effect = RuntimeError("MT5 down")
        assert market_context.compute_market_context("XAUUSD") is None
```

- [ ] **Paso 4.2 — Correr tests (red)**

```
pytest tests/test_market_context.py -q
```
Esperado: ImportError (`market_context` no existe).

- [ ] **Paso 4.3 — Implementar `market_context.py`**

```python
"""market_context.py — snapshot de mercado al recibir una senal.

Capturado en el handler de entrada para que cada trade del ledger lleve
el contexto (ATR M5x14, rango reciente 5m, precio) — permite separar
fallos de ejecucion vs regimen de mercado en el analisis.

Defensivo: si MT5 falla, devuelve None y el caller registra contexto null.
"""
from typing import Optional

import MetaTrader5 as mt5


def compute_market_context(symbol: str) -> Optional[dict]:
    """ATR M5x14 + rango reciente 5m + precio actual.

    None si MT5 esta desconectado o algo falla.
    """
    try:
        # M5_TIMEFRAME = 5; copy_rates_from_pos(symbol, timeframe, start, count)
        bars = mt5.copy_rates_from_pos(symbol, 5, 0, 14)
        if not bars or len(bars) < 2:
            return None

        # ATR = media de True Range; aqui simplificamos a high-low
        # (suficiente para clasificar regimen de volatilidad).
        atr = sum((b.high - b.low) for b in bars) / len(bars)
        # Rango reciente = ultima vela M5
        last = bars[-1]
        recent_range = [round(last.low, 2), round(last.high, 2)]

        # Precio actual: mid bid/ask
        tick = mt5.symbol_info_tick(symbol)
        cur = round((tick.bid + tick.ask) / 2, 2) if tick else None

        return {
            "atr_m5_14": round(atr, 3),
            "recent_5m_range": recent_range,
            "current_price_at_signal": cur,
        }
    except Exception:
        return None
```

- [ ] **Paso 4.4 — Correr tests (green)**

```
pytest tests/test_market_context.py -q
```
Esperado: 3 passed.

- [ ] **Paso 4.5 — Cablear en `listener.py`** — en los 3 handlers de entrada (canal2 new, canal1 sticker, canal1 text-only), inmediatamente tras emitir `signal_received` y antes de `market_filled`:

```python
from market_context import compute_market_context  # import al top
# ...
ctx = await _run(compute_market_context, config.MT5_SYMBOL)
if ctx:
    journal.event(sig_id_pre, "market_context", **ctx)
```

- [ ] **Paso 4.6 — Suite completa**

```
pytest tests/ -q
```
Esperado: sigue verde + 3 nuevos = ≥408 passed.

- [ ] **Paso 4.7 — Commit**

```
git checkout -- data/trade_events.jsonl 2>/dev/null
git add market_context.py tests/test_market_context.py listener.py
git commit -m "feat(observability): market_context al recibir senal

compute_market_context() captura ATR M5x14 + rango reciente 5m + precio
al recibir una senal. Anadido en los 3 handlers de entrada (canal2 new,
canal1 sticker, canal1 text-only). Defensivo — si MT5 falla, ctx=None
y el listener lo loguea sin bloquear la apertura.

Permite al analisis posterior separar 'fallo de ejecucion' de 'regimen
de mercado adverso' en cada trade."
```

---

## Task 5: Enriquecer el ledger Pt. 1 — anomalies + health + bot_version + entry_quality + market_context

**Files:**
- Modify: `reconcile.py` (helpers de rollup + extender la fila)
- Modify: `tests/test_reconcile.py` (tests del enriquecimiento)

**Diseño:** `load_journal_index` ya recoge eventos. Se extiende para que el índice por sig_id capture también: lista de eventos `ev="anomaly"`, el `layered_decision`, el `market_context`. Y un nuevo índice global del `session_started` cuya ventana cubre cada signal_dt. `reconcile_signal` añade los campos al dict de salida.

- [ ] **Paso 5.1 — Tests fallando** — añadir al `tests/test_reconcile.py` un nuevo class:

```python
class TestReconcileSignalEnrichedV1:
    """Rollup de anomalies + health + bot_version + entry_quality + market_context."""

    def test_anomalies_y_health_failed(self):
        journal = {
            "channel": "canal2", "direction": "BUY",
            "signal_dt_utc": "2026-05-20T10:00:00",
            "journal_total_pl": None, "has_signal_closed": False,
            "n_market_filled": 1, "n_market_b_filled": 0, "n_dca_filled": 0,
            "tp_hit_indices": set(),
            "anomalies": [
                {"ts": "2026-05-20T10:00:05", "category": "naked",
                 "severity": "critical", "detail": "no SL"},
            ],
        }
        row = reconcile_signal("canal2_88888", journal, [])
        assert row["anomalies"] == journal["anomalies"]
        assert row["health"] == "failed"

    def test_health_ok_sin_anomalias(self):
        journal = {"channel": "canal2", "direction": "BUY",
                   "signal_dt_utc": "2026-05-20T10:00:00",
                   "journal_total_pl": None, "has_signal_closed": False,
                   "n_market_filled": 0, "n_market_b_filled": 0,
                   "n_dca_filled": 0, "tp_hit_indices": set(),
                   "anomalies": []}
        row = reconcile_signal("canal2_99", journal, [])
        assert row["health"] == "ok"
        assert row["anomalies"] == []

    def test_entry_quality_y_market_context_se_propagan(self):
        journal = {
            "channel": "canal2", "direction": "BUY",
            "signal_dt_utc": "2026-05-20T10:00:00",
            "journal_total_pl": None, "has_signal_closed": False,
            "n_market_filled": 0, "n_market_b_filled": 0,
            "n_dca_filled": 0, "tp_hit_indices": set(),
            "anomalies": [],
            "entry_quality": {"case": "A_inside",
                              "distance_to_zone_usd": 0.0},
            "market_context": {"atr_m5_14": 1.85,
                               "recent_5m_range": [2000.0, 2002.0],
                               "current_price_at_signal": 2001.0},
            "bot_version": {"git_commit": "abc1234", "git_branch": "main",
                            "git_dirty": False,
                            "session_started_utc": "2026-05-20T09:00:00"},
        }
        row = reconcile_signal("canal2_77", journal, [])
        assert row["entry_quality"]["case"] == "A_inside"
        assert row["market_context"]["atr_m5_14"] == 1.85
        assert row["bot_version"]["git_commit"] == "abc1234"
```

- [ ] **Paso 5.2 — Correr tests (red)**

```
pytest tests/test_reconcile.py::TestReconcileSignalEnrichedV1 -q
```
Esperado: KeyError o assert fallido — los campos no existen en la fila.

- [ ] **Paso 5.3 — Implementar en `reconcile.py`**:

A) En `load_journal_index`, al setear el dict por sig_id, añadir claves vacías:
```python
d = index.setdefault(sid, {
    ...,  # campos existentes
    "anomalies": [],
    "entry_quality": None,
    "market_context": None,
    "bot_version": None,
})
```

B) Añadir branches al loop `for line in ...`:
```python
elif ev == "anomaly":
    d["anomalies"].append({
        "ts": e.get("ts"),
        "category": e.get("category"),
        "severity": e.get("severity"),
        "detail": e.get("detail"),
        **{k: v for k, v in e.items()
           if k not in ("ts", "sig", "ev", "category", "severity", "detail")},
    })
elif ev == "layered_decision":
    d["entry_quality"] = {
        "case": e.get("case"),
        "distance_to_zone_usd": round(
            abs((e.get("entry") or 0) - (e.get("range_high") or e.get("entry") or 0)), 2)
        if (e.get("case") or "").startswith(("B_", "C_")) else 0.0,
    }
elif ev == "market_context":
    d["market_context"] = {k: v for k, v in e.items()
                           if k not in ("ts", "sig", "ev")}
```

C) **bot_version**: necesita un pase separado — al final de `load_journal_index`, hacer un segundo pase por los eventos `session_started` y, para cada sig en el índice, encontrar el `session_started` cuyo `started_utc <= sig.signal_dt_utc < next_session_started_utc`. Helper:

```python
def _attach_bot_version(index: dict, sessions: list[dict]):
    """Asocia cada signal con la sesion que corria cuando llego."""
    if not sessions:
        return
    sessions = sorted(sessions, key=lambda s: s.get("started_utc") or "")
    for sid, d in index.items():
        sig_ts = d.get("signal_dt_utc") or ""
        # Ultima sesion iniciada antes (o en) el momento del signal
        match = None
        for s in sessions:
            if (s.get("started_utc") or "") <= sig_ts:
                match = s
            else:
                break
        if match:
            d["bot_version"] = {
                "git_commit": match.get("git_commit"),
                "git_branch": match.get("git_branch"),
                "git_dirty": match.get("git_dirty"),
                "session_started_utc": match.get("started_utc"),
            }
```

Y al cargar el journal, recoger en paralelo `sessions = [...]` cuando `ev == "session_started"`, luego llamar `_attach_bot_version(index, sessions)`.

D) En `reconcile_signal`, añadir al dict de retorno (al final, antes de `"reconciled_at"`):

```python
"anomalies": journal.get("anomalies", []),
"health": health_verdict(journal.get("anomalies", [])),
"entry_quality": journal.get("entry_quality"),
"market_context": journal.get("market_context"),
"bot_version": journal.get("bot_version"),
```

E) Import al top de reconcile.py: `from journal import health_verdict`.

- [ ] **Paso 5.4 — Correr tests (green)**

```
pytest tests/test_reconcile.py -q
```
Esperado: nuevos passed + los anteriores siguen verde.

- [ ] **Paso 5.5 — Regenerar el ledger contra datos reales (smoke real)**

```
python reconcile.py 2>&1 | tail -8
```
Esperado: `>>> Ledger escrito: ... (N trades)` sin errores. Las filas nuevas pueden tener `anomalies: []`, `health: "ok"`, etc. para trades viejos pre-migración — correcto.

- [ ] **Paso 5.6 — Commit**

```
git checkout -- data/trade_events.jsonl 2>/dev/null
git add reconcile.py tests/test_reconcile.py
git commit -m "feat(ledger): enriquecimiento Pt.1 — anomalies+health+ctx

Cada fila del ledger gana: anomalies[] (rollup de ev=anomaly por sig),
health (ok/degraded/failed via journal.health_verdict), entry_quality
(case del layered_decision), market_context (ATR/range al entrar),
bot_version (commit/branch/dirty de la sesion que ejecuto).

Aditivo: campos existentes intactos. Tests + smoke contra journal real."
```

---

## Task 6: Enriquecer el ledger Pt. 2 — per-leg + management + signal_text + timeline

**Files:**
- Modify: `reconcile.py`
- Modify: `tests/test_reconcile.py`

**Diseño:** completar el expediente con los campos restantes.

- **per-leg `sl_history`/`tp_history`**: `pending_actions._run` ya registra `sl_by_ticket` al confirmar un MODIFY (commit `12a6cfc`). Para reconstruir la HISTORIA por ticket reconcile mira los eventos del journal: para cada ticket de la señal, buscar eventos donde aparezca ese ticket en `act_ticket`/`ticket`/`closed_tickets` y registrar `{ts, sl, source}`. Source puede inferirse del evento (`be_armed_classifier` → `mgmt:MOVE_SL_TO_BE`, etc.). Pragmatico: empezar simple — sólo registrar el SL del proveedor (sl_arrived/sl_updated) y los movimientos detectables via `be_armed_classifier`.
- **management[]**: lista de `mgmt_msg` events del sig, cada uno cruzado con la acción posterior detectable. v1 simple: `{ts, raw_text, classified, confidence, applied: will_apply, skip_reason: action si no se aplico}`.
- **signal_text**: del primer evento `signal_received` (canal2 trae `raw_text`; canal1 stick trae texto vacío y se rellena con `canal1_text_processing.text_preview`).
- **timeline**: lista filtrada de hitos. Set fijo: `signal_received`, `market_filled`, `range_arrived`, `tp_hit`, `signal_closed`, `naked_signal_detected`, `mt5_action_failed`, `canal1_parser_incomplete`. Sólo `{ts, event}`.

- [ ] **Paso 6.1 — Tests** (añadir al test_reconcile.py):

```python
class TestReconcileSignalEnrichedV2:
    def test_signal_text_y_timeline(self):
        journal = {
            "channel": "canal2", "direction": "BUY",
            "signal_dt_utc": "2026-05-20T10:00:00",
            "journal_total_pl": 5.0, "has_signal_closed": True,
            "n_market_filled": 1, "n_market_b_filled": 0,
            "n_dca_filled": 0, "tp_hit_indices": {0},
            "anomalies": [],
            "signal_text": "XAU USD BUY NOW",
            "timeline": [
                {"ts": "2026-05-20T10:00:00", "event": "signal_received"},
                {"ts": "2026-05-20T10:00:01", "event": "market_filled"},
                {"ts": "2026-05-20T10:00:30", "event": "tp_hit"},
                {"ts": "2026-05-20T10:00:31", "event": "signal_closed"},
            ],
            "management": [],
        }
        row = reconcile_signal("canal2_44", journal, [])
        assert row["signal_text"] == "XAU USD BUY NOW"
        assert len(row["timeline"]) == 4
        assert row["timeline"][0]["event"] == "signal_received"

    def test_management_se_propaga(self):
        journal = {"channel": "canal2", "direction": "BUY",
                   "signal_dt_utc": "2026-05-20T10:00:00",
                   "journal_total_pl": None, "has_signal_closed": False,
                   "n_market_filled": 0, "n_market_b_filled": 0,
                   "n_dca_filled": 0, "tp_hit_indices": set(),
                   "anomalies": [],
                   "management": [
                       {"ts": "2026-05-20T10:05:00",
                        "raw_text": "Move SL to BE",
                        "classified": "MOVE_SL_TO_BE",
                        "confidence": 0.95, "applied": True,
                        "skip_reason": None}
                   ]}
        row = reconcile_signal("canal2_33", journal, [])
        assert len(row["management"]) == 1
        assert row["management"][0]["classified"] == "MOVE_SL_TO_BE"
```

- [ ] **Paso 6.2 — Correr tests (red)**

```
pytest tests/test_reconcile.py::TestReconcileSignalEnrichedV2 -q
```
Esperado: assert/KeyError — campos no existen.

- [ ] **Paso 6.3 — Implementar en `reconcile.py`**

A) `load_journal_index` — añadir al `setdefault`:
```python
"signal_text": None,
"management": [],
"timeline": [],
```

B) Branches en el loop:
```python
elif ev == "signal_received":
    # ... existente ...
    if e.get("raw_text"):
        d["signal_text"] = e.get("raw_text")
elif ev == "canal1_text_processing":
    if not d.get("signal_text"):
        d["signal_text"] = e.get("text_preview")
elif ev == "mgmt_msg":
    d["management"].append({
        "ts": e.get("ts"),
        "raw_text": e.get("raw_snippet"),
        "classified": e.get("action"),
        "confidence": e.get("confidence"),
        "applied": e.get("will_apply", False),
        "skip_reason": ("ambiguous_notified"
                        if e.get("ambiguous_notified") else None),
    })

# Timeline: hitos clave
_TIMELINE_EVS = {
    "signal_received", "market_filled", "range_arrived", "tp_hit",
    "signal_closed", "naked_signal_detected", "mt5_action_failed",
    "canal1_parser_incomplete", "canal1_text_processed_but_naked",
}
if ev in _TIMELINE_EVS:
    d["timeline"].append({"ts": e.get("ts"), "event": ev})
```

C) En `reconcile_signal`, añadir al dict de retorno:
```python
"signal_text": journal.get("signal_text"),
"management": journal.get("management", []),
"timeline": journal.get("timeline", []),
```

D) **per-leg sl_history** — v1 mínima viable: en `load_mt5_positions`, para cada `position_dict` añadir `"sl_history": []` y `"tp_history": []`. La reconstrucción detallada (cross-ref con `sl_by_ticket` events) queda para una iteración futura — esta v1 sólo deja el campo presente.

- [ ] **Paso 6.4 — Correr tests + smoke**

```
pytest tests/ -q && python reconcile.py 2>&1 | tail -4
```
Esperado: todos green; ledger regenerado sin errores.

- [ ] **Paso 6.5 — Verificación end-to-end** con datos reales

```python
# Quick check en un script ad-hoc o REPL:
import json
rows = [json.loads(l) for l in open("data/ledger.jsonl", encoding="utf-8")
        if l.strip()]
# Una fila reciente debe tener todos los campos nuevos
r = next(r for r in rows if r["sig_id"].startswith("canal2_") and r["status"]=="closed")
print({k: (v if not isinstance(v, list) else f"[{len(v)} items]")
       for k, v in r.items()})
# Confirmar presencia de: anomalies, health, entry_quality, market_context,
# bot_version, signal_text, management, timeline
```

- [ ] **Paso 6.6 — Commit**

```
git checkout -- data/trade_events.jsonl 2>/dev/null
git add reconcile.py tests/test_reconcile.py
git commit -m "feat(ledger): enriquecimiento Pt.2 — management+signal+timeline

Cada fila del ledger gana: signal_text (raw del canal), management[]
(cada mensaje del canal con clasificacion + applied), timeline (hitos
clave del trade), per-leg sl_history/tp_history (v1 vacios, se rellenan
en siguiente iteracion cuando reconcile cruce sl_by_ticket events).

Cierra el spec: el ledger es ya el expediente completo por trade.
Una fila = la historia entera; pandas + el ledger = patrones en una linea."
```

- [ ] **Paso 6.7 — Push final**

```
git push origin main
```

---

## Self-Review (interno antes de entregar)

**Cobertura del spec:**
- ✅ `anomaly()` + severidad + categoría (T1)
- ✅ `critical → notify()` automático (T1)
- ✅ `health_verdict()` (T1)
- ✅ Migración de los 6 puntos (T3)
- ✅ `session_started`/`session_closed` con git info (T2)
- ✅ `market_context` al entrar (T4)
- ✅ `bot_version`, `entry_quality`, `market_context`, `anomalies`, `health` en ledger (T5)
- ✅ `signal_text`, `management`, `timeline`, per-leg lifecycle skeleton en ledger (T6)

**Placeholders:** ninguno (sl_history/tp_history v1 con `[]` está documentado como decisión consciente, no como TBD).

**Consistencia de tipos:** `health` strings consistentes (`ok`/`degraded`/`failed`), severidades y categorías importadas como constantes `journal.SEVERITIES`/`journal.CATEGORIES`.

**Decisión consciente:** la reconstrucción detallada de `sl_history` por ticket queda fuera de v1 (estructura presente vacía). Justificada por YAGNI — la pieza más valiosa (que el cierre por SL movido se etiquete correctamente) ya la dio el fix `12a6cfc`.
