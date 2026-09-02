"""Versioned catalog of prospective multichannel shadow strategies."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from strategy_runtime_contract import all_strategy_contracts
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
        channel: tuple(
            contract.to_shadow_policy(
                role=role(channel, contract.strategy_id),
            )
            for contract in all_strategy_contracts()
            if contract.channel == channel
        )
        for channel in ("canal1", "canal2")
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
