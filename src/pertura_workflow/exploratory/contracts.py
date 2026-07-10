from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from pertura_core import DependencyRef, ScopeKey, SourceClass
from pertura_core.models import CanonicalModel


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
        if not any(item.object_id == self.effect_result_id and item.object_hash == self.effect_result_hash for item in self.dependencies):
            raise ValueError("response programs must depend explicitly on the committed effect result")
        return self


class ExploratoryInterpretationRecord(CanonicalModel):
    schema_version: str = "pertura-exploratory-interpretation-v0"
    interpretation_id: str = ""
    id_field = "interpretation_id"
    id_prefix = "xinterpretation"

    role: Literal["measured", "derived", "prior", "contradiction", "hypothesis", "next_experiment"]
    source_class: SourceClass
    statement: str
    result_ids: tuple[str, ...] = ()
    dependency_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _keep_roles_below_source_ceiling(self) -> "ExploratoryInterpretationRecord":
        if self.role in {"prior", "hypothesis", "next_experiment"} and self.source_class == SourceClass.measured_result:
            raise ValueError("prior/hypothesis/next-experiment records cannot claim measured_result source")
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
                raise ValueError(f"virtual split axis {axis} has overlapping partitions")
        return self

    @property
    def test_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item for partitions in self.axes.values() for item in partitions.get("test", ())}))


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


class ExploratoryLeakageAudit(CanonicalModel):
    schema_version: str = "pertura-exploratory-leakage-audit-v0"
    leakage_audit_id: str = ""
    id_field = "leakage_audit_id"
    id_prefix = "xleakage"

    split_id: str
    split_hash: str
    status: Literal["clear", "blocked"]
    test_ids: tuple[str, ...]
    state_reference_training_ids: tuple[str, ...] = ()
    module_reference_training_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _status_matches_reasons(self) -> "ExploratoryLeakageAudit":
        expected = "blocked" if self.reasons else "clear"
        if self.status != expected:
            raise ValueError("leakage audit status disagrees with detected reasons")
        return self


def audit_virtual_leakage(
    split: ExploratoryVirtualSplitContract,
    *,
    state_reference_training_ids: tuple[str, ...] = (),
    module_reference_training_ids: tuple[str, ...] = (),
) -> ExploratoryLeakageAudit:
    test = set(split.test_ids)
    reasons = []
    state_overlap = sorted(test & set(state_reference_training_ids))
    module_overlap = sorted(test & set(module_reference_training_ids))
    if state_overlap:
        reasons.append("state reference used test split IDs: " + ", ".join(state_overlap))
    if module_overlap:
        reasons.append("module reference used test split IDs: " + ", ".join(module_overlap))
    return ExploratoryLeakageAudit(
        split_id=split.virtual_split_id,
        split_hash=split.canonical_hash,
        status="blocked" if reasons else "clear",
        test_ids=split.test_ids,
        state_reference_training_ids=state_reference_training_ids,
        module_reference_training_ids=module_reference_training_ids,
        reasons=tuple(reasons),
    )
