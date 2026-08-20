from level_interpreter import (
    align_provider_plan_to_market_context,
    interpret_entry_levels,
)


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

    def test_tp_below_actual_buy_fill_uses_fill_based_fallback(self):
        parsed = {
            "direction": "BUY",
            "range": (4088.0, 4090.0),
            "tps": [4095.0, 4099.0, 4105.0, 4110.0],
            "sl": 4070.0,
        }

        result = interpret_entry_levels(
            "canal1", "BUY", parsed, reference_price=4095.13)

        assert result["parsed"]["tps"] == [4098.13, 4099.0, 4105.0, 4110.0]
        assert all(tp > 4095.13 for tp in result["parsed"]["tps"])
        assert any(c["field"] == "tps" and c["index"] == 0
                   for c in result["corrections"])

    def test_replaced_buy_tp_keeps_sequence_monotonic(self):
        parsed = {
            "direction": "BUY",
            "range": (4085.0, 4088.0),
            "tps": [4091.0, 4095.0, 4100.0, 4005.0],
            "sl": 4070.0,
        }

        result = interpret_entry_levels(
            "canal1", "BUY", parsed, reference_price=4089.50)

        tps = result["parsed"]["tps"]
        assert tps[:3] == [4091.0, 4095.0, 4100.0]
        assert tps[3] > tps[2]
        assert all(tp > 4089.50 for tp in tps)
        assert any(c["field"] == "tps" and c["index"] == 3
                   for c in result["corrections"])

    def test_coherent_hundred_dollar_shift_is_corrected_as_one_plan(self):
        parsed = {
            "direction": "SELL",
            "range": (4132.0, 4137.0),
            "tps": [4130.0, 4128.0, 4120.0],
            "sl": 4140.0,
        }

        result = interpret_entry_levels(
            "canal2", "SELL", parsed, reference_price=4032.68)

        assert result["parsed"]["range"] == (4032.0, 4037.0)
        assert result["parsed"]["tps"][:3] == [4030.0, 4028.0, 4020.0]
        assert result["parsed"]["sl"] == 4040.0
        assert any(
            correction["kind"] == "market_context_shift"
            and correction["offset"] == -100.0
            for correction in result["corrections"]
        )

    def test_far_directionally_valid_sl_is_not_applied(self):
        parsed = {
            "direction": "SELL",
            "range": (4032.0, 4037.0),
            "tps": [4030.0, 4028.0, 4020.0],
            "sl": 4140.0,
        }

        result = interpret_entry_levels(
            "canal2", "SELL", parsed, reference_price=4032.68)

        assert result["parsed"]["sl"] == 4041.0
        assert any(
            correction["field"] == "sl"
            and correction["kind"] == "sl_replaced"
            and correction["reason"] == "sl_too_far_from_entry"
            for correction in result["corrections"]
        )

    def test_market_shift_does_not_move_levels_already_near_live_market(self):
        parsed = {
            "direction": "SELL",
            "range": (4132.0, 4137.0),
            "tps": [4030.0, 4028.0, 4020.0],
            "sl": 4040.0,
        }

        result = interpret_entry_levels(
            "canal2", "SELL", parsed, reference_price=4032.68)

        assert result["parsed"]["range"] == (4032.0, 4037.0)
        assert result["parsed"]["tps"][:3] == parsed["tps"]
        assert result["parsed"]["sl"] == 4040.0

    def test_mixed_hundred_dollar_typo_repairs_each_bad_level_from_context(self):
        parsed = {
            "direction": "BUY",
            "range": (4389.0, 4494.0),
            "tps": [4496.5],
            "sl": 4486.0,
        }

        result = interpret_entry_levels(
            "canal2", "BUY", parsed, reference_price=4394.5)

        assert result["parsed"]["range"] == (4389.0, 4394.0)
        assert result["parsed"]["tps"][:4] == [
            4396.5,
            4399.0,
            4401.0,
            4403.0,
        ]
        assert result["parsed"]["sl"] == 4386.0
        assert any(
            correction["kind"] == "mixed_market_context_shift"
            and correction["shifted_fields"] == ["range_high", "tps", "sl"]
            for correction in result["corrections"]
        )

    def test_valid_far_runner_is_not_shifted_toward_market(self):
        parsed = {
            "direction": "BUY",
            "range": (4495.0, 4500.0),
            "tps": [4503.0, 4505.0, 4508.0, 4510.0, 4546.0],
            "sl": 4492.0,
        }

        result = interpret_entry_levels(
            "canal2", "BUY", parsed, reference_price=4501.65)

        assert result["parsed"]["tps"] == parsed["tps"]
        assert not any(
            correction["kind"] == "mixed_market_context_shift"
            for correction in result["corrections"]
        )


def test_zone_bundle_alignment_shifts_only_supplied_provider_levels():
    parsed = {
        "direction": "SELL",
        "zones": [[4062.0, 4067.0]],
        "tps": [4060.0, 4058.0, 4047.0],
        "sl": 4070.0,
        "has_open_runner": True,
    }

    result = align_provider_plan_to_market_context(
        "SELL",
        parsed,
        reference_price=4259.8,
    )

    assert result["parsed"]["zones"] == [[4262.0, 4267.0]]
    assert result["parsed"]["tps"] == [4260.0, 4258.0, 4247.0]
    assert result["parsed"]["sl"] == 4270.0
    assert "range" not in result["parsed"]
    assert result["corrections"] == [{
        "field": "plan",
        "kind": "market_context_shift",
        "offset": 200.0,
        "reference_price": 4259.8,
        "original_range": [4062.0, 4067.0],
        "corrected_range": [4262.0, 4267.0],
        "residual": 2.2,
        "shifted_fields": ["zones", "tps", "sl"],
    }]
    assert result["provisional"] is True
