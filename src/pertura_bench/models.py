from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, HttpUrl, model_validator

from pertura_core.models import CanonicalModel


_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class BenchmarkSourceManifest(CanonicalModel):
    schema_version: str
    manifest_id: str = ""
    id_field = "manifest_id"
    id_prefix = "benchsource"

    dataset_id: str
    article_id: int | None = None
    article_url: HttpUrl | None = None
    file: dict[str, Any] | None = None
    source: str | None = None
    source_url: HttpUrl | None = None
    source_versions: dict[str, str] = Field(default_factory=dict)
    conversion: str | None = None
    intended_uses: tuple[str, ...]
    license_review_url: HttpUrl
    license_status: Literal["reviewed", "required"] = "required"
    license_reviewed_by: str | None = None
    license_review_basis: str | None = None
    output_sha256: str | None = None
    perturb_seq_claim_allowed: bool = True
    download_tier: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _validate_source(self) -> "BenchmarkSourceManifest":
        if not self.file and not self.source:
            raise ValueError("benchmark manifest needs a downloadable file or an explicit conversion source")
        if self.file:
            required = ("name", "download_url", "supplied_md5", "size_bytes")
            missing = [name for name in required if not self.file.get(name)]
            if missing:
                raise ValueError("downloadable benchmark file is incomplete: " + ", ".join(missing))
            if not _HEX32.fullmatch(str(self.file["supplied_md5"])):
                raise ValueError("supplied_md5 must be 32 lowercase hexadecimal characters")
            if int(self.file["size_bytes"]) <= 0:
                raise ValueError("benchmark source size must be positive")
        if self.output_sha256 and not _SHA256.fullmatch(self.output_sha256):
            raise ValueError("output_sha256 must use the canonical sha256: prefix")
        if self.license_status == "reviewed" and not (
            self.license_reviewed_by and self.license_review_basis
        ):
            raise ValueError(
                "reviewed benchmark licenses require reviewer and review basis"
            )
        if self.license_status == "required" and (
            self.license_reviewed_by or self.license_review_basis
        ):
            raise ValueError(
                "unreviewed benchmark licenses cannot carry review attestations"
            )
        return self


class BenchmarkArtifactLock(CanonicalModel):
    schema_version: str = "pertura-benchmark-artifact-lock-v1"
    lock_id: str = ""
    id_field = "lock_id"
    id_prefix = "benchlock"

    dataset_id: str
    source_manifest_hash: str
    artifact_sha256: str
    size_bytes: int = Field(gt=0)
    upstream_checksum: str | None = None
    upstream_lock_hash: str | None = None
    conversion_script_hash: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    package_versions: dict[str, str] = Field(default_factory=dict)
    license_status: Literal["reviewed", "required"]

    @model_validator(mode="after")
    def _validate_hashes(self) -> "BenchmarkArtifactLock":
        for name in (
            "source_manifest_hash",
            "artifact_sha256",
            "upstream_lock_hash",
            "conversion_script_hash",
        ):
            value = getattr(self, name)
            if value is not None and not _SHA256.fullmatch(value):
                raise ValueError(f"{name} must use the canonical sha256: prefix")
        for value in self.parameters.values():
            if isinstance(value, str) and (value.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", value)):
                raise ValueError("portable benchmark locks cannot contain absolute paths")
        return self


class BenchmarkSubsetSpec(CanonicalModel):
    schema_version: str = "pertura-benchmark-subset-spec-v1"
    subset_spec_id: str = ""
    id_field = "subset_spec_id"
    id_prefix = "subset_spec"

    dataset_id: str
    source_lock_hash: str
    split: Literal["calibration", "evaluation"]
    label_column: str
    labels: tuple[str, ...]
    max_cells_per_label: int = Field(default=500, gt=0)
    seed: int = 1729
    selection: dict[str, Any] = Field(default_factory=dict)


class BenchmarkSubsetLock(CanonicalModel):
    schema_version: str = "pertura-benchmark-subset-lock-v1"
    subset_lock_id: str = ""
    id_field = "subset_lock_id"
    id_prefix = "subset_lock"

    dataset_id: str
    subset_spec_hash: str
    source_lock_hash: str
    output_sha256: str
    n_cells: int = Field(gt=0)
    n_genes: int = Field(gt=0)
    subset_script_hash: str
    selected_ids_sha256: str | None = None
    selection_manifest_sha256: str | None = None


class BenchmarkSplitManifest(CanonicalModel):
    schema_version: str = "pertura-benchmark-split-v1"
    split_id: str = ""
    id_field = "split_id"
    id_prefix = "benchsplit"

    modality: Literal["crispri", "crispra"]
    seed: int = 1729
    calibration_fraction: float = 0.60
    calibration_ids: tuple[str, ...]
    evaluation_ids: tuple[str, ...]
    label_class: Literal["published_proxy", "expert_adjudicated"] = "published_proxy"

    @model_validator(mode="after")
    def _validate_split(self) -> "BenchmarkSplitManifest":
        if set(self.calibration_ids) & set(self.evaluation_ids):
            raise ValueError("calibration and evaluation target sets must be disjoint")
        if not self.calibration_ids or not self.evaluation_ids:
            raise ValueError("both calibration and evaluation target sets are required")
        if self.label_class == "expert_adjudicated":
            if len(self.calibration_ids) + len(self.evaluation_ids) < 50:
                raise ValueError("expert benchmark requires at least 50 target verdicts")
            if len(self.evaluation_ids) < 20:
                raise ValueError("expert benchmark requires at least 20 final-evaluation targets")
        return self


class TargetVerdict(CanonicalModel):
    schema_version: str = "pertura-target-verdict-v1"
    target_verdict_id: str = ""
    id_field = "target_verdict_id"
    id_prefix = "target_verdict"

    modality: Literal["crispri", "crispra"]
    dataset_id: str
    target_id: str
    expected_direction: Literal["down", "up"]
    verdict: Literal["screen_passed", "caution", "blocked", "unresolved"]
    reason_codes: tuple[str, ...]
    label_source: Literal["published_proxy", "expert_adjudicated"]
    validated: bool = False
    doi: str | None = None
    pmid: str | None = None
    supplement: str | None = None
    table: str | None = None
    sheet: str | None = None
    row: str | None = None
    importer_version: str
    importer_hash: str
    reviewer_ids: tuple[str, ...] = ()
    adjudication: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_provenance(self) -> "TargetVerdict":
        if not _SHA256.fullmatch(self.importer_hash):
            raise ValueError("importer_hash must use the canonical sha256: prefix")
        if self.label_source == "published_proxy":
            if self.validated:
                raise ValueError("published proxy verdicts can never be production validated")
            if not (self.doi or self.pmid) or not (self.table or self.supplement):
                raise ValueError("published proxy verdict needs a publication and exact table/supplement provenance")
        if self.label_source == "expert_adjudicated":
            if len(self.reviewer_ids) < 2 or not self.adjudication.get("adjudicator_id"):
                raise ValueError("expert verdict needs two reviewers and explicit adjudication")
        return self


class TargetVerdictSet(CanonicalModel):
    schema_version: str = "pertura-target-verdict-set-v1"
    verdict_set_id: str = ""
    id_field = "verdict_set_id"
    id_prefix = "verdict_set"

    modality: Literal["crispri", "crispra"]
    label_source: Literal["published_proxy", "expert_adjudicated"]
    split_manifest_hash: str
    verdicts: tuple[TargetVerdict, ...]
    validated: bool = False

    @model_validator(mode="after")
    def _validate_set(self) -> "TargetVerdictSet":
        if any(item.modality != self.modality or item.label_source != self.label_source for item in self.verdicts):
            raise ValueError("verdict set contains inconsistent modality or label source")
        if self.label_source == "published_proxy" and self.validated:
            raise ValueError("published proxy verdict sets cannot be production validated")
        if self.validated and any(not item.validated for item in self.verdicts):
            raise ValueError("validated verdict sets cannot contain unvalidated target verdicts")
        return self


class GoldenComparison(CanonicalModel):
    schema_version: str = "pertura-golden-comparison-v1"
    comparison_id: str = ""
    id_field = "comparison_id"
    id_prefix = "golden"

    environment_lock_hash: str
    input_hashes: dict[str, str]
    reference_script_hash: str
    runner_hash: str
    maximum_errors: dict[str, float]
    tolerance: float = 1e-7
    cases: dict[str, str]
    passed: bool

    @model_validator(mode="after")
    def _validate_pass(self) -> "GoldenComparison":
        calculated = all(value <= self.tolerance for value in self.maximum_errors.values())
        if self.passed != calculated:
            raise ValueError("golden comparison passed flag disagrees with recorded errors")
        return self

# Public re-exports keep the benchmark model surface discoverable from
# pertura_bench.models while the capability-specific definitions remain
# isolated from the legacy benchmark protocol implementation.
from pertura_bench.capability_models import (  # noqa: E402
    CapabilityBenchmarkCase,
    CapabilityBenchmarkMatrix,
    CapabilityBenchmarkMetric,
    CapabilityBenchmarkSpec,
    CapabilityBenchmarkVerdict,
    CapabilityCoverageEntry,
    ScientificResultDigest,
    ServerBenchmarkPlan,
)
