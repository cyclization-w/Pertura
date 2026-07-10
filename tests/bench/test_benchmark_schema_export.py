from __future__ import annotations

from pathlib import Path

from pertura_bench.schema_export import export_benchmark_schemas


def test_packaged_benchmark_json_schemas_have_no_drift() -> None:
    root = Path(__file__).resolve().parents[2]
    assert export_benchmark_schemas(root / "src" / "pertura_bench" / "schemas", check=True) == []
