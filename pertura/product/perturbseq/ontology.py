"""Perturb-seq product vocabulary and lightweight schema heuristics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DesignFieldSpec:
    field_id: str
    label: str
    question: str
    aliases: tuple[str, ...] = ()
    required_before_interpretation: bool = True


DESIGN_FIELDS: tuple[DesignFieldSpec, ...] = (
    DesignFieldSpec(
        "dataset_path",
        "Dataset",
        "Which AnnData or count matrix should Pertura analyze?",
        ("adata", "h5ad", "matrix", "data_path"),
        False,
    ),
    DesignFieldSpec(
        "control_labels",
        "Controls",
        "Which labels are non-targeting, safe-targeting, mock, or untreated controls?",
        ("control", "controls", "negative_control", "ntc", "non_targeting"),
    ),
    DesignFieldSpec(
        "guide_column",
        "Guide column",
        "Which obs column stores guide or sgRNA assignments?",
        ("guide", "sgRNA", "grna", "guide_id", "guide_assignment"),
    ),
    DesignFieldSpec(
        "target_column",
        "Target column",
        "Which obs column stores target gene or perturbation labels?",
        ("target", "gene_target", "perturbation", "gene"),
    ),
    DesignFieldSpec(
        "batch_column",
        "Batch",
        "Which column should be treated as batch, donor, sample, or replicate?",
        ("batch", "sample", "donor", "replicate", "library"),
        False,
    ),
    DesignFieldSpec(
        "contrast",
        "Contrast",
        "Which target/control contrast should be analyzed first?",
        ("comparison", "condition", "case_control", "contrast_definition"),
    ),
    DesignFieldSpec(
        "perturbation_modality",
        "Modality",
        "What perturbation modality is this screen using?",
        ("crispr", "crispri", "crispra", "ko", "knockout", "drug", "overexpression"),
    ),
    DesignFieldSpec(
        "moi",
        "MOI / loading",
        "What MOI or guide loading assumption should guide assignment use?",
        ("multiplicity", "loading", "single_guide", "multi_guide"),
        False,
    ),
    DesignFieldSpec(
        "state_column",
        "Cell state",
        "Which column defines cell type, state, cluster, or trajectory context?",
        ("cell_type", "celltype", "state", "cluster", "leiden", "annotation"),
        False,
    ),
)

FIELD_BY_ID = {item.field_id: item for item in DESIGN_FIELDS}
FIELD_ORDER = [item.field_id for item in DESIGN_FIELDS]


def normalize_design_key(key: str) -> str:
    text = str(key or "").strip().lower()
    if text in FIELD_BY_ID:
        return text
    for spec in DESIGN_FIELDS:
        candidates = {spec.field_id, *spec.aliases}
        if text in {item.lower() for item in candidates}:
            return spec.field_id
    return text


def source_label(value: Any) -> str:
    if not value:
        return "unknown"
    text = str(value)
    aliases = {
        "api_confirmed": "user_confirmed",
        "pi_confirmed": "user_confirmed",
        "user": "user_confirmed",
        "llm_inferred": "llm_hypothesis",
        "data": "data_observed",
    }
    return aliases.get(text, text)


def confidence_label(value: Any, *, has_value: bool = False) -> str:
    text = str(value or "").strip().lower()
    if text in {"high", "medium", "low"}:
        return text
    return "medium" if has_value else "missing"


def candidate_columns_from_text(text: str) -> dict[str, list[str]]:
    """Return field -> candidate column names from a schema-like text payload."""
    out = {field_id: [] for field_id in FIELD_ORDER}
    tokens = _tokenize_schema_text(text)
    for token in tokens:
        low = token.lower()
        for spec in DESIGN_FIELDS:
            if spec.field_id == "dataset_path":
                continue
            if any(alias.lower() in low for alias in spec.aliases):
                out[spec.field_id].append(token)
    return {key: _dedupe(vals)[:8] for key, vals in out.items() if vals}


def _tokenize_schema_text(text: str) -> list[str]:
    for char in "[]{}(),;:\n\t":
        text = text.replace(char, " ")
    return [
        item.strip("'\"` ")
        for item in text.split(" ")
        if 1 < len(item.strip("'\"` ")) < 80
    ]


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
