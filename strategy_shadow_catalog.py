"""Versioned catalog of prospective multichannel shadow strategies."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from strategy_shadow_contracts import ShadowPolicy


DUBAI_BALANCED_FINGERPRINT = (
    "32cb5c0fe8205ad00a0c655bacd5446c6cc219d1ad7338967212c71781860631"
)
DUBAI_FRONTLOADED_30M_FINGERPRINT = (
    "d486f5ce418094e862fe3b58e6ccc14068a136ef7116f8a9a80c347083e6dc1c"
)
DUBAI_FRONTLOADED_40M_FINGERPRINT = (
    "cdee2bdfc53aff748d0b87e1d57301793eeb620a4287916c4494cb6681a070b0"
)
GOLD_555_FINGERPRINT = (
    "555124a24b534aa2abda53ddaaa2ee35fd3afd07e61d05937eb14c80ad0676f0"
)
GOLD_B210_FINGERPRINT = (
    "b210010f4122b5fc2d5e657c512c8a8e94db81647b4d8fe9b0b95228983b5f58"
)
GOLD_C490_FINGERPRINT = (
    "c4900550abae98de1500bf5b849072956175fdecda102fad69be9f7975cbf8d6"
)

DEFAULT_LIVE_CONTROLS = MappingProxyType({
    "canal1": "dubai_balanced_v1",
    "canal2": "gold_now_555_v1",
})


def build_shadow_catalog(
    *,
    live_controls: Mapping[str, str] | None = None,
) -> Mapping[str, tuple[ShadowPolicy, ...]]:
    controls = dict(DEFAULT_LIVE_CONTROLS if live_controls is None else live_controls)
    if set(controls) != {"canal1", "canal2"}:
        raise ValueError("live controls must define canal1 and canal2")

    def role(channel: str, candidate_id: str) -> str:
        return (
            "live_control"
            if controls[channel] == candidate_id
            else "candidate"
        )

    catalog = {
        "canal1": (
            ShadowPolicy(
                candidate_id="dubai_balanced_v1",
                channel="canal1",
                role=role("canal1", "dubai_balanced_v1"),
                strategy_fingerprint=DUBAI_BALANCED_FINGERPRINT,
                entry_mode="market_ladder",
                entry_volumes=(0.01, 0.04, 0.04),
                ladder_step=4.0,
                ladder_expiry_minutes=15,
                basket_stop_eur=25.0,
                profit_arm_eur=10.0,
                profit_giveback_eur=2.0,
                time_exit_minutes=40,
                time_exit_mode="loss_only",
                provider_management_mode="exact",
            ),
            ShadowPolicy(
                candidate_id="dubai_frontloaded_30m_v1",
                channel="canal1",
                role=role("canal1", "dubai_frontloaded_30m_v1"),
                strategy_fingerprint=DUBAI_FRONTLOADED_30M_FINGERPRINT,
                entry_mode="market_ladder",
                entry_volumes=(0.01, 0.05, 0.01, 0.02, 0.01, 0.02),
                ladder_step=4.0,
                ladder_expiry_minutes=15,
                basket_stop_eur=30.0,
                profit_arm_eur=10.0,
                profit_giveback_eur=8.0,
                time_exit_minutes=30,
                time_exit_mode="loss_only",
                provider_management_mode="exact",
            ),
            ShadowPolicy(
                candidate_id="dubai_frontloaded_40m_v1",
                channel="canal1",
                role=role("canal1", "dubai_frontloaded_40m_v1"),
                strategy_fingerprint=DUBAI_FRONTLOADED_40M_FINGERPRINT,
                entry_mode="market_ladder",
                entry_volumes=(0.01, 0.05, 0.01, 0.02, 0.01, 0.02),
                ladder_step=4.0,
                ladder_expiry_minutes=15,
                basket_stop_eur=30.0,
                profit_arm_eur=10.0,
                profit_giveback_eur=8.0,
                time_exit_minutes=40,
                time_exit_mode="loss_only",
                provider_management_mode="exact",
            ),
        ),
        "canal2": (
            ShadowPolicy(
                candidate_id="gold_now_555_v1",
                channel="canal2",
                role=role("canal2", "gold_now_555_v1"),
                strategy_fingerprint=GOLD_555_FINGERPRINT,
                entry_mode="adverse_reversal",
                entry_volumes=(0.04, 0.03, 0.03, 0.03, 0.03),
                ladder_step=1.5,
                ladder_expiry_minutes=30,
                entry_adverse=1.0,
                entry_reversal=1.5,
                target_steps=(0.5, 1.0, 1.5, 2.0, 2.5),
                trailing_distance=30.0,
                profit_arm_eur=30.0,
                profit_giveback_eur=1.0,
                time_exit_minutes=180,
                time_exit_mode="non_negative",
                provider_management_mode="explicit_close_only",
            ),
            ShadowPolicy(
                candidate_id="gold_now_b210_v1",
                channel="canal2",
                role=role("canal2", "gold_now_b210_v1"),
                strategy_fingerprint=GOLD_B210_FINGERPRINT,
                entry_mode="market_ladder",
                entry_volumes=(0.01, 0.01, 0.01, 0.01, 0.01, 0.01),
                ladder_step=1.0,
                ladder_expiry_minutes=15,
                basket_stop_eur=60.0,
                profit_arm_eur=30.0,
                profit_giveback_eur=10.0,
                time_exit_minutes=3,
                time_exit_mode="profit_only",
                provider_management_mode="exact",
            ),
            ShadowPolicy(
                candidate_id="gold_now_c490_v1",
                channel="canal2",
                role=role("canal2", "gold_now_c490_v1"),
                strategy_fingerprint=GOLD_C490_FINGERPRINT,
                entry_mode="immediate_multi",
                entry_volumes=(0.01, 0.01, 0.01, 0.01, 0.01),
                hard_stop_eur_per_leg=20.0,
                break_even_trigger_xau=12.0,
                basket_stop_eur=100.0,
                profit_arm_eur=10.0,
                profit_giveback_eur=8.0,
                time_exit_minutes=40,
                time_exit_mode="loss_only",
                provider_management_mode="ignore",
            ),
        ),
    }
    validate_shadow_catalog(catalog)
    return MappingProxyType(catalog)


def validate_shadow_catalog(
    catalog: Mapping[str, tuple[ShadowPolicy, ...]],
) -> None:
    if set(catalog) != {"canal1", "canal2"}:
        raise ValueError("shadow catalog must define canal1 and canal2")
    all_ids: set[str] = set()
    for channel, policies in catalog.items():
        if len(policies) != 3:
            raise ValueError(f"{channel} must contain exactly three candidates")
        controls = [item for item in policies if item.role == "live_control"]
        if len(controls) != 1:
            raise ValueError(f"{channel} must contain one live control")
        for policy in policies:
            if policy.channel != channel:
                raise ValueError("candidate stored under the wrong channel")
            if policy.candidate_id in all_ids:
                raise ValueError("candidate IDs must be globally unique")
            all_ids.add(policy.candidate_id)


def policy_by_id(candidate_id: str) -> ShadowPolicy:
    for policies in build_shadow_catalog().values():
        for policy in policies:
            if policy.candidate_id == candidate_id:
                return policy
    raise KeyError(candidate_id)
