from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pertura_gate.core.schema import ScopeFit


_CONTROL_ALIASES = {
    "negctrl",
    "negativecontrol",
    "negative_control",
    "ntc",
    "non_targeting",
    "nontargeting",
    "non-targeting",
    "mock",
    "vehicle",
}

_MODALITY_TOKENS = {
    "crispr",
    "crispri",
    "crispra",
    "ko",
    "knockout",
    "perturbation",
    "guide",
    "guides",
    "targeting",
    "pooled",
    "pool",
    "control",
    "controls",
    "negative",
    "background",
    "versus",
    "vs",
    "and",
    "with",
}


@dataclass(frozen=True)
class CanonicalScope:
    dataset_id: str | None = None
    assay: str | None = None
    cell_type: str | None = None
    perturbation_id: str | None = None
    perturbation_kind: str | None = None
    control_id: str | None = None
    contrast_id: str | None = None
    estimand: str | None = None
    inferred: bool = False

    @property
    def comparable(self) -> bool:
        return any(
            [
                self.dataset_id,
                self.assay,
                self.cell_type,
                self.perturbation_id,
                self.control_id,
                self.contrast_id,
                self.estimand,
            ]
        )


def compare_canonical_scope(claim_scope: dict[str, Any], artifact_scope: dict[str, Any]) -> ScopeFit | None:
    """Conservatively compare Perturb-seq scope using canonical IDs.

    This handles common guide-label aliases before falling back to the older
    token comparison. It returns None only when there is not enough canonical
    structure to make a useful decision.
    """

    claim = canonicalize_scope(claim_scope)
    artifact = canonicalize_scope(artifact_scope)
    if not claim.comparable or not artifact.comparable:
        return None

    compared = False
    missing = False
    compatible = False
    for field in ["dataset_id", "assay", "cell_type", "estimand"]:
        left = getattr(claim, field)
        right = getattr(artifact, field)
        if not left:
            continue
        compared = True
        if not right:
            missing = True
            continue
        if left != right:
            return ScopeFit.mismatch

    if claim.perturbation_id:
        compared = True
        if not artifact.perturbation_id:
            missing = True
        elif claim.perturbation_id != artifact.perturbation_id:
            return ScopeFit.mismatch
        elif claim.perturbation_kind and artifact.perturbation_kind and claim.perturbation_kind != artifact.perturbation_kind:
            return ScopeFit.mismatch
        elif claim.inferred or artifact.inferred:
            compatible = True

    if claim.control_id:
        compared = True
        if not artifact.control_id:
            missing = True
        elif claim.control_id != artifact.control_id:
            return ScopeFit.mismatch
        elif claim.inferred or artifact.inferred:
            compatible = True

    if claim.contrast_id:
        compared = True
        if not artifact.contrast_id:
            missing = True
        elif claim.contrast_id != artifact.contrast_id:
            return ScopeFit.mismatch
        elif claim.inferred or artifact.inferred:
            compatible = True

    if not compared:
        return None
    if missing:
        return ScopeFit.weaker
    return ScopeFit.compatible if compatible else ScopeFit.exact


def canonicalize_scope(scope: dict[str, Any] | None) -> CanonicalScope:
    data = dict(scope or {})
    perturbation_raw = _first(data, "perturbation_id", "perturbation", "contrast_left", "subject", "id")
    control_raw = _first(data, "control_id", "control", "contrast_baseline", "baseline")
    contrast_raw = _first(data, "contrast_id", "contrast")

    inferred = False
    if (not perturbation_raw or not control_raw) and contrast_raw:
        left, right = _split_contrast(str(contrast_raw))
        if left and not perturbation_raw:
            perturbation_raw = left
            inferred = True
        if right and not control_raw:
            control_raw = right
            inferred = True

    perturbation_id, perturbation_kind, perturbation_inferred, inferred_control = _canonical_perturbation(perturbation_raw)
    control_id, control_inferred = _canonical_control(control_raw)
    if not control_id and inferred_control:
        control_id = inferred_control
        control_inferred = True
    contrast_id = _contrast_id(perturbation_id, control_id)
    if contrast_raw and not contrast_id:
        contrast_id = _simple_id(contrast_raw)

    return CanonicalScope(
        dataset_id=_simple_id(_first(data, "dataset_id", "dataset", "source_data")),
        assay=_simple_id(_first(data, "assay")),
        cell_type=_simple_id(_first(data, "cell_type", "cell_state")),
        perturbation_id=perturbation_id,
        perturbation_kind=perturbation_kind,
        control_id=control_id,
        contrast_id=contrast_id,
        estimand=_simple_id(_first(data, "estimand")),
        inferred=bool(inferred or perturbation_inferred or control_inferred),
    )


def _canonical_perturbation(value: Any) -> tuple[str | None, str | None, bool, str | None]:
    if value in (None, ""):
        return None, None, False, None
    if isinstance(value, (list, tuple, set)):
        values = [_canonical_gene_token(item) for item in value]
        genes = sorted({item for item in values if item})
        if not genes:
            return None, None, False, None
        kind = "combinatorial" if len(genes) > 1 else "single"
        return "_".join(genes), kind, True, None

    text = str(value)
    tokens = _scope_tokens(text)
    gene_tokens: list[str] = []
    control_seen = False
    for token in tokens:
        if _is_control_token(token):
            control_seen = True
            continue
        if token in _MODALITY_TOKENS:
            continue
        gene = _canonical_gene_token(token)
        if gene:
            gene_tokens.append(gene)
    genes = sorted(set(gene_tokens))
    if not genes:
        return None, None, False, "negctrl_pool" if control_seen else None
    kind = "combinatorial" if len(genes) > 1 else "single"
    return "_".join(genes), kind, control_seen or len(genes) > 1 or "__" in text, "negctrl_pool" if control_seen else None


def _canonical_control(value: Any) -> tuple[str | None, bool]:
    if value in (None, ""):
        return None, False
    tokens = _scope_tokens(str(value))
    if any(_is_control_token(token) for token in tokens):
        return "negctrl_pool", True
    simple = _simple_id(value)
    return simple, False


def _split_contrast(text: str) -> tuple[str | None, str | None]:
    match = re.split(r"\bvs\.?\b|_vs_|-vs-", text, flags=re.IGNORECASE, maxsplit=1)
    if len(match) == 2:
        return match[0], match[1]
    return None, None


def _contrast_id(perturbation_id: str | None, control_id: str | None) -> str | None:
    if perturbation_id and control_id:
        return f"{perturbation_id}_vs_{control_id}"
    return None


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _simple_id(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _canonical_gene_token(value: Any) -> str | None:
    token = re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())
    if not token or token.isdigit() or _is_control_token(token) or token in _MODALITY_TOKENS:
        return None
    return token.upper()


def _scope_tokens(value: str) -> list[str]:
    return [token for token in re.split(r"[^A-Za-z0-9]+", value.lower()) if token]


def _is_control_token(token: str) -> bool:
    normalized = token.lower().replace("-", "_")
    if normalized.isdigit():
        return True
    if normalized.startswith("negctrl"):
        return True
    return normalized in _CONTROL_ALIASES
