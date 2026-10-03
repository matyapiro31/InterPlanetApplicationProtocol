"""Verification layer 1 (Section 9.1, MUST): syntax and forbidden-API checks.

Generated programs must define ``main(input, assets)`` and may import only an
allowlist of pure-computation standard-library modules. This is a policy check,
not a security boundary; the sandbox enforces the same import policy at run time
and adds OS-level limits.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

ALLOWED_MODULES = frozenset({
    "bisect", "collections", "copy", "dataclasses", "datetime", "decimal", "enum",
    "fractions", "functools", "heapq", "itertools", "json", "math", "operator", "random",
    "re", "statistics", "string", "typing",
})

FORBIDDEN_CALLS = frozenset({
    "__import__", "breakpoint", "compile", "delattr", "eval", "exec", "exit", "getattr",
    "globals", "locals", "open", "quit", "setattr", "vars",
})

# Removed from the program's builtins at run time. ``input`` is only removed here
# (not flagged statically) because ``main(input, assets)`` uses it as a parameter name.
RUNTIME_REMOVED_BUILTINS = (FORBIDDEN_CALLS - {"__import__"}) | {"input"}

MAX_SOURCE_BYTES = 64 * 1024


@dataclass
class StaticReport:
    passed: bool
    issues: list[str] = field(default_factory=list)


class _Checker(ast.NodeVisitor):
    def __init__(self, allowed: frozenset[str]) -> None:
        self.allowed = allowed
        self.issues: list[str] = []

    def _flag(self, node: ast.AST, msg: str) -> None:
        self.issues.append(f"line {getattr(node, 'lineno', '?')}: {msg}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name.split(".")[0] not in self.allowed:
                self._flag(node, f"import of {alias.name!r} is not allowed")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level or (node.module or "").split(".")[0] not in self.allowed:
            self._flag(node, f"import from {node.module!r} is not allowed")

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_CALLS or node.id == "__builtins__":
            self._flag(node, f"use of {node.id!r} is not allowed")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self._flag(node, f"access to dunder attribute {node.attr!r} is not allowed")
        self.generic_visit(node)


def check_source(source: str, allowed_modules: frozenset[str] = ALLOWED_MODULES) -> StaticReport:
    if len(source.encode()) > MAX_SOURCE_BYTES:
        return StaticReport(False, [f"source exceeds {MAX_SOURCE_BYTES} bytes"])
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return StaticReport(False, [f"syntax error at line {e.lineno}: {e.msg}"])
    checker = _Checker(allowed_modules)
    checker.visit(tree)
    issues = checker.issues
    mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    if not mains:
        issues.append("no top-level function 'main(input, assets)' defined")
    else:
        args = mains[-1].args
        if len(args.posonlyargs) + len(args.args) < 2 and args.vararg is None:
            issues.append("'main' must accept two positional arguments (input, assets)")
    return StaticReport(not issues, issues)
