from __future__ import annotations

import ast
import importlib
import inspect
from functools import lru_cache
from typing import Any


EXECUTOR_PARAMETER_ADDITIONS: dict[str, frozenset[str]] = {
    "sceptre_association": frozenset({
        "response_matrix_path", "guide_matrix_path", "guide_target_map_path",
        "discovery_pairs_path", "response_ids_path", "guide_ids_path",
        "cell_ids_path", "covariates_path",
    }),
    "intake_materialize": frozenset({
        "guide_row_manifest_path", "guide_column_manifest_path", "guide_modality", "guide_layer",
    }),
    "guide_integrity": frozenset({"row_manifest_path", "column_manifest_path", "modality", "layer"}),
    "guide_nb_mixture": frozenset({"row_manifest_path", "column_manifest_path", "modality", "layer"}),
    "guide_ambient": frozenset({"row_manifest_path", "column_manifest_path", "modality", "layer"}),
}
EXECUTOR_DELEGATES: dict[str, tuple[str, ...]] = {
    # Wrapper executors may call a runner imported from another module.  The
    # local AST walk cannot see across that import boundary, so the delegation
    # is explicit and audited rather than silently omitted.
    "control_nmf": ("nmf_modules",),
}
RESOURCE_PARAMETER_NAMES = frozenset({"max_memory_gb", "n_jobs", "chunk_rows"})
@lru_cache(maxsize=None)
def executor_parameter_names(executor_name: str) -> frozenset[str]:
    """Return request-parameter names read by an executor and local helpers."""

    from pertura_workflow.capabilities.executors import _EXECUTORS, _EXECUTOR_TARGETS

    target = _EXECUTOR_TARGETS.get(executor_name)
    if target:
        module_name, function_name = target.rsplit(":", 1)
        module = importlib.import_module(module_name)
        entry = getattr(module, function_name)
    else:
        entry = _EXECUTORS[executor_name]
        module = inspect.getmodule(entry)
    if module is None:
        return frozenset()
    try:
        source = inspect.getsource(module)
    except (OSError, TypeError):
        return frozenset()
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    queue = [entry.__name__]
    visited: set[str] = set()
    parameters: set[str] = set()
    while queue:
        name = queue.pop()
        if name in visited or name not in functions:
            continue
        visited.add(name)
        node = functions[name]
        for child in ast.walk(node):
            key = _parameter_key(child)
            if key:
                parameters.add(key)
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                if child.func.id in functions and child.func.id not in visited:
                    queue.append(child.func.id)
    for delegate in EXECUTOR_DELEGATES.get(executor_name, ()):
        parameters.update(executor_parameter_names(delegate))
    return frozenset(parameters)


def _parameter_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "get" and node.args and _is_parameter_mapping(node.func.value):
            return _literal_string(node.args[0])
    if isinstance(node, ast.Subscript) and _is_parameter_mapping(node.value):
        return _literal_string(node.slice)
    return None


def _is_parameter_mapping(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in {"parameters", "params"}
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "parameters"
        and isinstance(node.value, ast.Name)
        and node.value.id == "request"
    )


def _literal_string(node: ast.AST) -> str | None:
    return str(node.value) if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def schema_example(schema: dict[str, Any]) -> Any:
    if "default" in schema:
        return schema["default"]
    if schema.get("enum"):
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((item for item in kind if item != "null"), kind[0] if kind else None)
    if kind == "string":
        role = schema.get("x-pertura-asset-role")
        return f"asset_example_{role}" if role else "example"
    if kind == "integer":
        return max(1, int(schema.get("minimum", 1)))
    if kind == "number":
        return max(1.0, float(schema.get("minimum", schema.get("exclusiveMinimum", 0.0))) + 0.1)
    if kind == "boolean":
        return False
    if kind == "array":
        return [schema_example(dict(schema.get("items") or {"type": "string"}))]
    if kind == "object":
        return {}
    return "example"

def expected_executor_parameter_names(executor_name: str, *, include_resources: bool = True) -> frozenset[str]:
    names = set(executor_parameter_names(executor_name))
    names.update(EXECUTOR_PARAMETER_ADDITIONS.get(executor_name, ()))
    if include_resources:
        names.update(RESOURCE_PARAMETER_NAMES)
    return frozenset(names)
