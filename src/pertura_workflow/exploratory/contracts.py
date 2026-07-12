from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from pertura_core import DependencyRef, ScopeKey, SourceClass
from pertura_core.models import CanonicalModel


_SHA256_PREFIX = "sha256:"


class ExploratoryEffectMatrixContract(CanonicalModel):
    schema_version: str = "pertura-exploratory-effect-matrix-v0"
    effect_matrix_id: str = ""
    id_field = "effect_matrix_id"
    id_prefix = "xeffectmatrix"

    effect_result_ids: tuple[str, ...]
    effect_result_hashes: dict[str, str]
    matrix_hash: str
    learning_scope: ScopeKey
    effect_scale: Literal["log2_fold_change", "standardized_effect"]
    feature_namespace: str
    perturbation_ids: tuple[str, ...]
    feature_ids_hash: str
    missing_mask_hash: str
    method_ids: tuple[str, ...]
    dependencies: tuple[DependencyRef, ...]

    @model_validator(mode="after")
    def _validate_effect_bindings(self) -> "ExploratoryEffectMatrixContract":
        if len(self.effect_result_ids) < 2:
            raise ValueError("effect matrix requires at least two committed effect results")
        if set(self.effect_result_ids) != set(self.effect_result_hashes):
            raise ValueError("effect result IDs and hashes disagree")
        dependencies = {
            (item.object_id, item.object_hash)
            for item in self.dependencies
            if item.required and item.state == "current"
        }
        missing = [
            result_id
            for result_id, result_hash in self.effect_result_hashes.items()
            if (result_id, result_hash) not in dependencies
        ]
        if missing:
            raise ValueError(
                "effect matrix is missing committed result dependencies: "
                + ", ".join(sorted(missing))
            )
        if len(set(self.perturbation_ids)) != len(self.perturbation_ids):
            raise ValueError("effect matrix perturbation IDs must be unique")
        return self


class ExploratoryResponseProgramContract(CanonicalModel):
    schema_version: str = "pertura-exploratory-response-program-v0"
    response_program_id: str = ""
    id_field = "response_program_id"
    id_prefix = "xresponse"

    namespace: Literal["response_program"] = "response_program"
    effect_result_id: str
    effect_result_hash: str
    effect_matrix_hash: str
    learning_scope: ScopeKey
    algorithm: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    program_ids: tuple[str, ...]
    dependencies: tuple[DependencyRef, ...]
    perturbation_labels_used: bool = True
    leakage_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_effect_dependency(self) -> "ExploratoryResponseProgramContract":
        if not self.program_ids:
            raise ValueError("response program contract needs at least one program")
        if not any(
            item.object_id == self.effect_result_id
            and item.object_hash == self.effect_result_hash
            for item in self.dependencies
        ):
            raise ValueError(
                "response programs must depend explicitly on the committed effect result"
            )
        return self


class ExploratoryKnowledgeResourceLock(CanonicalModel):
    schema_version: str = "pertura-exploratory-knowledge-resource-lock-v0"
    resource_lock_id: str = ""
    id_field = "resource_lock_id"
    id_prefix = "xresource"

    profile: str
    release: str
    species: Literal["human", "mouse"]
    identifier_namespace: str
    license: str
    source_urls: tuple[str, ...]
    content_hashes: dict[str, str]
    manifest_hash: str
    local_paths: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _require_portable_identity(self) -> "ExploratoryKnowledgeResourceLock":
        if not self.source_urls or not self.content_hashes:
            raise ValueError("knowledge resource lock requires sources and content hashes")
        if any(not value.startswith(_SHA256_PREFIX) for value in self.content_hashes.values()):
            raise ValueError("knowledge resource content hashes must be SHA-256 values")
        return self


class ExploratoryLiteratureRecordSet(CanonicalModel):
    schema_version: str = "pertura-exploratory-literature-record-set-v0"
    literature_record_set_id: str = ""
    id_field = "literature_record_set_id"
    id_prefix = "xliterature"

    source_class: Literal[SourceClass.curated_prior] = SourceClass.curated_prior
    provider: Literal["europe_pmc"] = "europe_pmc"
    api_version: str
    query: str
    query_hash: str
    response_hash: str
    cache_hash: str
    record_ids: tuple[str, ...]
    pmids: tuple[str, ...] = ()
    dois: tuple[str, ...] = ()
    dependencies: tuple[DependencyRef, ...] = ()


class ExploratoryInterpretationRecord(CanonicalModel):
    schema_version: str = "pertura-exploratory-interpretation-v0"
    interpretation_id: str = ""
    id_field = "interpretation_id"
    id_prefix = "xinterpretation"

    role: Literal[
        "measured",
        "derived",
        "prior",
        "contradiction",
        "hypothesis",
        "next_experiment",
    ]
    source_class: SourceClass
    statement: str
    result_ids: tuple[str, ...] = ()
    dependency_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    role_assignment: Literal["deterministic", "agent_proposed"] = "agent_proposed"

    @model_validator(mode="after")
    def _keep_roles_below_source_ceiling(self) -> "ExploratoryInterpretationRecord":
        if (
            self.role in {"prior", "hypothesis", "next_experiment", "contradiction"}
            and self.source_class == SourceClass.measured_result
        ):
            raise ValueError(
                "prior/contradiction/hypothesis/next-experiment records cannot "
                "claim measured_result source"
            )
        if not self.result_ids and not self.dependency_ids:
            raise ValueError("interpretation records require explicit provenance")
        return self


class ExploratoryVirtualSplitContract(CanonicalModel):
    schema_version: str = "pertura-exploratory-virtual-split-v0"
    virtual_split_id: str = ""
    id_field = "virtual_split_id"
    id_prefix = "xsplit"

    dataset_id: str
    axes: dict[
        Literal["perturbation", "context", "combo", "dose_time", "donor"],
        dict[Literal["train", "validation", "test"], tuple[str, ...]],
    ]
    heldout_axes: tuple[
        Literal["perturbation", "context", "combo", "dose_time", "donor"], ...
    ] = ()
    state_reference_hash: str | None = None
    module_reference_hash: str | None = None
    dependencies: tuple[DependencyRef, ...] = ()

    @model_validator(mode="after")
    def _validate_axis_partitions(self) -> "ExploratoryVirtualSplitContract":
        if "perturbation" not in self.axes:
            raise ValueError("virtual split requires a perturbation axis")
        for axis, partitions in self.axes.items():
            train = set(partitions.get("train", ()))
            validation = set(partitions.get("validation", ()))
            test = set(partitions.get("test", ()))
            if train & validation or train & test or validation & test:
                raise ValueError(
                    f"virtual split axis {axis} has overlapping partitions"
                )
        unknown = set(self.heldout_axes).difference(self.axes)
        if unknown:
            raise ValueError(
                "heldout axes are absent from split: " + ", ".join(sorted(unknown))
            )
        return self

    @property
    def test_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    item
                    for partitions in self.axes.values()
                    for item in partitions.get("test", ())
                }
            )
        )


class ExploratoryPredictionEnvelope(CanonicalModel):
    schema_version: str = "pertura-exploratory-prediction-v0"
    prediction_id: str = ""
    id_field = "prediction_id"
    id_prefix = "xprediction"

    source_class: Literal[SourceClass.prediction] = SourceClass.prediction
    model_id: str
    model_version: str
    split_id: str
    split_hash: str
    prediction_hash: str
    uncertainty_hash: str | None = None
    prediction_unit: str
    prediction_scale: str
    dependencies: tuple[DependencyRef, ...] = ()


class ExploratoryPredictionBundleContract(CanonicalModel):
    schema_version: str = "pertura-exploratory-prediction-bundle-v0"
    prediction_bundle_id: str = ""
    id_field = "prediction_bundle_id"
    id_prefix = "xpredictionbundle"

    source_class: Literal[SourceClass.prediction] = SourceClass.prediction
    model_id: str
    model_version: str
    split_id: str
    split_hash: str
    format: Literal["h5ad", "matrix_bundle", "long_parquet", "chunked_zarr"]
    prediction_scale: str
    row_count: int = Field(gt=0)
    feature_count: int = Field(gt=0)
    row_index_hash: str
    feature_index_hash: str
    prediction_hash: str
    observed_hash: str
    uncertainty_kind: Literal["none", "standard_error", "interval", "quantiles"]
    uncertainty_hash: str | None = None
    axis_columns: tuple[str, ...]
    row_partition_hash: str
    axis_partition_hash: str
    model_training_ids: tuple[str, ...] = ()
    source_paths: tuple[str, ...]
    dependencies: tuple[DependencyRef, ...] = ()

    @model_validator(mode="after")
    def _validate_uncertainty(self) -> "ExploratoryPredictionBundleContract":
        if self.uncertainty_kind == "none" and self.uncertainty_hash is not None:
            raise ValueError("uncertainty hash is present but uncertainty kind is none")
        if self.uncertainty_kind != "none" and not self.uncertainty_hash:
            raise ValueError("declared uncertainty requires a content hash")
        return self


class ExploratoryBaselineResult(CanonicalModel):
    schema_version: str = "pertura-exploratory-baseline-v0"
    baseline_result_id: str = ""
    id_field = "baseline_result_id"
    id_prefix = "xbaseline"

    baseline: Literal["control_mean", "context_mean", "linear_additive"]
    split_id: str
    split_hash: str
    metrics: dict[str, float]
    output_hash: str


class ExploratoryVirtualEvaluationProfile(CanonicalModel):
    schema_version: str = "pertura-exploratory-virtual-evaluation-profile-v0"
    virtual_evaluation_profile_id: str = ""
    id_field = "virtual_evaluation_profile_id"
    id_prefix = "xevalprofile"

    profile_name: str = "dev_unvalidated_v0"
    primary_metric: Literal["median_row_spearman"] = "median_row_spearman"
    bootstrap_iterations: int = Field(default=1000, ge=100)
    seed: int = 1729
    minimum_units: int = Field(default=20, ge=2)
    minimum_features: int = Field(default=100, ge=2)
    collapse_variance_ratio_min: float = Field(default=0.10, gt=0)
    collapse_distance_ratio_min: float = Field(default=0.10, gt=0)
    uncertainty_nominal_coverage: float = Field(default=0.90, gt=0, lt=1)
    uncertainty_tolerance: float = Field(default=0.05, ge=0, lt=0.5)


class ExploratoryLeakageAudit(CanonicalModel):
    schema_version: str = "pertura-exploratory-leakage-audit-v0"
    leakage_audit_id: str = ""
    id_field = "leakage_audit_id"
    id_prefix = "xleakage"

    split_id: str
    split_hash: str
    status: Literal["clear", "limited", "blocked"]
    test_ids: tuple[str, ...]
    model_training_ids: tuple[str, ...] = ()
    state_reference_training_ids: tuple[str, ...] = ()
    module_reference_training_ids: tuple[str, ...] = ()
    preprocessing_training_ids: tuple[str, ...] = ()
    unresolved_checks: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _status_matches_reasons(self) -> "ExploratoryLeakageAudit":
        expected = "blocked" if self.reasons else (
            "limited" if self.unresolved_checks else "clear"
        )
        if self.status != expected:
            raise ValueError("leakage audit status disagrees with detected reasons")
        return self


class ExploratoryNextPanelContract(CanonicalModel):
    schema_version: str = "pertura-exploratory-next-panel-v0"
    next_panel_id: str = ""
    id_field = "next_panel_id"
    id_prefix = "xnextpanel"

    source_class: Literal[SourceClass.hypothesis] = SourceClass.hypothesis
    evaluation_result_id: str
    evaluation_result_hash: str
    candidate_hash: str
    selected_ids: tuple[str, ...]
    rejected_ids: tuple[str, ...]
    budget: float = Field(gt=0)
    budget_used: float = Field(ge=0)
    weights: dict[str, float]
    dependencies: tuple[DependencyRef, ...]

    @model_validator(mode="after")
    def _validate_panel(self) -> "ExploratoryNextPanelContract":
        if self.budget_used > self.budget:
            raise ValueError("selected next panel exceeds budget")
        if set(self.selected_ids) & set(self.rejected_ids):
            raise ValueError("next-panel selected and rejected IDs overlap")
        if not any(
            item.object_id == self.evaluation_result_id
            and item.object_hash == self.evaluation_result_hash
            for item in self.dependencies
        ):
            raise ValueError("next panel requires its committed evaluation dependency")
        return self


def audit_virtual_leakage(
    split: ExploratoryVirtualSplitContract,
    *,
    test_row_ids: tuple[str, ...] | None = None,
    model_training_ids: tuple[str, ...] = (),
    state_reference_training_ids: tuple[str, ...] = (),
    module_reference_training_ids: tuple[str, ...] = (),
    preprocessing_training_ids: tuple[str, ...] = (),
    unresolved_checks: tuple[str, ...] = (),
) -> ExploratoryLeakageAudit:
    effective_test_ids = (
        tuple(sorted(test_row_ids)) if test_row_ids is not None else split.test_ids
    )
    test = set(effective_test_ids)
    reasons: list[str] = []
    groups = (
        ("model", model_training_ids),
        ("state reference", state_reference_training_ids),
        ("module reference", module_reference_training_ids),
        ("preprocessing", preprocessing_training_ids),
    )
    for label, values in groups:
        overlap = sorted(test & set(values))
        if overlap:
            reasons.append(f"{label} used test split IDs: " + ", ".join(overlap))
    status = "blocked" if reasons else ("limited" if unresolved_checks else "clear")
    return ExploratoryLeakageAudit(
        split_id=split.virtual_split_id,
        split_hash=split.canonical_hash,
        status=status,
        test_ids=effective_test_ids,
        model_training_ids=model_training_ids,
        state_reference_training_ids=state_reference_training_ids,
        module_reference_training_ids=module_reference_training_ids,
        preprocessing_training_ids=preprocessing_training_ids,
        unresolved_checks=unresolved_checks,
        reasons=tuple(reasons),
    )
