from level_interpreter import interpret_entry_levels


class TestInterpretEntryLevels:
    def test_sell_invalid_sl_is_replaced_from_range_pattern(self):
        parsed = {
            "direction": "SELL",
            "range": (4030.5, 4035.5),
            "tps": [4027.5, 4025.5, 4023.0, 4021.0, 4017.0, 4000.0],
            "sl": 4022.0,
        }

        result = interpret_entry_levels(
            "canal2", "SELL", parsed, reference_price=4030.7)

        assert result["parsed"]["range"] == (4030.5, 4035.5)
        assert result["parsed"]["tps"][:6] == parsed["tps"]
        assert result["parsed"]["sl"] == 4039.5
        assert any(c["field"] == "sl" and c["original"] == 4022.0
                   for c in result["corrections"])

    def test_missing_levels_are_inferred_from_reference_price(self):
        result = interpret_entry_levels(
            "canal1", "BUY", {"direction": "BUY"}, reference_price=4018.7)

        assert result["parsed"]["range"] == (4013.7, 4018.7)
        assert result["parsed"]["tps"] == [4021.7, 4023.7, 4025.7, 4027.7]
        assert result["parsed"]["sl"] == 4009.7
        assert {c["field"] for c in result["corrections"]} >= {
            "range", "tps", "sl",
        }

    def test_absurd_range_is_rebuilt_around_reference_price(self):
        parsed = {
            "direction": "BUY",
            "range": (3894.5, 3988.5),
            "tps": [3991.5],
            "sl": 3978.0,
        }

        result = interpret_entry_levels(
            "canal2", "BUY", parsed, reference_price=3988.8)

        assert result["parsed"]["range"] == (3983.8, 3988.8)
        assert result["parsed"]["tps"][0] == 3991.5
        assert result["parsed"]["sl"] == 3978.0
        assert any(c["field"] == "range" for c in result["corrections"])

    def test_tp_typo_is_corrected_before_direction_validation(self):
        parsed = {
            "direction": "BUY",
            "range": (4690.0, 4695.0),
            "tps": [4696.0, 4698.0, 46700.0, 4702.0],
            "sl": 4686.0,
        }

        result = interpret_entry_levels(
            "canal1", "BUY", parsed, reference_price=4695.2)

        assert result["parsed"]["tps"] == [4696.0, 4698.0, 4700.0, 4702.0]
        assert any(c["field"] == "tps" and c["kind"] == "typo_corrected"
                   for c in result["corrections"])
