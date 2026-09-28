"""Executable temporary baseline, not a certificate of complete live isolation."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs/development/mt5-native-access-baseline.json"
OFFLINE_ROOTS = {"mt5_tester_replay.py", "reconcile_mt5_ledger.py"}


def native_references(source: str) -> list[dict]:
    tree = ast.parse(source)
    aliases = {}
    rows = []
    nodes = list(ast.walk(tree))
    for node in nodes:
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name] = item.name
                if item.name == "MetaTrader5":
                    rows.append({"kind": "import", "target": "MetaTrader5", "line": node.lineno})
        elif isinstance(node, ast.ImportFrom):
            for item in node.names:
                name = f"{node.module}.{item.name}"
                aliases[item.asname or item.name] = name
                if node.module == "MetaTrader5":
                    rows.append({"kind": "import", "target": name, "line": node.lineno})

    def qualified(node, visited=None):
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            parent = qualified(node.value)
            name = f"{parent}.{node.attr}" if parent else ""
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
            parent = qualified(node.args[0])
            key = node.args[1].value if isinstance(node.args[1], ast.Constant) else "<dynamic>"
            return f"{parent}.{key}" if parent else ""
        elif isinstance(node, ast.Call) and qualified(node.func) in {"importlib.import_module", "__import__"}:
            return node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else ""
        else:
            return ""
        visited = set() if visited is None else visited
        while name in aliases and name not in visited:
            visited.add(name)
            name = aliases[name]
        return name

    # Track simple module/function aliases, including aliases of aliases.
    for _ in range(len(nodes) + 1):
        changed = False
        for node in nodes:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                name = qualified(node.value)
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and name.startswith("MetaTrader5") and aliases.get(target.id) != name:
                        aliases[target.id] = name
                        changed = True
        if not changed:
            break
    for node in nodes:
        if isinstance(node, (ast.Attribute, ast.Name, ast.Call)):
            name = qualified(node)
            if name.startswith("MetaTrader5."):
                target = name.removeprefix("MetaTrader5.")
                rows.append({"kind": "constant" if target.isupper() else "access", "target": target, "line": node.lineno})
            elif isinstance(node, ast.Call) and name == "MetaTrader5":
                rows.append({"kind": "import", "target": "MetaTrader5", "line": node.lineno})
    return rows


def inventory(root: Path = ROOT) -> dict:
    paths = list(root.glob("*.py"))
    for directory in ("tools", "analysis"):
        paths.extend((root / directory).rglob("*.py"))
    files = {}
    for path in sorted(paths):
        rows = native_references(path.read_text(encoding="utf-8-sig"))
        if not rows:
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "mt5_worker.py":
            scope = "isolated_live_owner"
        elif relative in OFFLINE_ROOTS or relative.startswith(("tools/", "analysis/")):
            scope = "offline_or_tool_requires_reachability_review"
        else:
            scope = "legacy_live_pending_migration"
        counts = Counter((row["kind"], row["target"]) for row in rows)
        files[relative] = {"scope": scope, "references": [
            {"kind": kind, "target": target, "count": count}
            for (kind, target), count in sorted(counts.items())
        ]}
    return {"schema_version": 1, "status": "temporary_baseline_not_live_approval",
            "scan_scope": ["root/*.py", "tools/**/*.py", "analysis/**/*.py"], "files": files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()
    actual = inventory()
    if args.write_baseline:
        BASELINE.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Temporary inventory written: {len(actual['files'])} files; no migration certified")
        return 0
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))
    if actual != expected:
        changed = sorted(name for name in actual["files"].keys() | expected["files"].keys()
                         if actual["files"].get(name) != expected["files"].get(name))
        print("Native inventory changed: " + ", ".join(changed))
        return 1
    legacy = sum(row["scope"] == "legacy_live_pending_migration" for row in actual["files"].values())
    print(f"Baseline matches; {legacy} live modules retain native MT5 access")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
