from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, model_validator

from pertura_core.models import CanonicalModel


BenchmarkTier = Literal["unit", "synthetic_ci", "frozen_subset", "full_dataset"]
BenchmarkOutcome = Literal[
    "passed",
    "failed",
    "not_available",
    "not_run_environment_missing",
]
BenchmarkExecutionMode = Literal["product_path", "protocol_fake", "stale_audit"]
BenchmarkSplit = Literal["calibration", "evaluation"]

_ABSOLUTE_WINDOWS = re.compile(r"^[A-Za-z]:[\\/]")


def _is_absolute_identity(value: str) -> bool:
    return value.startswith(("/", "\\\\")) or bool(_ABSOLUTE_WINDOWS.match(value))


class CapabilityBenchmarkMetric(CanonicalModel):
    schema_version: str = "pertura-capability-benchmark-metric-v2"
    metric_id: str = ""
    id_field = "metric_id"
    id_prefix = "benchmetric"

    name: str
    operator: Literal["eq", "lte", "gte"]
    threshold: float | int | str
    observed: float | int | str | None = None
    passed: bool | None = None


class CapabilityBenchmarkCase(CanonicalModel):
    schema_version: str = "pertura-capability-benchmark-case-v2"
    case_id: str = ""
    id_field = "case_id"
    id_prefix = "benchcase"

    capability_id: str
    capability_version: str
    tier: BenchmarkTier
    scenario: Literal[
        "happy",
        "caution_or_unresolved",
        "blocked",
        "planted_failure",
        "determinism",
        "stale_propagation",
    ]
    fixture_id: str
    fixture_version: str = "1"
    execution_mode: BenchmarkExecutionMode = "product_path"
    seed: int = 1729
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_statuses: tuple[str, ...] = ()
    expected_blocker_contains: tuple[str, ...] = ()
    required_outputs: tuple[str, ...] = ()
    metrics: tuple[CapabilityBenchmarkMetric, ...] = ()
    environment_profile: str | None = None
    environment_required: bool = False
    max_memory_gb: float = Field(default=4.0, gt=0)
    timeout_seconds: int = Field(default=900, gt=0)
    dataset_id: str | None = None

    @model_validator(mode="after")
    def _portable(self) -> "CapabilityBenchmarkCase":
        for value in (self.fixture_id, self.dataset_id or ""):
            if _is_absolute_identity(value):
                raise ValueError("benchmark case identities cannot contain absolute paths")
        if self.tier in {"frozen_subset", "full_dataset"} and not self.dataset_id:
            raise ValueError("real-data benchmark cases require dataset_id")
        if self.execution_mode == "protocol_fake" and not self.environment_profile:
            raise ValueError("protocol fakes must name the external environment they emulate")
        return self


class CapabilityBenchmarkSpec(CanonicalModel):
    schema_version: str = "pertura-capability-benchmark-spec-v2"
    benchmark_spec_id: str = ""
    id_field = "benchmark_spec_id"
    id_prefix = "capbenchspec"

    catalog_version: str = "v1"
    capability_id: str
    capability_version: str
    cases: tuple[CapabilityBenchmarkCase, ...]
    required_real_datasets: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> "CapabilityBenchmarkSpec":
        if any(
            case.capability_id != self.capability_id
            or case.capability_version != self.capability_version
            for case in self.cases
        ):
            raise ValueError("capability benchmark spec contains a case for another capability")
        scenarios = [case.scenario for case in self.cases if case.tier == "synthetic_ci"]
        required = {
            "happy",
            "caution_or_unresolved",
            "blocked",
            "planted_failure",
            "determinism",
            "stale_propagation",
        }
        if set(scenarios) != required or len(scenarios) != len(required):
            raise ValueError("each capability needs exactly the six synthetic scenarios")
        return self


class ScientificResultDigest(CanonicalModel):
    """Path-, clock- and run-identity-free digest of scientific behavior."""

    schema_version: str = "pertura-scientific-result-digest-v1"
    scientific_digest_id: str = ""
    id_field = "scientific_digest_id"
    id_prefix = "scidigest"

    capability_id: str
    capability_version: str
    status: str
    result_kind: str
    source_class: str
    scope_payload: dict[str, Any]
    blockers: tuple[str, ...] = ()
    cautions: tuple[str, ...] = ()
    metrics: dict[str, Any] = Field(default_factory=dict)
    output_content_hashes: dict[str, str] = Field(default_factory=dict)
    dependency_content_hashes: tuple[str, ...] = ()
    scientific_metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityBenchmarkVerdict(CanonicalModel):
    schema_version: str = "pertura-capability-benchmark-verdict-v2"
    verdict_id: str = ""
    id_field = "verdict_id"
    id_prefix = "capbenchverdict"

    case_id: str
    case_hash: str
    capability_id: str
    capability_version: str
    tier: BenchmarkTier
    execution_mode: BenchmarkExecutionMode
    outcome: BenchmarkOutcome
    observed_status: str | None = None
    observed_blockers: tuple[str, ...] = ()
    metrics: tuple[CapabilityBenchmarkMetric, ...] = ()
    input_hashes: dict[str, str] = Field(default_factory=dict)
    output_hashes: dict[str, str] = Field(default_factory=dict)
    scientific_result_hash: str | None = None
    runner_hash: str | None = None
    environment_lock_hash: str | None = None
    reasons: tuple[str, ...] = ()
    runtime_seconds: float | None = None
    peak_memory_mb: float | None = None


class CapabilityCoverageEntry(CanonicalModel):
    schema_version: str = "pertura-capability-coverage-entry-v2"
    coverage_entry_id: str = ""
    id_field = "coverage_entry_id"
    id_prefix = "capcoverage"

    capability_id: str
    capability_version: str
    code_ready: bool
    local_fixture_ready: bool
    environment_ready: bool | None = None
    real_benchmark_ready: bool = False
    synthetic_case_ids: tuple[str, ...] = ()
    current_verdict_ids: tuple[str, ...] = ()
    required_real_datasets: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


class CapabilityBenchmarkMatrix(CanonicalModel):
    schema_version: str = "pertura-capability-benchmark-matrix-v2"
    matrix_id: str = ""
    id_field = "matrix_id"
    id_prefix = "capmatrix"

    entries: tuple[CapabilityCoverageEntry, ...]
    code_ready: bool
    local_fixture_ready: bool
    optional_environment_ready: bool | None
    real_benchmark_ready: bool
    release_ready: bool = False

    @model_validator(mode="after")
    def _derived_flags(self) -> "CapabilityBenchmarkMatrix":
        if self.code_ready != all(item.code_ready for item in self.entries):
            raise ValueError("matrix code_ready disagrees with coverage entries")
        if self.local_fixture_ready != all(item.local_fixture_ready for item in self.entries):
            raise ValueError("matrix local_fixture_ready disagrees with coverage entries")
        if self.real_benchmark_ready != all(item.real_benchmark_ready for item in self.entries):
            raise ValueError("matrix real_benchmark_ready disagrees with coverage entries")
        known = [item.environment_ready for item in self.entries if item.environment_ready is not None]
        derived_environment = all(known) if known else None
        if self.optional_environment_ready != derived_environment:
            raise ValueError("matrix optional_environment_ready disagrees with coverage entries")
        if self.release_ready and not self.real_benchmark_ready:
            raise ValueError("release cannot be ready without real-data benchmark coverage")
        return self


class ServerBenchmarkPlan(CanonicalModel):
    schema_version: str = "pertura-server-benchmark-plan-v2"
    plan_id: str = ""
    id_field = "plan_id"
    id_prefix = "serverbench"

    artifacts: tuple[dict[str, Any], ...]
    jobs: tuple[dict[str, Any], ...]
    datasets: tuple[str, ...]
    scheduler: Literal["neutral"] = "neutral"
    cache_layout: str = "datasets/{dataset_id}/{artifact_kind}/{artifact_hash}"
    retry_policy: dict[str, Any] = Field(
        default_factory=lambda: {"max_attempts": 2, "retry_on": ["timeout", "worker_lost"]}
    )
    checkpoint_binding: dict[str, str | None] = Field(
        default_factory=lambda: {
            "git_commit": None,
            "wheel_sha256": None,
            "case_catalog_hash": None,
            "agent_case_catalog_hash": None,
            "skill_bundle_hash": None,
            "capability_spec_hash": None,
            "judge_manifest_hash": None,
            "report_turn_schema_hash": None,
            "template_digest": None,
            "resource_lock_set_hash": None,
            "prediction_bundle_set_hash": None,
            "server_plan_hash": None,
        }
    )
    executable: bool = False

    @model_validator(mode="after")
    def _no_shell_placeholders(self) -> "ServerBenchmarkPlan":
        rendered = str(self.model_dump(mode="json", exclude={"canonical_hash"}))
        if "<" in rendered or ">" in rendered:
            raise ValueError("server benchmark plans cannot contain manual angle-bracket placeholders")
        required = {
            "git_commit",
            "wheel_sha256",
            "case_catalog_hash",
            "agent_case_catalog_hash",
            "skill_bundle_hash",
            "capability_spec_hash",
            "judge_manifest_hash",
            "report_turn_schema_hash",
            "template_digest",
            "resource_lock_set_hash",
            "prediction_bundle_set_hash",
            "server_plan_hash",
        }
        if set(self.checkpoint_binding) != required:
            raise ValueError(
                "server benchmark checkpoint_binding must contain exactly: "
                + ", ".join(sorted(required))
            )
        bound = all(self.checkpoint_binding.get(name) for name in required)
        if self.executable != bound:
            raise ValueError(
                "server benchmark plan is executable only when every checkpoint binding is present"
            )
        return self
