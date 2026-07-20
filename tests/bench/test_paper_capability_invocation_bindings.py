from __future__ import annotations

import json
from pathlib import Path

from pertura_bench.paper_agent_execution import (
    _paper_dataset_contract,
    _register_submitted_task_artifacts,
)
from pertura_bench.paper_capability_bindings import (
    build_paper_task_invocation_bindings,
    provider_binding_contract,
)
from pertura_bench.capability_availability import (
    availability_by_task,
    build_task_capability_availability,
)
from pertura_runtime.project.assets import DataAssetRegistry
from pertura_runtime.project.models import AssetBinding
from pertura_runtime.project.workspace import ProjectWorkspace
from pertura_workflow.capabilities.registry import CapabilityRegistry
from pertura_workflow.capability_contracts import (
    build_capability_contract_catalog,
)


ROOT = Path(__file__).resolve().parents[2]


def _workspace(tmp_path: Path):
    project = ProjectWorkspace.initialize(tmp_path / "project")
    run = project.create_run(logical_name="paper binding")
    conversation = project.create_conversation(run.run_id, title="paper binding")
    project.store.begin_turn(conversation.conversation_id, "compile binding")
    registry = DataAssetRegistry(
        project_id=project.project.project_id,
        store=project.store,
        object_root=project.objects_dir,
    )
    return project, run, registry


def _register(
    registry: DataAssetRegistry,
    project: ProjectWorkspace,
    run_id: str,
    path: Path,
    role: str,
    *,
    kind: str = "external_resource",
):
    asset = registry.register(path, role=role, kind=kind)
    project.store.put_asset_binding(
        AssetBinding(run_id=run_id, asset_id=asset.asset_id, role=role)
    )
    return asset


def test_submitted_artifacts_bridge_to_provenance_assets_including_directories(
    tmp_path: Path,
) -> None:
    project, run, registry = _workspace(tmp_path)
    task_output = project.run_workspace(run.run_id).root / "outputs/tasks/PAPA-02"
    model = task_output / "state_reference_model"
    model.mkdir(parents=True)
    (model / "model.json").write_text('{"kind":"state-reference"}\n')
    (task_output / "reference_cell_manifest.tsv").write_text(
        "cell_id\ttechnical_state_id\nc1\t0\n", encoding="utf-8"
    )
    task = {
        "task_id": "PAPA-02",
        "output_contract": {
            "artifact_paths": {
                "state_reference_model": "state_reference_model",
                "reference_cell_manifest": "reference_cell_manifest.tsv",
            }
        },
    }
    active_turn_id = project.store.get_run(run.run_id).active_turn_id

    assets = _register_submitted_task_artifacts(
        registry,
        project=project,
        run_id=run.run_id,
        task=task,
        task_output=task_output,
        submission_receipt={"submission_id": "submission_fixture"},
        observed_roles=("state_reference_model", "reference_cell_manifest"),
        turn_id=active_turn_id,
        input_asset_ids=("asset_input_fixture",),
    )

    by_role = {asset.role: asset for asset in assets}
    assert by_role["state_reference_model"].format == "directory"
    assert by_role["state_reference_model"].source_class == "derived_artifact"
    assert by_role["reference_cell_manifest"].schema_validation_status == "validated"
    assert by_role["reference_cell_manifest"].submission_id == "submission_fixture"
    assert by_role["reference_cell_manifest"].created_by_turn == active_turn_id
    assert "submission:submission_fixture" in by_role[
        "reference_cell_manifest"
    ].dependencies


def test_papa02_binding_uses_frozen_assets_contract_and_minimal_provider_call(
    tmp_path: Path,
) -> None:
    project, run, asset_registry = _workspace(tmp_path)
    data = tmp_path / "papalexi.h5ad"
    data.write_bytes(b"frozen-h5ad")
    split = tmp_path / "calibration.tsv"
    split.write_text("cell_id\tis_control\nc1\ttrue\n", encoding="utf-8")
    retained = tmp_path / "retained_cell_manifest.tsv"
    retained.write_text(
        "dataset_id\tsplit\tcell_id\texpected_state\n"
        "papalexi_thp1_eccite\tcalibration\tc1\t"
        "retain_for_external_label_proxy\n",
        encoding="utf-8",
    )
    primary_h5ad = _register(
        asset_registry, project, run.run_id, data, "primary_h5ad"
    )
    primary_dataset = _register(
        asset_registry, project, run.run_id, data, "primary_dataset"
    )
    calibration_split = _register(
        asset_registry, project, run.run_id, split, "calibration_split"
    )
    retained_asset = asset_registry.register(
        retained,
        role="retained_cell_manifest",
        kind="derived",
        source_class="derived_artifact",
        origin_task_id="PAPA-01",
        submission_id="submission_papa01",
        schema_validation_status="validated",
    )
    project.store.put_asset_binding(
        AssetBinding(
            run_id=run.run_id,
            asset_id=retained_asset.asset_id,
            role=retained_asset.role,
        )
    )
    registered = {
        asset.role: {
            "asset_id": asset.asset_id,
            "path": str(path),
            "content_sha256": asset.content_sha256,
            "kind": asset.kind,
            "source_class": asset.source_class,
        }
        for asset, path in (
            (primary_h5ad, data),
            (primary_dataset, data),
            (calibration_split, split),
            (retained_asset, retained),
        )
    }
    confirmations = json.loads(
        (ROOT / "src/pertura_bench/cases/design_confirmations.v1.json").read_text(
            encoding="utf-8"
        )
    )
    template = confirmations["datasets"]["papalexi_thp1_eccite"][
        "paper_contract"
    ]
    contract = _paper_dataset_contract(
        dataset_id="papalexi_thp1_eccite",
        template=template,
        registered_assets=registered,
    )
    task = {
        "task_id": "PAPA-02",
        "expected_probe_capabilities": [],
        "output_contract": {
            "artifact_paths": {
                "state_reference_model": "state_reference_model"
            }
        },
    }

    bindings = build_paper_task_invocation_bindings(
        run_id=run.run_id,
        task=task,
        dataset_id="papalexi_thp1_eccite",
        contract=contract,
        registry=CapabilityRegistry.load_default(),
        asset_registry=asset_registry,
        project=project,
        registered_assets=registered,
        committed_results=(),
        advertised_capability_ids=("state.reference.fit.v1",),
    )

    assert len(bindings) == 1
    binding = bindings[0]
    assert binding.readiness == "ready"
    assert binding.bound_parameters["h5ad_path"] == primary_dataset.asset_id
    assert binding.bound_parameters["control_column"] == "gene"
    assert binding.bound_parameters["control_values"] == ["NT"]
    assert retained_asset.content_sha256 in {
        item.content_sha256 for item in binding.input_assets
    }
    assert binding.output_mapping == {
        "state_reference_fit": "state_reference_model"
    }

    visible = provider_binding_contract(bindings)
    assert visible == [
        {
            "binding_id": binding.binding_id,
            "capability_id": "state.reference.fit.v1",
            "tool": "run_analysis",
            "readiness": "ready",
            "blockers": [],
            "allowed_overrides": [],
            "output_mapping": {
                "state_reference_fit": "state_reference_model"
            },
            "minimal_call": {
                "tool": "run_analysis",
                "arguments": {
                    "binding_id": binding.binding_id,
                    "objective": (
                        "Execute state.reference.fit.v1 under the frozen task binding"
                    ),
                },
            },
        }
    ]
    assert "h5ad_path" not in json.dumps(visible)
    assert "retained_cell_manifest" not in json.dumps(visible)


def test_every_advertised_paper_surface_compiles_without_parameter_guessing(
    tmp_path: Path,
) -> None:
    catalog = json.loads(
        (ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text(
            encoding="utf-8"
        )
    )
    availability = availability_by_task(
        build_task_capability_availability(
            catalog, build_capability_contract_catalog()
        )
    )
    confirmations = json.loads(
        (ROOT / "src/pertura_bench/cases/design_confirmations.v1.json").read_text(
            encoding="utf-8"
        )
    )["datasets"]
    capability_registry = CapabilityRegistry.load_default()

    for workflow in catalog["workflows"]:
        for task in workflow["turns"]:
            advertised = tuple(
                availability[task["task_id"]]["advertised_capability_ids"]
            )
            if not advertised or task.get("role") == "optional":
                continue
            project, run, asset_registry = _workspace(
                tmp_path / task["task_id"]
            )
            registered = {}
            for role in task["required_input_roles"]:
                path = tmp_path / task["task_id"] / "inputs" / role
                if role == "state_reference_model":
                    path.mkdir(parents=True, exist_ok=True)
                    (path / "model.json").write_text("{}\n", encoding="utf-8")
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if role == "target_expression":
                        content = "cell_id\tNT\tCAV1\nc1\t1\t0\n"
                    else:
                        content = "cell_id\tcondition\treplicate\nc1\tctrl\tr1\n"
                    path.write_text(content, encoding="utf-8")
                asset = _register(
                    asset_registry, project, run.run_id, path, role
                )
                registered[role] = {
                    "asset_id": asset.asset_id,
                    "path": str(path),
                    "content_sha256": asset.content_sha256,
                    "kind": asset.kind,
                    "source_class": asset.source_class,
                }
            contract = _paper_dataset_contract(
                dataset_id=workflow["dataset_id"],
                template=confirmations[workflow["dataset_id"]]["paper_contract"],
                registered_assets=registered,
            )

            bindings = build_paper_task_invocation_bindings(
                run_id=run.run_id,
                task=task,
                dataset_id=workflow["dataset_id"],
                contract=contract,
                registry=capability_registry,
                asset_registry=asset_registry,
                project=project,
                registered_assets=registered,
                committed_results=(),
                advertised_capability_ids=advertised,
            )

            assert [item.capability_id for item in bindings] == list(advertised)
            by_capability = {item.capability_id: item for item in bindings}
            if task["task_id"] in {"REPL-01", "NORM-01"}:
                assert by_capability[
                    "diagnostic.design_balance.v1"
                ].readiness == "ready"
            same_turn_dependencies = {
                "PAPA-04": {
                    "effect.guide_target_sensitivity.v1": "target.guide_efficacy.v1"
                },
                "PAPA-05": {
                    "target.reliability.aggregate.v1": "target.responder.mixscape.v1"
                },
                "PAPA-08": {"enrichment.ora.v1": "effect.matrix.assemble.v1"},
                "KANG-02": {
                    "composition.propeller.v1": "diagnostic.design_balance.v1"
                },
            }
            for downstream_id, upstream_id in same_turn_dependencies.get(
                task["task_id"], {}
            ).items():
                assert by_capability[downstream_id].dependency_binding_ids == (
                    by_capability[upstream_id].binding_id,
                )
            for binding in bindings:
                if binding.readiness != "blocked_probe":
                    assert binding.output_mapping
                    assert not binding.blockers
                visible = provider_binding_contract((binding,))[0]
                assert set(visible["minimal_call"]["arguments"]) <= {
                    "binding_id",
                    "objective",
                }


def test_missing_upstream_assets_compile_to_structured_blocked_chain(
    tmp_path: Path,
) -> None:
    catalog = json.loads(
        (ROOT / "benchmarks/paper_v1/agent_tasks.v2.json").read_text(
            encoding="utf-8"
        )
    )
    workflow = next(
        item for item in catalog["workflows"]
        if item["workflow_id"] == "WF-PAPA"
    )
    task = next(
        item for item in workflow["turns"] if item["task_id"] == "PAPA-08"
    )
    availability = availability_by_task(
        build_task_capability_availability(
            catalog, build_capability_contract_catalog()
        )
    )
    project, run, asset_registry = _workspace(tmp_path)
    template = json.loads(
        (ROOT / "src/pertura_bench/cases/design_confirmations.v1.json").read_text(
            encoding="utf-8"
        )
    )["datasets"]["papalexi_thp1_eccite"]["paper_contract"]
    contract = _paper_dataset_contract(
        dataset_id="papalexi_thp1_eccite",
        template=template,
        registered_assets={},
    )

    bindings = build_paper_task_invocation_bindings(
        run_id=run.run_id,
        task=task,
        dataset_id="papalexi_thp1_eccite",
        contract=contract,
        registry=CapabilityRegistry.load_default(),
        asset_registry=asset_registry,
        project=project,
        registered_assets={},
        committed_results=(),
        advertised_capability_ids=tuple(
            availability["PAPA-08"]["advertised_capability_ids"]
        ),
    )

    assert bindings
    assert all(item.readiness == "blocked_probe" for item in bindings)
    by_capability = {item.capability_id: item for item in bindings}
    assert "effect_table" in " ".join(
        by_capability["effect.matrix.assemble.v1"].blockers
    )
    assert "bound predecessor" in " ".join(
        by_capability["enrichment.ora.v1"].blockers
    )
