"""Canonical evidence describing the strategy catalog used by a runtime."""

from __future__ import annotations

from typing import Any, Mapping

from strategy_shadow_catalog import build_shadow_catalog
from strategy_shadow_contracts import ShadowPolicy, canonical_hash


def build_catalog_manifest(
    catalog: Mapping[str, tuple[ShadowPolicy, ...]] | None = None,
) -> dict[str, Any]:
    selected = catalog or build_shadow_catalog()
    payload = {
        "schema_version": 1,
        "policies": [
            {
                "channel": channel,
                "candidate_id": policy.candidate_id,
                "role": policy.role,
                "strategy_fingerprint": policy.strategy_fingerprint,
                "execution_fingerprint": policy.execution_fingerprint,
            }
            for channel in ("canal1", "canal2")
            for policy in selected[channel]
        ],
    }
    return {**payload, "manifest_hash": canonical_hash(payload)}


def catalog_manifest_matches(
    value: object,
    catalog: Mapping[str, tuple[ShadowPolicy, ...]] | None = None,
) -> bool:
    return isinstance(value, Mapping) and dict(value) == build_catalog_manifest(
        catalog,
    )
