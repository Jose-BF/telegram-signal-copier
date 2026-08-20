"""
Parsea señales de texto de Canal 1 y Canal 2.

Canal 1 (DT Investing):
  Sticker → mercado inmediato
  Texto: "SELL GOLD NOW  4785-90\n🎯 TP1: 4780.00 ... ❌ SL: 4798.00"

Canal 2 (Gold Standard):
  Mensaje inicial: "XAU USD BUY NOW"  → mercado inmediato
  Edit 1: "XAU USD BUY NOW\n\n4799-4795"  → rango
  Edit 2: texto completo con TPs y SL
"""

import re
from typing import Optional


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_abbreviated_range(raw: str) -> Optional[tuple[float, float]]:
    """
    Convierte rangos abreviados como '4785-90' o '4745/50' en dos floats.
    Devuelve (low, high) o None si no puede parsear.
    """
    raw = raw.strip().replace("/", "-")
    parts = raw.split("-")
    if len(parts) != 2:
        return None
    try:
        a = float(parts[0].strip())
        b_str = parts[1].strip()
        b = float(b_str)

        # Si b parece abreviado (hasta 2 digitos enteros), conserva tambien
        # sus decimales: "4488-95.00" significa 4488-4495, no 95 dolares.
        b_integer_digits = len(b_str.lstrip("+-").split(".", 1)[0])
        if b < 100 and b_integer_digits <= 2:
            base = int(a / 100) * 100
            b_full = base + b
            # Ajusta si la diferencia sería > 50 (signo equivocado de centena)
            if abs(b_full - a) > 50:
                b_full += 100 if b_full < a else -100
            b = b_full

        lo, hi = (a, b) if a <= b else (b, a)
        return lo, hi
    except ValueError:
        return None


def _extract_range(text: str) -> Optional[tuple[float, float]]:
    """Busca el primer patrón de rango en el texto.

    Filtra rangos con precios fuera del rango razonable de XAUUSD (1000-9999).
    Evita capturar typos como '33319' que debería ser '3331.9'.
    """
    pattern = r"(\d{3,5}(?:\.\d+)?)\s*[-/]\s*(\d{2,5}(?:\.\d+)?)"
    for m in re.finditer(pattern, text):
        result = _parse_abbreviated_range(f"{m.group(1)}-{m.group(2)}")
        if result is None:
            continue
        lo, hi = result
        # Filtra precios fuera de rango razonable para XAUUSD
        if 1000 <= lo <= 9999 and 1000 <= hi <= 9999:
            return lo, hi
    return None


def _extract_tps(text: str) -> list[float]:
    """Extrae TP1…TP5 en orden.

    Patrón estricto: 3-5 dígitos enteros + hasta 3 decimales opcionales.
    Descarta IPs, URLs y cualquier número que no sea un precio de trading
    (p.ej. '52.88.50' no casa porque tiene más de un punto).
    """
    # \d{3,5} → precio entre 100 y 99999 (XAUUSD ronda 2000-4000)
    # (?:\.\d{1,3})? → hasta 3 decimales opcionales
    pattern = r"\bTP\s*\d*\s*[:\s=\s]\s*(\d{3,5}(?:\.\d{1,3})?)\b"
    results = []
    for m in re.finditer(pattern, text, re.IGNORECASE):
        try:
            results.append(float(m.group(1)))
        except ValueError:
            pass
    if results:
        return results

    # Nuevo formato canal2 (2026-07-08):
    #   Targets
    #   4121.5
    #   4119.5
    #   4117
    #   Open
    # o una variante en una sola linea: "Target | 4063 | 4065".
    # Solo se activa despues de la palabra Target(s) para no confundir rangos,
    # profit updates o mensajes de gestion con TPs.
    in_targets_block = False
    price_pattern = r"\b(\d{3,5}(?:\.\d{1,3})?)\b"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()
        if re.search(r"\bFINAL\s+TARGET\b", upper):
            continue
        if re.search(r"\bTARGETS?\b", upper):
            in_targets_block = True
            after_label = re.split(
                r"\bTARGETS?\b", line, maxsplit=1, flags=re.IGNORECASE)[-1]
            for m in re.finditer(price_pattern, after_label):
                try:
                    results.append(float(m.group(1)))
                except ValueError:
                    pass
            continue
        if not in_targets_block:
            continue
        if re.search(r"\b(?:SL|SP|STOP|LOSS|INVALID)\b", upper):
            break
        if re.fullmatch(r"OPEN\b.*", upper):
            break
        for m in re.finditer(price_pattern, line):
            try:
                results.append(float(m.group(1)))
            except ValueError:
                pass
    return results


def _extract_final_target(text: str) -> Optional[float]:
    """Extrae un objetivo final sin confundirlo con un TP aislado."""
    match = re.search(
        r"\bFINAL\s+TARGET(?:\s+FOR\s+THIS)?\s*"
        r"(?:(?:IS|AT)\s*)?(?:[:=]\s*)?"
        r"(\d{3,5}(?:\.\d{1,3})?)\b",
        text or "",
        re.IGNORECASE,
    )
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_sl(text: str) -> Optional[float]:
    """Extrae el SL con el mismo patrón estricto de precio.

    Acepta los aliases reales que usan los canales:
      • SL  — estándar
      • SP  — "stop price", visto en canal2 (ej: 'TP1 4705.50  SP 4716.50')
    Si el canal usara otro alias futuro, añadirlo aquí en vez de en el
    classifier — el parser es la fuente canónica para extracción de niveles.
    """
    m = re.search(
        r"\b(?:SL|SP|STOP\s*LOSS)\b\s*"
        r"(?:/\s*(?:INVALID|VALID)?\s*)?"
        r"(?:(?:[:=\s]|\bis\b|\bat\b|\bto\b)+\s*)?"
        r"\s*(\d{3,5}(?:\.\d{1,3})?)\b",
                  text, re.IGNORECASE)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _direction(text: str) -> Optional[str]:
    immediate = _canal2_entry_match(text or "")
    if immediate:
        return immediate.group("direction").upper()
    match = re.search(r"\b(BUY|SELL)\b", text or "", re.IGNORECASE)
    return match.group(1).upper() if match else None


# ─── Detección de tipo de mensaje ─────────────────────────────────────────────

_CANAL2_IMMEDIATE_ENTRY_RE = re.compile(
    r"\b(?P<direction>BUY|SELL)\b"
    r"(?:\s+(?:XAU(?:\s*USD)?|GOLD|ZONE))?"
    r"(?:\s+AGAIN)?\s+NOW\b",
    re.IGNORECASE,
)
_CANAL2_PRODUCT_RE = re.compile(
    r"\b(?:XAU(?:\s*USD)?|GOLD|ZONE)\b",
    re.IGNORECASE,
)
_CANAL2_ENTRY_NEGATION_RE = re.compile(
    r"(?:"
    r"\b(?:DO\s+NOT|DON['\u2019]?T|DONT|NEVER)\b[^.!?;]{0,40}"
    r"|\b(?:NO|NOT)(?:\s+(?:A|AN|THE))?\s*[,;:]?\s*"
    r")$",
    re.IGNORECASE,
)
_CANAL2_ENTRY_CONDITIONAL_RE = re.compile(
    r"\b(?:IF|WHEN|ONCE|UNLESS|WOULD|COULD|MIGHT|MAY)\b|"
    r"\b(?:WAIT|LOOK)\s+(?:FOR|UNTIL)\b|"
    r"\b(?:POTENTIAL|POSSIBLE)\b(?!\s+CONFIRMED\b)",
    re.IGNORECASE,
)
_CANAL2_ZONE_RANGE_RE = re.compile(
    r"(?<!\d)(\d{3,5}(?:\.\d+)?)\s*[-\u2013\u2014]\s*"
    r"(\d{3,5}(?:\.\d+)?)(?!\d)"
)
_CANAL2_ZONE_TARGET_RE = re.compile(
    r"\bTARGET(?:\s+IS|\s*:|\s+AT)?\s*(\d{3,5}(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_CANAL2_SINGLE_ZONE_RE = re.compile(
    r"\b(?:NEXT|NEW|ANOTHER)?\s*(?P<direction>BUY|SELL)\s+ZONE"
    r"\s*(?:AT|AROUND|NEAR|@)?\s*(?P<price>\d{3,5}(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_CANAL2_REACTION_ZONE_RE = re.compile(
    r"\b(?P<direction>BUY|SELL)\s+ZONE\b"
    r"[^\d\n]{0,60}\bREACTION\s+(?:IS|AT)\s+"
    r"(?P<price>\d{3,5}(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_CANAL2_EXPECTED_AREAS_RE = re.compile(
    r"\bAREAS?\b[^\n.!?]{0,100}"
    r"\b(?P<direction>BUY|SELL)\s+FROM\b",
    re.IGNORECASE,
)
_CANAL2_BARE_PRICE_LINE_RE = re.compile(
    r"(?m)^\s*(\d{3,5}(?:\.\d+)?)\s*$"
)


def _canal2_entry_match(text: str):
    if not text:
        return None
    normalized = " ".join(text.split())
    if not _CANAL2_PRODUCT_RE.search(normalized):
        return None
    for match in _CANAL2_IMMEDIATE_ENTRY_RE.finditer(normalized):
        prefix = normalized[max(0, match.start() - 80):match.start()]
        clause = re.split(r"[.!?;]", prefix)[-1]
        if _CANAL2_ENTRY_NEGATION_RE.search(clause):
            continue
        if _CANAL2_ENTRY_CONDITIONAL_RE.search(clause):
            continue
        return match
    return None


def is_canal2_entry(text: str) -> bool:
    """Detect an immediate order, not words scattered through commentary."""
    return _canal2_entry_match(text) is not None


def canal2_entry_command_key(text: str) -> Optional[str]:
    """Canonical identity for one explicit Canal 2 immediate command."""
    match = _canal2_entry_match(text)
    if match is None:
        return None
    command = " ".join(match.group(0).upper().split())
    command = re.sub(r"\bXAU(?:\s*USD)?\b", "GOLD", command)
    command = re.sub(r"\bZONE\b", "GOLD", command)
    return command


def parse_canal2_zone_plan(
    text: str,
    inherited_direction: Optional[str] = None,
) -> Optional[dict]:
    """Extract a future zone without turning it into a market entry."""
    if not text or is_canal2_entry(text):
        return None

    upper = " ".join(text.upper().split())
    direction = None
    single = (
        _CANAL2_SINGLE_ZONE_RE.search(text)
        or _CANAL2_REACTION_ZONE_RE.search(text)
    )
    expected_areas = _CANAL2_EXPECTED_AREAS_RE.search(text)
    limit_plan = re.search(
        r"\b(?P<direction>BUY|SELL)\s+LIMIT\b",
        text,
        re.IGNORECASE,
    )
    possible_zone = re.search(
        r"\bPOSSIBLE\s+(?P<direction>BUY|SELL)\b"
        r"[^\n.!?]{0,80}\b(?:AROUND|AT|NEAR|ZONE|AREA)\b",
        text,
        re.IGNORECASE,
    )
    if single:
        direction = single.group("direction").upper()
    elif expected_areas:
        direction = expected_areas.group("direction").upper()
    elif limit_plan:
        direction = limit_plan.group("direction").upper()
    elif possible_zone:
        direction = possible_zone.group("direction").upper()
    elif re.search(
        r"\bBUY\s+ZONES?\b|\bZONES?\s+(?:I|WE)\s+WOULD\s+BUY\b",
        upper,
    ):
        direction = "BUY"
    elif re.search(
        r"\bSELL\s+ZONES?\b|\bZONES?\s+(?:I|WE)\s+WOULD\s+SELL\b",
        upper,
    ):
        direction = "SELL"
    elif str(inherited_direction or "").upper() in {"BUY", "SELL"}:
        direction = str(inherited_direction).upper()
    if direction is None:
        return None

    zones = []
    for match in _CANAL2_ZONE_RANGE_RE.finditer(text):
        zone = sorted([float(match.group(1)), float(match.group(2))])
        if zone not in zones:
            zones.append(zone)
    if single and not zones:
        price = float(single.group("price"))
        zones = [[price, price]]
    elif expected_areas and not zones:
        zones = [
            [float(match.group(1)), float(match.group(1))]
            for match in _CANAL2_BARE_PRICE_LINE_RE.finditer(text)
        ]

    target_match = _CANAL2_ZONE_TARGET_RE.search(text)
    target = float(target_match.group(1)) if target_match else None
    plan_language = bool(re.search(
        r"\bZONES?\s+(?:I\s+)?WOULD\b|"
        r"\bZONES?\s+MARKED\s+OUT\b|"
        r"\bNEXT\s+(?:BUY|SELL)\s+ZONE\b|"
        r"\bAREAS?\b[^\n.!?]{0,100}\b(?:BUY|SELL)\s+FROM\b",
        upper,
    ))
    if not zones and target is None and not plan_language:
        return None
    return {
        "direction": direction,
        "zones": zones,
        "target": target,
        "tps": _extract_tps(text),
        "sl": _extract_sl(text),
        "has_open_runner": bool(re.search(
            r"(?mi)^\s*OPEN(?:\s+(?:RUNNER|TRADE))?\s*$",
            text,
        )),
    }


def is_canal1_signal_text(text: str) -> bool:
    """El mensaje de texto que llega ~2 min después del sticker en Canal 1.

    Histórico: filtro estricto ("BUY/SELL GOLD NOW" + "TP") perdía variantes
    del canal (e.g. "Buy Gold @4700 TP1 4695 SL 4710" sin literal "NOW").
    Sesión 2026-05-06 canal1_19440 quedó sin TPs/SL aplicados por esto, la
    posición vivió naked y cerró por causa externa con -$7.97.

    Filtro relajado actual exige los 3 ingredientes de una señal de oro:
      - Direction keyword: BUY/SELL/LONG/SHORT
      - Mención de oro: XAU/GOLD/ORO
      - Al menos un TP CON NIVEL NUMÉRICO (no solo la cadena "TP")

    El TP numérico es crítico: antes bastaba `"TP" in t`, y un parte de
    estado del canal ("GOLD UPDATE… after the earlier TP1 hit") pasaba el
    filtro. Aguas abajo `_open_canal1_from_text` abría un market sobre ese
    texto sin niveles → posición naked (bug canal1_19778, 2026-05-19: 4
    posiciones naked, −$129). Una señal real SIEMPRE trae los TPs con su
    precio; un comentario, no.
    """
    t = text.upper()
    has_dir  = ("BUY" in t) or ("SELL" in t) or ("LONG" in t) or ("SHORT" in t)
    has_gold = ("XAU" in t) or ("GOLD" in t) or ("ORO" in t)
    # TP con nivel: "TP1: 4545", "TP 4500", "TP1 4695" → sí. "TP1 hit" → no.
    has_tp   = bool(re.search(r"TP\s*\d*\s*[:=]?\s*\d{3,5}", t))
    return has_dir and has_gold and has_tp


# ─── Parsers principales ───────────────────────────────────────────────────────

def parse_canal2(text: str) -> dict:
    """
    Parsea cualquier estado del mensaje de Canal 2.
    Devuelve dict con las claves presentes: direction, range, tps, sl.
    """
    result = {}
    d = _direction(text)
    if d:
        result["direction"] = d

    rng = _extract_range(text)
    if rng:
        result["range"] = rng

    tps = _extract_tps(text)
    if tps:
        result["tps"] = tps

    final_target = _extract_final_target(text)
    if final_target is not None:
        result["final_target"] = final_target

    if re.search(
        r"(?mi)^\s*OPEN(?:\s+(?:RUNNER|TRADE))?\s*$",
        text or "",
    ):
        result["has_open_runner"] = True

    sl = _extract_sl(text)
    if sl:
        result["sl"] = sl

    return result


def parse_canal1_text(text: str) -> dict:
    """
    Parsea el mensaje de texto de Canal 1 (llega después del sticker).
    Mismo formato que Canal 2 pero con 4 TPs y rango frecuentemente abreviado.
    """
    return parse_canal2(text)  # misma lógica, distintos emojis pero estructura igual


# ─── Predicción de TPs/SL a partir del rango ──────────────────────────────────
#
# Patrón observado en Gold Standard (Canal 2) sobre 10 señales:
#   BUY  → entry = range_high | TPs = entry+[3,5,7,9] | SL = range_low - 4
#   SELL → entry = range_low  | TPs = entry-[3,5,7,9] | SL = range_high + 4
#
# TP5 no se predice porque oscila entre +12 y +15 (variable).
# Permite abrir posiciones con SL desde el primer tick sin esperar al edit real.

# Offsets RE-CALIBRADOS 2026-05-15 con análisis de 56 señales reales del
# journal de producción (evento prediction_validated):
#
#   TP1: el offset anterior era +2, pero la mediana real del canal es
#        +2.82 USD (p25=2.34, p75=3.55). El +2 se quedaba corto el 40% de
#        las veces. Cambiado a +3 — centra el predictor en la distribución
#        real (error pasa de +0.82 a -0.18 USD respecto a la mediana).
#
#   TP2-TP5: progresión +5, +7, +9 manteniendo el espaciado típico de ~$2
#        entre TPs del canal. Coherente con el nuevo TP1=+3.
#
#   SL: edge-4 sigue siendo correcto (57% exacto, mediana=4). Sin cambio.
#
# Nota: el predictor es PROVISIONAL — se sobrescribe cuando llega el edit
# con los TPs reales. El TP1 es el más crítico porque dispara el cierre
# rápido de la posición market A.
#
_TP_OFFSETS = (3, 5, 7, 9)   # re-calibrado: TP1 +3 (mediana real 2.82)
_SL_OFFSET  = 4               # edge-4 (57% exacto, mediana=4)


def predict_levels(direction: str, range_low: float, range_high: float) -> dict:
    """Predicción provisional de TPs y SL basada en patrón estadístico del canal.

    Se aplica mientras llega el edit con los valores reales. Los valores
    reales siempre sobreescriben la predicción (ver listener._update_signal_from_parsed).
    """
    if direction == "BUY":
        entry = range_high
        tps = [round(entry + off, 2) for off in _TP_OFFSETS]
        sl  = round(range_low - _SL_OFFSET, 2)
    else:
        entry = range_low
        tps = [round(entry - off, 2) for off in _TP_OFFSETS]
        sl  = round(range_high + _SL_OFFSET, 2)
    return {"tps": tps, "sl": sl}


# ─── Validacion de RANGE vs precio actual ─────────────────────────────────────
#
# El canal a veces manda rangos absurdamente lejos del precio actual (typo
# del trader, ej. "4780-4785" cuando queria "4680-4685"). Si aceptamos el
# range absurdo, el predictor genera TPs/SL del lado equivocado y MT5 rechaza.
#
# Caso real (canal2_12338, sesion 2026-05-13):
#   SELL @ 4680.41 / canal envio range 4780-4785 (+100$ del entry)
#   → predictor genero tps=[4778,4776,4774,4772] sl=4789 (todos inversos)
#   → 357 reintentos hasta cap, BE prematuro, etc.
#
# Esta funcion valida que el range este "razonablemente cerca" del entry.
# Si difiere >max_dist_usd, rechaza.

def validate_range_vs_entry(direction: str, entry: float,
                             range_low: float, range_high: float,
                             max_dist_usd: float = 30.0) -> dict:
    """Valida que el rango este cerca del entry. Detecta typos del canal.

    Para BUY/SELL en oro (XAUUSD), rangos tipicos son 4-10 USD y siempre
    cercanos al precio actual. Si la distancia minima del range al entry
    supera max_dist_usd, asumimos typo del trader.

    Devuelve dict:
      {
        "ok": bool,
        "reason": str | None,    # motivo del rechazo (para log/notify)
        "min_dist_usd": float,   # distancia minima del range al entry
      }
    """
    # Distancia minima del range al entry: 0 si entry esta dentro del range,
    # sino la diferencia con el extremo mas cercano.
    if range_low <= entry <= range_high:
        min_dist = 0.0
    else:
        min_dist = min(abs(range_low - entry), abs(range_high - entry))

    if min_dist > max_dist_usd:
        reason = (f"range [{range_low}-{range_high}] esta a "
                  f"{min_dist:.2f} USD del entry={entry} "
                  f"(maximo permitido {max_dist_usd}). Probable typo del canal.")
        return {"ok": False, "reason": reason, "min_dist_usd": min_dist}

    return {"ok": True, "reason": None, "min_dist_usd": min_dist}


# ─── Predictor de SL desde entry (sin necesitar range) ────────────────────────
#
# Cuando rechazamos el range del canal por absurdo (typo), aun queremos
# proteger la posicion. predict_sl_from_entry da un SL razonable basado en
# el ENTRY (no en el range), para que la posicion no quede descubierta.
#
# El offset $10 esta calibrado al patron del canal (predictor usa range_low-4,
# y range tipico es 5USD desde entry, total ~9USD desde entry).

_SL_OFFSET_FROM_ENTRY_USD = 10.0


def predict_sl_from_entry(direction: str, entry: float,
                          offset_usd: float = _SL_OFFSET_FROM_ENTRY_USD) -> float:
    """SL provisional cuando no tenemos range valido (typo del canal).

    Permite proteger la posicion durante la ventana entre la apertura y
    la llegada de TPs/SL coherentes del canal. Mantiene el mismo orden
    de magnitud que predict_levels (~$10) para no ser ni demasiado cerca
    (cerraria por ruido) ni demasiado lejos (perdida grande).
    """
    if direction == "BUY":
        return round(entry - offset_usd, 2)
    return round(entry + offset_usd, 2)


# ─── Correccion de typos en TPs ────────────────────────────────────────────────
#
# El trader del canal a veces comete typos al teclear los TPs: un digito de
# mas, un 0 extra, etc. Caso real canal2_12382 (2026-05-14): TP3 llego como
# "46700" cuando queria "4700" (BUY @ 4694, vecinos TP2=4698 y TP4=4702).
#
# En lugar de rechazar el TP (perdiendo la informacion), intentamos CORREGIR
# el typo de forma logica: generamos candidatos de correccion del numero y
# elegimos el que mejor encaja en el escalonado de los TPs vecinos validos.

def _correction_candidates(bad_value: float) -> set:
    """Genera valores candidatos a partir de un numero con typo.

    Estrategias de typo cubiertas:
      - Digito de mas: quitar cada digito ("46700" -> 6700, 4700, 4670, ...)
      - Cero extra al final: dividir por 10 ("47000" -> 4700)
      - Punto decimal mal: dividir/multiplicar por 10
    """
    candidates = set()
    # Representacion entera si el valor es entero
    s = str(int(bad_value)) if bad_value == int(bad_value) else str(bad_value)
    digits_only = s.replace(".", "")
    # Quitar cada digito uno a uno
    for i in range(len(digits_only)):
        c = digits_only[:i] + digits_only[i + 1:]
        if c and c.isdigit():
            candidates.add(float(c))
    # Dividir por 10 / 100 (ceros extra)
    candidates.add(round(bad_value / 10, 2))
    candidates.add(round(bad_value / 100, 2))
    candidates.discard(bad_value)
    return candidates


def correct_tp_typos(direction: str, entry: float, tps: list,
                     max_dist_usd: float = 50.0) -> tuple:
    """Detecta TPs con typos y los corrige por interpolacion con vecinos.

    Un TP es "absurdo" si esta del lado equivocado del entry o a mas de
    max_dist_usd. Para cada absurdo, genera candidatos de correccion y elige
    el que mejor encaja en el escalonado (entre los TPs vecinos validos).

    Devuelve (tps_corregidos, correcciones):
      tps_corregidos: lista con los typos arreglados (los no corregibles
                      quedan con su valor original).
      correcciones: lista de dicts {index, original, corrected} para log.
    """
    if not tps or entry is None:
        return list(tps), []

    direction = (direction or "").upper()

    def es_valido(tp):
        if tp is None:
            return False
        if direction == "BUY" and tp <= entry:
            return False
        if direction == "SELL" and tp >= entry:
            return False
        return abs(tp - entry) <= max_dist_usd

    corregidos = list(tps)
    correcciones = []

    for i, tp in enumerate(tps):
        if es_valido(tp):
            continue
        # TP absurdo — buscar vecinos validos para interpolar
        prev_valid = next((tps[j] for j in range(i - 1, -1, -1)
                           if es_valido(tps[j])), None)
        next_valid = next((tps[j] for j in range(i + 1, len(tps))
                           if es_valido(tps[j])), None)

        # Target esperado por escalonado
        if prev_valid is not None and next_valid is not None:
            target = (prev_valid + next_valid) / 2
        elif prev_valid is not None:
            # Continuar la progresion: ~$2 mas alla del anterior
            target = prev_valid + (2 if direction == "BUY" else -2)
        elif next_valid is not None:
            target = next_valid + (-2 if direction == "BUY" else 2)
        else:
            target = entry  # sin vecinos: ancla al entry

        # Generar candidatos y filtrar los plausibles
        plausibles = [c for c in _correction_candidates(tp) if es_valido(c)]
        if not plausibles:
            continue  # no se puede corregir — se deja el original

        best = min(plausibles, key=lambda c: abs(c - target))
        corregidos[i] = best
        correcciones.append({"index": i, "original": tp, "corrected": best,
                              "target_interpolado": round(target, 2)})

    return corregidos, correcciones


# ─── Validacion de coherencia direccional ─────────────────────────────────────

def levels_consistent_with_direction(direction: str, entry: float,
                                     tps: list | None = None,
                                     sl: float | None = None) -> dict:
    """Valida que TPs y SL sean COHERENTES con la direccion y entry.

    Para BUY: todos los TPs > entry, SL < entry.
    Para SELL: todos los TPs < entry, SL > entry.

    Casos reales detectados (sesion 2026-05-13):
      - canal2_12334: canal mando "SL 4796" para BUY @ 4704.84
        (typo del trader: queria 4696). MT5 rechazo 216 veces antes del cap.
      - canal2_12338: canal mando range 4780-4785 para SELL @ 4680.41
        (typo del trader: queria 4680-4685). Genero TPs absurdos.

    Esta funcion permite al listener DETECTAR esos typos y NO aplicar valores
    incoherentes — preservando los valores anteriores (predictor o ultimo
    edit valido) y esperando a que el canal corrija.

    Devuelve dict:
      {
        "tps_ok": bool,
        "tps_problems": list[str],
        "sl_ok": bool,
        "sl_problem": str | None,
        "ok": bool,                    # tps_ok AND sl_ok
        "any_problem": str | None,     # primer problema encontrado (para log)
      }
    """
    direction = (direction or "").upper()
    if direction not in ("BUY", "SELL"):
        # Direction desconocida: no podemos validar, dar pase.
        return {"tps_ok": True, "tps_problems": [],
                "sl_ok": True, "sl_problem": None,
                "ok": True, "any_problem": None}

    tps_problems = []
    if tps:
        for i, tp in enumerate(tps):
            if direction == "BUY" and tp <= entry:
                tps_problems.append(
                    f"TP{i+1}={tp} <= entry={entry} (BUY requiere TP > entry)")
            elif direction == "SELL" and tp >= entry:
                tps_problems.append(
                    f"TP{i+1}={tp} >= entry={entry} (SELL requiere TP < entry)")
    tps_ok = len(tps_problems) == 0

    sl_problem = None
    if sl is not None:
        if direction == "BUY" and sl >= entry:
            sl_problem = f"SL={sl} >= entry={entry} (BUY requiere SL < entry)"
        elif direction == "SELL" and sl <= entry:
            sl_problem = f"SL={sl} <= entry={entry} (SELL requiere SL > entry)"
    sl_ok = sl_problem is None

    any_problem = (tps_problems[0] if tps_problems else sl_problem)
    return {
        "tps_ok": tps_ok, "tps_problems": tps_problems,
        "sl_ok": sl_ok, "sl_problem": sl_problem,
        "ok": tps_ok and sl_ok,
        "any_problem": any_problem,
    }


# ─── DCA levels ───────────────────────────────────────────────────────────────

def dca_limit_prices(direction: str, range_low: float, range_high: float,
                      step: float = 1.0, entry_price: float | None = None) -> list[float]:
    """
    Devuelve precios DCA dentro del rango. Actúa como "buy/sell limit lógico":
    el código espera a que el precio TOQUE cada nivel y ahí abre mercado.

    Si se pasa entry_price, filtra niveles al lado ADVERSO de la entrada:
      BUY  → niveles por debajo de entry_price (el precio cae → DCA mejora avg)
      SELL → niveles por encima de entry_price (el precio sube → DCA mejora avg)

    Sin entry_price, devuelve todos los niveles del rango (comportamiento legacy).
    """
    levels: list[float] = []
    if direction == "BUY":
        price = range_high - step
        while price >= range_low - 0.01:
            levels.append(round(price, 2))
            price -= step
    else:
        price = range_low + step
        while price <= range_high + 0.01:
            levels.append(round(price, 2))
            price += step

    if entry_price is not None:
        if direction == "BUY":
            levels = [l for l in levels if l < entry_price]
        else:
            levels = [l for l in levels if l > entry_price]

    return levels


def dca_equispaced_prices(direction: str, range_low: float, range_high: float,
                           n_entries: int, entry_price: float | None = None) -> list[float]:
    """Devuelve N-1 niveles DCA equiespaciados dentro del rango (excluye el extremo
    cercano que ya cubre el market inicial). Para Canal 2 estrategia intra_dca.

    Ejemplo:  direction=BUY, rng=(2000, 2010), n_entries=4
              → step = 10/3 = 3.33
              → niveles BUY: [2010-3.33, 2010-6.67, 2010-10] = [2006.67, 2003.33, 2000]
              (el market ya cubre 2010)

    Si se pasa entry_price, filtra los que están al lado adverso (idéntico a
    dca_limit_prices). Para BUY → niveles < entry_price. Para SELL → niveles > entry_price.
    """
    if n_entries <= 1:
        return []

    span = range_high - range_low
    step = span / (n_entries - 1)

    if direction == "BUY":
        # Empieza desde range_high (extremo cercano), genera N puntos equiespaciados,
        # descarta el primero (el market ya lo cubre).
        levels = [round(range_high - i * step, 2) for i in range(1, n_entries)]
    else:
        # SELL → empieza desde range_low, va subiendo
        levels = [round(range_low + i * step, 2) for i in range(1, n_entries)]

    if entry_price is not None:
        if direction == "BUY":
            levels = [l for l in levels if l < entry_price]
        else:
            levels = [l for l in levels if l > entry_price]

    return levels


def far_extreme_price(direction: str, range_low: float, range_high: float) -> float:
    """Devuelve el extremo OPUESTO al lado natural de entrada del trade.

    Para BUY  → range_low  (el precio caería hasta ahí, allí entramos a mejor precio)
    Para SELL → range_high (el precio subiría hasta ahí)

    Usado por Canal 1 estrategia "extremes": market en extremo cercano +
    limit en extremo lejano (la 2ª entrada solo si el precio se va en contra).
    """
    return range_low if direction == "BUY" else range_high
