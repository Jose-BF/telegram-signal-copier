from __future__ import annotations

import pytest

import config


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("c490", "c490"),
        ("555", "555"),
        ("legacy", "legacy"),
        (" 555 ", "555"),
        ("C490", "c490"),
    ],
)
def test_gold_policy_selector_accepts_supported_values(
    raw: str,
    expected: str,
) -> None:
    assert config.normalize_gold_now_policy(raw) == expected


def test_gold_policy_selector_defaults_to_c490() -> None:
    assert config.normalize_gold_now_policy(None) == "c490"
    assert config.normalize_gold_now_policy("") == "c490"


def test_gold_policy_selector_rejects_unknown_values() -> None:
    with pytest.raises(ValueError, match="GOLD_NOW_LIVE_POLICY"):
        config.normalize_gold_now_policy("experimental")
