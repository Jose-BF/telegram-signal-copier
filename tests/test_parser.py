"""
test_parser.py — Suite de regresión para parser.py.

Cubre:
  - is_canal2_entry / is_canal1_signal_text (detección)
  - parse_canal2 / parse_canal1_text (parsing completo)
  - _extract_range incluyendo abreviado "4785-90"
  - _extract_tps con varios separadores y filtro de rango XAUUSD
  - _extract_sl con alias SP
  - dca_equispaced_prices (intra_dca)
  - dca_limit_prices (DCA por step)
  - far_extreme_price (extremo opuesto)
  - predict_levels (BUY/SELL desde rango)

Casos REALES capturados del journal de produccion estan marcados con
# REAL → conftest.py para no inventar y reflejar lo que el bot ve en vivo.
"""

import pytest

from parser import (
    is_canal1_signal_text,
    is_canal2_entry,
    parse_canal1_text,
    parse_canal2,
    parse_canal2_zone_plan,
    _extract_range,
    _extract_tps,
    _extract_sl,
    _direction,
    _parse_abbreviated_range,
    dca_equispaced_prices,
    dca_limit_prices,
    far_extreme_price,
    predict_levels,
)
from tests.conftest import (
    CANAL2_ENTRY_NEW,
    CANAL2_ENTRY_NEW_SELL,
    CANAL2_ENTRY_WITH_RANGE,
    CANAL2_ENTRY_WITH_RANGE_BUY,
    CANAL2_FULL_SIGNAL,
    CANAL1_TEXT_SELL,
    CANAL1_TEXT_BUY_SINGLE,
    CANAL1_TEXT_RELAXED,
    REAL_RANGES_CANAL2,
)


# ─── _direction ─────────────────────────────────────────────────────────────

class TestDirection:
    def test_buy_uppercase(self):
        assert _direction("XAU USD BUY NOW") == "BUY"

    def test_sell_uppercase(self):
        assert _direction("XAU USD SELL NOW") == "SELL"

    def test_buy_mixed_case(self):
        assert _direction("Buy gold now") == "BUY"

    def test_sell_lowercase(self):
        assert _direction("just sell here") == "SELL"

    def test_no_direction(self):
        assert _direction("TP1 hit, running well") is None

    def test_empty(self):
        assert _direction("") is None

    def test_immediate_sell_command_wins_over_later_buy_commentary(self):
        assert (
            _direction("Sell Gold Now. Buyers may defend 4030")
            == "SELL"
        )

    def test_valid_command_wins_over_earlier_negated_command(self):
        text = "Do not Buy Gold Now. Sell Gold Now"

        assert is_canal2_entry(text) is True
        assert _direction(text) == "SELL"
        assert parse_canal2(text)["direction"] == "SELL"


# ─── is_canal2_entry ────────────────────────────────────────────────────────

class TestIsCanal2Entry:
    def test_buy_now_xau(self):
        assert is_canal2_entry("XAU USD BUY NOW") is True

    def test_sell_now_xau(self):
        assert is_canal2_entry("XAU USD SELL NOW") is True

    def test_buy_now_with_range(self):
        # Edit posterior con rango incluido — sigue siendo entry
        assert is_canal2_entry("XAU USD BUY NOW\n\n4795-4799") is True

    def test_buy_now_gold_alias(self):
        assert is_canal2_entry("GOLD BUY NOW") is True

    def test_new_format_buy_gold_now(self):
        assert is_canal2_entry("Buy Gold Now") is True

    def test_new_format_sell_zone_now(self):
        assert is_canal2_entry("Sell Zone Now") is True

    def test_repeat_zone_command_is_entry(self):
        assert is_canal2_entry("Sell zone again now") is True

    def test_context_with_right_now_and_potential_sell_is_not_entry(self):
        text = (
            "As mentioned on the live call I will start to send charts so "
            "you can see what I see & where I mark my zones!\n\n"
            "Right now we are between 2 key areas\n\n"
            "Potential sell at 4051"
        )
        assert is_canal2_entry(text) is False

    @pytest.mark.parametrize(
        "text",
        [
            "Do not buy Gold now",
            "Don't SELL GOLD NOW",
            "Never buy Gold now",
            "No, Buy Gold Now",
            "Not a Sell Gold Now",
        ],
    )
    def test_negated_immediate_order_is_not_entry(self, text):
        assert is_canal2_entry(text) is False

    @pytest.mark.parametrize(
        "text",
        [
            "Wait is over, Buy Gold Now",
            "No need to wait. Buy Gold Now",
            "Potential confirmed: Sell Gold Now",
        ],
    )
    def test_completed_context_allows_immediate_order(self, text):
        assert is_canal2_entry(text) is True

    def test_unresolved_conditional_order_is_not_entry(self):
        assert (
            is_canal2_entry(
                "If price closes above 4050, Buy Gold Now"
            )
            is False
        )

    def test_possible_setup_is_not_entry(self):
        text = "Possible buy coming at 4054 area\n\nWait for the signal"
        assert is_canal2_entry(text) is False

    def test_full_signal_with_tps_sl(self):
        # Mensaje completo (BUY NOW + range + TPs + SL) sigue siendo entry
        assert is_canal2_entry(CANAL2_FULL_SIGNAL) is True

    def test_lowercase(self):
        assert is_canal2_entry("xau buy now") is True

    def test_management_msg_not_entry(self):
        assert is_canal2_entry("TP1 hit") is False

    def test_close_all_not_entry(self):
        assert is_canal2_entry("Close all positions") is False

    def test_just_buy_no_now(self):
        # Sin "NOW" no es entry oficial canal 2
        assert is_canal2_entry("XAU USD BUY") is False

    def test_just_now_no_buy(self):
        assert is_canal2_entry("XAU USD now") is False

    def test_empty(self):
        assert is_canal2_entry("") is False


# ─── parse_canal2_zone_plan ─────────────────────────────────────────────────

class TestParseCanal2ZonePlan:
    def test_real_sell_limit_plan_is_future_zone_not_market_entry(self):
        text = (
            "Good Evening All\n\n"
            "I am still holding my buys risk free\n\n"
            "I am looking at a possible sell around 4121 - 4125 area\n\n"
            "We can expect a reaction at this zone.\n\n"
            "You can consider a Sell Limit with the following parameters\n\n"
            "Sell Limit\n"
            "Entry 4121-4125\n"
            "Taps 4118/4115/4110/4100\n"
            "SL 4131"
        )

        assert is_canal2_entry(text) is False
        assert parse_canal2_zone_plan(text) == {
            "direction": "SELL",
            "zones": [[4121.0, 4125.0]],
            "target": None,
            "tps": [],
            "sl": 4131.0,
            "has_open_runner": False,
        }

    def test_single_future_sell_zone(self):
        parsed = parse_canal2_zone_plan(
            "Next Sell Zone at 4030\n\n"
            "Bear in mind FOMC at 7\n\n"
            "Look for a quick reaction"
        )

        assert parsed == {
            "direction": "SELL",
            "zones": [[4030.0, 4030.0]],
            "target": None,
            "tps": [],
            "sl": None,
            "has_open_runner": False,
        }

    def test_single_future_zone_with_reaction_language(self):
        parsed = parse_canal2_zone_plan(
            "Next Sell Zone we can expect a reaction is 4017"
        )

        assert parsed == {
            "direction": "SELL",
            "zones": [[4017.0, 4017.0]],
            "target": None,
            "tps": [],
            "sl": None,
            "has_open_runner": False,
        }

    def test_bare_levels_with_expected_sell_areas_are_zone_plan(self):
        parsed = parse_canal2_zone_plan(
            "4007\n4010\n4017\n\n"
            "These are all strong areas we can expect gold to sell from"
        )

        assert parsed == {
            "direction": "SELL",
            "zones": [
                [4007.0, 4007.0],
                [4010.0, 4010.0],
                [4017.0, 4017.0],
            ],
            "target": None,
            "tps": [],
            "sl": None,
            "has_open_runner": False,
        }

    def test_multi_zone_plan(self):
        parsed = parse_canal2_zone_plan(
            "Buy Zones Marked Out\n\n4075-4073\n4070-4069"
        )

        assert parsed == {
            "direction": "BUY",
            "zones": [[4073.0, 4075.0], [4069.0, 4070.0]],
            "target": None,
            "tps": [],
            "sl": None,
            "has_open_runner": False,
        }

    def test_immediate_zone_now_is_not_future_plan(self):
        assert parse_canal2_zone_plan("Sell Zone Now") is None

    def test_complete_zone_preserves_trade_levels(self):
        parsed = parse_canal2_zone_plan(
            "Gold Buy Zone\n"
            "4058 - 4053\n"
            "Targets\n"
            "4060\n"
            "4062\n"
            "Open\n"
            "SL 4050"
        )

        assert parsed == {
            "direction": "BUY",
            "zones": [[4053.0, 4058.0]],
            "target": None,
            "tps": [4060.0, 4062.0],
            "sl": 4050.0,
            "has_open_runner": True,
        }

    def test_session_map_can_inherit_direction_without_becoming_a_signal(self):
        parsed = parse_canal2_zone_plan(
            "The zones are\n4073-4071\n4068-4067",
            inherited_direction="BUY",
        )

        assert parsed == {
            "direction": "BUY",
            "zones": [[4071.0, 4073.0], [4067.0, 4068.0]],
            "target": None,
            "tps": [],
            "sl": None,
            "has_open_runner": False,
        }


# ─── is_canal1_signal_text ──────────────────────────────────────────────────

class TestIsCanal1SignalText:
    """Filtro relajado tras 2026-05-06: BUY/SELL/LONG/SHORT + GOLD/XAU/ORO + TP."""

    def test_canonical_sell_gold_now(self):
        assert is_canal1_signal_text(CANAL1_TEXT_SELL) is True

    def test_canonical_buy_gold_single(self):
        assert is_canal1_signal_text(CANAL1_TEXT_BUY_SINGLE) is True

    def test_relaxed_no_now(self):
        # Variante sin "NOW" — debe pasar tras el relax
        assert is_canal1_signal_text(CANAL1_TEXT_RELAXED) is True

    def test_long_xau_tp(self):
        # Direccion alternativa "LONG"
        assert is_canal1_signal_text("LONG XAU @4700 TP1 4710") is True

    def test_short_oro_tp(self):
        # "ORO" como alias de gold (es)
        assert is_canal1_signal_text("SHORT ORO TP 4500") is True

    def test_missing_direction(self):
        assert is_canal1_signal_text("Gold @4700 TP1 4705") is False

    def test_missing_gold(self):
        # Solo BUY + TP, sin XAU/GOLD/ORO
        assert is_canal1_signal_text("BUY @4700 TP1 4705") is False

    def test_missing_tp(self):
        assert is_canal1_signal_text("BUY GOLD @4700 SL 4690") is False

    def test_management_msg(self):
        # "Move SL to BE" no es signal text
        assert is_canal1_signal_text("Move SL to BE") is False

    def test_empty(self):
        assert is_canal1_signal_text("") is False

    def test_status_update_no_es_senal(self):
        """REGRESION canal1_19778 (2026-05-19): un parte de estado del canal
        NO es una entrada. El bot lo tomo por señal, abrio 4 posiciones y las
        dejo naked → el usuario las cerro a mano con -$129.

        El mensaje contiene 'buy', 'gold'/'xau' y la cadena 'TP' (en "TP1
        hit"), pero NINGUN TP con precio. is_canal1_signal_text debe
        rechazarlo: una señal real lleva TPs con NIVEL numerico."""
        update = ("**GOLD UPDATE — XAUUSD** Gold is still holding around our "
                  "buy entry zone at 4538-4540 after the earlier TP1 hit. "
                  "We already secured 80+ pips, price is consolidating.")
        assert is_canal1_signal_text(update) is False

    def test_tp_hit_announcement_no_es_senal(self):
        """Anuncio puro de "TP hit" — menciona dir+gold+TP pero sin precio."""
        assert is_canal1_signal_text("BUY GOLD — TP1 hit, 80 pips ✅") is False


# ─── _parse_abbreviated_range ───────────────────────────────────────────────

class TestParseAbbreviatedRange:
    def test_full_two_floats(self):
        assert _parse_abbreviated_range("4785.0-4790.0") == (4785.0, 4790.0)

    def test_abbreviated_short(self):
        # "4785-90" → 4790 expandido
        assert _parse_abbreviated_range("4785-90") == (4785.0, 4790.0)

    def test_abbreviated_with_centena_jump(self):
        # "4795-05" debería expandir a 4805 si la diferencia hace sentido
        result = _parse_abbreviated_range("4795-05")
        assert result is not None
        lo, hi = result
        # Acepta cualquier interpretación razonable (4795-4805 o 4795-4805)
        assert lo == 4795.0
        assert hi in (4805.0,)

    def test_abbreviated_decimal_suffix_keeps_left_price_context(self):
        # REAL C1: "4488-95.00" means 4488-4495, not 95 dollars.
        assert _parse_abbreviated_range("4488-95.00") == (4488.0, 4495.0)

    def test_slash_separator(self):
        # "4745/50" — usar / como separador
        assert _parse_abbreviated_range("4745/50") == (4745.0, 4750.0)

    def test_reverse_order_swap(self):
        # "4790-4785" debería ordenar
        assert _parse_abbreviated_range("4790-4785") == (4785.0, 4790.0)

    def test_invalid_single_number(self):
        assert _parse_abbreviated_range("4785") is None

    def test_invalid_text(self):
        assert _parse_abbreviated_range("not a range") is None


# ─── _extract_range ─────────────────────────────────────────────────────────

class TestExtractRange:
    def test_canal2_entry_with_range(self):
        assert _extract_range("XAU USD SELL NOW\n\n4585-4590") == (4585.0, 4590.0)

    def test_full_signal(self):
        assert _extract_range(CANAL2_FULL_SIGNAL) == (4795.0, 4799.0)

    def test_canal1_abbreviated(self):
        # CANAL1_TEXT_SELL tiene "4785-90"
        result = _extract_range(CANAL1_TEXT_SELL)
        assert result == (4785.0, 4790.0)

    def test_no_range(self):
        assert _extract_range("XAU USD BUY NOW") is None

    def test_filters_out_xauusd_implausible(self):
        # IPs y typos como "192.168.1.1" o "33319" deben filtrarse
        # (3-5 dígitos, 1000-9999 range)
        assert _extract_range("192-168") is None  # fuera de rango XAUUSD
        assert _extract_range("100-200") is None

    @pytest.mark.parametrize("lo,hi", REAL_RANGES_CANAL2)
    def test_real_journal_ranges(self, lo, hi):
        # Reconstruimos el texto como lo vería el bot
        text = f"XAU USD BUY NOW\n\n{lo}-{hi}"
        assert _extract_range(text) == (lo, hi)


# ─── _extract_tps ───────────────────────────────────────────────────────────

class TestExtractTps:
    def test_full_signal(self):
        # CANAL2_FULL_SIGNAL tiene 5 TPs: 4801, 4803, 4805, 4807, 4810
        assert _extract_tps(CANAL2_FULL_SIGNAL) == [4801.0, 4803.0, 4805.0, 4807.0, 4810.0]

    def test_canal1_emojis(self):
        # CANAL1_TEXT_SELL tiene 4 TPs con emoji
        assert _extract_tps(CANAL1_TEXT_SELL) == [4780.0, 4775.0, 4770.0, 4765.0]

    def test_decimals(self):
        text = "TP1 4705.50\nTP2 4707.25\nTP3 4710.0"
        assert _extract_tps(text) == [4705.5, 4707.25, 4710.0]

    def test_pipe_separator(self):
        text = "TP1=4688.41 | TP2=4690.41 | TP3=4692.41"
        assert _extract_tps(text) == [4688.41, 4690.41, 4692.41]

    def test_single_tp(self):
        # canal 2 a veces manda solo TP1 primero
        assert _extract_tps("TP1 4705.50") == [4705.5]

    def test_new_canal2_targets_block(self):
        text = "Sell Gold Now\n\n4123.5 - 4128.5\n\nTargets \n4121.5\n4119.5\n4117\nOpen"
        assert _extract_tps(text) == [4121.5, 4119.5, 4117.0]

    def test_new_canal2_singular_target_block(self):
        text = "Buy Gold Now\n\n4061 - 4055\n\nTarget\n\n4063\n4065\n4067\nOpen"
        assert _extract_tps(text) == [4063.0, 4065.0, 4067.0]

    def test_parse_canal2_preserves_explicit_open_runner(self):
        parsed = parse_canal2(
            "Buy Gold Now\n\n4061 - 4055\n\n"
            "Target\n4063\n4065\n4067\nOpen\n\nSL 4051"
        )

        assert parsed["tps"] == [4063.0, 4065.0, 4067.0]
        assert parsed["has_open_runner"] is True

    def test_parse_canal2_keeps_final_target_semantics_separate(self):
        parsed = parse_canal2("Final target for this is 4436")

        assert parsed == {"final_target": 4436.0}

    def test_parse_canal2_full_signal_adds_final_target_after_numbered_tps(self):
        parsed = parse_canal2(
            "Sell Gold Now\n4477 - 4482\n"
            "TP1 4472\nTP2 4469\nTP3 4465\n"
            "Final target 4436\nSL 4487"
        )

        assert parsed["tps"] == [4472.0, 4469.0, 4465.0]
        assert parsed["final_target"] == 4436.0

    def test_no_tps(self):
        assert _extract_tps("Move SL to BE") == []

    def test_filters_url_with_dots(self):
        # "52.88.50" no debe matchear (más de 1 punto)
        assert _extract_tps("URL 52.88.50.1 TP") == []

    def test_too_many_decimals_truncated_not_rejected(self):
        # Comportamiento ACTUAL del regex: el grupo decimal es opcional con
        # cuantificador {1,3}. Si hay >3 decimales, matchea el entero ("4705")
        # y \b corta antes del ".". Resultado: 4705.0 (truncado, no rechazado).
        # Esto es razonable: el broker MT5 redondea a su tick size igualmente.
        assert _extract_tps("TP1 4705.50000") == [4705.0]


# ─── _extract_sl ────────────────────────────────────────────────────────────

class TestExtractSl:
    def test_basic_sl(self):
        assert _extract_sl("SL 4791") == 4791.0

    def test_sl_with_colon(self):
        assert _extract_sl("SL: 4791") == 4791.0

    def test_sl_decimal(self):
        assert _extract_sl("SL 4791.5") == 4791.5

    def test_sp_alias(self):
        # SP es alias de SL en canal 2 ("Stop Price")
        assert _extract_sl("SP 4716.50") == 4716.5

    def test_sp_with_levels(self):
        # Caso real del journal: "TP1 4705.50\n\nSP 4716.50"
        assert _extract_sl("TP1 4705.50\n\nSP 4716.50") == 4716.5

    def test_new_canal2_sl_invalid_slash(self):
        assert _extract_sl("SL/ invalid 4131.5") == 4131.5
        assert _extract_sl("SL/invalid 4045") == 4045.0

    def test_canal1_emoji(self):
        # CANAL1_TEXT_SELL: "❌ SL: 4798.00"
        assert _extract_sl(CANAL1_TEXT_SELL) == 4798.0

    def test_no_sl(self):
        assert _extract_sl("TP1 hit") is None

    def test_lowercase(self):
        assert _extract_sl("sl 4700") == 4700.0

    def test_sl_requires_immediate_number(self):
        # Comportamiento ACTUAL: el regex exige número INMEDIATAMENTE tras "SL "
        # (separadores aceptados: ":" o whitespace, sin texto entremedio).
        # "SL placed at 4700" → no matchea (palabra "placed" entre SL y 4700).
        # Esto es defensivo: evita capturar números de mensajes no-SL.
        # Si en el futuro quisiéramos capturar este caso, requeriría cambiar
        # el regex (riesgo de falsos positivos).
        assert _extract_sl("SL placed at 4700 for safety") is None
        # En cambio sí matchea cuando el número es inmediato:
        assert _extract_sl("SL 4700 for safety") == 4700.0
        assert _extract_sl("SL: 4700") == 4700.0


# ─── parse_canal2 ───────────────────────────────────────────────────────────

class TestParseCanal2:
    def test_only_direction(self):
        # "XAU USD BUY NOW" — primer mensaje del canal
        result = parse_canal2(CANAL2_ENTRY_NEW)
        assert result == {"direction": "BUY"}

    def test_only_direction_sell(self):
        result = parse_canal2(CANAL2_ENTRY_NEW_SELL)
        assert result == {"direction": "SELL"}

    def test_direction_with_range(self):
        # Edit con rango añadido
        result = parse_canal2(CANAL2_ENTRY_WITH_RANGE)
        assert result["direction"] == "SELL"
        assert result["range"] == (4585.0, 4590.0)
        assert "tps" not in result
        assert "sl" not in result

    def test_full_signal(self):
        result = parse_canal2(CANAL2_FULL_SIGNAL)
        assert result["direction"] == "BUY"
        assert result["range"] == (4795.0, 4799.0)
        assert result["tps"] == [4801.0, 4803.0, 4805.0, 4807.0, 4810.0]
        assert result["sl"] == 4791.0

    def test_buy_with_inverted_range_writing(self):
        # Canal real escribe BUY como "high-low" → parser detecta y ordena
        result = parse_canal2(CANAL2_ENTRY_WITH_RANGE_BUY)
        assert result["direction"] == "BUY"
        # range_low siempre <= range_high tras parse
        assert result["range"][0] <= result["range"][1]

    def test_management_msg_no_direction(self):
        # Mensajes de gestión sin "BUY/SELL" obvio
        result = parse_canal2("TP1 hit running well")
        assert "direction" not in result
        # Los TPs sin precio explícito no se extraen
        assert "tps" not in result

    def test_just_tps_and_sl(self):
        # Reply en canal 2: "TP1 4705.50\nSL 4720"
        result = parse_canal2("TP1 4705.50\nSL 4720")
        assert "direction" not in result
        assert result["tps"] == [4705.5]
        assert result["sl"] == 4720.0

    def test_bare_tp_and_sl_reply(self):
        # Caso real nuevo canal 2551: "TP 3991.5 SL 3978"
        result = parse_canal2("TP 3991.5 SL 3978")
        assert result["tps"] == [3991.5]
        assert result["sl"] == 3978.0

    def test_sl_is_phrase(self):
        # Caso real nuevo canal 2551: "SL is 3975 Most got an entry..."
        result = parse_canal2("SL is 3975 Most got an entry of 3985")
        assert result["sl"] == 3975.0

    def test_new_canal2_layered_full_signal(self):
        text = (
            "Sell Gold Now\n\n"
            "4123.5 - 4128.5\n\n"
            "Targets \n"
            "4121.5\n"
            "4119.5\n"
            "4117\n"
            "Open\n\n"
            "SL/invalid 4131.5"
        )
        result = parse_canal2(text)
        assert result["direction"] == "SELL"
        assert result["range"] == (4123.5, 4128.5)
        assert result["tps"] == [4121.5, 4119.5, 4117.0]
        assert result["sl"] == 4131.5


# ─── parse_canal1_text ──────────────────────────────────────────────────────

class TestParseCanal1Text:
    """Implementación delega a parse_canal2 — comparte logica."""

    def test_canal1_full_with_emojis(self):
        result = parse_canal1_text(CANAL1_TEXT_SELL)
        assert result["direction"] == "SELL"
        assert result["range"] == (4785.0, 4790.0)
        assert result["tps"] == [4780.0, 4775.0, 4770.0, 4765.0]
        assert result["sl"] == 4798.0

    def test_canal1_single_price_no_range(self):
        # CANAL1_TEXT_BUY_SINGLE tiene "@4810" no rango
        result = parse_canal1_text(CANAL1_TEXT_BUY_SINGLE)
        assert result["direction"] == "BUY"
        # No debería tener range (es @ entry, no range)
        assert "range" not in result
        assert result["tps"] == [4815.0, 4820.0, 4825.0, 4830.0]
        assert result["sl"] == 4800.0


# ─── dca_equispaced_prices ──────────────────────────────────────────────────

class TestDcaEquispacedPrices:
    """Niveles intra_dca: N-1 puntos equiespaciados, market en extremo cercano."""

    def test_buy_5_entries(self):
        # n=5 → 4 limits descendentes desde range_high
        # span=4, step=1.0 → market en 4799, limits en [4798, 4797, 4796, 4795]
        levels = dca_equispaced_prices("BUY", 4795.0, 4799.0, n_entries=5)
        assert levels == [4798.0, 4797.0, 4796.0, 4795.0]

    def test_sell_5_entries(self):
        # SELL: market en range_low, limits ascendentes
        levels = dca_equispaced_prices("SELL", 4795.0, 4799.0, n_entries=5)
        assert levels == [4796.0, 4797.0, 4798.0, 4799.0]

    def test_n_entries_1(self):
        # n=1 → solo market, sin limits
        assert dca_equispaced_prices("BUY", 4795.0, 4799.0, n_entries=1) == []

    def test_n_entries_2(self):
        # n=2 → 1 limit en el extremo opuesto
        assert dca_equispaced_prices("BUY", 4795.0, 4799.0, n_entries=2) == [4795.0]

    def test_filter_by_entry_buy(self):
        # entry_price=4797 → solo niveles < 4797 (lado adverso BUY)
        levels = dca_equispaced_prices("BUY", 4795.0, 4799.0, n_entries=5,
                                        entry_price=4797.0)
        assert all(l < 4797.0 for l in levels)
        assert levels == [4796.0, 4795.0]

    def test_filter_by_entry_sell(self):
        # entry_price=4797 → solo niveles > 4797 (lado adverso SELL)
        levels = dca_equispaced_prices("SELL", 4795.0, 4799.0, n_entries=5,
                                        entry_price=4797.0)
        assert all(l > 4797.0 for l in levels)
        assert levels == [4798.0, 4799.0]


# ─── dca_limit_prices ───────────────────────────────────────────────────────

class TestDcaLimitPrices:
    """Niveles DCA por step (no equiespaciado por N)."""

    def test_buy_step_1(self):
        # range 4795-4799, step=1 → 4798, 4797, 4796, 4795
        levels = dca_limit_prices("BUY", 4795.0, 4799.0, step=1.0)
        assert levels == [4798.0, 4797.0, 4796.0, 4795.0]

    def test_sell_step_1(self):
        levels = dca_limit_prices("SELL", 4795.0, 4799.0, step=1.0)
        assert levels == [4796.0, 4797.0, 4798.0, 4799.0]

    def test_filter_by_entry_buy(self):
        levels = dca_limit_prices("BUY", 4795.0, 4799.0, step=1.0,
                                   entry_price=4797.0)
        assert all(l < 4797.0 for l in levels)


# ─── far_extreme_price ──────────────────────────────────────────────────────

class TestFarExtremePrice:
    def test_buy_returns_low(self):
        # BUY: el precio adverso es bajar → range_low
        assert far_extreme_price("BUY", 4795.0, 4799.0) == 4795.0

    def test_sell_returns_high(self):
        # SELL: el precio adverso es subir → range_high
        assert far_extreme_price("SELL", 4795.0, 4799.0) == 4799.0


# ─── predict_levels ────────────────────────────────────────────────────────

class TestPredictLevels:
    """Predicción provisional cuando llega rango sin TPs/SL."""

    def test_buy_offsets(self):
        # Offsets re-calibrados 2026-05-15: (3,5,7,9)
        # entry = range_high = 4800
        # TPs = entry+[3,5,7,9] = [4803, 4805, 4807, 4809]
        # SL = range_low - 4 = 4791
        result = predict_levels("BUY", 4795.0, 4800.0)
        assert result["tps"] == [4803.0, 4805.0, 4807.0, 4809.0]
        assert result["sl"] == 4791.0

    def test_sell_offsets(self):
        # entry = range_low = 4795
        # TPs = entry-[3,5,7,9] = [4792, 4790, 4788, 4786]
        # SL = range_high + 4 = 4804
        result = predict_levels("SELL", 4795.0, 4800.0)
        assert result["tps"] == [4792.0, 4790.0, 4788.0, 4786.0]
        assert result["sl"] == 4804.0

    def test_tp1_offset_is_3(self):
        # TP1 re-calibrado a +3 (mediana real del canal = 2.82 USD)
        result = predict_levels("BUY", 4795.0, 4800.0)
        assert result["tps"][0] - 4800.0 == 3.0  # TP1 = entry + 3

    def test_returns_4_tps(self):
        result = predict_levels("BUY", 4000.0, 4005.0)
        assert len(result["tps"]) == 4
        assert "sl" in result


# ─── levels_consistent_with_direction ──────────────────────────────────────

class TestLevelsConsistentWithDirection:
    """Validacion direccional de TPs/SL — detecta typos del canal.

    Casos REALES del journal sesion 2026-05-13:
      - canal2_12334: SL=4796 para BUY @ 4704 (typo del trader: queria 4696)
      - canal2_12338: range/TPs absurdos para SELL @ 4680 (typo: 100usd off)
    """

    def test_buy_all_valid(self):
        from parser import levels_consistent_with_direction
        result = levels_consistent_with_direction(
            "BUY", entry=4700.0, tps=[4702, 4704, 4706, 4708], sl=4690)
        assert result["ok"] is True
        assert result["tps_ok"] is True
        assert result["sl_ok"] is True
        assert result["tps_problems"] == []
        assert result["sl_problem"] is None

    def test_sell_all_valid(self):
        from parser import levels_consistent_with_direction
        result = levels_consistent_with_direction(
            "SELL", entry=4700.0, tps=[4698, 4696, 4694], sl=4710)
        assert result["ok"] is True

    def test_buy_sl_above_entry_REJECTED(self):
        """Caso real canal2_12334: BUY @ 4704.84 con SL=4796 (typo)."""
        from parser import levels_consistent_with_direction
        result = levels_consistent_with_direction(
            "BUY", entry=4704.84, tps=[4707.5], sl=4796.0)
        assert result["ok"] is False
        assert result["sl_ok"] is False
        assert result["tps_ok"] is True  # los TPs si son validos
        assert "SL=4796" in result["sl_problem"]
        assert "BUY" in result["sl_problem"]

    def test_sell_tps_above_entry_REJECTED(self):
        """Caso real canal2_12338: SELL @ 4680.41 con TPs=[4778.5,4776.5,...]
        (typo del range: 4780-4785 en vez de 4680-4685).

        Nota: el SL=4789.5 SI esta al lado correcto para SELL (arriba del entry),
        solo los TPs estan invertidos. El validador detecta solo eso."""
        from parser import levels_consistent_with_direction
        result = levels_consistent_with_direction(
            "SELL", entry=4680.41,
            tps=[4778.5, 4776.5, 4774.5, 4772.5], sl=4789.5)
        assert result["ok"] is False
        assert result["tps_ok"] is False
        assert result["sl_ok"] is True   # SL si esta al lado correcto (arriba)
        assert len(result["tps_problems"]) == 4   # los 4 TPs invalidos
        assert "TP1=4778.5" in result["tps_problems"][0]

    def test_partial_invalid_only_one_tp(self):
        """Si solo 1 TP esta invalido, lo marca pero el resto OK."""
        from parser import levels_consistent_with_direction
        result = levels_consistent_with_direction(
            "BUY", entry=4700.0, tps=[4702, 4699, 4706])  # tp2=4699 < entry
        assert result["ok"] is False
        assert result["tps_ok"] is False
        assert len(result["tps_problems"]) == 1
        assert "TP2=4699" in result["tps_problems"][0]

    def test_sell_sl_below_entry_REJECTED(self):
        from parser import levels_consistent_with_direction
        result = levels_consistent_with_direction(
            "SELL", entry=4700.0, tps=[4698, 4696], sl=4690)
        assert result["sl_ok"] is False
        assert "SL=4690" in result["sl_problem"]

    def test_no_tps_no_sl_passes(self):
        """Sin valores que validar, devuelve ok=True."""
        from parser import levels_consistent_with_direction
        result = levels_consistent_with_direction("BUY", entry=4700.0)
        assert result["ok"] is True

    def test_unknown_direction_passes(self):
        """Direccion desconocida: no podemos validar, no bloqueamos."""
        from parser import levels_consistent_with_direction
        result = levels_consistent_with_direction(
            "OTHER", entry=4700.0, tps=[4000], sl=9999)
        assert result["ok"] is True

    def test_lowercase_direction_works(self):
        from parser import levels_consistent_with_direction
        result = levels_consistent_with_direction(
            "buy", entry=4700.0, tps=[4702], sl=4796)
        assert result["sl_ok"] is False  # detecta typo aunque venga lowercase

    def test_any_problem_field_for_logging(self):
        """any_problem siempre devuelve el primer problema (para log/notify)."""
        from parser import levels_consistent_with_direction
        result = levels_consistent_with_direction(
            "BUY", entry=4700.0, tps=[4699], sl=4710)
        # tps_problem aparece primero
        assert "TP1=4699" in result["any_problem"]


# ─── validate_range_vs_entry ──────────────────────────────────────────────

class TestValidateRangeVsEntry:
    """Validacion del range vs entry para detectar typos del canal.

    Caso REAL canal2_12338 (sesion 2026-05-13): canal mando range
    4780-4785 para SELL @ 4680.41 (typo +100 USD)."""

    def test_range_around_entry_ok(self):
        from parser import validate_range_vs_entry
        # SELL @ 4680, range [4680-4685]: entry dentro del rango → ok
        result = validate_range_vs_entry("SELL", 4680.0, 4680.0, 4685.0)
        assert result["ok"] is True
        assert result["min_dist_usd"] == 0.0

    def test_range_close_to_entry_ok(self):
        from parser import validate_range_vs_entry
        # entry a 5usd del rango, dentro de tolerancia (default 30)
        result = validate_range_vs_entry("BUY", 4700.0, 4705.0, 4710.0)
        assert result["ok"] is True
        assert result["min_dist_usd"] == 5.0

    def test_range_far_from_entry_REJECTED(self):
        """Caso real canal2_12338: SELL @ 4680.41, range 4780.5-4785.5 (+100$)."""
        from parser import validate_range_vs_entry
        result = validate_range_vs_entry("SELL", 4680.41, 4780.5, 4785.5)
        assert result["ok"] is False
        assert result["min_dist_usd"] == pytest.approx(100.09, abs=0.1)
        assert "Probable typo" in result["reason"]

    def test_custom_max_dist(self):
        from parser import validate_range_vs_entry
        # Con max_dist=5, un range a 6usd ya rechaza
        result = validate_range_vs_entry("BUY", 4700.0, 4706.0, 4710.0,
                                         max_dist_usd=5.0)
        assert result["ok"] is False


class TestPredictSlFromEntry:
    """SL provisional desde entry cuando rechazamos el range del canal."""

    def test_buy_sl_below_entry(self):
        from parser import predict_sl_from_entry
        # BUY: SL debe estar por debajo del entry
        sl = predict_sl_from_entry("BUY", 4700.0, offset_usd=10.0)
        assert sl == 4690.0

    def test_sell_sl_above_entry(self):
        from parser import predict_sl_from_entry
        # SELL: SL debe estar por encima del entry
        sl = predict_sl_from_entry("SELL", 4700.0, offset_usd=10.0)
        assert sl == 4710.0

    def test_default_offset_is_10(self):
        from parser import predict_sl_from_entry
        sl = predict_sl_from_entry("BUY", 4700.0)
        assert sl == 4690.0  # default offset = 10

    def test_canal2_12338_protective_sl(self):
        """Caso real: SELL @ 4680.41 → SL protector deberia ser ~4690."""
        from parser import predict_sl_from_entry
        sl = predict_sl_from_entry("SELL", 4680.41)
        assert sl == 4690.41


class TestCorrectTpTypos:
    """Correccion de typos en TPs por interpolacion con vecinos.

    Caso REAL canal2_12382 (2026-05-14): BUY @ 4694.44, TP3 llego como
    '46700' (typo) cuando los vecinos eran TP2=4698 y TP4=4702."""

    def test_canal2_12382_real_case(self):
        from parser import correct_tp_typos
        # BUY @ 4694.44, TPs con typo en indice 2
        tps = [4696.0, 4698.0, 46700.0, 4702.0, 4707.0]
        corrected, corrections = correct_tp_typos("BUY", 4694.44, tps)
        assert len(corrections) == 1
        assert corrections[0]["index"] == 2
        assert corrections[0]["original"] == 46700.0
        # El correcto debe estar ~4700 (entre 4698 y 4702)
        assert corrected[2] == 4700.0
        # Los demas TPs intactos
        assert corrected == [4696.0, 4698.0, 4700.0, 4702.0, 4707.0]

    def test_no_typo_no_change(self):
        from parser import correct_tp_typos
        tps = [4696.0, 4698.0, 4700.0, 4702.0]
        corrected, corrections = correct_tp_typos("BUY", 4694.0, tps)
        assert corrections == []
        assert corrected == tps

    def test_extra_zero_at_end(self):
        from parser import correct_tp_typos
        # SELL @ 4700: TP2 con cero extra "46900" -> 4690
        tps = [4697.0, 46900.0, 4693.0]
        corrected, corrections = correct_tp_typos("SELL", 4700.0, tps)
        assert len(corrections) == 1
        assert corrected[1] == 4690.0  # entre 4697 y 4693

    def test_uncorrectable_left_as_is(self):
        from parser import correct_tp_typos
        # Un TP imposible sin candidato plausible queda igual
        tps = [4696.0, 999999.0]
        corrected, corrections = correct_tp_typos("BUY", 4694.0, tps)
        # 999999 -> candidatos: 99999, 9999.99, ... ninguno cerca de 4694+-50
        assert corrected[1] == 999999.0  # sin corregir
        assert corrections == []

    def test_no_entry_no_correction(self):
        from parser import correct_tp_typos
        corrected, corrections = correct_tp_typos("BUY", None, [4696.0, 46700.0])
        assert corrections == []
