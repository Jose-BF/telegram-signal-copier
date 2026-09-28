import ast
from pathlib import Path

import journal


ROOT = Path(__file__).resolve().parents[1]
# Historical evidence can contain scripts for a different Python runtime.
EXCLUDED_PARTS = {".git", ".venv", "tests", "analysis", "docs", "runtime_data"}


def test_literal_journal_anomaly_categories_are_registered():
    invalid = []

    for path in ROOT.rglob("*.py"):
        if EXCLUDED_PARTS.intersection(path.relative_to(ROOT).parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "journal"
                and func.attr == "anomaly"
            ):
                continue
            category = node.args[1]
            if (
                isinstance(category, ast.Constant)
                and isinstance(category.value, str)
                and category.value not in journal.CATEGORIES
            ):
                invalid.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}={category.value}"
                )

    assert invalid == []
