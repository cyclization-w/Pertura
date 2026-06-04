"""Notebook code safety — AST-based checks before execution."""

from __future__ import annotations

import ast
from pathlib import Path

# Patterns that are always blocked
_BLOCKED_BUILTINS = {"eval", "exec", "compile", "__import__"}
_BLOCKED_MODULES = {"subprocess", "shutil", "ctypes", "socket", "pickle", "code", "multiprocessing"}
_BLOCKED_ATTRS = {"system", "popen", "call", "rmtree", "remove", "unlink", "rmdir", "chmod", "chown", "kill", "terminate"}
_READ_PATHS = {"workspace", "Path"}
_WRITE_PATHS = {"artifacts_dir"}


def check(code: str, *, workspace: str, artifacts_dir: str) -> list[str]:
    """Return list of safety violations. Empty list = safe."""
    violations: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"Syntax error: {e}"]

    checker = _SafetyVisitor(workspace, artifacts_dir)
    checker.visit(tree)
    return checker.violations


class _SafetyVisitor(ast.NodeVisitor):
    def __init__(self, workspace: str, artifacts_dir: str):
        self.violations: list[str] = []
        self.workspace = workspace
        self.artifacts_dir = artifacts_dir

    def visit_Call(self, node):
        # Blocked builtins
        if isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_BUILTINS:
            self.violations.append(f"Dangerous builtin: {node.func.id}()")
        # Blocked modules (full ban) or dangerous os attrs
        if isinstance(node.func, ast.Attribute):
            base = node.func.value.id if isinstance(node.func.value, ast.Name) else ""
            if base in _BLOCKED_MODULES:
                self.violations.append(f"Blocked module: {base}.{node.func.attr}()")
            elif base == "os" and node.func.attr in _BLOCKED_ATTRS:
                self.violations.append(f"Dangerous call: os.{node.func.attr}()")
        # open() with write mode
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            if node.args:
                mode = _get_string(node.args[1]) if len(node.args) > 1 else "r"
                if isinstance(mode, str) and "w" in mode:
                    path = _get_string(node.args[0])
                    if isinstance(path, str) and not _in_dir(path, self.artifacts_dir):
                        self.violations.append(f"open('{path}', '{mode}') writes outside artifacts_dir")
        self.generic_visit(node)

    def visit_Import(self, node):
        for alias in node.names:
            if alias.name.split(".")[0] in _BLOCKED_MODULES:
                self.violations.append(f"Blocked import: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module and node.module.split(".")[0] in _BLOCKED_MODULES:
            self.violations.append(f"Blocked import: {node.module}")
        self.generic_visit(node)


def _get_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "f-string"
    return None


def _in_dir(path: str, dir_path: str) -> bool:
    try:
        return Path(path).resolve().is_relative_to(Path(dir_path).resolve())
    except (ValueError, OSError):
        return False
