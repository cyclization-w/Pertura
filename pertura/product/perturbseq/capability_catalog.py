"""Perturb-seq capability cards and turn-card rendering."""

from __future__ import annotations

from typing import Any

from pertura.models import Snapshot

from .ontology import FIELD_BY_ID
from .sweeps import BRANCHABLE_BY_CAPABILITY


COMMON_ERRORS = {
    "load_dataset": ["AnnData path not found", "matrix and metadata dimensions differ"],
    "run_qc": ["QC metric column already exists", "sparse matrix converted unexpectedly"],
    "assign_guides": ["guide column absent", "multi-guide cells not handled"],
    "run_de": ["control labels not resolved", "target column missing", "Scanpy groupby mismatch"],
    "validate_perturbation": ["target gene not present in var names", "contrast is underpowered"],
}

REQUIRED_DESIGN_FIELDS = {
    "audit_controls": ["control_labels"],
    "audit_experimental_design": ["control_labels", "perturbation_modality"],
    "audit_guide_capture": ["guide_column"],
    "audit_moi_loading": ["moi"],
    "assign_guides": ["guide_column"],
    "audit_guide_counts": ["guide_column"],
    "compare_thresholds": ["guide_column"],
    "audit_guide_mapping": ["guide_column", "target_column"],
    "check_target_coverage": ["control_labels", "target_column"],
    "check_guide_concordance": ["guide_column", "target_column"],
    "aggregate_target": ["target_column"],
    "validate_perturbation": ["control_labels", "guide_column", "target_column"],
    "run_de": ["control_labels", "target_column"],
    "score_signature": ["control_labels", "target_column"],
    "compare_methods": ["control_labels", "target_column"],
    "report_assembly": ["control_labels", "contrast"],
}

REPAIR_HINTS = {
    "load_dataset": ["Use the dataset path from the design ledger before custom loading code."],
    "assign_guides": ["Confirm guide_column and multi-guide policy before interpreting assignments."],
    "run_de": ["Check control_labels, target_column, and groupby values before retrying."],
    "run_qc": ["Register n_cells/n_genes and filtering_decision observations."],
}

BIOLOGICAL_QUESTIONS = {
    "inspect_workspace": "What input files and prior analysis material are available?",
    "load_dataset": "Which matrix-level dataset should become the active AnnData object?",
    "inspect_schema": "Which obs/var fields encode perturb-seq design?",
    "audit_controls": "Which cells are valid controls for target-level interpretation?",
    "run_qc": "Is the matrix suitable for perturbation-specific analysis?",
    "assign_guides": "How should guides and targets be assigned to cells?",
    "check_target_coverage": "Which targets have enough cells and guides for interpretation?",
    "validate_perturbation": "Do perturbations show expected direction or signature evidence?",
    "run_de": "What target effects are supported under the selected contrast?",
    "compare_methods": "Are target effects stable across methods or parameters?",
    "report_assembly": "Which claims, figures, limitations, and trace links belong in the report?",
}


def compile_capability_catalog(
    snap: Snapshot | None,
    design_ledger: dict[str, Any] | None = None,
    *,
    active_node_id: str = "",
) -> dict[str, Any]:
    if snap is None:
        return {
            "cards": [],
            "ready_capabilities": [],
            "blocked_capabilities": [],
            "selected_capability": {},
            "hidden_tool_ids": [],
        }

    ledger = design_ledger or {}
    active_node = _active_node(snap, active_node_id or getattr(snap, "active_node_id", ""))
    allowed = list(active_node.get("allowed_capabilities") or [])
    if not allowed:
        allowed = [cap.get("capability_id") or cap.get("id") for cap in getattr(snap, "capabilities", []) or []]
    raw_by_id = _raw_capabilities_by_id(snap)
    cards = [
        _card_for_capability(cap_id, raw_by_id.get(cap_id, {}), ledger, active_node)
        for cap_id in allowed
        if cap_id
    ]
    ready = [item for item in cards if item.get("ready")]
    blocked = [item for item in cards if not item.get("ready")]
    selected = ready[0] if ready else (blocked[0] if blocked else {})
    hidden = _hidden_tool_ids(snap, ledger, cards)
    return {
        "cards": cards,
        "ready_capabilities": ready,
        "blocked_capabilities": blocked,
        "selected_capability": selected,
        "hidden_tool_ids": hidden,
    }


def render_turn_card(view: dict[str, Any], *, outcome_text: str = "", last_attempt_delta: dict[str, Any] | None = None) -> str:
    ledger = view.get("design_ledger") or {}
    active = view.get("active_stage") or {}
    catalog = view.get("capability_catalog") or {}
    quality_flags = view.get("quality_flags") or []
    selected = catalog.get("selected_capability") or {}
    navigation = view.get("navigation") or {}
    lines = [
        "# Perturb-seq Turn Card",
        "",
        f"Goal: {_line(view.get('goal') or 'No goal recorded')}",
        f"Stage: {active.get('node_id') or 'none'} - {_line(active.get('title') or '')}",
        f"Stage purpose: {_line(active.get('purpose') or '')}",
        f"Navigation: {navigation.get('status', 'stay')} - {_line(navigation.get('reason') or '')}",
        "",
        "## Design Ledger",
    ]
    for field in (ledger.get("fields") or [])[:10]:
        value = field.get("value")
        shown = value if value not in (None, "", []) else field.get("status")
        lines.append(f"- {field.get('field_id')}: {shown} ({field.get('source')}, {field.get('confidence')})")
    questions = ledger.get("suggested_questions") or []
    if questions:
        lines.extend(["", "## Design Questions"])
        for question in questions[:4]:
            lines.append(f"- {question.get('field_id')}: {_line(question.get('question'))}")
    lines.extend(["", "## Selected Capability"])
    if selected:
        lines.append(f"- id: {selected.get('id')}")
        lines.append(f"- question: {_line(selected.get('biological_question') or '')}")
        lines.append(f"- ready: {bool(selected.get('ready'))}")
        if selected.get("missing"):
            lines.append(f"- missing: {', '.join(str(item) for item in selected.get('missing', [])[:8])}")
        if selected.get("prechecks"):
            lines.append(f"- prechecks: {', '.join(str(item) for item in selected.get('prechecks', [])[:6])}")
        if selected.get("expected_observations"):
            lines.append(f"- expected observations: {', '.join(str(item) for item in selected.get('expected_observations', [])[:6])}")
        if selected.get("expected_artifacts"):
            lines.append(f"- expected artifacts: {', '.join(str(item) for item in selected.get('expected_artifacts', [])[:6])}")
        for item in (selected.get("common_errors") or [])[:3]:
            lines.append(f"- common error: {_line(item)}")
        for item in (selected.get("repair_hints") or [])[:3]:
            lines.append(f"- repair hint: {_line(item)}")
    else:
        lines.append("- none")
    if quality_flags:
        lines.extend(["", "## Quality Flags"])
        for flag in quality_flags[:5]:
            lines.append(f"- {flag.get('severity')}: {_line(flag.get('label'))}")
    if outcome_text:
        lines.extend(["", "## Previous Outcome", _line(outcome_text)])
    delta = last_attempt_delta or {}
    if delta:
        lines.extend(["", "## Last Attempt Delta"])
        if delta.get("attempt_id"):
            lines.append(f"- attempt: {delta.get('attempt_id')} ({delta.get('status')})")
        if delta.get("observations_registered") is not None:
            lines.append(f"- observations registered: {delta.get('observations_registered')}")
    lines.extend(["", "## Next Action Policy"])
    guidance = view.get("node_execution_guidance") or {}
    if guidance.get("primary_instruction"):
        lines.append(f"- {guidance.get('primary_instruction')}")
        for item in (guidance.get("avoid_actions") or [])[:4]:
            lines.append(f"- avoid: {_line(item)}")
    elif navigation.get("status") == "advance":
        lines.append(f"- Complete the current stage or request transition to {navigation.get('target_node_id')}. Do not repeat dataset inspection.")
    else:
        lines.append("- Choose one ready capability and register observations/artifacts.")
    return "\n".join(lines).strip()


def _card_for_capability(cap_id: str, raw: dict[str, Any], ledger: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    product = _product_contract(raw)
    required = list(
        product.get("required_design_fields")
        or product.get("required_inputs")
        or raw.get("required_inputs")
        or REQUIRED_DESIGN_FIELDS.get(cap_id, [])
    )
    required_design = [item for item in required if item in FIELD_BY_ID]
    required_materials = [item for item in required if item not in FIELD_BY_ID]
    missing_design = _missing_design_fields(ledger, required_design)
    missing_materials = _missing_materials(ledger, required_materials)
    missing = missing_design + missing_materials
    ready = not missing
    return {
        "id": cap_id,
        "title": raw.get("title") or cap_id.replace("_", " "),
        "description": raw.get("description") or "",
        "stage": raw.get("stage") or node.get("node_id", ""),
        "biological_question": product.get("biological_question") or BIOLOGICAL_QUESTIONS.get(cap_id, raw.get("description") or cap_id.replace("_", " ")),
        "required_design_fields": required_design,
        "required_materials": required_materials,
        "missing": missing,
        "missing_inputs": missing,
        "ready": ready,
        "prechecks": list(product.get("prechecks") or _prechecks(cap_id)),
        "method_defaults": product.get("method_defaults") or _method_defaults(cap_id),
        "expected_observations": list(product.get("expected_observations") or raw.get("expected_observations") or []),
        "expected_artifacts": list(product.get("expected_artifacts") or raw.get("expected_artifacts") or _expected_artifacts(cap_id)),
        "expected_plots": list(product.get("expected_plots") or _expected_plots(cap_id)),
        "common_errors": list(product.get("common_errors") or COMMON_ERRORS.get(cap_id, [])),
        "repair_hints": list(product.get("repair_hints") or REPAIR_HINTS.get(cap_id, [])),
        "risk_level": raw.get("risk") or "low",
        "branchable_parameters": list(product.get("branchable_parameters") or BRANCHABLE_BY_CAPABILITY.get(cap_id, [])),
        "packages": list(raw.get("packages") or []),
        "functions": list(raw.get("functions") or []),
        "tool_names": list(raw.get("tool_names") or []),
        "next_repair": _next_repair(missing),
    }


def _product_contract(raw: dict[str, Any]) -> dict[str, Any]:
    contract = raw.get("contract") or {}
    if not isinstance(contract, dict):
        return {}
    product = contract.get("product") or contract.get("perturbseq") or {}
    return product if isinstance(product, dict) else {}


def _raw_capabilities_by_id(snap: Snapshot) -> dict[str, dict[str, Any]]:
    out = {}
    for item in getattr(snap, "capabilities", []) or []:
        cap_id = item.get("capability_id") or item.get("id")
        if cap_id:
            out[cap_id] = item
    return out


def _active_node(snap: Snapshot, node_id: str) -> dict[str, Any]:
    for item in (getattr(snap, "analysis_spec", {}) or {}).get("nodes", []) or []:
        if item.get("node_id") == node_id:
            return item
    return {}


def _missing_design_fields(ledger: dict[str, Any], required: list[str]) -> list[str]:
    known = {
        item.get("field_id") for item in (ledger.get("fields") or [])
        if item.get("status") == "known"
    }
    return [item for item in required if item not in known]


def _missing_materials(ledger: dict[str, Any], required: list[str]) -> list[str]:
    dataset_loaded = bool((ledger.get("dataset_profile") or {}).get("loaded"))
    missing = []
    for item in required:
        if item in {"adata", "dataset", "workspace_files"} and not dataset_loaded:
            missing.append(item)
    return missing


def _hidden_tool_ids(snap: Snapshot, ledger: dict[str, Any], cards: list[dict[str, Any]]) -> list[str]:
    dataset_loaded = bool((ledger.get("dataset_profile") or {}).get("loaded"))
    if not dataset_loaded:
        return []
    hidden = ["inspect_workspace", "load_dataset"]
    active_node = getattr(snap, "active_node_id", "")
    if active_node not in {"workspace_inspection", ""}:
        hidden.extend(["list_analysis_nodes"])
    return hidden


def _prechecks(cap_id: str) -> list[str]:
    return {
        "run_de": ["controls confirmed", "target column confirmed", "coverage checked"],
        "assign_guides": ["guide column candidate reviewed", "MOI/loading assumption recorded"],
        "check_target_coverage": ["guide assignment recorded", "control labels confirmed"],
        "run_qc": ["dataset loaded", "obs/var schema summarized"],
    }.get(cap_id, [])


def _method_defaults(cap_id: str) -> dict[str, Any]:
    return {
        "run_de": {"method": "wilcoxon", "correction": "benjamini-hochberg"},
        "check_target_coverage": {"min_cells_per_target": 30},
        "assign_guides": {"multi_guide_policy": "record_and_flag"},
    }.get(cap_id, {})


def _expected_plots(cap_id: str) -> list[str]:
    return {
        "run_qc": ["qc_histograms", "mitochondrial_fraction"],
        "run_de": ["volcano_or_ranked_effects"],
        "check_target_coverage": ["coverage_distribution"],
        "assign_guides": ["guide_count_distribution"],
    }.get(cap_id, [])


def _expected_artifacts(cap_id: str) -> list[str]:
    return {
        "run_qc": ["qc_figure", "filtered_dataset_checkpoint"],
        "assign_guides": ["guide_assignment_table"],
        "check_target_coverage": ["target_coverage_table"],
        "run_de": ["differential_expression_table"],
        "compare_methods": ["branch_comparison_table"],
        "report_assembly": ["report"],
    }.get(cap_id, [])


def _next_repair(missing: list[str]) -> str:
    if not missing:
        return ""
    design = [item for item in missing if item in FIELD_BY_ID]
    if design:
        return "confirm design: " + ", ".join(design)
    return "materialize inputs: " + ", ".join(missing)


def _line(value: Any) -> str:
    return " ".join(str(value or "").split())
