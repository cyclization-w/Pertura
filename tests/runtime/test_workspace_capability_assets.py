from __future__ import annotations

from pathlib import Path

import pytest

from pertura_core import DatasetContract
from pertura_runtime.parameter_protocol import CapabilityParameterError
from pertura_runtime.product import PerturaProductRuntime
from pertura_runtime.project.models import AssetBinding
from pertura_runtime.project.workspace import ProjectWorkspace


def _runtime(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    project = ProjectWorkspace.initialize(tmp_path / "project")
    run = project.create_run(logical_name="workspace asset provenance")
    conversation = project.create_conversation(run.run_id, title="asset binding")
    turn = project.store.begin_turn(conversation.conversation_id, "run capability")
    workspace = project.run_workspace(run.run_id)
    runtime = PerturaProductRuntime(
        workspace,
        project_workspace=project,
        run_id=run.run_id,
    )
    contract = DatasetContract(
        dataset_id="workspace-assets",
        input_format="tsv",
        identity_fields={
            "control": {"status": "confirmed", "value": ["NTC"]},
        },
    )
    runtime.register_dataset_contract(contract)
    return runtime, project, run, turn, contract


def test_workspace_paths_become_hashed_receipt_dependencies(
    tmp_path: Path, monkeypatch
) -> None:
    runtime, project, run, turn, contract = _runtime(tmp_path, monkeypatch)
    absolute = runtime.workspace.root / "outputs" / "tasks" / "T-01" / "one.gmt"
    absolute.parent.mkdir(parents=True)
    absolute.write_text("MODULE_A\tdescription\tG1\tG2\n", encoding="utf-8")
    relative = runtime.workspace.root / "outputs" / "tasks" / "T-01" / "two.gmt"
    relative.write_text("MODULE_B\tdescription\tG2\tG3\n", encoding="utf-8")
    spec = runtime.registry.get("module.import.gmt.v1")

    absolute_parameters = runtime._register_workspace_parameter_assets(
        spec,
        {"gmt_path": str(absolute)},
    )
    relative_parameters = runtime._register_workspace_parameter_assets(
        spec,
        {"gmt_path": relative.relative_to(runtime.workspace.root).as_posix()},
    )

    absolute_asset = project.store.get_asset(absolute_parameters["gmt_path"])
    relative_asset = project.store.get_asset(relative_parameters["gmt_path"])
    assert absolute_asset is not None
    assert relative_asset is not None
    assert absolute_asset.role == relative_asset.role == "gene_modules"
    assert absolute_asset.kind == relative_asset.kind == "derived"
    assert absolute_asset.created_by_turn == relative_asset.created_by_turn == turn.turn_id
    assert runtime.planning_material(contract.contract_id)[1] == ()

    response = runtime._run(
        "module.import.gmt.v1",
        kind="analysis",
        contract_id=contract.contract_id,
        scope={"dataset_id": contract.dataset_id},
        parameters={
            **absolute_parameters,
            "species": "human",
            "identifier_namespace": "symbol",
        },
    )
    assert response["status"] == "completed"
    assert response["receipt_id"].startswith("receipt_")
    committed = runtime.planning_material(contract.contract_id)[1]
    result = next(
        item for item in committed if item.capability_id == "module.import.gmt.v1"
    )
    data_dependencies = [
        item for item in result.dependencies if item.kind == "data_asset"
    ]
    assert len(data_dependencies) == 1
    assert data_dependencies[0].object_id == absolute_asset.asset_id
    assert data_dependencies[0].object_hash == absolute_asset.identity_hash
    assert project.store.get_run(run.run_id).active_turn_id == turn.turn_id
    runtime.close()


@pytest.mark.parametrize(
    "value",
    [
        "../escape.gmt",
        "outputs/tasks/T-01/missing.gmt",
    ],
)
def test_workspace_asset_registration_rejects_escape_and_missing_paths(
    tmp_path: Path, monkeypatch, value: str
) -> None:
    runtime, _, _, _, _ = _runtime(tmp_path, monkeypatch)
    spec = runtime.registry.get("module.import.gmt.v1")
    with pytest.raises(CapabilityParameterError):
        runtime._register_workspace_parameter_assets(spec, {"gmt_path": value})
    runtime.close()


def test_workspace_asset_registration_rejects_external_paths_and_wrong_roles(
    tmp_path: Path, monkeypatch
) -> None:
    runtime, project, run, _, contract = _runtime(tmp_path, monkeypatch)
    external = tmp_path / "external.gmt"
    external.write_text("MODULE\tdescription\tG1\n", encoding="utf-8")
    spec = runtime.registry.get("module.import.gmt.v1")
    with pytest.raises(CapabilityParameterError, match="external path"):
        runtime._register_workspace_parameter_assets(
            spec,
            {"gmt_path": str(external)},
        )

    wrong = runtime.asset_registry.register(
        external,
        role="primary_dataset",
        kind="external_resource",
    )
    project.store.put_asset_binding(
        AssetBinding(run_id=run.run_id, asset_id=wrong.asset_id, role=wrong.role)
    )
    with pytest.raises(ValueError, match="expected 'gene_modules'"):
        runtime._run(
            "module.import.gmt.v1",
            kind="analysis",
            contract_id=contract.contract_id,
            scope={"dataset_id": contract.dataset_id},
            parameters={
                "gmt_path": wrong.asset_id,
                "species": "human",
                "identifier_namespace": "symbol",
            },
        )
    runtime.close()


def test_workspace_asset_cannot_replace_required_upstream_result_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    runtime, _, _, _, contract = _runtime(tmp_path, monkeypatch)
    h5ad = runtime.workspace.root / "outputs" / "tasks" / "PAPA-02" / "controls.h5ad"
    h5ad.parent.mkdir(parents=True)
    h5ad.write_bytes(b"not-read-before-dependency-resolution")

    response = runtime._run(
        "reference.state.control_pca_leiden.v1",
        kind="analysis",
        contract_id=contract.contract_id,
        scope={"dataset_id": contract.dataset_id},
        parameters={"h5ad_path": str(h5ad)},
    )

    assert response["status"] == "blocked"
    assert response["result_id"] is None
    assert response["required_upstream"] == [
        "diagnostic.guide_assignment.v1"
    ]
    assert any(
        "required dependency is missing" in blocker
        for blocker in response["blockers"]
    )
    assert runtime.planning_material(contract.contract_id)[1] == ()
    runtime.close()
