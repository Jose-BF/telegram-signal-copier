"""
Clasifica mensajes de gestión de operaciones.

Devuelve una LISTA de acciones (un mensaje puede contener varias):
  - "TP1 hit. Move SL to BE" → [INFORMATIONAL, MOVE_SL_TO_BE]
  - "Move SL to 4750"        → [MOVE_SL_TO_PRICE]
  - "Close all"              → [CLOSE_ALL]

Pipeline:
  1. Regex local (sin coste, batería ampliada con frases reales del histórico).
  2. Si el regex no encuentra NADA, fallback a Gemini 2.5 Flash con retry+backoff.
  3. Si Gemini falla todos los intentos → devolvemos INFORMATIONAL pero
     marcamos `_gemini_failed` para que el journal lo registre.
"""

import asyncio
import re
import json
import time
from google import genai
import config
from interpretation_firewall import (
    extract_provider_stated_be_price,
    normalize_classifier_outputs,
)

_client = genai.Client(api_key=config.GOOGLE_API_KEY)

# ───────────────────────────────────────────────────────────────────────────
# Prompt LEGACY (sin contexto) — usado cuando classify() se llama sin signal.
# Se mantiene por backward compat, pero CONTEXTUAL es el camino preferido.
# ───────────────────────────────────────────────────────────────────────────
_PROMPT_TEMPLATE = """You are a trading signal classifier for a gold (XAUUSD) Telegram channel.
The message MAY contain MULTIPLE actions. Return a JSON ARRAY of every action present.
Respond ONLY with valid JSON, no extra text or markdown.

Message: "{msg}"

Available actions:
- CLOSE_ALL        → close ALL positions for this trade
- CLOSE_FIRST      → close the FIRST/EARLIEST entries
- CLOSE_AT_TP      → close position at specific TP (price = tp number 1..5)
- CLOSE_PARTIAL    → record that the provider took partial profit; do not
                     invent a live close quantity
- SECURE_BASKET    → close only mathematically proved partials so the
                     remaining basket cannot lose at its installed stops
- MOVE_SL_TO_BE    → explicit instruction to move stop loss to entry
- MOVE_SL_TO_PRICE → move stop loss to specific price (price = the number)
- INFORMATIONAL    → no action needed (TP/SL hit announcements, status, commentary)

JSON format: [{{"action": "ACTION", "price": null_or_number, "confidence": 0.0_to_1.0}}, ...]

If purely informational, return [{{"action": "INFORMATIONAL", "price": null, "confidence": 1.0}}].
If you cannot understand the message, return []."""


# ───────────────────────────────────────────────────────────────────────────
# Prompt CONTEXTUAL — usado cuando hay un Signal asociado (caso normal en
# producción para mgmt msgs durante un trade activo).
#
# Diferencias vs LEGACY:
#  • Incluye estado del trade (dirección, P&L, posiciones, TPs/SL, tiempo)
#  • Tiene ejemplos de frases típicas del trader (ambos canales)
#  • Acciones extendidas: PROTECT_AND_NOTIFY, HIGH_RISK_WARNING, SIGNAL_UPDATED
#  • Pide reasoning en cada acción para debug/journal
#  • Bias conservador explícito
# ───────────────────────────────────────────────────────────────────────────
_PROMPT_CONTEXTUAL = """You are a trading assistant for XAUUSD signals on Telegram.
The trader writes both NARRATIVE messages (canal 1, e.g. DT Investing) and
STRUCTURED notifications (canal 2). Your job: extract any actionable trading
instruction. Respond ONLY with valid JSON, no markdown.

═══ CURRENT TRADE CONTEXT ═══
Channel:        {channel}
Direction:      {direction}
Entry price:    {entry_price}
Current TPs:    {tps}
Current SL:     {sl}
Open positions: {n_open} of {n_initial} originally opened
Time elapsed:   {elapsed_min} min
Floating P&L:   {floating_pnl} USD
Current price:  {current_price}
BE armed:       {be_armed}

═══ TRADER'S TYPICAL PHRASES ═══
EXPLICIT ACTIONS:
  "Move SL to BE" → MOVE_SL_TO_BE (high conf)
  "Make the trade risk free" / "0% risk" → SECURE_BASKET (high conf)
  "Close all" / "out of trade" / "closing our last entries" → CLOSE_ALL (high conf)
  "Move SL to 4750" / "adjust stop to 4750" → MOVE_SL_TO_PRICE (price=4750)
  "Close first entry" / "close early entries" → CLOSE_FIRST (high conf)
  "Setup is no longer valid" / "trade invalidated" → CLOSE_ALL (high conf)
  "Reenter now SL to 4336" → REENTRY_SIGNAL (review; do not invent entries)

CHANGED LEVELS (signal still active but TPs/SL different):
  "TP1 was shared wrong. Please change TP1 to 4134" → LEVEL_CORRECTION
  "TP1 4296.50 SL 4308" as reply to a signal → LEVEL_UPDATE
  "Entries have changed, check them again" → ENTRY_UPDATE
  "New entry zones" / "different SL now" → ENTRY_UPDATE

WARNINGS (no direct action, just info):
  "High risk trade 🚨" → HIGH_RISK_WARNING (the bot already labeled at signal start)
  "Don't add more entries" / "stay out for now" → MARKET_COMMENTARY
    (the bot doesn't add positions on its own; future signals processed normally)

NON-EXECUTABLE INTENTS (classify precisely; no direct MT5 action):
  "TP1 hit" / "TP4 smashed" → TP_HIT_ANNOUNCEMENT
  "SL HIT" / "stop loss hit" → SL_HIT_ANNOUNCEMENT
  "+50 pips" / "running 90+ pips" → PROGRESS_UPDATE
  "Out at breakeven" / "B/E" as result/summary → BE_ANNOUNCEMENT
  Daily/weekly recaps → DAILY_SUMMARY or WEEKLY_SUMMARY
  Gold reacted from our zone / market commentary → MARKET_COMMENTARY

CONDITIONAL (CRITICAL — never auto-act on conditions):
  Any message with structure "If X then Y" / "Watch X, then we will Y" /
  "If 15M closes above N, we will close" → CONDITIONAL_PLAN.

  The bot CANNOT watch sub-timeframe candle closes, sub-second price
  triggers, or any other condition that's not a direct order. The trader
  WILL send a follow-up message with the actual order if the condition
  triggers. UNTIL that follow-up arrives, treat the conditional as
  pure information.

  Examples (all → CONDITIONAL_PLAN, conf 0.95):
    "If 15M closes above 4700, we will close this trade"
    "Watch the 15M candle close now"
    "If gold breaks 4700, setup invalid"
    "If price closes above X, we will reverse"
    "If we lose this level we exit"

  KEY DISTINCTION:
    "we will close" + condition (If/Watch/When) → CONDITIONAL_PLAN
    "we are closing" / "closing now" / "close" (imperative) → CLOSE_ALL

CRITICAL EXAMPLE (caso real canal1_19649, sesion 2026-05-13):
  Trader message:
    "Guys, watch the 15M candle close now.
     If the 15M closes above 4700, we will close this trade around entry
     and wait for a better re-entry."

  CORRECT:
    {{"message_role": "conditional_plan", "actions": [],
      "is_conditional": true, "is_optional": false,
      "requires_review": false,
      "reasoning": "If 15M closes above 4700 then close; wait for follow-up order"}}

  WRONG (lo que el bot regex hizo, perdio -$9.94):
    [{{"action": "CLOSE_ALL", "confidence": 0.90}}]
  Why wrong: the regex matched "close this trade" but ignored the "If"
  prefix. There's NO unconditional close order here — the trader is
  setting up a watching scenario. The bot must NOT pre-empt the condition.

═══ AMBIGUOUS / PROTECTIVE ═══
"Secure profits if you're satisfied" / "protect your trade" without explicit
action → OPTIONAL_SUGGESTION or AMBIGUOUS/UNKNOWN (review; no direct MT5 action).

═══ IMPERATIVE vs OPTIONAL (CRITICAL — read carefully) ═══
The trader sometimes mixes ORDERS and SUGGESTIONS in the same message.
Distinguish them carefully:

  ORDER (must execute, strong verb):
    "Set SL at 4685" → MOVE_SL_TO_PRICE (price=4685)
    "Move SL to BE" → MOVE_SL_TO_BE
    "Close all now" → CLOSE_ALL
    "Closing the trade" → CLOSE_ALL

  OPTIONAL (sugiere a sub-grupo de traders, NO ejecutar):
    "you can close now"  → IGNORE this, NOT a CLOSE_ALL order
    "feel free to close"  → IGNORE
    "if you want, exit"  → IGNORE
    "for those who don't want more risk, you can ..."  → IGNORE the suggestion
    "anyone who is uncomfortable can close"  → IGNORE
    "members who are out, ..."  → IGNORE the conditional

KEY RULE: Phrases starting with "you can / if you want / feel free / for
anyone / for those who" indicate OPTIONAL action for SOME traders, NOT a
universal order. Extract ONLY the imperative actions, ignore the optional
suggestions.

CRITICAL EXAMPLE (real case sesion 2026-05-12, lost -$8.58):
  Trader message:
    "Give gold a bit more room now.
     Set SL at **4685.00**.
     For anyone who doesn't want to take more risk, you can close now."

  CORRECT classification:
    [{{"action": "MOVE_SL_TO_PRICE", "price": 4685.00, "confidence": 0.95,
      "reasoning": "Imperative: Set SL at 4685"}}]

  WRONG (what the bot did before this fix):
    [{{"action": "CLOSE_ALL", ...}}]
  Why wrong: "you can close" is a suggestion for traders who want out,
  NOT an order to close all positions. The order is "Set SL at 4685"
  (give the trade more room).

If a message has both an imperative AND an optional close suggestion,
ALWAYS prioritize the imperative. The optional close is just info for
risk-averse subscribers; the bot follows the trader's main directive.

═══ MESSAGE TO CLASSIFY ═══
"{msg}"

Return ONE JSON object, no markdown. The bot will normalize this contract and
run every action through a firewall before MT5:
{{
  "message_role": "direct_order|conditional_plan|optional_suggestion|daily_summary|weekly_summary|progress_update|market_commentary|media_companion|unknown",
  "actions": [
    {{
      "type": "CLOSE_ALL|CLOSE_FIRST|CLOSE_AT_TP|CLOSE_PARTIAL|SECURE_BASKET|MOVE_SL_TO_BE|MOVE_SL_TO_PRICE|LEVEL_UPDATE|LEVEL_CORRECTION|ENTRY_UPDATE|REENTRY_SIGNAL|CONDITIONAL_PLAN|OPTIONAL_SUGGESTION|TP_HIT_ANNOUNCEMENT|SL_HIT_ANNOUNCEMENT|BE_ANNOUNCEMENT|PROGRESS_UPDATE|DAILY_SUMMARY|WEEKLY_SUMMARY|MARKET_COMMENTARY|HIGH_RISK_WARNING|UNKNOWN",
      "price": null_or_number,
      "confidence": 0.0_to_1.0,
      "target": "all_open_positions|first_entries|single_position|none",
      "evidence": "exact words that justify this interpretation"
    }}
  ],
  "is_conditional": true_or_false,
  "is_optional": true_or_false,
  "requires_review": true_or_false,
  "reasoning": "brief why"
}}

Use precise non-executable intents instead of INFORMATIONAL when possible:
TP_HIT_ANNOUNCEMENT, SL_HIT_ANNOUNCEMENT, BE_ANNOUNCEMENT, PROGRESS_UPDATE,
DAILY_SUMMARY, WEEKLY_SUMMARY, MARKET_COMMENTARY, CONDITIONAL_PLAN,
OPTIONAL_SUGGESTION, REENTRY_SIGNAL.

═══ CONFIDENCE GUIDELINES ═══
- Explicit instruction matching trader's typical phrase: 0.85 - 1.0
- Implied action / context-dependent: 0.5 - 0.8 (will trigger user notify)
- Pure commentary: MARKET_COMMENTARY with conf 0.9+
- Unsure: UNKNOWN with conf 0.0 and requires_review=true

CONSERVATIVE BIAS: when in doubt, UNKNOWN or MARKET_COMMENTARY > acting wrongly.
The bot trusts MT5 for actual TP/SL fills — don't suggest closures on
"TP hit" messages because MT5 already handles those.

If you cannot parse, return {{"message_role": "unknown", "actions": [
  {{"type": "UNKNOWN", "price": null, "confidence": 0.0,
    "target": "none", "evidence": ""}}
], "is_conditional": false, "is_optional": false,
"requires_review": true, "reasoning": "Could not parse confidently"}}.
"""


def _build_context_block(signal) -> dict:
    """Construye dict de contexto para format del prompt contextual.

    Si el signal tiene build_context() (caso normal), lo usa.
    Si no, devuelve dict de placeholders mínimos.
    """
    try:
        ctx = signal.build_context()
        return {
            "channel": ctx.channel,
            "direction": ctx.direction,
            "entry_price": ctx.entry_price if ctx.entry_price else "n/a",
            "tps": ctx.tps if ctx.tps else "[]",
            "sl": ctx.sl if ctx.sl else "n/a",
            "n_open": ctx.n_open,
            "n_initial": ctx.n_initial,
            "elapsed_min": ctx.elapsed_min,
            "floating_pnl": f"{ctx.floating_pnl_total:+.2f}",
            "current_price": ctx.current_price if ctx.current_price else "n/a",
            "be_armed": "yes" if ctx.be_armed else "no",
        }
    except Exception as e:
        print(f"[Classifier] _build_context_block error: {e} — usando placeholders")
        return {
            "channel": "unknown", "direction": "?", "entry_price": "n/a",
            "tps": "[]", "sl": "n/a", "n_open": 0, "n_initial": 0,
            "elapsed_min": 0, "floating_pnl": "0.00",
            "current_price": "n/a", "be_armed": "no",
        }


# ─── Regex local ────────────────────────────────────────────────────────────

_NEG_SL_HIT = ("sl hit", "stop loss hit", "sl was", "sl reached")
_EXPLICIT_NEGATED_ACTION_RE = re.compile(
    r"\b(?:do\s+not|don['\u2019]?t|dont|never)\s+"
    r"(?:take|close|move|set|change|adjust|put|book|secure|"
    r"protect|cut|delete|open|add)\b",
    re.IGNORECASE,
)
_EXPLICIT_ADDITIONAL_ENTRY_RE = re.compile(
    r"\b(?:i|we)(?:['\u2019]ve|\s+have)?\s+(?:just\s+)?"
    r"(?:put|added|opened|took)\s+(?:some\s+)?more\s+"
    r"(?P<direction>buys?|sells?)"
    r"(?:\s+(?:entries|positions|trades?))?\s+"
    r"(?:on|at|around)\s+"
    r"(?P<price>\d{3,5}(?:\.\d{1,3})?)\b",
    re.IGNORECASE,
)
_DIRECT_REENTRY_PERMISSION_RE = re.compile(
    r"\byou\s+(?:can\s+still|still\s+can|may\s+still)\s+"
    r"(?:re[-\s]?)?enter\s+(?:the\s+)?(?P<direction>buy|sell)\s+trade\b",
    re.IGNORECASE,
)
_RISK_FREE_EXPLANATION_RE = re.compile(
    r"\brisk.?free\b.{0,80}\b(?:does\s+not|doesn['\u2019]?t|doesnt)\s+"
    r"mean\s+(?:move|moving|set|setting)\b",
    re.IGNORECASE | re.DOTALL,
)
_GENERIC_RISK_FREE_RE = re.compile(
    r"\brisk.?free\b|\b0\s*%?\s*risk\b|\bzero\s+risk\b",
    re.IGNORECASE,
)
_EXPLICIT_BE_PATTERNS = (
    r"\bmove\s+(?:my\s+|your\s+|the\s+)?sl\s+to\s+be\b",
    r"\bmove\s+(?:my\s+|your\s+|the\s+)?stop.?loss\s+to\s+"
    r"(?:be|breakeven|entry)\b",
    r"\bmove\s+(?:my\s+|your\s+|the\s+)?(?:sl|stop.?loss)\s+"
    r"(?:above|below)\s+(?:the\s+|my\s+|your\s+)?"
    r"(?:entry|entries|lowest|highest)\b",
    r"\bset(?:ting)?\s+(?:sl\s+to\s+)?(?:be|breakeven)\b",
    r"\bsl\s+to\s+(?:be|breakeven|entry)\b",
    r"\bmove\b.{0,30}\b(?:sl|stop.?loss)\b.{0,30}\b"
    r"(?:0\s*%?\s*risk|zero\s+risk)\b",
    r"\bto\s+breakeven\b",
)


def _is_risk_free_explanation(text: str) -> bool:
    return bool(_RISK_FREE_EXPLANATION_RE.search(text or ""))


def _has_explicit_be_instruction(text: str) -> bool:
    return any(
        re.search(pattern, text or "", re.IGNORECASE)
        for pattern in _EXPLICIT_BE_PATTERNS
    )


def _has_generic_risk_free_instruction(text: str) -> bool:
    return bool(_GENERIC_RISK_FREE_RE.search(text or ""))


def _negated_action_review(text: str) -> dict | None:
    if not _EXPLICIT_NEGATED_ACTION_RE.search(text or ""):
        return None
    return {
        "action": "UNKNOWN",
        "price": None,
        "confidence": 1.0,
        "requires_review": True,
        "_reason": "explicit_negated_instruction",
    }


def _affirmative_action_text(text: str) -> str:
    """Remove only clauses containing an explicitly negated action."""
    clauses = re.split(
        r"[.!?;,\n]+"
        r"|\s+[-\u2013\u2014]\s+"
        r"|\b(?:BUT|HOWEVER)\b"
        r"|\bAND\b(?=\s+(?:DO\s+NOT|DON['\u2019]?T|DONT|NEVER)\b)",
        text or "",
        flags=re.IGNORECASE,
    )
    return " ".join(
        clause.strip()
        for clause in clauses
        if clause.strip()
        and not _EXPLICIT_NEGATED_ACTION_RE.search(clause)
    )


def _explicit_additional_entry_action(text: str) -> dict | None:
    """Detect a provider-confirmed extra entry without inferring execution."""
    match = _EXPLICIT_ADDITIONAL_ENTRY_RE.search(text or "")
    if not match:
        return None
    raw_direction = match.group("direction").upper()
    direction = "BUY" if raw_direction.startswith("BUY") else "SELL"
    return {
        "action": "REENTRY_SIGNAL",
        "price": float(match.group("price")),
        "entry_direction": direction,
        "confidence": 0.98,
        "_reason": "provider_confirmed_additional_entry",
    }


def _direct_reentry_permission_action(text: str) -> dict | None:
    """Preserve a direct re-entry permission for review without inventing it."""
    match = _DIRECT_REENTRY_PERMISSION_RE.search(text or "")
    if not match:
        return None
    return {
        "action": "REENTRY_SIGNAL",
        "price": None,
        "entry_direction": match.group("direction").upper(),
        "confidence": 0.98,
        "_reason": "provider_direct_reentry_permission",
    }


def _canal1_safe_regex_classify(text: str) -> list[dict]:
    """Regex estrecho para canal1 antes de Gemini.

    Canal1 no puede usar el regex general: ya hubo un bug real donde
    "If ... we will close" se ejecuto como CLOSE_ALL. Este helper solo cubre
    ordenes directas, sin condicionales, para que Gemini no sea punto unico de
    fallo en mensajes mecanicamente obvios.
    """
    negated = _negated_action_review(text)
    if negated:
        text = _affirmative_action_text(text)
        if not text:
            return [negated]
    t = text.lower()
    if _is_risk_free_explanation(text):
        return [{
            "action": "INFORMATIONAL",
            "price": None,
            "confidence": 1.0,
            "_reason": "provider_risk_free_explanation",
        }]
    if re.search(r"\b(if|when|once|unless)\b", t):
        return []
    if re.search(r"\bwatch\b.*\b(close|exit|sl|stop|move)\b", t):
        return []

    actions: list[dict] = []
    explicit_addition = _explicit_additional_entry_action(text)
    if explicit_addition:
        actions.append(explicit_addition)
    direct_reentry = _direct_reentry_permission_action(text)
    if direct_reentry:
        actions.append(direct_reentry)

    if (
        re.search(
            r"\b(?:TAKE|CLOSE|CLOSING|BOOK|SECURE)\s+(?:SOME\s+)?"
            r"PARTIAL(?:S|\s+PROFITS?)?\b",
            t,
            re.IGNORECASE,
        )
        or re.search(
            r"\bTAKE\s+PROFITS?\s+FROM\s+(?:THE\s+)?LAYERS?\b",
            t,
            re.IGNORECASE,
        )
    ):
        actions.append({
            "action": "CLOSE_PARTIAL",
            "price": None,
            "confidence": 0.95,
            "_reason": "provider_partial_profit",
        })

    if _has_explicit_be_instruction(text):
        action = {"action": "MOVE_SL_TO_BE",
                  "price": None, "confidence": 0.95,
                  "_reason": "canal1_safe_direct_be"}
        provider_price = extract_provider_stated_be_price(text)
        if provider_price is not None:
            action["provider_stated_be_price"] = provider_price
        actions.append(action)
    elif _has_generic_risk_free_instruction(text):
        actions.append({
            "action": "SECURE_BASKET",
            "price": None,
            "confidence": 0.95,
            "_reason": "provider_generic_risk_free",
        })

    close_all_phrases = [
        r"\bclose\s+all\b",
        r"\bclose\s+everything\b",
        r"\bclos(?:e|ing)\s+(?:the\s+)?trade\s+now\b",
        r"\bclos(?:e|ing)\s+(?:my\s+|our\s+|the\s+)?trades?\s+now\b",
        r"\bclos(?:e|ing)\s+(?:our\s+|the\s+)?last\s+entries\b",
        r"\bi(?:'m| am)\s+out\s+(?:of\s+)?(?:this\s+|the\s+)?trade\b",
    ]
    if any(re.search(p, t) for p in close_all_phrases):
        actions.append({"action": "CLOSE_ALL",
                        "price": None, "confidence": 0.92,
                        "_reason": "canal1_safe_direct_close"})

    return actions


def _regex_classify_all(text: str) -> list[dict]:
    """Detecta TODAS las acciones presentes en el texto. Lista vacía si nada."""
    actions: list[dict] = []

    negated = _negated_action_review(text)
    if negated:
        text = _affirmative_action_text(text)
        if not text:
            return [negated]
    t = text.lower()
    if _is_risk_free_explanation(text):
        return [{
            "action": "INFORMATIONAL",
            "price": None,
            "confidence": 1.0,
            "_reason": "provider_risk_free_explanation",
        }]

    if re.search(
        r"\bclos(?:e|ing)\s+(?:the\s+)?overall\s+profits?\s+"
        r"(?:or|/)\s+(?:set|move|put)\s+(?:the\s+)?"
        r"(?:(?:stop.?loss|sl)\s+to\s+)?(?:be|breakeven)\b",
        t,
    ):
        return [{
            "action": "CLOSE_PROFIT_OR_BE",
            "price": None,
            "confidence": 0.99,
            "_reason": "close_profit_or_exact_be",
        }]

    # 0. Anuncio puro de niveles (TP solos o TP+SL/SP combinados).
    # Evita que Gemini interprete "TP1 4705.50" como CLOSE_AT_TP @4705.5.
    # El parser ya extrae los niveles vía parse_canal2 antes del classify,
    # así que estos mensajes son INFORMATIONAL desde el punto de vista de
    # acciones — el SL/TPs ya se aplicaron por el camino correcto.
    # Formatos cubiertos:
    #   "TP1=4688.41 | TP2=4690.41 | TP3=4692.41"
    #   "TP1 4688.41"
    #   "TP1 4705.50  SP 4716.50"  (la línea SP la captura el parser, no aquí)
    #   "TP1 4705.50  SL 4716.50"
    if re.fullmatch(r"(?:(?:tp\s*\d+|s[lp])\s*[=:\s]+[\d.]+\s*[|,\s]*)+",
                    t.strip()):
        return [{"action": "INFORMATIONAL", "price": None, "confidence": 1.0,
                 "_reason": "pure_levels_announcement"}]

    explicit_addition = _explicit_additional_entry_action(text)
    if explicit_addition:
        actions.append(explicit_addition)
    direct_reentry = _direct_reentry_permission_action(text)
    if direct_reentry:
        actions.append(direct_reentry)

    if re.search(
        r"\bclos(?:e|ing)\s+(?:in\s+)?overall\s+profits?\b",
        t,
    ):
        actions.append({
            "action": "CLOSE_ALL",
            "price": None,
            "confidence": 0.98,
            "_reason": "close_overall_profit",
        })

    # 1. SL a precio explícito — verbos extendidos (move/change/adjust/set/put)
    m = re.search(
        r"(?:moving?|move|chang(?:e|ing)|adjust(?:ing)?|set(?:ting)?|put(?:ting)?)"
        r"\s+(?:my\s+|the\s+|your\s+)?(?:stop.?loss|sl)"
        r"(?:\s+\w+){0,3}?\s+(?:to|at)\s+([\d.]+)",
        t,
    )
    if m and not re.search(
        r"\b(?:BE|BREAKEVEN|BREAK\s+EVEN|ENTRY)\b",
        m.group(0),
        re.IGNORECASE,
    ):
        actions.append({"action": "MOVE_SL_TO_PRICE",
                        "price": float(m.group(1)), "confidence": 1.0})

    if not any(a["action"] == "MOVE_SL_TO_PRICE" for a in actions):
        m = re.search(
            r"\bmake\s+(?:my\s+|the\s+|your\s+)?(?:stop.?loss|sl)\s+"
            r"(\d{3,5}(?:\.\d{1,3})?)\b",
            t,
        )
        if m:
            actions.append({"action": "MOVE_SL_TO_PRICE",
                            "price": float(m.group(1)), "confidence": 0.92})

    # 1b. Bare "SL 4700" / "SL: 4700" — sin verbo, contexto de gestión
    if not any(a["action"] == "MOVE_SL_TO_PRICE" for a in actions):
        m = re.search(r"\bsl\s*[:=]?\s*(\d{3,5}(?:\.\d{1,3})?)\b", t)
        if m and not any(neg in t for neg in _NEG_SL_HIT):
            actions.append({"action": "MOVE_SL_TO_PRICE",
                            "price": float(m.group(1)), "confidence": 0.80})

    if re.search(
        r"\bprotect\s+(?:(?:my|your|the|our)\s+)?"
        r"(?:capital|profits?|account|trade)\b",
        t,
        re.IGNORECASE,
    ):
        actions.append({
            "action": "PROTECT_AND_NOTIFY",
            "price": None,
            "confidence": 0.95,
            "_reason": "provider_protection_instruction",
        })

    # Preserve the provider's partial-profit decision without inventing how
    # many of our positions should close live.
    if (
        re.search(
            r"\b(?:TAKE|CLOSE|CLOSING|BOOK|SECURE)\s+(?:SOME\s+)?"
            r"PARTIAL(?:S|\s+PROFITS?)?\b",
            t,
            re.IGNORECASE,
        )
        or re.search(
            r"\bTAKE\s+PROFITS?\s+FROM\s+(?:THE\s+)?LAYERS?\b",
            t,
            re.IGNORECASE,
        )
    ):
        actions.append({
            "action": "CLOSE_PARTIAL",
            "price": None,
            "confidence": 0.95,
            "_reason": "provider_partial_profit",
        })

    # 2. Explicit BE and generic risk-free are different provider intents.
    if _has_explicit_be_instruction(text):
        action = {"action": "MOVE_SL_TO_BE",
                  "price": None, "confidence": 0.95}
        provider_price = extract_provider_stated_be_price(text)
        if provider_price is not None:
            action["provider_stated_be_price"] = provider_price
        actions.append(action)
    elif _has_generic_risk_free_instruction(text):
        actions.append({
            "action": "SECURE_BASKET",
            "price": None,
            "confidence": 0.95,
            "_reason": "provider_generic_risk_free",
        })

    # 3. "I am out at BE" / "closing here at BE" → trader cierra todo en BE
    out_be_phrases = [
        r"\bout\s+(?:of\s+)?(?:this\s+)?trade\s+at\s+(?:be|breakeven)",
        r"clos(?:e|ing)\s+(?:here\s+)?at\s+(?:be|breakeven)",
        r"\bout\s+at\s+(?:be|breakeven)\b",
        r"\bi\s+am\s+out\s+(?:of\s+)?this\s+trade\b",
        r"\bi'm\s+out\s+(?:of\s+)?this\s+trade\b",
    ]
    if any(re.search(p, t) for p in out_be_phrases):
        actions.append({"action": "CLOSE_ALL", "price": None,
                        "confidence": 0.95, "_reason": "out_at_be"})

    # 4. CLOSE_FIRST contextual.
    # Detecta singular vs plural — semantica DISTINTA con doble_market activo:
    #   "close first entry"   (singular) → cerrar 1 pos (la peor por P&L)
    #   "close first entries" (plural)   → cerrar TODAS las kind=market
    #                                      (Pos A + Pos B), dejar DCAs corriendo
    # Esto ultimo es lo que el trader pide: "asegura las primeras (markets) y
    # deja correr las DCAs de abajo, donde hay mas recorrido por hacer".
    # Caso real canal2_12347 (sesion 2026-05-13): bot cerro solo 1 markets
    # de 2, manteniendo Pos A en pesimo P&L cuando deberia haber cerrado
    # ambas y dejar DCAs (que aun no habian llenado).
    plural_match = re.search(
        r"clos(?:e|ing)\s+(?:the\s+|your\s+|my\s+)?"
        r"(?:first|early|oldest|initial)\s+"
        r"(?:entries|entires|positions|ones)\b", t)
    singular_match = re.search(
        r"clos(?:e|ing)\s+(?:the\s+|your\s+|my\s+)?"
        r"(?:first|early|oldest|initial)\s+"
        r"(?:entry|position)\b", t)
    compound_match = re.search(
        r"clos(?:e|ing)\s+(?:the\s+|your\s+|my\s+)?"
        r"(?:first|early|oldest|initial)\s+and\s+(?:move|adjust|set)", t)
    if plural_match:
        actions.append({"action": "CLOSE_FIRST", "price": None,
                        "confidence": 0.92, "is_plural": True,
                        "_reason": "close_first_contextual_plural"})
    elif singular_match or compound_match:
        actions.append({"action": "CLOSE_FIRST", "price": None,
                        "confidence": 0.92, "is_plural": False,
                        "_reason": "close_first_contextual_singular"})

    # 5. "If you have one entry close it now" → CLOSE_ALL.
    # Mensaje compañero típico cuando el canal manda "close your first entries
    # now. If you have one entry close it now." Si CLOSE_FIRST ya disparó
    # arriba, NO añadimos CLOSE_ALL — el ejecutor de CLOSE_FIRST cubre el
    # caso n=1 (cierra esa única posición = mismo efecto).
    if (re.search(
            r"if\s+you\s+(?:have|got|only\s+have)\s+one\s+entry.*clos(?:e|ing)", t)
        and not any(a["action"] == "CLOSE_FIRST" for a in actions)):
        actions.append({"action": "CLOSE_ALL", "price": None,
                        "confidence": 0.95, "_reason": "single_entry_close"})

    # 6. Cierre total — fraseos genéricos.
    # IMPORTANTE: si CLOSE_FIRST ya se detectó, NO añadimos CLOSE_ALL aquí.
    # "close your first entries now" matchea tanto "first entries" (regla 4)
    # como "close.*entries.*now" (esta regla). La intención del canal es
    # parcial, no total — CLOSE_FIRST tiene prioridad por especificidad.
    close_all_phrases = [
        "close the rest", r"close\s+all\b", "close your entries",
        r"close.*entries.*now", r"gold\s+is\s+back.*entry",
        r"close\s+everything", r"close\s+the\s+trade\b",
        r"close\s+this\s+trade\b", r"close\s+now\b",
        r"clos(?:e|ing)\s+all\s+positions",
    ]
    if any(re.search(p, t) for p in close_all_phrases):
        if not any(a["action"] in ("CLOSE_ALL", "CLOSE_FIRST") for a in actions):
            actions.append({"action": "CLOSE_ALL", "price": None, "confidence": 0.90})

    # 7. "Let's close TP1 here" / "Close TP3 here" → CLOSE_AT_TP
    m = re.search(r"clos(?:e|ing)\s+(?:the\s+)?tp\s*(\d)", t)
    if m:
        actions.append({"action": "CLOSE_AT_TP",
                        "price": int(m.group(1)), "confidence": 0.85})

    # 8. INFORMATIONAL — solo si no detectamos NINGUNA acción real arriba.
    # Las variantes de SL hit ("already", "just", "was", "has been") las
    # dejamos como info: el bot detecta el cierre real vía MT5 auto-finalize
    # del position_lifecycle_monitor (cuando n_open=0). Defensa en profundidad: además
    # _SL_HIT_RE del listener detecta estas mismas variantes y dispara
    # _finalize_signal por si MT5 tarda en reportar el cierre.
    if not actions:
        info_phrases = [
            r"tp\s*\d+\s*(?:hit|reached|secured|done|smashed|tapped|✅)",
            r"\bsl\s+(?:was\s+|already\s+|just\s+|has\s+been\s+)?hit\b",
            r"stop\s+loss\s+(?:was\s+|already\s+|just\s+|has\s+been\s+)?hit",
            r"\bsl\s+(?:reached|triggered|edited)\b",
            r"\+?\d+\s*(?:/\s*\d+\s*)?pips?\b",
            r"pips?\s+(?:profit|secured|gained|locked)",
            r"pips?\s+from\s+entry",
            r"target.*down", r"running\s+in\s+profit",
            r"strong\s+move", r"stay\s+patient",
            r"keep\s+the\s+same", r"let.*trade\s+develop",
            r"trade\s+is\s+now\s+risky",
            r"secur(?:e|ing)\s+some\s+profits?",
            r"clos(?:e|ing)\s+some\s+profits?",
            r"i\s+am\s+clos(?:e|ing)\s+some",
            r"let.*secure",
            r"\bin\s+profit\b", r"good\s+move", r"nice\s+move",
        ]
        if any(re.search(p, t) for p in info_phrases):
            actions.append({"action": "INFORMATIONAL",
                            "price": None, "confidence": 0.90})

    return actions


# ─── Gemini con retry+backoff ───────────────────────────────────────────────

def _parse_gemini_json(raw: str) -> list[dict]:
    """Parsea la respuesta de Gemini, normalizando dict/list a list."""
    raw = raw.strip()
    # Limpiar fences markdown si los hay
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()
    parsed = json.loads(raw)
    if isinstance(parsed, dict):
        return normalize_classifier_outputs(parsed)
    if isinstance(parsed, list):
        return normalize_classifier_outputs(parsed)
    raise ValueError(f"Gemini devolvió tipo inesperado: {type(parsed).__name__}")


def _gemini_classify(text: str, signal=None, max_retries: int = 3,
                     base_wait: float = 2.0) -> list[dict]:
    """Llama a Gemini con backoff exponencial (2s, 4s, 8s).

    Si signal se proporciona, usa el prompt CONTEXTUAL (incluye estado del
    trade: dirección, P&L, posiciones, TPs/SL, ejemplos del trader).
    Si no, usa el prompt LEGACY (genérico, backward compat).

    El prompt contextual produce mejor accuracy en mensajes narrativos del
    canal 1 y maneja casos como typos del canal 2 con awareness del estado.
    """
    if signal is not None:
        ctx_data = _build_context_block(signal)
        prompt = _PROMPT_CONTEXTUAL.format(msg=text, **ctx_data)
    else:
        prompt = _PROMPT_TEMPLATE.format(msg=text)

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = _client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            actions = _parse_gemini_json(resp.text or "")
            if not actions:
                # Lista vacía es respuesta válida = "no entiendo"
                return []
            provider_price = extract_provider_stated_be_price(text)
            if provider_price is not None:
                for action in actions:
                    if action.get("action") == "MOVE_SL_TO_BE":
                        action["price"] = None
                        action.setdefault(
                            "provider_stated_be_price",
                            provider_price,
                        )
            return actions
        except Exception as e:  # incluye 503, JSONDecodeError, ValueError
            last_error = e
            if attempt < max_retries - 1:
                wait = base_wait * (2 ** attempt)
                print(f"[Classifier] Gemini intento {attempt+1}/{max_retries} "
                      f"falló ({type(e).__name__}): {e} — retry en {wait:.0f}s")
                time.sleep(wait)
            else:
                print(f"[Classifier] Gemini agotó {max_retries} intentos. "
                      f"Último error: {e}")

    # Fallback final: marcamos para que el journal sepa que perdimos info
    return [{"action": "INFORMATIONAL", "price": None, "confidence": 0.0,
             "_gemini_failed": True,
             "_last_error": str(last_error) if last_error else "unknown"}]


# ─── API pública ────────────────────────────────────────────────────────────

def classify(text: str, signal=None) -> list[dict]:
    """Version SINCRONA. Devuelve la lista de acciones detectadas en el texto.

    Args:
        text: el mensaje del trader.
        signal: opcional. Si se proporciona, Gemini recibe el contexto del
            trade (estado en vivo desde MT5) y usa prompt enriquecido. Si no,
            Gemini recibe solo el texto (prompt legacy). El regex local NO
            usa contexto — siempre se aplica primero.

    Lista vacía = mensaje irrelevante / no parseable.
    Lista con varios elementos = mensaje compuesto (ej: "TP1 hit. Move SL to BE").

    AVISO: si la rama Gemini se ejecuta (regex no matchea), esta funcion
    BLOQUEA hasta 14s (3 retries con backoff 2/4/8s + tiempo de respuesta).
    En codigo async usar `classify_async` para no bloquear el event loop
    (bug C1 fix). Esta version sync se mantiene para tools/scripts y para
    el regex puro (que es <1ms).
    """
    if not text or not text.strip():
        return []

    actions = _regex_classify_all(text)
    if actions:
        return actions

    return _gemini_classify(text, signal=signal)


def classify_local(text: str) -> list[dict]:
    """Return deterministic regex actions without invoking Gemini."""
    if not text or not text.strip():
        return []
    return _regex_classify_all(text)


async def classify_async(text: str, signal=None) -> list[dict]:
    """Version ASYNC de classify.

    Routing:
      - Canal 1 con signal: SIEMPRE Gemini (skip regex). Razon — el canal 1
        usa lenguaje narrativo/contextual con muchos condicionales ("if X
        then Y", "watch the candle close") que el regex pattern-matchea
        catastroficamente. Caso real canal1_19649 (sesion 2026-05-13): el
        regex matched "close this trade" dentro de "If 15M closes above
        4700, we will close this trade" → CLOSE_ALL prematuro, perdida
        -$9.94. Para canal 1 priorizamos COMPRENSION CONTEXTUAL sobre
        velocidad — el coste extra de ~3-5s por mgmt_msg es aceptable
        porque mgmt_msgs son raros (~5-10 por sesion).

      - Canal 2 / sin signal: regex first (rapido, ~1ms). El canal 2 manda
        ordenes mas directas y cortas que el regex maneja bien.

    Si el regex matchea (canal 2) → retorna sin Gemini.
    Si no matchea o es canal 1 → Gemini en thread pool (no bloquea loop).
    """
    if not text or not text.strip():
        return []

    # Canal 1: bot CONTEXTUAL — Gemini siempre con contexto del trade.
    # Excepcion: si signal es None (no hay contexto), fallback a regex
    # para evitar llamada Gemini sin info util.
    if signal is not None and getattr(signal, "channel", None) == "canal1":
        actions = _canal1_safe_regex_classify(text)
        if actions:
            return actions
        return await asyncio.to_thread(_gemini_classify, text, signal)

    # Canal 2 / signal None: regex first (rapido)
    actions = _regex_classify_all(text)
    if actions:
        return actions

    return await asyncio.to_thread(_gemini_classify, text, signal)


def classify_one(text: str) -> dict:
    """Helper retrocompat: devuelve solo la primera acción.

    Útil si el código consumidor aún no sabe iterar sobre la lista.
    Si no hay acciones, devuelve INFORMATIONAL.
    """
    actions = classify(text)
    if actions:
        return actions[0]
    return {"action": "INFORMATIONAL", "price": None, "confidence": 0.0}
