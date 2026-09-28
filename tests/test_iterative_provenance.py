from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import shutil

import pytest

import research.iterative_provenance as provenance


@pytest.fixture
def source_copy(tmp_path, monkeypatch):
    original = provenance.SOURCE_ROOT
    for relative in provenance.IMPLEMENTATION_FILES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original / relative, target)
    monkeypatch.setattr(provenance, "SOURCE_ROOT", tmp_path)
    return tmp_path


def test_identity_is_verifiable_and_portable(source_copy, monkeypatch):
    identity = provenance.implementation_identity()
    payload = {key: value for key, value in identity.items() if key != "sha256"}
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == identity["sha256"]
    for relative, digest in identity["source_sha256"].items():
        assert not Path(relative).is_absolute()
        assert hashlib.sha256((source_copy / relative).read_bytes()).hexdigest() == digest
    (source_copy / "unrelated.txt").write_text("not computational identity")
    (source_copy / "research/dubai_iterative/engine.py").touch()
    assert provenance.implementation_identity() == identity
    monkeypatch.setattr(provenance, "SOURCE_ROOT", Path(provenance.__file__).parents[1])
    assert provenance.implementation_identity() == identity


@pytest.mark.parametrize("relative", (
    "research/dubai_iterative/engine.py",
    "research/dubai_iterative/market_contract.py",
    "research/dubai_iterative/market.py",
    "research/dubai_iterative/oracle.py",
    "research/dubai_iterative/fast_engine.py",
    "research/dubai_iterative/evolution.py",
    "research/gold_iterative/dataset.py",
    "research/gold_iterative/search.py",
    "research/gold_iterative/seeds.py",
    "research/gold_iterative/folds.py",
    "broker_money.py",
    "broker_tick_clock.py",
    "provider_action_semantics.py",
))
def test_local_dependency_byte_change_changes_identity(source_copy, relative):
    first = provenance.implementation_identity()
    target = source_copy / relative
    target.write_bytes(target.read_bytes() + b"\n# changed fixture implementation\n")
    changed = provenance.implementation_identity()
    assert changed["sha256"] != first["sha256"]
    assert changed["source_sha256"][relative] != first["source_sha256"][relative]


def test_missing_dependency_fails_closed_without_writing(source_copy):
    target = source_copy / "research/dubai_iterative/oracle.py"
    target.unlink()
    before = set(source_copy.rglob("*"))
    with pytest.raises(ValueError, match="oracle.py"):
        provenance.implementation_identity()
    assert set(source_copy.rglob("*")) == before


@pytest.mark.parametrize("package", provenance.RUNTIME_PACKAGES)
def test_relevant_package_version_changes_identity(monkeypatch, package):
    first = provenance.implementation_identity()
    version = provenance.metadata.version
    monkeypatch.setattr(
        provenance.metadata, "version",
        lambda name: "different-runtime" if name == package else version(name),
    )
    assert provenance.implementation_identity()["sha256"] != first["sha256"]


def test_numba_execution_setting_changes_identity(monkeypatch):
    monkeypatch.delenv("NUMBA_DISABLE_JIT", raising=False)
    first = provenance.implementation_identity()
    monkeypatch.setenv("NUMBA_DISABLE_JIT", "1")
    assert provenance.implementation_identity()["sha256"] != first["sha256"]


def test_transitive_tick_cache_bytes_change_identity(source_copy):
    relative = "mt5_tick_cache.py"
    original = Path(provenance.__file__).resolve().parents[1] / relative
    target = source_copy / relative
    shutil.copyfile(original, target)
    before = provenance.implementation_identity()
    target.write_bytes(target.read_bytes() + b"\n# changed cache admission fixture\n")
    assert provenance.implementation_identity()["sha256"] != before["sha256"]


def test_explicit_dependencies_cover_transitive_local_imports(record_property):
    declared = set(provenance.IMPLEMENTATION_FILES)
    edges = {}
    for relative in sorted(declared):
        path = provenance.SOURCE_ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=relative)
        package = Path(relative).with_suffix("").parts[:-1]
        local = set()
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".") for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = list(package[:len(package) - node.level + 1]) if node.level else []
                module = base + (node.module.split(".") if node.module else [])
                modules = [module] + [
                    module + [alias.name] for alias in node.names if alias.name != "*"
                ]
            for module in modules:
                if not module:
                    continue
                candidates = [Path(*module).with_suffix(".py")]
                candidates.extend(
                    Path(*module[:index]) / "__init__.py"
                    for index in range(1, len(module) + 1)
                )
                # Only inspect paths named by imports, including function-local
                # imports. No globbing, workspace walk or imported-code execution.
                local.update(
                    candidate.as_posix() for candidate in candidates
                    if (provenance.SOURCE_ROOT / candidate).is_file()
                )
        edges[relative] = sorted(local)
    missing = {
        source: sorted(set(dependencies) - declared)
        for source, dependencies in edges.items() if set(dependencies) - declared
    }
    record_property("declared_files", len(declared))
    record_property("local_import_edges", json.dumps(edges, sort_keys=True))
    record_property("missing_local_dependencies", json.dumps(missing, sort_keys=True))
    assert not missing, f"unfingerprinted local import dependencies: {missing}"
