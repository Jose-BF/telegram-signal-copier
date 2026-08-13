from state import Signal

from interpretation_firewall import (
    EXECUTABLE_ACTIONS,
    normalize_classifier_outputs,
    firewall_decision,
)


def test_normalizes_new_gemini_contract_actions_to_legacy_executable_actions():
    raw = {
        "message_role": "direct_order",
        "actions": [
            {
                "type": "MOVE_SL_TO_BE",
                "target": "all_open_positions",
                "confidence": 0.94,
                "evidence": "move sl now to BE for 0% risk",
                "execution_policy": "auto_execute",
            }
        ],
        "is_conditional": False,
        "is_optional": False,
        "requires_review": False,
    }

    normalized = normalize_classifier_outputs(raw)

    assert len(normalized) == 1
    assert normalized[0]["action"] == "MOVE_SL_TO_BE"
    assert normalized[0]["confidence"] == 0.94
    assert normalized[0]["evidence"] == "move sl now to BE for 0% risk"
    assert normalized[0]["message_role"] == "direct_order"
    assert normalized[0]["execution_policy"] == "auto_execute"


def test_normalization_preserves_provider_stated_be_price_as_evidence():
    normalized = normalize_classifier_outputs({
        "action": "MOVE_SL_TO_BE",
        "price": None,
        "provider_stated_be_price": 4030.0,
        "confidence": 0.95,
    })

    assert normalized[0]["action"] == "MOVE_SL_TO_BE"
    assert normalized[0]["price"] is None
    assert normalized[0]["provider_stated_be_price"] == 4030.0


def test_conditional_plan_from_gemini_is_log_only_and_never_executable():
    raw = {
        "message_role": "conditional_plan",
        "actions": [],
        "is_conditional": True,
        "requires_review": False,
    }
    normalized = normalize_classifier_outputs(raw)
    sig = Signal(channel="canal1", message_id=20708, direction="BUY")

    decision = firewall_decision(
        sig, normalized[0], raw_text="If M5 closes below 4325 we close."
    )

    assert normalized[0]["action"] == "CONDITIONAL_PLAN"
    assert decision.policy == "log_only"
    assert decision.will_execute is False
    assert "conditional" in decision.reason


def test_optional_suggestion_close_is_notify_review_not_close_all():
    raw = {
        "message_role": "optional_suggestion",
        "actions": [
            {
                "type": "CLOSE_ALL",
                "confidence": 0.88,
                "evidence": "you can close around entry",
            }
        ],
        "is_optional": True,
        "requires_review": True,
    }
    normalized = normalize_classifier_outputs(raw)
    sig = Signal(channel="canal1", message_id=20354, direction="BUY")

    decision = firewall_decision(
        sig, normalized[0],
        raw_text="If you don't want risk, you can close around entry.",
    )

    assert normalized[0]["action"] == "CLOSE_ALL"
    assert decision.policy == "notify_review"
    assert decision.will_execute is False
    assert "optional" in decision.reason


def test_daily_summary_is_log_only_even_when_it_mentions_sl_and_be():
    raw = {
        "message_role": "daily_summary",
        "actions": [],
        "summary": {"trades": 3, "wins": 2, "sl": 0, "be": 1},
        "requires_review": False,
    }
    normalized = normalize_classifier_outputs(raw)
    sig = Signal(channel="canal2", message_id=2803, direction="SELL")

    decision = firewall_decision(
        sig, normalized[0],
        raw_text="Friday Summary\n3 Trades Sent\n2 Winning Trades\n1 B/E",
    )

    assert normalized[0]["action"] == "DAILY_SUMMARY"
    assert decision.policy == "log_only"
    assert decision.will_execute is False


def test_partial_close_is_preserved_as_log_only_simulation_evidence():
    normalized = normalize_classifier_outputs({
        "action": "CLOSE_PARTIAL",
        "confidence": 0.95,
        "evidence": "closing partial profits",
    })
    sig = Signal(channel="canal2", message_id=3470, direction="SELL")

    decision = firewall_decision(
        sig, normalized[0], raw_text="I am closing partial profits"
    )

    assert normalized[0]["action"] == "CLOSE_PARTIAL"
    assert decision.policy == "log_only"
    assert decision.will_execute is False
    assert decision.requires_review is False


def test_reentry_requires_review_until_specific_reentry_engine_exists():
    raw = {
        "message_role": "direct_order",
        "actions": [
            {
                "type": "REENTRY_SIGNAL",
                "confidence": 0.91,
                "evidence": "reenter now SL to 4336",
            }
        ],
    }
    normalized = normalize_classifier_outputs(raw)
    sig = Signal(channel="canal1", message_id=20124, direction="SELL")

    decision = firewall_decision(
        sig, normalized[0], raw_text="Reenter now SL to 4336.00"
    )

    assert normalized[0]["action"] == "REENTRY_SIGNAL"
    assert "REENTRY_SIGNAL" not in EXECUTABLE_ACTIONS
    assert decision.policy == "notify_review"
    assert decision.will_execute is False


def test_legacy_protective_intents_are_explicit_human_review():
    sig = Signal(channel="canal1", message_id=20810, direction="BUY")

    for action in ("SIGNAL_UPDATED", "PROTECT_AND_NOTIFY"):
        decision = firewall_decision(
            sig,
            {"action": action, "confidence": 0.86, "message_role": "direct_order"},
            raw_text="Protect this trade",
        )

        assert decision.policy == "notify_review"
        assert decision.will_execute is False
        assert decision.reason == "requires_review_intent"


def test_executable_direct_order_passes_firewall():
    sig = Signal(channel="canal2", message_id=2793, direction="SELL")
    action = {
        "action": "MOVE_SL_TO_PRICE",
        "price": 4180.0,
        "confidence": 0.92,
        "message_role": "direct_order",
    }

    decision = firewall_decision(sig, action, raw_text="Adjust SL to 4180")

    assert decision.policy == "auto_execute"
    assert decision.will_execute is True
    assert decision.reason == "direct_executable"


def test_secured_basket_is_a_first_class_executable_intent():
    sig = Signal(channel="canal2", message_id=1474, direction="BUY")
    action = {
        "action": "SECURE_BASKET",
        "confidence": 0.98,
        "message_role": "direct_order",
    }

    decision = firewall_decision(
        sig, action, raw_text="Make your trade risk free"
    )

    assert "SECURE_BASKET" in EXECUTABLE_ACTIONS
    assert decision.policy == "auto_execute"
    assert decision.will_execute is True


def test_non_holder_scope_can_never_close_the_live_trade():
    sig = Signal(channel="canal1", message_id=21399, direction="BUY")
    action = {
        "action": "CLOSE_ALL",
        "confidence": 0.92,
        "message_role": "direct_order",
        "_reason": "canal1_safe_direct_close",
    }

    decision = firewall_decision(
        sig,
        action,
        raw_text="This is for who is out of the trade",
    )

    assert decision.policy == "log_only"
    assert decision.will_execute is False
    assert decision.reason == "non_holder_scope"
