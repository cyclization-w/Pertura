from __future__ import annotations

import ast
from pathlib import Path


def test_gate_import_boundary_includes_workflow() -> None:
    gate_root = Path(__file__).resolve().parents[2] / "src" / "pertura_gate"
    forbidden = ("pertura_runtime", "pertura_bench", "pertura_workflow")
    offenders: list[str] = []
    for path in gate_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden):
                        offenders.append(f"{path}:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith(forbidden):
                    offenders.append(f"{path}:{module}")
    assert offenders == []


def test_workflow_does_not_import_runtime_or_benchmark() -> None:
    workflow_root = Path(__file__).resolve().parents[2] / "src" / "pertura_workflow"
    forbidden = ("pertura_runtime", "pertura_bench")
    offenders: list[str] = []
    for path in workflow_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden):
                        offenders.append(f"{path}:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith(forbidden):
                    offenders.append(f"{path}:{module}")
    assert offenders == []
