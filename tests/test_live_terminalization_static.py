from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIVE_MODULES = (
    "listener.py",
    "main.py",
    "position_lifecycle_monitor.py",
    "live_auditor.py",
    "pending_actions.py",
)


class _ClosedMutationVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.functions: list[str] = []
        self.violations: list[tuple[int, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> None:
        if _assigns_closed_status(node.targets, node.value):
            function = self.functions[-1] if self.functions else "<module>"
            if function != "_finalize_signal":
                self.violations.append((node.lineno, function))
        self.generic_visit(node)


def _assigns_closed_status(targets, value) -> bool:
    if not (
        isinstance(value, ast.Constant)
        and value.value == "closed"
    ):
        return False
    return any(
        isinstance(target, ast.Attribute) and target.attr == "status"
        for target in targets
    )


def test_live_modules_only_mark_closed_inside_canonical_finalizer():
    violations = {}
    for filename in LIVE_MODULES:
        visitor = _ClosedMutationVisitor()
        visitor.visit(ast.parse(
            (ROOT / filename).read_text(encoding="utf-8"),
            filename=filename,
        ))
        if visitor.violations:
            violations[filename] = visitor.violations

    assert violations == {}
