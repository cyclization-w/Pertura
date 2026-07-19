from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from pertura_workflow.capabilities import CapabilityRegistry
from pertura_runtime.agent_bundle import BUNDLED_SKILL_NAMES


PAPER_CONDITIONS = ("free_codeact", "prompt_only", "pertura_full")
PAPER_REPEATS = (1, 2)
PAPER_AGENT_MAX_TURNS = 64
PAPER_WORKFLOW_MEMORY_GB = {
    "WF-REPL": 48.0,
    "WF-PAPA": 32.0,
    "WF-NORM": 32.0,
    "WF-KANG": 32.0,
}
PAPER_CODEACT_PROTOCOL_IDS = (
    "composition.propeller.v1",
    "pseudobulk.edger_ql.v1",
)
PAPER_TASK_EVALUATION_DOMAINS = {
    "REPL-01": "protocol_claim_compliance",
    "REPL-02": "scientific_fidelity",
    "REPL-03": "protocol_claim_compliance",
    "REPL-04": "protocol_claim_compliance",
    "PAPA-01": "scientific_fidelity",
    "PAPA-02": "scientific_fidelity",
    "PAPA-03": "scientific_fidelity",
    "PAPA-04": "scientific_fidelity",
    "PAPA-05": "scientific_fidelity",
    "PAPA-06": "scientific_fidelity",
    "PAPA-07": "scientific_fidelity",
    "PAPA-08": "protocol_claim_compliance",
    "NORM-01": "protocol_claim_compliance",
    "NORM-02": "scientific_fidelity",
    "NORM-03": "protocol_claim_compliance",
    "NORM-04": "protocol_claim_compliance",
    "NORM-05": "protocol_claim_compliance",
    "NORM-06": "protocol_claim_compliance",
    "KANG-01": "supplemental_scientific_fidelity",
    "KANG-02": "supplemental_scientific_fidelity",
    "VIRT-01": "optional_prediction_protocol",
}
PAPER_SCIENTIFIC_EVALUATOR_TASKS = frozenset(
    task_id
    for task_id, domain in PAPER_TASK_EVALUATION_DOMAINS.items()
    if domain in {"scientific_fidelity", "supplemental_scientific_fidelity"}
)
PAPER_CUSTOM_EVALUATOR_KEY_CONTRACTS = {
    "PAPA-06": {
        "trans_de_results.tsv": ("target_uid", "gene"),
        "trans_de_design_matrices.tsv": ("target_uid", "sample_id"),
    },
    "PAPA-07": {
        "global_effect_claims.tsv": ("target_uid",),
    },
}
PAPER_TASK_SKILLS = {
    "REPL-01": ("operate-pertura-workflow", "inspect-perturb-seq-design"),
    "REPL-02": ("operate-pertura-workflow", "diagnose-perturb-seq-screen"),
    "REPL-03": (
        "operate-pertura-workflow",
        "inspect-perturb-seq-design",
        "diagnose-perturb-seq-screen",
    ),
    "REPL-04": ("operate-pertura-workflow", "interpret-perturb-seq-results"),
    "PAPA-01": ("operate-pertura-workflow", "diagnose-perturb-seq-screen"),
    "PAPA-02": ("operate-pertura-workflow", "inspect-perturb-seq-design"),
    "PAPA-03": ("operate-pertura-workflow",),
    "PAPA-04": ("operate-pertura-workflow", "diagnose-perturb-seq-screen"),
    "PAPA-05": ("operate-pertura-workflow", "diagnose-perturb-seq-screen"),
    "PAPA-06": (
        "operate-pertura-workflow",
        "run-replicate-aware-pseudobulk-de",
    ),
    "PAPA-07": ("interpret-perturb-seq-results",),
    "PAPA-08": ("operate-pertura-workflow", "interpret-perturb-seq-results"),
    "NORM-01": ("operate-pertura-workflow", "inspect-perturb-seq-design"),
    "NORM-02": (
        "operate-pertura-workflow",
        "inspect-perturb-seq-design",
        "diagnose-perturb-seq-screen",
    ),
    "NORM-03": ("operate-pertura-workflow", "inspect-perturb-seq-design"),
    "NORM-04": ("operate-pertura-workflow", "interpret-perturb-seq-results"),
    "NORM-05": (
        "operate-pertura-workflow",
        "inspect-perturb-seq-design",
        "interpret-perturb-seq-results",
    ),
    "NORM-06": (
        "operate-pertura-workflow",
        "inspect-perturb-seq-design",
        "interpret-perturb-seq-results",
    ),
    "KANG-01": (
        "operate-pertura-workflow",
        "run-replicate-aware-pseudobulk-de",
        "run-design-preserving-null-calibration",
    ),
    "KANG-02": (
        "operate-pertura-workflow",
        "inspect-perturb-seq-design",
        "interpret-perturb-seq-results",
    ),
    "VIRT-01": (
        "operate-pertura-workflow",
        "evaluate-virtual-perturb-seq-model",
    ),
}


@dataclass(frozen=True)
class PaperTaskCatalog:
    path: Path
    payload: Mapping[str, Any]
    sha256: str

    @property
    def workflows(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(self.payload.get("workflows") or ())

    def workflow(self, workflow_id: str) -> Mapping[str, Any]:
        for workflow in self.workflows:
            if workflow.get("workflow_id") == workflow_id:
                return workflow
        raise KeyError(f"unknown paper workflow: {workflow_id}")

    def tasks(self, *, include_optional: bool = True) -> tuple[Mapping[str, Any], ...]:
        tasks = tuple(
            task for workflow in self.workflows for task in workflow.get("turns") or ()
        )
        if include_optional:
            return tasks
        return tuple(task for task in tasks if task.get("role") != "optional")


def default_paper_task_catalog(repo_root: str | Path) -> Path:
    return Path(repo_root).resolve() / "benchmarks" / "paper_v1" / "agent_tasks.v2.json"


def load_paper_task_catalog(
    path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    validate: bool = True,
) -> PaperTaskCatalog:
    if path is None:
        if repo_root is None:
            repo_root = Path(__file__).resolve().parents[2]
        resolved = default_paper_task_catalog(repo_root)
    else:
        resolved = Path(path).resolve()
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if validate:
        problems = validate_paper_task_catalog(payload)
        if problems:
            raise ValueError("invalid paper task catalog: " + "; ".join(problems))
    return PaperTaskCatalog(
        path=resolved,
        payload=payload,
        sha256="sha256:" + hashlib.sha256(resolved.read_bytes()).hexdigest(),
    )


def validate_paper_task_catalog(payload: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    if payload.get("schema_version") != "pertura-paper-agent-tasks-v2":
        problems.append("schema_version must be pertura-paper-agent-tasks-v2")
    protocol = payload.get("execution_protocol") or {}
    conditions = tuple(protocol.get("conditions") or ())
    if set(conditions) != set(PAPER_CONDITIONS) or len(conditions) != 3:
        problems.append("conditions must be the frozen three-condition set")
    if protocol.get("repeats") != 2:
        problems.append("repeats must equal 2")

    capability_specs = {
        item.capability_id: item
        for item in CapabilityRegistry.load_default(include_external=False).specs()
    }
    known_capabilities = set(capability_specs)
    known_codeact_protocols = set(PAPER_CODEACT_PROTOCOL_IDS)
    workflows = tuple(payload.get("workflows") or ())
    if len(workflows) != 4:
        problems.append("exactly four workflows are required")
    task_ids: list[str] = []
    output_paths: list[str] = []
    primary: list[Mapping[str, Any]] = []
    supplemental: list[Mapping[str, Any]] = []
    optional: list[Mapping[str, Any]] = []
    tier_counts = {"basic": 0, "intermediate": 0, "advanced": 0}

    for workflow in workflows:
        workflow_id = str(workflow.get("workflow_id") or "")
        role = workflow.get("role")
        if role not in {"primary", "supplemental"}:
            problems.append(f"{workflow_id}: invalid workflow role")
        prior: set[str] = set()
        for expected_index, task in enumerate(workflow.get("turns") or (), start=1):
            task_id = str(task.get("task_id") or "")
            task_ids.append(task_id)
            if task.get("turn_index") != expected_index:
                problems.append(f"{task_id}: nonsequential turn_index")
            dependencies = set(task.get("depends_on_tasks") or ())
            unknown_dependencies = dependencies - prior
            if unknown_dependencies:
                problems.append(
                    f"{task_id}: dependencies are not prior workflow turns: "
                    f"{sorted(unknown_dependencies)}"
                )
            prior.add(task_id)
            unknown_capabilities = (
                set(task.get("expected_capability_dag") or ()) - known_capabilities
            )
            if unknown_capabilities:
                problems.append(
                    f"{task_id}: unknown capabilities: {sorted(unknown_capabilities)}"
                )
            expected_capabilities = set(task.get("expected_capability_dag") or ())
            explicit_nonexecutions = set(
                task.get("expected_nonexecutions") or ()
            )
            unknown_nonexecutions = explicit_nonexecutions - known_capabilities
            if unknown_nonexecutions:
                problems.append(
                    f"{task_id}: unknown explicit nonexecutions: "
                    f"{sorted(unknown_nonexecutions)}"
                )
            conflicting_capabilities = (
                expected_capabilities & explicit_nonexecutions
            )
            if conflicting_capabilities:
                problems.append(
                    f"{task_id}: capabilities cannot be both expected and explicit "
                    f"nonexecutions: {sorted(conflicting_capabilities)}"
                )
            blocked_dependencies = {
                dependency
                for capability_id in expected_capabilities - unknown_capabilities
                for dependency in capability_specs[capability_id].depends_on
                if dependency in explicit_nonexecutions
            }
            if blocked_dependencies:
                problems.append(
                    f"{task_id}: expected capabilities depend on explicit "
                    f"nonexecutions: {sorted(blocked_dependencies)}"
                )
            if task.get("execution_mode") in {
                "codeact_scientific",
                "evidence_interpretation",
            } and task.get("expected_capability_dag"):
                problems.append(
                    f"{task_id}: non-capability task declares a capability DAG"
                )
            codeact = dict(task.get("codeact_protocol") or {})
            if task.get("execution_mode") == "codeact_scientific" and not codeact:
                problems.append(
                    f"{task_id}: codeact_scientific requires a frozen protocol"
                )
            if task.get("execution_mode") == "evidence_interpretation" and codeact:
                problems.append(
                    f"{task_id}: evidence interpretation cannot declare CodeAct"
                )
            if codeact:
                protocol_id = str(codeact.get("protocol_id") or "")
                if protocol_id not in known_codeact_protocols:
                    problems.append(
                        f"{task_id}: unknown CodeAct protocol {protocol_id!r}"
                    )
                for field in ("analysis_unit", "design", "pairing"):
                    if not codeact.get(field):
                        problems.append(
                            f"{task_id}: CodeAct binding is missing {field}"
                        )
                role_bindings = dict(codeact.get("input_role_bindings") or {})
                unknown_roles = set(role_bindings.values()) - set(
                    task.get("required_input_roles") or ()
                )
                if unknown_roles:
                    problems.append(
                        f"{task_id}: CodeAct binding uses unknown input roles: "
                        f"{sorted(unknown_roles)}"
                    )
            raw_skills = task.get("pertura_skills")
            if not isinstance(raw_skills, list):
                problems.append(f"{task_id}: missing pertura_skills")
            else:
                normalized_skills = tuple(str(item) for item in raw_skills)
                if normalized_skills != PAPER_TASK_SKILLS.get(task_id):
                    problems.append(f"{task_id}: invalid frozen skill binding")
                unknown_skills = set(normalized_skills) - set(BUNDLED_SKILL_NAMES)
                if unknown_skills:
                    problems.append(
                        f"{task_id}: unknown skills: {sorted(unknown_skills)}"
                    )
                if len(normalized_skills) != len(set(normalized_skills)):
                    problems.append(f"{task_id}: duplicate skill binding")
                if len(normalized_skills) > 3:
                    problems.append(f"{task_id}: more than three skills")
            for field in (
                "objective",
                "paper_anchor_ids",
                "split_usage",
                "required_input_roles",
                "required_artifact_roles",
                "output_contract",
                "task_hard_gates",
                "metric_ids",
                "task_reference_ids",
                "claim_ceiling",
                "resources",
            ):
                if not task.get(field):
                    problems.append(f"{task_id}: missing {field}")
            output_path = str(
                (task.get("output_contract") or {}).get("benchmark_result") or ""
            )
            output_contract = dict(task.get("output_contract") or {})
            allowed_units = output_contract.get("allowed_analysis_units")
            if allowed_units is not None:
                if (
                    not isinstance(allowed_units, list)
                    or not allowed_units
                    or any(
                        not isinstance(item, str)
                        or not item
                        or item != item.strip().lower()
                        for item in allowed_units
                    )
                    or len(allowed_units) != len(set(allowed_units))
                ):
                    problems.append(
                        f"{task_id}: allowed_analysis_units must be a unique "
                        "non-empty lowercase string list"
                    )
            if {
                "required_text_patterns",
                "forbidden_text_patterns",
            } & set(output_contract):
                problems.append(
                    f"{task_id}: evaluator text patterns cannot enter the task contract"
                )
            if output_path:
                output_paths.append(output_path)
                expected_path = f"outputs/tasks/{task_id}/benchmark_result.json"
                if output_path != expected_path:
                    problems.append(
                        f"{task_id}: benchmark_result must be {expected_path}"
                    )
            artifact_paths = dict(
                (task.get("output_contract") or {}).get("artifact_paths") or {}
            )
            artifact_schemas = dict(
                (task.get("output_contract") or {}).get("artifact_schemas") or {}
            )
            artifact_semantics = dict(
                (task.get("output_contract") or {}).get("artifact_semantics") or {}
            )
            if set(artifact_paths) != set(task.get("required_artifact_roles") or ()):
                problems.append(
                    f"{task_id}: every required artifact role needs one path"
                )
            for role_name, relative_name in artifact_paths.items():
                relative = Path(str(relative_name))
                if (
                    not str(role_name)
                    or not str(relative_name)
                    or relative.is_absolute()
                    or ".." in relative.parts
                    or ":" in str(relative_name)
                    or "\\" in str(relative_name)
                ):
                    problems.append(f"{task_id}: unsafe artifact path for {role_name}")
            if not set(artifact_schemas).issubset(set(artifact_paths.values())):
                problems.append(
                    f"{task_id}: artifact_schemas contains an undeclared path"
                )
            if any(
                not isinstance(columns, list) or not columns
                for columns in artifact_schemas.values()
            ):
                problems.append(f"{task_id}: artifact schema columns are invalid")
            if not set(artifact_semantics).issubset(set(artifact_paths.values())):
                problems.append(
                    f"{task_id}: artifact_semantics contains an undeclared path"
                )
            known_input_roles = set(task.get("required_input_roles") or ())
            for relative_name, semantic in artifact_semantics.items():
                if not isinstance(semantic, Mapping):
                    problems.append(
                        f"{task_id}: artifact semantic is invalid for {relative_name}"
                    )
                    continue
                key_columns = tuple(semantic.get("key_columns") or ())
                if not key_columns:
                    continue
                schema_columns = set(artifact_schemas.get(relative_name) or ())
                if not set(key_columns).issubset(schema_columns):
                    problems.append(
                        f"{task_id}: semantic keys are absent from the public schema "
                        f"for {relative_name}"
                    )
                if not str(semantic.get("row_scope") or ""):
                    problems.append(
                        f"{task_id}: row scope is missing for {relative_name}"
                    )
                if semantic.get("row_policy") != "exactly_once":
                    problems.append(
                        f"{task_id}: row policy must be exactly_once for {relative_name}"
                    )
                source_roles = set(semantic.get("row_universe_source_roles") or ())
                if not source_roles:
                    problems.append(
                        f"{task_id}: row-universe sources are missing for {relative_name}"
                    )
                elif not source_roles.issubset(known_input_roles):
                    problems.append(
                        f"{task_id}: unknown row-universe source roles for "
                        f"{relative_name}: {sorted(source_roles - known_input_roles)}"
                    )
            if task.get("role") == "optional":
                optional.append(task)
            elif role == "supplemental":
                supplemental.append(task)
            else:
                primary.append(task)
                tier = str(task.get("tier") or "")
                if tier in tier_counts:
                    tier_counts[tier] += 1
                else:
                    problems.append(f"{task_id}: invalid primary tier {tier}")

    if len(task_ids) != len(set(task_ids)):
        problems.append("task ids must be unique")
    if len(output_paths) != len(set(output_paths)):
        problems.append("task benchmark_result paths must be unique")
    if len(primary) != 18:
        problems.append(f"expected 18 primary tasks, observed {len(primary)}")
    if len(supplemental) != 2:
        problems.append(f"expected 2 supplemental tasks, observed {len(supplemental)}")
    if len(optional) != 1 or optional[0].get("task_id") != "VIRT-01":
        problems.append("VIRT-01 must be the only optional task")
    if tier_counts != {"basic": 6, "intermediate": 8, "advanced": 4}:
        problems.append(f"primary tier counts are incorrect: {tier_counts}")

    required_turns = len(primary) + len(supplemental)
    scored_turns = required_turns * len(PAPER_CONDITIONS) * len(PAPER_REPEATS)
    sessions = len(workflows) * len(PAPER_CONDITIONS) * len(PAPER_REPEATS)
    expected_protocol = {
        "required_primary_task_turns": len(primary),
        "required_supplemental_task_turns": len(supplemental),
        "optional_task_turns": len(optional),
        "required_scored_turns": scored_turns,
        "required_workflows": len(workflows),
        "required_agent_sessions": sessions,
    }
    for field, expected in expected_protocol.items():
        if protocol.get(field) != expected:
            problems.append(f"execution_protocol.{field} must equal {expected}")
    return problems


def validate_task_reference_catalog(
    payload: Mapping[str, Any], tasks: Iterable[Mapping[str, Any]]
) -> list[str]:
    problems: list[str] = []
    schema_version = payload.get("schema_version")
    if schema_version not in {
        "pertura-paper-task-reference-catalog-v1",
        "pertura-paper-task-reference-catalog-bound-v1",
    }:
        problems.append("unsupported task-reference catalog schema")
    bound = schema_version == "pertura-paper-task-reference-catalog-bound-v1"
    if bound and (
        payload.get("status") != "bound"
        or payload.get("passed") is not True
        or payload.get("problems")
    ):
        problems.append("bound task-reference catalog is not valid and complete")
    task_by_id = {str(item["task_id"]): item for item in tasks}
    bindings = tuple(payload.get("bindings") or ())
    by_task: dict[str, list[Mapping[str, Any]]] = {}
    reference_ids: list[str] = []
    scientific_contract_tasks: set[str] = set()
    for binding in bindings:
        task_id = str(binding.get("task_id") or "")
        reference_id = str(binding.get("task_reference_id") or "")
        reference_ids.append(reference_id)
        by_task.setdefault(task_id, []).append(binding)
        if task_id not in task_by_id:
            problems.append(f"{reference_id}: unknown task {task_id}")
            continue
        task_metrics = set(task_by_id[task_id].get("metric_ids") or ())
        binding_metrics = set(binding.get("metric_ids") or ())
        if task_metrics != binding_metrics:
            problems.append(f"{reference_id}: metric ids do not match {task_id}")
        if not binding.get("evaluator_id"):
            problems.append(f"{reference_id}: missing evaluator_id")
        if not binding.get("reference_sources"):
            problems.append(f"{reference_id}: missing reference_sources")
        if not binding.get("observed_artifact_roles"):
            problems.append(f"{reference_id}: missing observed_artifact_roles")
        route = str(binding.get("scoring_route") or "")
        if route not in {
            "artifact_evaluator",
            "protocol_hard_gate",
            "hybrid",
            "custom_artifact_evaluator",
        }:
            problems.append(f"{reference_id}: invalid scoring_route")
        evaluation_domain = str(binding.get("evaluation_domain") or "")
        expected_domain = PAPER_TASK_EVALUATION_DOMAINS.get(task_id)
        if evaluation_domain != expected_domain:
            problems.append(
                f"{reference_id}: evaluation_domain must equal {expected_domain}"
            )
        if (
            evaluation_domain == "protocol_claim_compliance"
            and route != "protocol_hard_gate"
        ):
            problems.append(
                f"{reference_id}: protocol compliance requires protocol_hard_gate"
            )
        if (
            evaluation_domain == "scientific_fidelity"
            and route == "protocol_hard_gate"
        ):
            problems.append(
                f"{reference_id}: scientific fidelity requires an artifact evaluator"
            )
        covered_metrics = {
            str(metric)
            for evaluator in binding.get("evaluator_templates") or ()
            for metric in evaluator.get("metric_ids") or ()
        }
        protocol = binding.get("protocol_evaluator") or {}
        task_units = tuple(
            str(item)
            for item in (
                (task_by_id[task_id].get("output_contract") or {}).get(
                    "allowed_analysis_units"
                )
                or ()
            )
        )
        evaluator_units = tuple(
            str(item) for item in protocol.get("allowed_analysis_units") or ()
        )
        if task_units != evaluator_units:
            problems.append(
                f"{reference_id}: provider-visible analysis units do not match "
                f"the evaluator expected={list(evaluator_units)} "
                f"observed={list(task_units)}"
            )
        covered_metrics.update(str(item) for item in protocol.get("metric_ids") or ())
        if route == "custom_artifact_evaluator":
            covered_metrics.update(
                str(item) for item in binding.get("metric_ids") or ()
            )
        if covered_metrics != binding_metrics:
            problems.append(f"{reference_id}: scoring routes do not cover metric ids")
        artifact_paths = set(
            (
                (task_by_id[task_id].get("output_contract") or {}).get("artifact_paths")
                or {}
            ).values()
        )
        artifact_semantics = dict(
            (task_by_id[task_id].get("output_contract") or {}).get(
                "artifact_semantics"
            )
            or {}
        )
        if task_id in PAPER_SCIENTIFIC_EVALUATOR_TASKS:
            scientific_contract_tasks.add(task_id)
            expected_key_contracts: dict[str, tuple[str, ...]] = {}
            evaluator_specs = tuple(
                binding.get("evaluator_templates")
                or binding.get("evaluators")
                or ()
            )
            for evaluator in evaluator_specs:
                observed_output = str(evaluator.get("observed_output") or "")
                key_columns = tuple(
                    str(item) for item in evaluator.get("key_columns") or ()
                )
                if observed_output and key_columns:
                    prior_keys = expected_key_contracts.setdefault(
                        observed_output, key_columns
                    )
                    if prior_keys != key_columns:
                        problems.append(
                            f"{reference_id}: evaluator key contracts disagree for "
                            f"{observed_output}"
                        )
            expected_key_contracts.update(
                PAPER_CUSTOM_EVALUATOR_KEY_CONTRACTS.get(task_id, {})
            )
            if not expected_key_contracts:
                problems.append(
                    f"{reference_id}: scientific evaluator has no public key contract"
                )
            for observed_output, expected_keys in expected_key_contracts.items():
                semantic = artifact_semantics.get(observed_output)
                if not isinstance(semantic, Mapping):
                    problems.append(
                        f"{reference_id}: provider-visible row-universe contract is "
                        f"missing for {observed_output}"
                    )
                    continue
                observed_keys = tuple(
                    str(item) for item in semantic.get("key_columns") or ()
                )
                if observed_keys != tuple(expected_keys):
                    problems.append(
                        f"{reference_id}: provider-visible keys do not match the "
                        f"evaluator for {observed_output} expected={list(expected_keys)} "
                        f"observed={list(observed_keys)}"
                    )
                if semantic.get("row_policy") != "exactly_once":
                    problems.append(
                        f"{reference_id}: provider-visible row policy must be "
                        f"exactly_once for {observed_output}"
                    )
                if not semantic.get("row_scope") or not semantic.get(
                    "row_universe_source_roles"
                ):
                    problems.append(
                        f"{reference_id}: provider-visible row universe is incomplete "
                        f"for {observed_output}"
                    )
            for evaluator in evaluator_specs:
                if str(evaluator.get("type") or "") != "classification":
                    continue
                observed_output = str(evaluator.get("observed_output") or "")
                observed_label = str(
                    evaluator.get("observed_label_column") or ""
                )
                semantic = artifact_semantics.get(observed_output) or {}
                label_type = str(evaluator.get("label_type") or "categorical")
                if label_type == "boolean":
                    constraint = (
                        (semantic.get("column_constraints") or {}).get(
                            observed_label
                        )
                        or {}
                    )
                    if constraint.get("type") != "boolean":
                        problems.append(
                            f"{reference_id}: provider-visible boolean contract is "
                            f"missing for {observed_output}.{observed_label}"
                        )
                    continue
                allowed_labels = tuple(
                    str(item) for item in evaluator.get("allowed_labels") or ()
                )
                if not allowed_labels:
                    continue
                visible_labels = tuple(
                    str(item)
                    for item in semantic.get(f"{observed_label}_values") or ()
                )
                if visible_labels != allowed_labels:
                    problems.append(
                        f"{reference_id}: provider-visible labels do not match the "
                        f"evaluator for {observed_output}.{observed_label} "
                        f"expected={list(allowed_labels)} "
                        f"observed={list(visible_labels)}"
                    )
            if task_id == "PAPA-06":
                design_semantics = artifact_semantics.get(
                    "trans_de_design_matrices.tsv"
                ) or {}
                if design_semantics.get("condition_label_roles") != {
                    "control": ["control", "frozen baseline value NTC"],
                    "target": ["target", "current row target_uid"],
                }:
                    problems.append(
                        f"{reference_id}: provider-visible condition-label roles "
                        "do not match the trans-DE evaluator"
                    )
        for evaluator in binding.get("evaluator_templates") or ():
            if evaluator.get("observed_output") not in artifact_paths:
                problems.append(
                    f"{reference_id}: evaluator output is absent from task contract"
                )
        for required in protocol.get("required_outputs") or ():
            if required not in artifact_paths:
                problems.append(
                    f"{reference_id}: protocol output is absent from task contract"
                )
        row_counts = protocol.get("required_table_row_counts") or {}
        if not isinstance(row_counts, Mapping):
            problems.append(f"{reference_id}: protocol row counts are invalid")
        else:
            for required, count in row_counts.items():
                if required not in artifact_paths:
                    problems.append(
                        f"{reference_id}: row-count output is absent from task contract"
                    )
                if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                    problems.append(
                        f"{reference_id}: protocol row count must be a non-negative integer"
                    )
        json_values = protocol.get("required_json_values") or {}
        if not isinstance(json_values, Mapping):
            problems.append(f"{reference_id}: protocol JSON values are invalid")
        else:
            for required, values in json_values.items():
                if required not in artifact_paths or not isinstance(values, Mapping):
                    problems.append(
                        f"{reference_id}: protocol JSON value output is invalid"
                    )
        balances = protocol.get("required_json_balances") or []
        if not isinstance(balances, list):
            problems.append(f"{reference_id}: protocol JSON balances are invalid")
        else:
            for balance in balances:
                if (
                    not isinstance(balance, Mapping)
                    or balance.get("output") not in artifact_paths
                    or not str(balance.get("total") or "")
                    or not isinstance(balance.get("parts"), list)
                    or not balance.get("parts")
                ):
                    problems.append(f"{reference_id}: protocol JSON balance is invalid")
        if bound:
            sources = tuple(binding.get("reference_sources") or ())
            bound_sources = tuple(binding.get("bound_reference_sources") or ())
            if len(bound_sources) != len(sources):
                problems.append(f"{reference_id}: reference sources are not bound")
            if route in {"artifact_evaluator", "hybrid"} and not binding.get(
                "evaluators"
            ):
                problems.append(f"{reference_id}: artifact evaluators are not bound")
            if route == "custom_artifact_evaluator" and not binding.get(
                "bound_evaluator"
            ):
                problems.append(f"{reference_id}: custom evaluator is not bound")
    if len(reference_ids) != len(set(reference_ids)):
        problems.append("task reference ids must be unique")
    if scientific_contract_tasks != PAPER_SCIENTIFIC_EVALUATOR_TASKS:
        problems.append(
            "scientific evaluator contract coverage mismatch "
            f"expected={sorted(PAPER_SCIENTIFIC_EVALUATOR_TASKS)} "
            f"observed={sorted(scientific_contract_tasks)}"
        )
    for task_id, task in task_by_id.items():
        expected = set(task.get("task_reference_ids") or ())
        observed = {
            str(item.get("task_reference_id")) for item in by_task.get(task_id, ())
        }
        if expected != observed:
            problems.append(
                f"{task_id}: task-reference binding mismatch "
                f"expected={sorted(expected)} observed={sorted(observed)}"
            )
    return problems


def validate_paper_anchor_catalog(
    payload: Mapping[str, Any], tasks: Iterable[Mapping[str, Any]]
) -> list[str]:
    problems: list[str] = []
    if payload.get("schema_version") != "pertura-paper-anchor-catalog-v1":
        problems.append("unsupported paper-anchor catalog schema")
    anchors = tuple(payload.get("anchors") or ())
    anchor_ids = [str(item.get("anchor_id") or "") for item in anchors]
    if len(anchor_ids) != len(set(anchor_ids)):
        problems.append("paper anchor ids must be unique")
    known = set(anchor_ids)
    for anchor in anchors:
        anchor_id = str(anchor.get("anchor_id") or "")
        for field in (
            "dataset_id",
            "study",
            "study_fact",
            "required_modalities",
            "evaluation_entities",
            "allowed_claim_level",
            "forbidden_promotions",
        ):
            if not anchor.get(field):
                problems.append(f"{anchor_id}: missing {field}")
    for task in tasks:
        unknown = set(task.get("paper_anchor_ids") or ()) - known
        if unknown:
            problems.append(
                f"{task.get('task_id')}: unknown paper anchors {sorted(unknown)}"
            )
    return problems


def validate_paper_asset_catalog(
    payload: Mapping[str, Any], catalog: PaperTaskCatalog
) -> list[str]:
    problems: list[str] = []
    if payload.get("schema_version") != "pertura-paper-agent-assets-v1":
        problems.append("unsupported paper asset catalog schema")
    if (
        payload.get("status") != "bound"
        or payload.get("passed") is not True
        or payload.get("problems")
    ):
        problems.append("paper asset catalog is not bound and complete")
    workflows = payload.get("workflows") or {}
    if not isinstance(workflows, Mapping):
        return [*problems, "paper asset workflows must be an object"]
    expected_workflows = {
        str(workflow["workflow_id"]) for workflow in catalog.workflows
    }
    if set(workflows) != expected_workflows:
        problems.append("paper asset workflow identities do not match the task catalog")
    for workflow in catalog.workflows:
        workflow_id = str(workflow["workflow_id"])
        entry = workflows.get(workflow_id) or {}
        assets = tuple(entry.get("assets") or ())
        roles = [str(item.get("role") or "") for item in assets]
        if (
            not roles
            or len(roles) != len(set(roles))
            or any(not role for role in roles)
        ):
            problems.append(f"{workflow_id}: asset roles are empty or duplicated")
        for asset in assets:
            if not str(asset.get("root") or "") or not str(
                asset.get("relative_path") or ""
            ):
                problems.append(f"{workflow_id}: asset lacks root or relative path")
            content_hash = str(asset.get("content_sha256") or "")
            if not content_hash.startswith("sha256:") or len(content_hash) != 71:
                problems.append(f"{workflow_id}: asset hash is invalid")
        turns = tuple(workflow.get("turns") or ())
        by_task = {str(task["task_id"]): task for task in turns}

        def ancestor_tasks(task_id: str) -> set[str]:
            ancestors: set[str] = set()
            pending = list(by_task[task_id].get("depends_on_tasks") or ())
            while pending:
                dependency = str(pending.pop())
                if dependency in ancestors:
                    continue
                ancestors.add(dependency)
                pending.extend(
                    by_task.get(dependency, {}).get("depends_on_tasks") or ()
                )
            return ancestors

        external_inputs: set[str] = set()
        for task in turns:
            internal_roles = {
                str(role)
                for dependency in ancestor_tasks(str(task["task_id"]))
                for role in by_task[dependency].get("required_artifact_roles") or ()
            }
            for role in task.get("required_input_roles") or ():
                if role not in internal_roles and not (
                    task.get("role") == "optional"
                    and role == "prediction_manifest_optional"
                ):
                    external_inputs.add(str(role))
        missing = external_inputs - set(roles)
        if missing:
            problems.append(
                f"{workflow_id}: external input assets are missing: {sorted(missing)}"
            )
        optional_external = {
            str(role)
            for task in turns
            if task.get("role") == "optional"
            for role in task.get("required_input_roles") or ()
        }
        produced_roles = {
            str(role)
            for task in turns
            for role in task.get("required_artifact_roles") or ()
        }
        masking = (set(roles) & produced_roles) - external_inputs - optional_external
        if masking:
            problems.append(
                f"{workflow_id}: unexpected external assets could mask dependencies: "
                f"{sorted(masking)}"
            )
    return problems
