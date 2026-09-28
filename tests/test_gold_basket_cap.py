"""Client-side basket loss cap with per-leg targets (Gold 555 family)."""
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.fast_engine import _market_profile_blockers
from research.dubai_iterative.protection import profile_blockers
from research.dubai_iterative.protection_contract import ProtectionProfile
from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.market_contract import MarketProfile


def _gold(**kw):
    base = dict(schema_version=2, entry_mode="adverse_reversal", entry_value=1.0, entry_confirmation_value=1.5,
                entry_expiry_min=30, entry_ladder_mode="adverse", entry_ladder_step=1.5, leg_count=2,
                volume_weights=(0.04, 0.03), target_mode="per_leg_steps", target_steps=(0.5, 1.0),
                stop_mode="none", be_mode="none", provider_management_mode="ignore", time_exit_min=180)
    base.update(kw)
    return StrategyGenome(**base)


def test_basket_cap_allowed_with_per_leg_targets():
    profile = ProtectionProfile(0.01, 2, 20, 0, 100, 1, 200)
    g = _gold(stop_mode="basket_money", stop_value=60.0)
    assert not g.validation_errors()
    assert "protection_policy_unsupported" not in profile_blockers(None, g, profile)


def test_basket_cap_needs_market_profile_in_fast_engine():
    g = _gold(stop_mode="basket_money", stop_value=60.0)
    prot = ProtectionProfile(0.01, 2, 20, 0, 100, 1, 200)
    no_market = ExecutionAssumptions(protection=prot)
    with_market = ExecutionAssumptions(protection=prot, market=MarketProfile(2, 164, 2, 0.01, 1.0, 0.01))
    assert "basket_money_cap_requires_market_profile" in _market_profile_blockers(g, no_market)
    assert "basket_money_cap_requires_market_profile" not in _market_profile_blockers(g, with_market)
