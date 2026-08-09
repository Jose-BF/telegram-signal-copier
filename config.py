import os
from dotenv import load_dotenv
import runtime_paths

load_dotenv()

def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Variable de entorno requerida no configurada: {key}")
    return val

def _int(key: str) -> int:
    return int(_require(key))

def _float(key: str, default: float) -> float:
    val = os.getenv(key)
    return float(val) if val else default


SUPPORTED_ENTRY_MODES = {"scale_out", "market_only"}
DEPRECATED_ENTRY_MODES = {"intra_dca", "extremes"}


def normalize_entry_mode(value: str | None, default: str = "scale_out") -> str:
    """Normaliza el modo de entrada soportado por el runtime vivo actual."""
    fallback = default if default in SUPPORTED_ENTRY_MODES else "scale_out"
    mode = (value or "").strip().lower()
    if mode in SUPPORTED_ENTRY_MODES:
        return mode
    if mode in DEPRECATED_ENTRY_MODES:
        return fallback
    return fallback

# Telegram
TELEGRAM_API_ID   = _int("TELEGRAM_API_ID")
TELEGRAM_API_HASH = _require("TELEGRAM_API_HASH")
TELEGRAM_PHONE    = _require("TELEGRAM_PHONE")

CANAL_1_ID = _int("CANAL_1_ID")
CANAL_2_ID = _int("CANAL_2_ID")

# Google Gemini
GOOGLE_API_KEY = _require("GOOGLE_API_KEY")

# MT5
MT5_LOGIN    = _int("MT5_LOGIN")
MT5_PASSWORD = _require("MT5_PASSWORD")
MT5_SERVER   = _require("MT5_SERVER")
MT5_SYMBOL   = "XAUUSD"

# Magic numbers separados por canal. Permite que el bot distinga al modificar/
# cerrar qué operación vino de qué canal, y que nunca toque la del otro canal
# (ni operaciones manuales, que tendrán magic distinto).
MT5_MAGIC_CANAL1 = 20260421
MT5_MAGIC_CANAL2 = 20260422
MT5_MAGIC = MT5_MAGIC_CANAL2  # alias por compatibilidad (código antiguo)


def magic_for(channel: str) -> int:
    """Devuelve el magic number del canal. Default = canal2 (más activo)."""
    if channel == "canal1":
        return MT5_MAGIC_CANAL1
    return MT5_MAGIC_CANAL2

# Trading
LOT_SIZE = _float("LOT_SIZE", 0.01)
STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL = _float(
    "STRATEGY_MAX_PLANNED_LOTS_PER_SIGNAL", 0.05,
)

# Heartbeat de runtime para el watcher externo. Si el proceso queda vivo pero
# congelado, tools/run_bot_watch.py reinicia el bot al ver este fichero viejo.
BOT_RUNTIME_HEARTBEAT_SEC = _float("BOT_RUNTIME_HEARTBEAT_SEC", 15.0)
BOT_RUNTIME_HEARTBEAT_FILE = os.getenv(
    "BOT_RUNTIME_HEARTBEAT_FILE",
    str(runtime_paths.data_path("runtime_heartbeat.json")),
)
BOT_FREEZE_TRACEBACK_SEC = _float("BOT_FREEZE_TRACEBACK_SEC", 120.0)
BOT_FREEZE_TRACEBACK_FILE = os.getenv(
    "BOT_FREEZE_TRACEBACK_FILE", "logs/freeze_traceback.log")
BOT_JOURNAL_HEARTBEAT_SEC = _float("BOT_JOURNAL_HEARTBEAT_SEC", 900.0)
BOT_CONNECTION_BEAT_SEC = _float("BOT_CONNECTION_BEAT_SEC", 7200.0)
BOT_BROKER_CONTRACT_POLL_SEC = _float(
    "BOT_BROKER_CONTRACT_POLL_SEC", 300.0)
BOT_BROKER_MONEY_CONTRACT_FILE = os.getenv(
    "BOT_BROKER_MONEY_CONTRACT_FILE",
    str(runtime_paths.data_path("broker_money_contract.json")),
)
POSITION_MONITOR_PL_SNAPSHOT_INTERVAL_S = _float(
    "POSITION_MONITOR_PL_SNAPSHOT_INTERVAL_S", 120.0)

# Auditor pasivo en vivo. No toca MT5: cruza state/MT5/pending_actions y deja
# snapshots/anomalies en el journal para poder auditar cada operacion despues.
LIVE_AUDITOR_ENABLED = os.getenv("LIVE_AUDITOR_ENABLED", "1") == "1"
LIVE_AUDITOR_INTERVAL_S = _float("LIVE_AUDITOR_INTERVAL_S", 5.0)
LIVE_AUDITOR_SNAPSHOT_EVERY_S = _float("LIVE_AUDITOR_SNAPSHOT_EVERY_S", 300.0)
LIVE_AUDITOR_LEVEL_APPLY_GRACE_S = _float(
    "LIVE_AUDITOR_LEVEL_APPLY_GRACE_S", 15.0)
LIVE_AUDITOR_NAKED_AFTER_S = _float("LIVE_AUDITOR_NAKED_AFTER_S", 120.0)
LIVE_AUDITOR_NO_POSITION_AFTER_S = _float(
    "LIVE_AUDITOR_NO_POSITION_AFTER_S", 90.0)
LIVE_AUDITOR_NO_POSITION_MISSING_GRACE_S = _float(
    "LIVE_AUDITOR_NO_POSITION_MISSING_GRACE_S", 45.0)
LIVE_AUDITOR_PENDING_STUCK_AFTER_S = _float(
    "LIVE_AUDITOR_PENDING_STUCK_AFTER_S", 30.0)
LIVE_AUDITOR_EXPECTED_LEGS_AFTER_S = _float(
    "LIVE_AUDITOR_EXPECTED_LEGS_AFTER_S", 15.0)


# ─── Estrategias derivadas del análisis del JSON ─────────────────────────────
#
# Toggles para activar/desactivar individualmente cada filtro. Los valores
# por defecto son los que MAXIMIZAN EV según analyze_dca_deep.py sobre 1492
# señales reales (2025+). Para rollback rápido: cambiar el valor a False
# o 0 sin tocar el código.
#
# Resultados esperados con TODO activo (ver analyze_dca_deep.py):
#   • Baseline 4-pos sin filtros:                $+1.710 ($+1,52/sig, MaxDD $3.584)
#   • + skip RE-ENTER + HR/2:                   $+2.005 ($+1,86/sig)
#   • + TIME-STOP 60min (4-pos lot igual):       $+5.552 ($+4,93/sig) ⭐
#

# Skip RE-ENTER signals (point 2): WR=52% vs 65% baseline → +$328 EV
STRATEGY_SKIP_REENTER = os.getenv("STRATEGY_SKIP_REENTER", "1") == "1"

# HIGH RISK: se etiqueta para auditoria, pero ya no reduce lotaje.
# La variable queda solo por compatibilidad con .env antiguos; strategies.py
# fuerza lot x1.0 para evitar que una VM vieja siga reduciendo sin querer.
STRATEGY_HIGH_RISK_LOT_MULT = _float("STRATEGY_HIGH_RISK_LOT_MULT", 1.0)

# TIME-STOP defensivo en minutos (point 7): cierra posiciones que llevan
# >N min sin haber tocado TP/SL → corta perdedoras antes de que se conviertan
# en SL completo. 60min es el óptimo del análisis (+$3.842 EV vs baseline).
#   60 = óptimo (+$5.552 total)
#   90 = +$5.644 total (mejor por $92, prácticamente igual)
#   30 = +$5.250 (más agresivo)
#    0 = desactivado
STRATEGY_TIME_STOP_MIN = int(os.getenv("STRATEGY_TIME_STOP_MIN", "60"))

# Post-SL momentum (point 4): tras un SL rápido (<10 min), la siguiente señal
# tiene P(TP5)=29% (vs base 14%). PERO mandar TODO a TP5 da -$400.
# La estrategia óptima es usar DCA escalonado normal pero capando el TP máximo
# en TP4, evitando esperar a TP5 que rara vez llega → +$1.078 vs +$422 sin cap.
STRATEGY_POST_SL_TP_CAP_ENABLED = os.getenv("STRATEGY_POST_SL_TP_CAP", "1") == "1"
STRATEGY_POST_SL_TP_CAP_INDEX   = int(os.getenv("STRATEGY_POST_SL_TP_CAP_INDEX", "3"))  # TP4
STRATEGY_POST_SL_WINDOW_MIN     = int(os.getenv("STRATEGY_POST_SL_WINDOW_MIN", "30"))
STRATEGY_POST_SL_MAX_DURATION_S = int(os.getenv("STRATEGY_POST_SL_MAX_DURATION_S", "600"))


# ─── Estrategias por canal (validadas IS/OOS 2026 con M1 real) ──────────────
#
# Resultado análisis tick-a-tick analyze_real_price_2026.py (308+490 señales 2026,
# split IS=ene-feb / OOS=mar-abr, costes spread+slip=$0.80/trade, lote 0.01):
#
# CANAL 1 — extremes/2p TP3+TS60 (mejor robusto):
#   IS  : $+1187 avg$+8.48 WR 63% DD$70  Sharpe+0.277 n=140
#   OOS : $+1509 avg$+8.98 WR 70% DD$119 Sharpe+0.421 n=168
#
# CANAL 2 — intra_dca/4p TP4+BE1 (más robusto, BE protege DD):
#   IS  : $+901  avg$+3.92 WR 76% DD$220 Sharpe+0.226 n=230
#   OOS : $+611  avg$+2.35 WR 79% DD$893 Sharpe+0.042 n=260
#
# AVISO: Las estrategias TP2 catastróficas en OOS (-$4200) confirman que el
# régimen de marzo-abril 2026 difiere del de ene-feb. Las TP3/TP4 sobreviven
# pero con expectativa OOS reducida vs IS. 4 meses no es muestra grande.

# ─── Estrategia consensuada para semana de prueba (jue 23/abr → mié 29/abr) ──
#
# Decisiones acordadas tras descartar simulación masiva:
#   • Lote conservador 0.01 (sin partial close por ahora)
#   • Histórico: intra_dca con limits. Runtime actual: scale_out sin limits.
#   • TPs ESCALONADOS por orden de apertura (target_tp_index=-1):
#       ticket 1 → TP1, ticket 2 → TP2, …, ticket 5 → TP5
#   • Sin BE automático: el classifier aplica los mensajes de gestión del canal
#   • Time-stop 60min: NO cierra solo, NOTIFICA al usuario por Telegram con
#     una recomendación contextual; el usuario decide qué hacer
#   • Caso C adverse: nueva acción "rescue_market" — mantener original + abrir
#     market en precio adverso (entrada óptima); SL común; TPs por calidad de
#     entrada (original→TP1, rescue→último TP disponible)

# CANAL 1 — modo scale_out (semana de prueba 2026-05-17).
# Al llegar la senal se abren NUM_ENTRIES posiciones market de LOT_SIZE de
# golpe (sin DCA, sin doble market). TPs escalonados: pos k -> TPk. Cada
# posicion se cierra en su TP -> "scaling out" / cierres parciales.
# Canal 1 suele tener 4 TPs -> 4 legs -> 0.04 lote total.
# Valores legacy DCA en .env se normalizan a scale_out en runtime.
STRATEGY_C1_ENTRY_MODE      = normalize_entry_mode(os.getenv("STRATEGY_C1_ENTRY_MODE", "scale_out"))
STRATEGY_C1_NUM_ENTRIES     = int(os.getenv("STRATEGY_C1_NUM_ENTRIES", "4"))
STRATEGY_C1_TARGET_TP_INDEX = int(os.getenv("STRATEGY_C1_TARGET_TP_INDEX", "-1"))  # -1 = escalonado
STRATEGY_C1_TIME_STOP_MIN   = int(os.getenv("STRATEGY_C1_TIME_STOP_MIN", "60"))    # notify only
STRATEGY_C1_BE_TP_INDEX     = int(os.getenv("STRATEGY_C1_BE_TP_INDEX", "-1"))      # -1 = sin BE

# Demo forward trial: protect the aggregate MT5 P/L of each Dubai signal.
# Values are in the account currency reported directly by MT5.
STRATEGY_C1_BASKET_GUARD_ENABLED = (
    os.getenv("STRATEGY_C1_BASKET_GUARD_ENABLED", "1") == "1"
)
STRATEGY_C1_BASKET_LOSS_CAP = _float(
    "STRATEGY_C1_BASKET_LOSS_CAP", -50.0)
STRATEGY_C1_BASKET_PROFIT_ARM = _float(
    "STRATEGY_C1_BASKET_PROFIT_ARM", 30.0)
STRATEGY_C1_BASKET_PROFIT_LOCK = _float(
    "STRATEGY_C1_BASKET_PROFIT_LOCK", 20.0)
STRATEGY_C1_BASKET_GUARD_POLL_S = _float(
    "STRATEGY_C1_BASKET_GUARD_POLL_S", 0.5)

# CANAL 2 — modo scale_out (semana de prueba 2026-05-17). Igual que C1:
# NUM_ENTRIES posiciones market de golpe, TPs escalonados (pos k -> TPk),
# cierres parciales segun el precio toca cada TP. Sin DCA, sin doble market.
# Canal 2 suele tener 5 TPs -> 5 legs -> 0.05 lote total.
# BE@TP1 ACTIVADO (default 0): al tocar TP1, BE mueve el SL de las legs
# restantes a su entry -> asegura TP1 y el resto corre sin riesgo. El
# escalonado lo activa target_tp_index=-1.
# Valores legacy DCA en .env se normalizan a scale_out en runtime.
STRATEGY_C2_ENTRY_MODE      = normalize_entry_mode(os.getenv("STRATEGY_C2_ENTRY_MODE", "scale_out"))
STRATEGY_C2_NUM_ENTRIES     = int(os.getenv("STRATEGY_C2_NUM_ENTRIES", "5"))
STRATEGY_C2_TARGET_TP_INDEX = int(os.getenv("STRATEGY_C2_TARGET_TP_INDEX", "-1"))  # -1 = escalonado
STRATEGY_C2_BE_TP_INDEX     = int(os.getenv("STRATEGY_C2_BE_TP_INDEX", "0"))       # 0 = BE en TP1
STRATEGY_C2_TIME_STOP_MIN   = int(os.getenv("STRATEGY_C2_TIME_STOP_MIN", "60"))    # notify only

# Gold Signals zone plans are observed on first touch, but only an explicit
# provider activation opens exposure by default. Set to 1 to restore the
# previous first-touch execution policy without changing code.
STRATEGY_C2_ZONE_FIRST_TOUCH_EXECUTION_ENABLED = (
    os.getenv("STRATEGY_C2_ZONE_FIRST_TOUCH_EXECUTION_ENABLED", "0") == "1"
)

# ─── Estrategia C: CLOSE_FIRST canal2 — rescate BE ──────────────────────────
# Cuando el canal 2 manda "close first entries" SIN que la posición esté en
# profit, en vez de cerrar a mercado en pérdida ponemos TP=BE en todas las
# posiciones (aprovecha rebotes rápidos al entry) y armamos un time-stop:
# si tras N segundos quedan posiciones abiertas, se cierran a mercado.
# Análisis 19+20 may: rescata canal2_12691 (−$26.55 → ~−$0.50).
#   STRATEGY_C2_CLOSE_FIRST_BE_ENABLED   : 1 activa la rama de rescate.
#   STRATEGY_C2_CLOSE_FIRST_BE_TIMEOUT_S : segundos antes del cierre a mercado.
#   STRATEGY_C2_CLOSE_FIRST_PROFIT_PTS   : profit (pts) por encima del cual se
#       usa la lógica clásica (cerrar mitad) en lugar del rescate BE.
STRATEGY_C2_CLOSE_FIRST_BE_ENABLED   = os.getenv("STRATEGY_C2_CLOSE_FIRST_BE_ENABLED", "1") == "1"
STRATEGY_C2_CLOSE_FIRST_BE_TIMEOUT_S = int(os.getenv("STRATEGY_C2_CLOSE_FIRST_BE_TIMEOUT_S", "60"))
STRATEGY_C2_CLOSE_FIRST_PROFIT_PTS   = float(os.getenv("STRATEGY_C2_CLOSE_FIRST_PROFIT_PTS", "0.5"))

# Guard semantico para mensajes tipo "close at breakeven / risk free".
# Si el canal pide cerrar/proteger en BE pero NUESTRA posicion real esta
# perdiendo mas que esta tolerancia, no cerramos a mercado: armamos TP=BE
# con time-stop. Los cierres explicitos "close all now" no pasan por este guard.
STRATEGY_BE_CLOSE_NEGATIVE_GUARD_ENABLED = (
    os.getenv("STRATEGY_BE_CLOSE_NEGATIVE_GUARD_ENABLED", "1") == "1"
)
STRATEGY_BE_CLOSE_NEGATIVE_TOLERANCE_USD = _float(
    "STRATEGY_BE_CLOSE_NEGATIVE_TOLERANCE_USD", 2.0)
STRATEGY_BE_CLOSE_RESCUE_TIMEOUT_S = int(os.getenv(
    "STRATEGY_BE_CLOSE_RESCUE_TIMEOUT_S",
    str(STRATEGY_C2_CLOSE_FIRST_BE_TIMEOUT_S)))

# ─── Double Market: doble entrada inicial con TPs distintos ─────────────────
#
# Cambio 2026-05-13. Inquietud del usuario: "muchas veces se llega a TP1, TP2
# o TP3 pero solo se ha abierto una operacion. Subiendo lotaje y dejandolas
# correr se podria sacar mas ganancia."
#
# Datos del journal real (74 trades): solo 4 de 41 wins (10%) capturaron mas
# alla de TP1. El otro 90% cobro pequeno y se quedo mirando como el precio
# seguia subiendo.
#
# Estructura nueva (cuando flag activo):
#   Pos A market (LOT_SIZE) → TP=TP1 (asegura ganancia rapida)
#   Pos B market (LOT_SIZE) → TP=TP_INDEX_B (deja correr al medio plazo)
#   DCAs limit (NUM_ENTRIES-1) → TPs escalonados (capturan mechazos)
#
# Cuando precio toca TP1 → BE@TP1 mueve SL de TODAS las pos a su entry.
#
# La Pos B usa el mecanismo `tp_overrides` (mismo patron que rescue_market).
# Si el flag esta off, comportamiento legacy (1 sola pos market).
# 2026-05-17: OFF por defecto — sustituido por el modo scale_out (ver arriba).
STRATEGY_DOUBLE_MARKET_ENABLED  = os.getenv("STRATEGY_DOUBLE_MARKET_ENABLED", "0") == "1"
STRATEGY_DOUBLE_MARKET_TP_INDEX = int(os.getenv("STRATEGY_DOUBLE_MARKET_TP_INDEX", "2"))  # TP3 (0-indexed)

# Time-stop comportamiento: 1 = solo notificar al usuario (no cerrar);
#                           0 = cerrar automáticamente (modo legacy).
STRATEGY_TIME_STOP_NOTIFY_ONLY = os.getenv("STRATEGY_TIME_STOP_NOTIFY_ONLY", "1") == "1"

# Si una senal queda abierta sin TP/SL por fallo de canal/Telegram, el watchdog
# aplica un SL provisional desde entry. No pone TP: evita limitar ganadoras.
STRATEGY_NAKED_PROTECTIVE_SL_ENABLED = (
    os.getenv("STRATEGY_NAKED_PROTECTIVE_SL_ENABLED", "1") == "1"
)
STRATEGY_NAKED_PROTECTIVE_SL_OFFSET_USD = _float(
    "STRATEGY_NAKED_PROTECTIVE_SL_OFFSET_USD", 10.0)

# Canal2 a veces publica dos BUY/SELL NOW casi seguidos y edita solo el
# segundo. En esa ventana aliasamos el segundo message_id al trade ya abierto.
STRATEGY_C2_DUPLICATE_ALIAS_WINDOW_S = _float(
    "STRATEGY_C2_DUPLICATE_ALIAS_WINDOW_S", 10.0)

# Canal1 a veces publica dos stickers iguales casi seguidos y luego manda el
# texto de niveles solo para el segundo. Abrimos una vez y aliasamos el duplicado.
STRATEGY_C1_DUPLICATE_STICKER_WINDOW_S = _float(
    "STRATEGY_C1_DUPLICATE_STICKER_WINDOW_S", 5.0)

# Si perdemos el NewMessage de Canal2 durante un restart pero recibimos un edit
# fresco con BUY/SELL NOW, se puede recuperar como entrada. Cutoff defensivo
# para no abrir senales viejas al arrancar tras una caida larga.
STRATEGY_C2_ORPHAN_EDIT_MAX_AGE_S = _float(
    "STRATEGY_C2_ORPHAN_EDIT_MAX_AGE_S", 180.0)

# Cutoff global para abrir entradas market nuevas desde mensajes atrasados de
# Telegram. Updates/replies de senales ya abiertas se siguen procesando.
STRATEGY_ENTRY_MAX_TG_DELAY_S = _float(
    "STRATEGY_ENTRY_MAX_TG_DELAY_S", 120.0)

# Si MT5 devuelve un retcode ambiguo en una apertura market pero incluye
# order/ticket (visto: 10012 Request timeout), comprobamos si la posicion
# existe antes de declarar fallo. No reintenta la orden: evita duplicados.
STRATEGY_MARKET_OPEN_ORDER_PROBE_ENABLED = (
    os.getenv("STRATEGY_MARKET_OPEN_ORDER_PROBE_ENABLED", "1") == "1"
)
STRATEGY_MARKET_OPEN_ORDER_PROBE_ATTEMPTS = int(
    os.getenv("STRATEGY_MARKET_OPEN_ORDER_PROBE_ATTEMPTS", "5"))
STRATEGY_MARKET_OPEN_ORDER_PROBE_SLEEP_S = _float(
    "STRATEGY_MARKET_OPEN_ORDER_PROBE_SLEEP_S", 0.5)

# Tolerancia (en USD) para clasificar A_inside cuando el entry está cerca de
# un extremo del rango. Si |entry - rango| ≤ tolerancia → tratamos como A.
# Evita que 0.45$ de diferencia por ruido de spread caiga en B_favorable o
# C_adverse y aplique lógica equivocada.
STRATEGY_RANGE_TOLERANCE_USD = _float("STRATEGY_RANGE_TOLERANCE_USD", 1.0)

# Separación mínima (USD) entre el fill de UN DCA y el fill ANTERIOR (market
# o DCA previo) para considerar la apertura útil. Cuando el precio se mueve
# muy rápido (dump/spike), varios niveles consecutivos pueden ejecutarse en
# el mismo tick → fills a $0.06 de distancia entre sí, sin promediar nada.
# Visto en sesión 28-abr: canal2_12006 abrió 2 posiciones a $0.06 y canal2_12003
# abrió 2 posiciones a $0.32. Estos fills "redundantes" duplican la pérdida
# sin mejorar la entrada media.
#
# Comportamiento: si el siguiente DCA fill quedaría a < SEPARATION del fill
# anterior, se DESCARTA ese nivel (se hace pop del pending sin abrir orden).
# El monitor sigue vigilando los niveles más alejados.
#
# MIN_SEPARATION ahora es DINAMICO (cambio 2026-05-15, Commit C):
# El umbral efectivo = max(SEPARATION_USD floor, range_width * SEPARATION_PCT).
# Razon: 0.5 fijo era demasiado estricto — saltaba mechazos legitimos a
# $0.40 de distancia. Con rangos tipicos de $4-5, el dinamico sale ~0.25,
# capturando el doble de mechazos sin reintroducir fills redundantes.
#
# 0 en el floor = desactivado (legacy: cualquier fill consecutivo se acepta).
STRATEGY_DCA_MIN_SEPARATION_USD = _float("STRATEGY_DCA_MIN_SEPARATION_USD", 0.25)
STRATEGY_DCA_MIN_SEPARATION_PCT = _float("STRATEGY_DCA_MIN_SEPARATION_PCT", 0.05)

# Timeout (segundos) del defer anti-TP-duplicado en position_lifecycle_monitor. Cuando un
# nivel DCA se toca pero aun no hay TP escalonado para esa posicion (el canal
# manda TPs en oleadas), el monitor DIFIERE la apertura esperando mas TPs.
# Si tras este timeout no llegan, abre IGUAL con el ultimo TP disponible
# (TP duplicado es mejor que perder el DCA — canal2_12379 perdio -$16.49
# por no abrir DCAs cuando el canal mando TP1 solo primero).
STRATEGY_DCA_DEFER_TIMEOUT_S = _float("STRATEGY_DCA_DEFER_TIMEOUT_S", 3.0)

# ─── Layered logic Etapa 2 — qué hacer al llegar el rango ───────────────────
#
# Cuando llega el rango (texto C1 ~2min después del sticker, edits C2 segundos
# después del "BUY NOW"), evaluamos dónde quedó el precio respecto al rango:
#
#   • Caso A (dentro del rango ± STRATEGY_RANGE_TOLERANCE_USD):
#     aplica SL/TPs reales y continua con scale_out/monitor si procede.
#
#   • Caso B (fuera FAVORABLE — la posición ya está en profit):
#     misma acción que A para C1 y C2 (b1) — abrir DCAs y aplicar SL/TPs.
#     Aceptamos el riesgo de TP1 ajustado en C2.
#
#   • Caso C (fuera ADVERSO — la posición está en pérdida):
#     - "rescue_market"      → mantener original + abrir market en precio
#                              actual (entrada óptima); SL común; TPs por
#                              calidad: original→TP1, rescue→último TP.
#                              ⭐ Default consensuado para semana de prueba.
#     - "close"              → cierra el market (legacy safety)
#     - "hold_with_limits"   → legacy desactivado; se trata como hold_no_limits
#     - "hold_no_limits"     → mantiene market sin limits (SL del proveedor)
#     - "hold_sl_to_extreme" → mantiene market con SL al extremo del rango
STRATEGY_C1_ADVERSE_ACTION = os.getenv("STRATEGY_C1_ADVERSE_ACTION", "rescue_market")
STRATEGY_C2_ADVERSE_ACTION = os.getenv("STRATEGY_C2_ADVERSE_ACTION", "rescue_market")

# Canal 1 stickers (opcionales hasta primer arranque)
CANAL1_BUY_STICKER_ID  = int(os.getenv("CANAL1_BUY_STICKER_ID"))  if os.getenv("CANAL1_BUY_STICKER_ID")  else None
CANAL1_SELL_STICKER_ID = int(os.getenv("CANAL1_SELL_STICKER_ID")) if os.getenv("CANAL1_SELL_STICKER_ID") else None

# Canal de pruebas (opcional) — si se configura, el bot procesa mensajes de ese
# canal como si fueran del Canal 1 o Canal 2 según el contenido/prefijo.
TEST_CHANNEL_ID = int(os.getenv("TEST_CHANNEL_ID")) if os.getenv("TEST_CHANNEL_ID") else None

# Canal/usuario de notificaciones del bot (time-stop, eventos críticos, etc.).
# Si no se configura, el bot envía a "me" (Saved Messages del propio usuario).
# Acepta un ID numérico (-100xxxx para canales) o "me".
NOTIFY_CHAT_ID = os.getenv("NOTIFY_CHAT_ID", "me")

# Token del bot Telegram que envía notificaciones (bot cangrejo).
# Si está configurado, notify() usa Bot HTTP API en lugar de client.send_message().
# Ventaja: un bot sí dispara push notifications en mobile; el cliente Telethon no.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Alertas graficas para situaciones que requieren revision humana. Son una
# capa de presentacion: si faltan Pillow/ticks/Telegram, se envia el texto.
REVIEW_ALERT_GRAPH_ENABLED = (
    os.getenv("REVIEW_ALERT_GRAPH_ENABLED", "1") == "1"
)
REVIEW_ALERT_GRAPH_WINDOW_MIN = _float(
    "REVIEW_ALERT_GRAPH_WINDOW_MIN", 15.0)
REVIEW_ALERT_GRAPH_BUILD_TIMEOUT_S = _float(
    "REVIEW_ALERT_GRAPH_BUILD_TIMEOUT_S", 3.0)
REVIEW_ALERT_GRAPH_SEND_TIMEOUT_S = _float(
    "REVIEW_ALERT_GRAPH_SEND_TIMEOUT_S", 8.0)
