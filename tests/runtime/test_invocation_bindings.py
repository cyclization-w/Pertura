from __future__ import annotations

from pathlib import Path

import pytest

from pertura_core import DatasetContract
from pertura_runtime.invocation_bindings import (
    CapabilityInvocationBindingError,
    build_invocation_binding,
)
from pertura_runtime.product import PerturaProductRuntime
from pertura_runtime.project.workspace import ProjectWorkspace
from pertura_runtime.project.models import TurnStatus


def _runtime(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    project = ProjectWorkspace.initialize(tmp_path / "project")
    run = project.create_run(logical_name="bound invocation")
    conversation = project.create_conversation(run.run_id)
    project.store.begin_turn(conversation.conversation_id, "invoke binding")
    runtime = PerturaProductRuntime(
        project.run_workspace(run.run_id),
        project_workspace=project,
        run_id=run.run_id,
    )
    contract = DatasetContract(
        dataset_id="binding-dataset",
        input_format="csv",
        identity_fields={
            "control": {"status": "confirmed", "value": ["NTC"]},
            "replicate": {
                "status": "confirmed",
                "value": ["r1", "r2", "r3"],
            },
        },
    )
    runtime.register_dataset_contract(contract)
    return runtime, project, run, contract


def test_binding_id_executes_without_provider_composed_parameters(
    tmp_path: Path, monkeypatch
) -> None:
    runtime, project, run, contract = _runtime(tmp_path, monkeypatch)
    spec = runtime.registry.get("diagnostic.contract_integrity.v1")
    binding = build_invocation_binding(
        run_id=run.run_id,
        task_id="REPL-01",
        spec=spec,
        contract=contract,
        tool_name="run_diagnostic",
        scope={"dataset_id": contract.dataset_id},
        bound_parameters={},
        project=project,
        output_mapping={"contract_integrity": "design_audit"},
    )
    runtime.replace_invocation_bindings(
        task_id="REPL-01", bindings=(binding,)
    )

    result = runtime.run_diagnostic(binding_id=binding.binding_id)

    assert result["status"] in {
        "completed",
        "caution",
        "screen_passed",
        "blocked",
    }
    assert result["result_id"] is not None
    assert result["output_mapping"] == {
        "contract_integrity": "design_audit"
    }
    runtime.close()


def test_binding_rejects_locked_parameter_and_cross_task_reuse(
    tmp_path: Path, monkeypatch
) -> None:
    runtime, project, run, contract = _runtime(tmp_path, monkeypatch)
    spec = runtime.registry.get("diagnostic.contract_integrity.v1")
    binding = build_invocation_binding(
        run_id=run.run_id,
        task_id="REPL-01",
        spec=spec,
        contract=contract,
        tool_name="run_diagnostic",
        scope={"dataset_id": contract.dataset_id},
        bound_parameters={},
        project=project,
    )
    runtime.replace_invocation_bindings(
        task_id="REPL-01", bindings=(binding,)
    )

    with pytest.raises(
        CapabilityInvocationBindingError, match="override locked parameters"
    ):
        runtime.run_diagnostic(
            binding_id=binding.binding_id,
            parameters={"invented": True},
        )

    runtime.replace_invocation_bindings(task_id="PAPA-01", bindings=())
    with pytest.raises(CapabilityInvocationBindingError, match="unknown or inactive"):
        runtime.run_diagnostic(binding_id=binding.binding_id)
    runtime.close()


def test_blocked_probe_is_a_structured_nonexecution(
    tmp_path: Path, monkeypatch
) -> None:
    runtime, project, run, contract = _runtime(tmp_path, monkeypatch)
    spec = runtime.registry.get("target.guide_efficacy.v1")
    binding = build_invocation_binding(
        run_id=run.run_id,
        task_id="REPL-03",
        spec=spec,
        contract=contract,
        tool_name="run_diagnostic",
        scope={"dataset_id": contract.dataset_id},
        bound_parameters={},
        project=project,
        readiness="blocked_probe",
        blockers=("guide-count evidence is unavailable",),
    )
    runtime.replace_invocation_bindings(
        task_id="REPL-03", bindings=(binding,)
    )

    result = runtime.run_diagnostic(binding_id=binding.binding_id)

    assert result["status"] == "blocked"
    assert result["result_id"] is None
    assert result["binding_id"] == binding.binding_id
    assert result["blockers"] == ["guide-count evidence is unavailable"]
    assert runtime.planning_material(contract.contract_id)[1] == ()
    runtime.close()


def test_executed_design_diagnostic_can_return_a_scientific_block(
    tmp_path: Path, monkeypatch
) -> None:
    runtime, project, run, contract = _runtime(tmp_path, monkeypatch)
    metadata = tmp_path / "project" / "unresolved_design.tsv"
    metadata.write_text(
        "cell_id\tconstruct\nc1\tA+B\n", encoding="utf-8"
    )
    asset = runtime.asset_registry.register(
        metadata, role="cell_metadata", kind="observed"
    )
    binding = build_invocation_binding(
        run_id=run.run_id,
        task_id="NORM-01",
        spec=runtime.registry.get("diagnostic.design_balance.v1"),
        contract=contract,
        tool_name="run_diagnostic",
        scope={"dataset_id": contract.dataset_id},
        bound_parameters={"metadata_path": asset.asset_id},
        project=project,
    )
    runtime.replace_invocation_bindings(task_id="NORM-01", bindings=(binding,))

    response = runtime.run_diagnostic(binding_id=binding.binding_id)

    assert response["status"] == "blocked"
    assert response["result_id"] is not None
    assert "missing required design columns" in " ".join(response["blockers"])
    runtime.close()


def test_bound_dataset_integrity_reads_small_materialized_h5ad(
    tmp_path: Path, monkeypatch
) -> None:
    ad = pytest.importorskip("anndata")
    np = pytest.importorskip("numpy")
    runtime, project, run, contract = _runtime(tmp_path, monkeypatch)
    h5ad = tmp_path / "tiny.h5ad"
    ad.AnnData(np.asarray([[1.0, 0.0], [0.0, 2.0]])).write_h5ad(h5ad)
    asset = runtime.asset_registry.register(
        h5ad,
        role="primary_dataset",
        kind="external_resource",
    )
    binding = build_invocation_binding(
        run_id=run.run_id,
        task_id="REPL-01",
        spec=runtime.registry.get("diagnostic.dataset_integrity.v1"),
        contract=contract,
        tool_name="run_diagnostic",
        scope={"dataset_id": contract.dataset_id},
        bound_parameters={
            "input_path": asset.asset_id,
            "max_memory_gb": 1.0,
            "n_jobs": 1,
        },
        project=project,
        output_mapping={"dataset_integrity": "dataset_profile"},
    )
    runtime.replace_invocation_bindings(task_id="REPL-01", bindings=(binding,))

    response = runtime.run_diagnostic(binding_id=binding.binding_id)

    assert response["result_id"] is not None
    assert response["status"] in {"caution", "screen_passed"}
    result = runtime.planning_material(contract.contract_id)[1][0]
    assert result.metrics["layer"]["name"] == "X"
    assert result.metrics["integer_like"] is True
    runtime.close()


def test_binding_expires_when_the_project_advances_to_another_turn(
    tmp_path: Path, monkeypatch
) -> None:
    runtime, project, run, contract = _runtime(tmp_path, monkeypatch)
    binding = build_invocation_binding(
        run_id=run.run_id,
        task_id="REPL-01",
        spec=runtime.registry.get("diagnostic.contract_integrity.v1"),
        contract=contract,
        tool_name="run_diagnostic",
        scope={"dataset_id": contract.dataset_id},
        bound_parameters={},
        project=project,
    )
    runtime.replace_invocation_bindings(task_id="REPL-01", bindings=(binding,))
    active = project.store.get_run(run.run_id).active_turn_id
    project.store.complete_turn(
        active,
        status=TurnStatus.completed,
        provider_final="done",
    )
    conversation = next(
        item
        for item in project.store.list_conversations(project.project.project_id)
        if item.run_id == run.run_id
    )
    project.store.begin_turn(conversation.conversation_id, "next task")

    with pytest.raises(CapabilityInvocationBindingError, match="stale"):
        runtime.run_diagnostic(binding_id=binding.binding_id)
    runtime.close()


def test_exploratory_result_can_feed_exploratory_binding_without_fake_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    runtime, project, run, contract = _runtime(tmp_path, monkeypatch)
    metadata = tmp_path / "project" / "cell_metadata.tsv"
    metadata.write_text(
        "cell_id\tind\tstim\tcell\n"
        "c1\td1\tctrl\tA\n"
        "c2\td1\tstim\tB\n"
        "c3\td2\tctrl\tA\n"
        "c4\td2\tstim\tB\n",
        encoding="utf-8",
    )
    asset = runtime.asset_registry.register(
        metadata,
        role="cell_metadata",
        kind="observed",
    )
    diagnostic_spec = runtime.registry.get("diagnostic.design_balance.v1")
    diagnostic = build_invocation_binding(
        run_id=run.run_id,
        task_id="KANG-02",
        spec=diagnostic_spec,
        contract=contract,
        tool_name="run_diagnostic",
        scope={"dataset_id": contract.dataset_id},
        bound_parameters={
            "metadata_path": asset.asset_id,
            "donor_column": "ind",
            "condition_column": "stim",
            "state_column": "cell",
            "paired": True,
        },
        project=project,
    )
    runtime.replace_invocation_bindings(task_id="KANG-02", bindings=(diagnostic,))
    response = runtime.run_diagnostic(binding_id=diagnostic.binding_id)
    assert response["receipt_id"] is None

    result = runtime.planning_material(contract.contract_id)[1][0]
    records = runtime.planning_commit_records()
    assert records[0]["verification_state"] == "validated_untrusted"
    downstream = build_invocation_binding(
        run_id=run.run_id,
        task_id="KANG-02",
        spec=runtime.registry.get("composition.propeller.v1"),
        contract=contract,
        tool_name="run_analysis",
        scope={"dataset_id": contract.dataset_id},
        bound_parameters={
            "metadata_path": asset.asset_id,
            "sample_column": "ind",
            "condition_column": "stim",
            "state_column": "cell",
            "contrast": ["ctrl", "stim"],
        },
        project=project,
        dependency_results=(result,),
        dependency_records={result.result_id: records[0]},
        output_mapping={"composition_effect": "propeller_results"},
    )
    assert downstream.dependency_result_ids == (result.result_id,)
    assert downstream.dependency_result_hashes == (result.canonical_hash,)
    assert downstream.dependency_verification_states == ("validated_untrusted",)
    assert downstream.dependency_receipt_ids == (None,)

    captured = {}

    def fake_run(capability_id, **kwargs):
        captured["capability_id"] = capability_id
        captured["dependencies"] = kwargs["dependencies"]
        return {"status": "blocked", "result_id": None}

    monkeypatch.setattr(runtime, "_run", fake_run)
    runtime.replace_invocation_bindings(task_id="KANG-02", bindings=(downstream,))
    runtime.run_analysis(
        "Execute the frozen composition analysis",
        binding_id=downstream.binding_id,
    )
    assert captured["capability_id"] == "composition.propeller.v1"
    assert captured["dependencies"][0] == {
        "object_id": result.result_id,
        "object_hash": result.canonical_hash,
        "state": "current",
    }
    runtime.close()


def test_conditional_binding_resolves_the_exact_same_turn_predecessor(
    tmp_path: Path, monkeypatch
) -> None:
    runtime, project, run, contract = _runtime(tmp_path, monkeypatch)
    metadata = tmp_path / "project" / "composition_metadata.tsv"
    metadata.write_text(
        "cell_id\tind\tstim\tcell\n"
        "c1\td1\tctrl\tA\n"
        "c2\td1\tstim\tB\n"
        "c3\td2\tctrl\tA\n"
        "c4\td2\tstim\tB\n",
        encoding="utf-8",
    )
    asset = runtime.asset_registry.register(
        metadata,
        role="cell_metadata",
        kind="observed",
    )
    diagnostic = build_invocation_binding(
        run_id=run.run_id,
        task_id="KANG-02",
        spec=runtime.registry.get("diagnostic.design_balance.v1"),
        contract=contract,
        tool_name="run_diagnostic",
        scope={"dataset_id": contract.dataset_id},
        bound_parameters={
            "metadata_path": asset.asset_id,
            "replicate_column": "ind",
            "condition_column": "stim",
            "state_column": "cell",
        },
        project=project,
    )
    downstream = build_invocation_binding(
        run_id=run.run_id,
        task_id="KANG-02",
        spec=runtime.registry.get("composition.propeller.v1"),
        contract=contract,
        tool_name="run_analysis",
        scope={"dataset_id": contract.dataset_id},
        bound_parameters={
            "metadata_path": asset.asset_id,
            "sample_column": "ind",
            "condition_column": "stim",
            "state_column": "cell",
            "contrast": ["ctrl", "stim"],
        },
        project=project,
        dependency_binding_ids=(diagnostic.binding_id,),
        output_mapping={"composition_effect": "propeller_results"},
        readiness="conditional_ready",
    )
    runtime.replace_invocation_bindings(
        task_id="KANG-02", bindings=(diagnostic, downstream)
    )
    with pytest.raises(
        CapabilityInvocationBindingError, match="has not produced"
    ):
        runtime.run_analysis("out of order", binding_id=downstream.binding_id)

    diagnostic_response = runtime.run_diagnostic(binding_id=diagnostic.binding_id)
    captured = {}

    def fake_run(capability_id, **kwargs):
        captured["dependencies"] = kwargs["dependencies"]
        return {"status": "blocked", "result_id": None}

    monkeypatch.setattr(runtime, "_run", fake_run)
    runtime.run_analysis("in order", binding_id=downstream.binding_id)
    assert diagnostic_response["result_id"] in {
        item["object_id"] for item in captured["dependencies"]
    }
    runtime.close()


def test_blocked_downstream_binding_does_not_require_a_blocked_predecessor(
    tmp_path: Path, monkeypatch
) -> None:
    runtime, project, run, contract = _runtime(tmp_path, monkeypatch)
    upstream = build_invocation_binding(
        run_id=run.run_id,
        task_id="PAPA-08",
        spec=runtime.registry.get("effect.matrix.assemble.v1"),
        contract=contract,
        tool_name="run_analysis",
        scope={"dataset_id": contract.dataset_id},
        bound_parameters={},
        project=project,
        readiness="blocked_probe",
        blockers=("effect-table asset is unavailable",),
    )
    downstream = build_invocation_binding(
        run_id=run.run_id,
        task_id="PAPA-08",
        spec=runtime.registry.get("enrichment.ora.v1"),
        contract=contract,
        tool_name="run_analysis",
        scope={"dataset_id": contract.dataset_id},
        bound_parameters={},
        project=project,
        dependency_binding_ids=(upstream.binding_id,),
        readiness="blocked_probe",
        blockers=("bound predecessor is unavailable",),
    )
    runtime.replace_invocation_bindings(
        task_id="PAPA-08", bindings=(upstream, downstream)
    )

    response = runtime.run_analysis(
        "Report the frozen predecessor blocker",
        binding_id=downstream.binding_id,
    )

    assert response["status"] == "blocked"
    assert response["result_id"] is None
    assert response["blockers"] == ["bound predecessor is unavailable"]
    runtime.close()
