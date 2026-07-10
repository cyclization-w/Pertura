from __future__ import annotations

from pathlib import Path

import pytest

from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.product import PerturaProductRuntime
from pertura_workflow.capabilities.edger import _read_counts


def test_edger_capability_never_installs_environment_during_analysis(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "screen"
    source.mkdir()
    (source / "counts.csv").write_text("gene,c1,c2\nG1,1,2\n", encoding="utf-8")
    (source / "metadata.csv").write_text("cell_id,condition,replicate\nc1,target,r1\nc2,baseline,r2\n", encoding="utf-8")
    monkeypatch.setenv("PERTURA_CACHE_DIR", str(tmp_path / "empty-cache"))
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    workspace = ClaudeRunWorkspace.create(root=tmp_path / "runs", input_source=source, run_id="edger-missing")
    runtime = PerturaProductRuntime(workspace)
    try:
        contract = runtime.inspect_dataset(confirmations={"control": "baseline", "replicate": "replicate"})
        result = runtime.run_analysis(
            "replicate-aware differential expression",
            capability_id="de.pseudobulk.edger.v1",
            contract_id=contract["contract_id"],
            parameters={
                "counts_path": "counts.csv",
                "metadata_path": "metadata.csv",
                "target_condition": "target",
                "baseline_condition": "baseline",
            },
        )
        assert result["status"] == "blocked"
        assert any("pertura env setup edger-v1" in item for item in result["blockers"])
        assert not (tmp_path / "empty-cache").exists()
    finally:
        runtime.close()


def test_edger_raw_count_contract_rejects_fractional_values(tmp_path: Path) -> None:
    path = tmp_path / "counts.csv"
    path.write_text("gene,c1,c2\nG1,1.5,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="raw nonnegative integer counts"):
        _read_counts(path, "gene")
