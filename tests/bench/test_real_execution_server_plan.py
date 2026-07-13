from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pertura_bench.capability_bench import benchmark_specs
from pertura_bench.capability_models import CapabilityBenchmarkCase
from pertura_bench.real_execution import (
    RealParametersNotConfigured,
    execute_capability_dag,
)
from pertura_bench.server_plan import (
    assert_server_plan_executable,
    bind_server_plan,
    build_server_plan,
    validate_checkpoint_binding,
)


@dataclass
class _FakeSpec:
    capability_id: str
    version: str
    kind: str
    depends_on: tuple[str, ...] = ()


class _FakeRegistry:
    def __init__(self, specs: list[_FakeSpec]) -> None:
        self.specs = {item.capability_id: item for item in specs}

    def get(self, capability_id: str, version: str | None = None) -> _FakeSpec:
        item = self.specs[capability_id]
        if version is not None and item.version != version:
            raise KeyError((capability_id, version))
        return item


@dataclass
class _FakeBroker:
    committed: list[dict[str, Any]] = field(default_factory=list)

    def list_committed(self, run_id: str) -> list[dict[str, Any]]:
        assert run_id == "run-one"
        return list(self.committed)


class _FakeRuntime:
    def __init__(self, root: Path) -> None:
        self.workspace = SimpleNamespace(root=root / "run-one")
        self.workspace.root.mkdir(parents=True)
        self.broker = _FakeBroker()
        self.calls: list[tuple[str, int, dict[str, Any]]] = []

    def run_diagnostic(self, capability_id: str, **kwargs: Any) -> dict[str, Any]:
        assert "dependencies" not in kwargs
        output = self.workspace.root / "upstream.json"
        output.write_text('{"ready": true}\n', encoding="utf-8")
        result = {
            "result_id": "result-upstream",
            "capability_id": capability_id,
            "capability_version": "0.1.0",
            "output_paths": [str(output.relative_to(self.workspace.root))],
        }
        self.broker.committed.append({"result": result})
        self.calls.append((capability_id, id(self), dict(kwargs["parameters"])))
        return {"result_id": result["result_id"], "status": "screen_passed"}

    def run_analysis(
        self, objective: str, *, capability_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        assert objective == capability_id == "target.v1"
        assert "dependencies" not in kwargs
        assert [
            item["result"]["capability_id"] for item in self.broker.committed
        ] == ["upstream.v1"]
        upstream_path = Path(kwargs["parameters"]["upstream_path"])
        assert upstream_path.read_text(encoding="utf-8").startswith("{")
        result = {
            "result_id": "result-target",
            "capability_id": capability_id,
            "capability_version": "0.1.0",
            "output_paths": [],
        }
        self.broker.committed.append({"result": result})
        self.calls.append((capability_id, id(self), dict(kwargs["parameters"])))
        return {"result_id": result["result_id"], "status": "completed"}


def _real_case() -> CapabilityBenchmarkCase:
    return CapabilityBenchmarkCase(
        capability_id="target.v1",
        capability_version="0.1.0",
        tier="frozen_subset",
        scenario="happy",
        fixture_id="locked/fixture/frozen_subset/evaluation",
        dataset_id="fixture",
        expected_statuses=("completed",),
    )


def _parameter_catalog(*, include_target: bool = True) -> dict[str, Any]:
    capabilities: dict[str, Any] = {
        "upstream.v1@0.1.0": {
            "parameters": {"input_path": {"artifact_ref": "primary"}}
        }
    }
    if include_target:
        capabilities["target.v1@0.1.0"] = {
            "parameters": {
                "upstream_path": {
                    "upstream_output": {
                        "capability_id": "upstream.v1",
                        "filename": "upstream.json",
                    }
                }
            }
        }
    return {
        "schema_version": "pertura-real-parameter-catalog-v1",
        "catalog_version": "fixture-v1",
        "datasets": {
            "fixture": {
                "contract_confirmations": {},
                "capabilities": capabilities,
            }
        },
    }


def test_real_dag_uses_one_runtime_and_authoritative_upstream_handoff(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact.h5ad"
    artifact.write_bytes(b"fixture")
    runtime = _FakeRuntime(tmp_path)
    registry = _FakeRegistry(
        [
            _FakeSpec("upstream.v1", "0.1.0", "diagnostic"),
            _FakeSpec(
                "target.v1", "0.1.0", "analysis", ("upstream.v1",)
            ),
        ]
    )
    result, order = execute_capability_dag(
        runtime,
        registry=registry,  # type: ignore[arg-type]
        target_capability_id="target.v1",
        target_capability_version="0.1.0",
        contract_id="contract-one",
        artifact=artifact,
        dataset_id="fixture",
        tier="frozen_subset",
        split="evaluation",
        lock_hashes={"artifact": "sha256:" + "1" * 64},
        parameter_catalog=_parameter_catalog(),
        parameter_catalog_hash="sha256:" + "2" * 64,
        case=_real_case(),
    )
    assert order == ("upstream.v1", "target.v1")
    assert result["result_id"] == "result-target"
    assert [item[0] for item in runtime.calls] == ["upstream.v1", "target.v1"]
    assert len({item[1] for item in runtime.calls}) == 1
    assert len(runtime.broker.committed) == 2
    for _, _, parameters in runtime.calls:
        assert "benchmark_context" not in parameters
        assert len(parameters) == 1
        assert set(parameters) <= {"input_path", "upstream_path"}


def test_real_dag_fails_precisely_when_dataset_mapping_is_absent(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact.h5ad"
    artifact.write_bytes(b"fixture")
    runtime = _FakeRuntime(tmp_path)
    registry = _FakeRegistry(
        [
            _FakeSpec("upstream.v1", "0.1.0", "diagnostic"),
            _FakeSpec(
                "target.v1", "0.1.0", "analysis", ("upstream.v1",)
            ),
        ]
    )
    with pytest.raises(
        RealParametersNotConfigured,
        match=r"dataset=fixture, capability=target\.v1@0\.1\.0, catalog=fixture-v1",
    ):
        execute_capability_dag(
            runtime,
            registry=registry,  # type: ignore[arg-type]
            target_capability_id="target.v1",
            target_capability_version="0.1.0",
            contract_id="contract-one",
            artifact=artifact,
            dataset_id="fixture",
            tier="full_dataset",
            split="calibration",
            lock_hashes={"artifact": "sha256:" + "1" * 64},
            parameter_catalog=_parameter_catalog(include_target=False),
            parameter_catalog_hash="sha256:" + "2" * 64,
            case=_real_case(),
        )
    assert runtime.calls == []
    assert runtime.broker.committed == []


def test_server_plan_expands_all_dimensions_and_requires_checkpoint_binding() -> None:
    from importlib import resources

    root = Path(__file__).resolve().parents[2]
    assert resources.files("pertura_bench").joinpath(
        "cases", "real_parameters.v1.json"
    ).is_file()
    plan = build_server_plan(benchmark_specs(), root)
    assert plan.executable is False
    assert plan.checkpoint_binding["git_commit"] is None
    assert plan.checkpoint_binding["wheel_sha256"] is None
    assert plan.checkpoint_binding["resource_lock_set_hash"] is None
    assert plan.checkpoint_binding["prediction_bundle_set_hash"] is None
    assert plan.checkpoint_binding["server_plan_hash"] is None
    assert plan.checkpoint_binding["template_digest"].startswith("sha256:")
    with pytest.raises(ValueError, match="not checkpoint-bound"):
        assert_server_plan_executable(plan)

    capability_jobs = [item for item in plan.jobs if item["kind"] == "capability"]
    expected_jobs = sum(
        len(spec.required_real_datasets) * 4 for spec in benchmark_specs()
    )
    assert len(capability_jobs) == expected_jobs
    for spec in benchmark_specs():
        for dataset_id in spec.required_real_datasets:
            dimensions = {
                (item["tier"], item["split"])
                for item in capability_jobs
                if item["dataset_id"] == dataset_id
                and item["capability_id"] == spec.capability_id
            }
            assert dimensions == {
                ("frozen_subset", "calibration"),
                ("frozen_subset", "evaluation"),
                ("full_dataset", "calibration"),
                ("full_dataset", "evaluation"),
            }
    for job in capability_jobs:
        assert len(job["depends_on"]) == 1
        assert job["depends_on"][0].startswith("prepare:")
        assert job["runtime_execution"]["scope"] == "single_persistent_pertura_runtime"
        assert job["runtime_execution"]["dependency_resolution"] == "runtime_owned"
        dag = job["runtime_execution"]["capability_dag"]
        assert dag[-1] == job["capability_id"]
        coverage = job["real_parameter_coverage"]
        assert [item["capability_id"] for item in coverage] == dag
        assert all(item["tier"] == job["tier"] for item in coverage)
        assert all(item["split"] == job["split"] for item in coverage)
        assert all(
            item["configured"] or str(item["reason"]).startswith("not_configured:")
            for item in coverage
        )
        assert job["configuration_state"] == (
            "configured"
            if all(item["configured"] for item in coverage)
            else "not_configured"
        )
        assert job["real_parameter_catalog"]["version"] == "v1"
        assert job["real_parameter_catalog"]["hash"].startswith("sha256:")
        assert job["checkpoint_requirement"]["required"] is True
        assert all(item.startswith("artifact:") for item in job["consumes"])
        assert all(item.startswith("verdict:") for item in job["produces"])
        assert set(job["benchmark_catalogs"]) == {
            "parameters", "design_confirmations", "metric_references"
        }
        argv = job["command"]["argv"]
        assert "--parameter-catalog" in argv
        assert "--design-confirmations" in argv
        assert "--metric-reference-catalog" in argv

    agent_jobs = [item for item in plan.jobs if item["kind"] == "agent_workflow"]
    assert len(agent_jobs) == 8 * 3 * 2
    grouped: dict[str, list[dict]] = {}
    for job in agent_jobs:
        grouped.setdefault(job["case_id"], []).append(job)
    for case_id, case_jobs in grouped.items():
        assert len(case_jobs) == 6
        assert {item["benchmark_condition"] for item in case_jobs} == {
            "pertura_full", "prompt_only", "free_codeact"
        }
        assert {item["repeat_index"] for item in case_jobs} == {1, 2}
        assert len({item["dataset_id"] for item in case_jobs}) == 1
        assert len({item["objective"] for item in case_jobs}) == 1
        assert len({str(item["resources"]) for item in case_jobs}) == 1
        assert all(item["controlled_comparison"]["same_model"] for item in case_jobs)
        assert all("--condition" in item["command"]["argv"] for item in case_jobs)
        assert all("--repeat-index" in item["command"]["argv"] for item in case_jobs)

    for binding_name in (
        "parameter_catalog_hash",
        "design_confirmation_catalog_hash",
        "metric_reference_catalog_hash",
    ):
        assert plan.checkpoint_binding[binding_name].startswith("sha256:")

    bound = bind_server_plan(
        plan,
        git_commit="a" * 40,
        wheel_sha256="sha256:" + "b" * 64,
        resource_lock_set_hash="sha256:" + "c" * 64,
        prediction_bundle_set_hash="sha256:" + "d" * 64,
    )
    assert bound.executable is True
    assert bound.checkpoint_binding["template_digest"] == plan.checkpoint_binding[
        "template_digest"
    ]
    assert bound.checkpoint_binding["server_plan_hash"].startswith("sha256:")
    assert bound.checkpoint_binding["server_plan_hash"] != bound.checkpoint_binding[
        "template_digest"
    ]
    assert_server_plan_executable(bound)
    assert validate_checkpoint_binding(plan, bound.checkpoint_binding) == dict(
        bound.checkpoint_binding
    )
    tampered = dict(bound.checkpoint_binding)
    tampered["server_plan_hash"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="server_plan_hash"):
        validate_checkpoint_binding(plan, tampered)
