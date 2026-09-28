"""Strict JSON boundary for the existing offline execution dataclasses."""

from collections.abc import Mapping
from dataclasses import fields
import json
from pathlib import Path

from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.market_contract import MarketProfile
from research.dubai_iterative.client_contract import ClientProfile
from research.dubai_iterative.latency_model import LatencyModel
from research.dubai_iterative.protection_contract import InitialProtection, ProtectionProfile


def _mapping(value, cls):
    if not isinstance(value, Mapping):
        raise ValueError(f"{cls.__name__} must be an object")
    unknown = value.keys() - {field.name for field in fields(cls)}
    if unknown:
        raise ValueError(f"unknown {cls.__name__} fields: {sorted(unknown, key=str)}")
    result = dict(value)
    for name in ("name", "ticket", "source"):
        if name in result and (not isinstance(result[name], str) or not result[name].strip()):
            raise ValueError(f"{cls.__name__}.{name} must be a nonempty string")
    return result


def execution_from_mapping(payload):
    """Decode without dropping fields or changing the engine's model defaults.

Choosing a profile does not admit a strategy, market tape or money contract.
Existing engine capability gates remain authoritative for those combinations.
"""
    try:
        data = _mapping(payload, ExecutionAssumptions)
        if data.get("protection") is not None:
            protection = _mapping(data["protection"], ProtectionProfile)
            initial = protection.get("initial_protections", [])
            if not isinstance(initial, (list, tuple)):
                raise ValueError("initial_protections must be an array")
            protection["initial_protections"] = tuple(
                InitialProtection(**_mapping(row, InitialProtection)) for row in initial)
            data["protection"] = ProtectionProfile(**protection)
        if data.get("market") is not None:
            data["market"] = MarketProfile(**_mapping(data["market"], MarketProfile))
            if data.get("protection") is None:
                raise ValueError("market execution requires an explicit protection profile")
        if data.get("client") is not None:
            data["client"] = ClientProfile(**_mapping(data["client"], ClientProfile))
            if data.get("market") is None or data.get("protection") is None:
                raise ValueError("client execution requires explicit market and protection profiles")
        if data.get("latency") is not None:
            latency = _mapping(data["latency"], LatencyModel)
            values = latency.get("entry_fill_ms")
            if not isinstance(values, (list, tuple)):
                raise ValueError("entry_fill_ms must be an array")
            latency["entry_fill_ms"] = tuple(values)
            data["latency"] = LatencyModel(**latency)
        return ExecutionAssumptions(**data)
    except (TypeError, OverflowError) as exc:
        raise ValueError(f"invalid execution profile: {exc}") from exc


def execution_to_scenario(execution, name="baseline"):
    """Keep immutable nested profiles as objects, not recursively decoded dicts."""
    from research.dubai_iterative.oracle import ExecutionScenario

    if not isinstance(execution, ExecutionAssumptions):
        raise ValueError("execution must be ExecutionAssumptions")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("scenario name must be a nonempty string")
    fields_ = dict(vars(execution))
    # The oracle has no per-request latency model; refuse instead of dropping it.
    if fields_.pop("latency", None) is not None:
        raise ValueError("latency model is not supported by the oracle")
    if fields_.pop("quote_view_lag_ms", 0):
        raise ValueError("quote view lag is not supported by the oracle")
    return ExecutionScenario(name=name, **fields_)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate execution profile field: {key}")
        result[key] = value
    return result


def load_execution_profile(path):
    """Read one bounded explicit profile, rejecting ambiguous JSON objects."""
    path = Path(path)
    if path.stat().st_size > 131_072:
        raise ValueError("execution profile exceeds 128 KiB")
    def invalid(value):
        raise ValueError(f"nonfinite JSON constant in execution profile: {value}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_unique_pairs,
                         parse_constant=invalid)
    return execution_from_mapping(payload)
