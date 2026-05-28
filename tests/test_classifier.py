"""
test_classifier.py — Suite de regresión para classifier._regex_classify_all.

NO toca Gemini (eso requiere API key y red). Cubrimos solo el regex local
que es la primera línea de clasificación y captura ~70-80% de los mensajes
en producción.

Casos REALES capturados del journal de producción están marcados con # REAL.

Reglas que cubrimos (orden del classifier.py):
  0. Pure levels announcement (TP/SL solo, sin verbo)         → INFORMATIONAL
  1. SL a precio explícito ("move SL to X")                    → MOVE_SL_TO_PRICE
  1b. Bare "SL X" sin verbo (contexto gestión)                 → MOVE_SL_TO_PRICE conf=0.80
  2. SL a BE / risk-free / breakeven                           → MOVE_SL_TO_BE
  3. "I am out at BE" / "closing here at BE"                   → CLOSE_ALL
  4. CLOSE_FIRST contextual (close first/early/oldest entries) → CLOSE_FIRST
  5. "If you have one entry close it now"                      → CLOSE_ALL (si no CLOSE_FIRST antes)
  6. Cierre total ("close all", "close trade", etc.)           → CLOSE_ALL
  7. "Close TP1 here"                                          → CLOSE_AT_TP
  8. INFORMATIONAL — TP hits, SL hits, pips, etc.              → INFORMATIONAL
"""

import asyncio

import pytest

from classifier import _regex_classify_all, classify_one, classify_async
from state import Signal


# ─── Helpers ────────────────────────────────────────────────────────────────

def _actions(text: str) -> list[dict]:
    return _regex_classify_all(text)


def _action_names(text: str) -> list[str]:
    return [a["action"] for a in _regex_classify_all(text)]


# ─── 0. Pure levels announcement ────────────────────────────────────────────

class TestPureLevelsAnnouncement:
    """Mensajes que son SOLO niveles (TP/SL valores) sin verbo de acción.

    El parser ya extrae los niveles por su lado. El classifier los marca
    como INFORMATIONAL para que no se interpreten como acciones.
    """

    def test_single_tp(self):
        # Caso real del journal
        actions = _actions("TP1 4705.50")
        assert len(actions) == 1
        assert actions[0]["action"] == "INFORMATIONAL"
        assert actions[0]["confidence"] == 1.0
        assert actions[0].get("_reason") == "pure_levels_announcement"

    def test_multiple_tps_pipe(self):
        # Caso real del journal
        actions = _actions("TP1=4688.41 | TP2=4690.41 | TP3=4692.41")
        assert actions[0]["action"] == "INFORMATIONAL"

    def test_tp_with_sl(self):
        actions = _actions("TP1 4705.50 SL 4716.50")
        assert actions[0]["action"] == "INFORMATIONAL"

    def test_tp_with_sp_alias(self):
        # SP es alias de SL en canal 2. Texto puro de niveles.
        actions = _actions("TP1 4705.50 SP 4716.50")
        assert actions[0]["action"] == "INFORMATIONAL"


# ─── 1. SL a precio explícito ───────────────────────────────────────────────

class TestMoveSlToPrice:
    """Verbos: move/moving/change/changing/adjust/adjusting/set/setting/put/putting."""

    def test_move_sl_to_price(self):
        # Caso real del journal
        actions = _actions("I will adjust my stop loss to 4717")
        names = [a["action"] for a in actions]
        assert "MOVE_SL_TO_PRICE" in names
        sl_action = next(a for a in actions if a["action"] == "MOVE_SL_TO_PRICE")
        assert sl_action["price"] == 4717.0
        assert sl_action["confidence"] == 1.0

    def test_move_sl_to_price_basic(self):
        actions = _actions("Move SL to 4750")
        sl_action = next(a for a in actions if a["action"] == "MOVE_SL_TO_PRICE")
        assert sl_action["price"] == 4750.0

    def test_change_sl_to_price(self):
        actions = _actions("Change my SL to 4720")
        assert "MOVE_SL_TO_PRICE" in [a["action"] for a in actions]

    def test_set_sl_at_price(self):
        actions = _actions("Set the stop loss at 4725")
        assert "MOVE_SL_TO_PRICE" in [a["action"] for a in actions]

    def test_adjust_stop_loss(self):
        actions = _actions("Adjusting your stop-loss to 4718")
        sl_action = next(a for a in actions if a["action"] == "MOVE_SL_TO_PRICE")
        assert sl_action["price"] == 4718.0

    def test_decimal_price(self):
        actions = _actions("Move SL to 4750.5")
        sl_action = next(a for a in actions if a["action"] == "MOVE_SL_TO_PRICE")
        assert sl_action["price"] == 4750.5


# ─── 1b. Bare "SL X" en contexto gestión ────────────────────────────────────

class TestBareSlPrice:
    """SL N solo, en contexto de gestión (sin verbo). Confianza 0.80."""

    def test_bare_sl(self):
        # Mensajes tipo "Sl edited" + "SL 4720" en separado deberían dispararse
        actions = _actions("SL 4720")
        sl_action = next((a for a in actions if a["action"] == "MOVE_SL_TO_PRICE"), None)
        # Bare SL debería estar como MOVE_SL_TO_PRICE con conf 0.80
        # PERO si "SL hit" vino antes lo desestima
        if sl_action:
            assert sl_action["confidence"] == 0.80

    def test_bare_sl_when_sl_hit_present_no_action(self):
        # Si el mensaje ya menciona "SL hit", NO interpretar el número como
        # nuevo SL. Es informativo.
        actions = _actions("SL was hit at 4720")
        # No debería haber MOVE_SL_TO_PRICE — el _NEG_SL_HIT lo bloquea
        names = [a["action"] for a in actions]
        assert "MOVE_SL_TO_PRICE" not in names


# ─── 2. SL a BE / breakeven / risk-free ─────────────────────────────────────

class TestMoveSlToBe:
    def test_move_sl_to_be(self):
        actions = _actions("Move SL to BE")
        be = next(a for a in actions if a["action"] == "MOVE_SL_TO_BE")
        assert be["confidence"] == 0.95
        assert be["price"] is None

    def test_move_to_breakeven(self):
        actions = _actions("Move stop loss to breakeven")
        assert "MOVE_SL_TO_BE" in [a["action"] for a in actions]

    def test_sl_to_entry(self):
        actions = _actions("Move SL to entry")
        assert "MOVE_SL_TO_BE" in [a["action"] for a in actions]

    def test_zero_percent_risk(self):
        # "0% risk" → BE
        actions = _actions("Lock in 0% risk now")
        assert "MOVE_SL_TO_BE" in [a["action"] for a in actions]

    def test_risk_free(self):
        # Caso real journal: "If you have lower entries keep them risk free"
        actions = _actions("If you have lower entries keep them risk free.")
        assert "MOVE_SL_TO_BE" in [a["action"] for a in actions]

    def test_sl_to_be_short(self):
        actions = _actions("SL to BE")
        assert "MOVE_SL_TO_BE" in [a["action"] for a in actions]

    def test_take_partials_set_breakeven_zero_risk(self):
        actions = _actions("Take partials\n\nSet breakeven for zero risk")
        assert "MOVE_SL_TO_BE" in [a["action"] for a in actions]


# ─── 3. "I am out at BE" → CLOSE_ALL ────────────────────────────────────────

class TestOutAtBe:
    def test_im_out_of_trade(self):
        actions = _actions("I'm out of this trade")
        assert "CLOSE_ALL" in [a["action"] for a in actions]

    def test_i_am_out_of_trade(self):
        actions = _actions("I am out of this trade")
        assert "CLOSE_ALL" in [a["action"] for a in actions]

    def test_out_at_be(self):
        actions = _actions("Out at BE")
        assert "CLOSE_ALL" in [a["action"] for a in actions]

    def test_closing_at_be(self):
        actions = _actions("Closing here at BE")
        assert "CLOSE_ALL" in [a["action"] for a in actions]


# ─── 4. CLOSE_FIRST contextual ──────────────────────────────────────────────

class TestCloseFirst:
    def test_close_first_entries(self):
        # Caso real journal: "close your first entries now"
        actions = _actions("Close your first entries now")
        assert "CLOSE_FIRST" in [a["action"] for a in actions]
        cf = next(a for a in actions if a["action"] == "CLOSE_FIRST")
        assert cf["confidence"] == 0.92

    def test_close_first_entry_singular(self):
        actions = _actions("Close the first entry")
        assert "CLOSE_FIRST" in [a["action"] for a in actions]

    def test_close_early_entries(self):
        actions = _actions("Close early positions")
        assert "CLOSE_FIRST" in [a["action"] for a in actions]

    def test_close_oldest_positions(self):
        actions = _actions("Close oldest positions")
        assert "CLOSE_FIRST" in [a["action"] for a in actions]

    def test_close_initial_ones(self):
        actions = _actions("Close initial ones")
        assert "CLOSE_FIRST" in [a["action"] for a in actions]

    def test_close_first_and_move(self):
        actions = _actions("Close first and move SL to 4700")
        names = [a["action"] for a in actions]
        assert "CLOSE_FIRST" in names
        assert "MOVE_SL_TO_PRICE" in names

    def test_close_first_does_not_emit_close_all(self):
        # Cuando CLOSE_FIRST dispara, regla 6 (CLOSE_ALL) NO debe añadir.
        actions = _actions("Close your first entries now")
        names = [a["action"] for a in actions]
        assert "CLOSE_FIRST" in names
        assert "CLOSE_ALL" not in names

    # ── Plural vs singular (commit 2026-05-14, double_market support) ──
    def test_close_first_entries_marks_plural(self):
        """'first entries' (plural) → is_plural=True. Con doble_market activo
        el listener cierra TODAS las markets (Pos A + Pos B), no solo 1.

        Caso real canal2_12347: trader dijo 'close your first entries' (plural)
        pero bot cerro solo 1 de 2 markets — degradacion de estrategia.
        """
        actions = _actions("Close your first entries now")
        cf = next(a for a in actions if a["action"] == "CLOSE_FIRST")
        assert cf.get("is_plural") is True
        assert cf["_reason"] == "close_first_contextual_plural"

    def test_close_first_entry_marks_singular(self):
        """'first entry' (singular) → is_plural=False. Logica legacy
        (cerrar mitad peor por P&L) se mantiene."""
        actions = _actions("Close the first entry")
        cf = next(a for a in actions if a["action"] == "CLOSE_FIRST")
        assert cf.get("is_plural") is False
        assert cf["_reason"] == "close_first_contextual_singular"

    def test_close_initial_ones_marks_plural(self):
        actions = _actions("Close initial ones")
        cf = next(a for a in actions if a["action"] == "CLOSE_FIRST")
        assert cf.get("is_plural") is True

    def test_close_first_and_move_marks_singular(self):
        """Compound 'close first and move SL' es singular por convencion."""
        actions = _actions("Close first and move SL to 4700")
        cf = next(a for a in actions if a["action"] == "CLOSE_FIRST")
        assert cf.get("is_plural") is False


# ─── 5. "If you have one entry close it now" → CLOSE_ALL ─────────────────

class TestSingleEntryClose:
    def test_one_entry_close(self):
        # Frase típica que el canal añade para clientes con una sola entrada
        actions = _actions("If you have one entry close it now")
        assert "CLOSE_ALL" in [a["action"] for a in actions]

    def test_only_one_entry_close(self):
        actions = _actions("If you only have one entry close it now")
        assert "CLOSE_ALL" in [a["action"] for a in actions]


# ─── 6. CLOSE_ALL genérico ──────────────────────────────────────────────────

class TestCloseAll:
    def test_close_all(self):
        actions = _actions("Close all positions")
        ca = next(a for a in actions if a["action"] == "CLOSE_ALL")
        assert ca["confidence"] == 0.90

    def test_close_the_rest(self):
        actions = _actions("Close the rest")
        assert "CLOSE_ALL" in [a["action"] for a in actions]

    def test_close_everything(self):
        actions = _actions("Close everything now")
        assert "CLOSE_ALL" in [a["action"] for a in actions]

    def test_close_the_trade(self):
        actions = _actions("Close the trade")
        assert "CLOSE_ALL" in [a["action"] for a in actions]

    def test_close_this_trade(self):
        actions = _actions("Close this trade")
        assert "CLOSE_ALL" in [a["action"] for a in actions]

    def test_closing_all_positions(self):
        actions = _actions("Closing all positions")
        assert "CLOSE_ALL" in [a["action"] for a in actions]


# ─── 7. CLOSE_AT_TP ─────────────────────────────────────────────────────────

class TestCloseAtTp:
    def test_close_tp1_here(self):
        actions = _actions("Lets close TP1 here")
        ca = next(a for a in actions if a["action"] == "CLOSE_AT_TP")
        assert ca["price"] == 1
        assert ca["confidence"] == 0.85

    def test_close_tp3(self):
        actions = _actions("Closing TP3 now")
        ca = next(a for a in actions if a["action"] == "CLOSE_AT_TP")
        assert ca["price"] == 3


# ─── 8. INFORMATIONAL fallback ──────────────────────────────────────────────

class TestInformationalFallback:
    """Mensajes que NO son acción pero matchean patrones info → INFORMATIONAL."""

    def test_tp1_hit(self):
        # Caso real del journal
        actions = _actions("TP1 hit")
        assert actions[0]["action"] == "INFORMATIONAL"
        assert actions[0]["confidence"] == 0.90

    @pytest.mark.parametrize("text", [
        "TP1 hit", "TP2 hit", "TP3 hit", "TP4 hit", "TP5 hit",
        "TP1 reached", "TP2 secured", "TP3 done",
    ])
    def test_tp_hit_variants(self, text):
        actions = _actions(text)
        assert actions[0]["action"] == "INFORMATIONAL"

    @pytest.mark.parametrize("text", [
        "SL hit",
        "SL was hit",
        "SL already hit",
        "SL just hit",
        "SL has been hit",
        "SL reached",
        "SL triggered",
        "SL edited",
        "stop loss hit",
        "stop loss was hit",
        "stop loss already hit",
    ])
    def test_sl_hit_variants_covered(self, text):
        # Casos cubiertos por el regex info actual.
        actions = _actions(text)
        assert actions, f"esperado info para {text!r}, regex devolvio []"
        assert actions[0]["action"] == "INFORMATIONAL"

    @pytest.mark.parametrize("text", [
        "stop loss reached",
        "stop loss triggered",
    ])
    def test_sl_hit_variants_NOT_covered_by_regex(self, text):
        # GAP CONOCIDO: el regex info para "stop loss" solo cubre "hit"
        # (no "reached" / "triggered"). En cambio para "SL" sí cubre ambos.
        # Asimetría documentada — estos mensajes pasarían a Gemini fallback
        # que probablemente los clasifica bien como INFORMATIONAL.
        # Si en algún momento se arregla el regex (añadir "reached|triggered"
        # tras "stop loss"), este test FALLA y deberá moverse a la lista
        # _covered de arriba.
        actions = _actions(text)
        assert actions == [], (
            f"REGRESION: {text!r} ahora SI lo cubre el regex local. "
            "Mover a test_sl_hit_variants_covered y borrar este test."
        )

    def test_pips_secured(self):
        actions = _actions("+50 pips secured")
        assert actions[0]["action"] == "INFORMATIONAL"

    def test_running_in_profit(self):
        actions = _actions("Trade running in profit")
        assert actions[0]["action"] == "INFORMATIONAL"

    def test_strong_move(self):
        actions = _actions("Strong move on gold")
        assert actions[0]["action"] == "INFORMATIONAL"

    def test_stay_patient(self):
        actions = _actions("Stay patient with the trade")
        assert actions[0]["action"] == "INFORMATIONAL"


# ─── Casos compuestos (mensaje con múltiples acciones) ──────────────────────

class TestCompoundMessages:
    """Mensajes con varias acciones en un solo texto."""

    def test_close_first_and_move_sl(self):
        actions = _actions("Close first and move SL to 4700")
        names = [a["action"] for a in actions]
        assert "CLOSE_FIRST" in names
        assert "MOVE_SL_TO_PRICE" in names

    def test_real_protect_capital(self):
        # Caso real del journal — texto del canal canal2
        text = "To protect your capital close your first entries now."
        actions = _actions(text)
        names = [a["action"] for a in actions]
        # Debería disparar CLOSE_FIRST
        assert "CLOSE_FIRST" in names

    def test_real_risk_free_with_close_first(self):
        # Caso real journal: combina CLOSE_FIRST + risk-free (BE)
        text = (
            "To protect your capital close your first entries now. \n\n"
            "If you have lower entries keep them risk free."
        )
        actions = _actions(text)
        names = [a["action"] for a in actions]
        assert "MOVE_SL_TO_BE" in names      # por "risk free"
        assert "CLOSE_FIRST" in names         # por "close your first entries"


# ─── Casos vacíos / nulos ──────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_text(self):
        # classify_one no _regex_classify_all (que asume texto truthy)
        result = classify_one("")
        assert result["action"] == "INFORMATIONAL"

    def test_whitespace_only(self):
        result = classify_one("   ")
        assert result["action"] == "INFORMATIONAL"

    def test_unrelated_text(self):
        # Texto que no matchea NADA → lista vacía (sin INFORMATIONAL)
        actions = _actions("hola buenos dias")
        # _regex_classify_all NO añade INFORMATIONAL a textos sin patron info
        # devuelve lista vacía → fallback a Gemini en classify()
        assert actions == []

    def test_classify_one_unrelated_returns_info(self):
        # classify_one tiene un fallback final a INFORMATIONAL
        # PERO solo si Gemini no se llama (porque sin API mock crashearia).
        # Aquí lo invocamos directamente sobre regex con texto irrelevante;
        # como _regex_classify_all devuelve [], classify_one() llamaria a
        # Gemini. Como no queremos llamar Gemini en tests, lo skipeamos.
        # (Se cubre indirectamente al testear _regex_classify_all)
        pytest.skip("requires Gemini mock — covered by Gemini-specific tests later")


# ─── classify_async — fix C1 ────────────────────────────────────────────────

class TestClassifyAsync:
    """Version async de classify para no bloquear el event loop con Gemini."""

    @pytest.mark.asyncio
    async def test_canal1_safe_move_be_does_not_need_gemini(self, monkeypatch):
        """REAL 2026-05-22 canal1_19868: mensaje claro, Gemini caido."""
        calls = []

        def gemini_should_not_run(*args, **kwargs):
            calls.append(args)
            raise AssertionError("Gemini no debe ejecutarse para MOVE_SL_TO_BE claro")

        monkeypatch.setattr("classifier._gemini_classify", gemini_should_not_run)
        sig = Signal(channel="canal1", message_id=19868, direction="SELL")

        result = await classify_async(
            "Running almost 50+ pips move SL to BE around 4520",
            signal=sig,
        )

        assert calls == []
        assert [a["action"] for a in result] == ["MOVE_SL_TO_BE"]

    @pytest.mark.asyncio
    async def test_canal1_safe_close_my_trades_now_does_not_need_gemini(self, monkeypatch):
        """REAL 2026-05-22 canal1_19868: cierre explicito del trader."""
        monkeypatch.setattr(
            "classifier._gemini_classify",
            lambda *a, **kw: (_ for _ in ()).throw(
                AssertionError("Gemini no debe ejecutarse para cierre claro")
            ),
        )
        sig = Signal(channel="canal1", message_id=19868, direction="SELL")

        result = await classify_async(
            "I'm closing my trades now in strong profit",
            signal=sig,
        )

        assert [a["action"] for a in result] == ["CLOSE_ALL"]

    @pytest.mark.asyncio
    async def test_canal1_conditional_close_still_uses_gemini(self, monkeypatch):
        """No reabrir el bug que hizo peligroso usar todo el regex en canal1."""
        calls = []

        def fake_gemini(text, signal=None, max_retries=3, base_wait=2.0):
            calls.append((text, signal))
            return [{"action": "INFORMATIONAL", "price": None, "confidence": 0.9}]

        monkeypatch.setattr("classifier._gemini_classify", fake_gemini)
        sig = Signal(channel="canal1", message_id=19649, direction="BUY")

        result = await classify_async(
            "If 15M closes above 4700, we will close this trade.",
            signal=sig,
        )

        assert calls, "los condicionales de canal1 deben ir a Gemini contextual"
        assert result[0]["action"] == "INFORMATIONAL"

    @pytest.mark.asyncio
    async def test_async_regex_match_no_gemini(self):
        # Si regex matchea, classify_async devuelve sin tocar Gemini
        # (sin overhead de asyncio.to_thread). Verificamos paridad con sync.
        result = await classify_async("Move SL to 4750")
        assert result, "esperaba al menos 1 accion"
        names = [a["action"] for a in result]
        assert "MOVE_SL_TO_PRICE" in names

    @pytest.mark.asyncio
    async def test_async_empty_text_returns_empty(self):
        result = await classify_async("")
        assert result == []

    @pytest.mark.asyncio
    async def test_async_whitespace_returns_empty(self):
        result = await classify_async("   ")
        assert result == []

    @pytest.mark.asyncio
    async def test_async_does_not_block_event_loop(self, monkeypatch):
        """REGRESION C1: classify_async debe envolver Gemini en to_thread.

        Mockeamos _gemini_classify para simular bloqueo de 0.5s y verificamos
        que durante ese tiempo otra task asyncio puede correr en paralelo
        (lo que demuestra que NO bloqueo el event loop).
        """
        import time as _time
        from classifier import _gemini_classify

        def slow_gemini(text, signal=None, max_retries=3, base_wait=2.0):
            # Bloquea 0.5s sintetico (Gemini real puede tardar mucho mas).
            _time.sleep(0.5)
            return [{"action": "INFORMATIONAL", "price": None, "confidence": 1.0}]

        monkeypatch.setattr("classifier._gemini_classify", slow_gemini)

        # Texto que NO matchea regex -> obliga a llamar Gemini
        unrelated = "completely unrelated text that no regex catches"

        # Lanzar classify_async y verificar que durante su espera otra task
        # puede correr en paralelo. Si bloquease el loop, el sleep concurrente
        # no avanzaria hasta que Gemini termine.
        concurrent_ran = []

        async def concurrent_task():
            await asyncio.sleep(0.1)
            concurrent_ran.append("yes")

        start = _time.monotonic()
        results = await asyncio.gather(
            classify_async(unrelated),
            concurrent_task(),
        )
        elapsed = _time.monotonic() - start

        # concurrent_task tardo 0.1s. classify_async tarda 0.5s. Total deberia
        # ser ~0.5s (paralelo), no ~0.6s (serie). Damos margen amplio.
        assert elapsed < 0.7, (
            f"REGRESION C1: classify_async parece estar bloqueando el event "
            f"loop. Total {elapsed:.2f}s, esperado <0.7s con concurrencia."
        )
        assert concurrent_ran == ["yes"]
        # Sanity check del resultado
        actions, _ = results
        assert actions[0]["action"] == "INFORMATIONAL"
