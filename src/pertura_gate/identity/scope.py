from __future__ import annotations

import re
from typing import Any

from pertura_gate.identity.design_manifest import compare_manifest_scope
from pertura_gate.identity.canonical_scope import compare_canonical_scope
from pertura_gate.core.schema import ScopeFit


_SCOPE_ALIASES = {
    "perturbation": ("perturbation", "contrast_left", "subject"),
    "control": ("control", "contrast_baseline", "baseline"),
    "contrast": ("contrast",),
    "dataset_id": ("dataset_id", "dataset", "source_data"),
    "cell_type": ("cell_type", "cell_state"),
    "assay": ("assay",),
    "organism": ("organism",),
    "target": ("target", "gene", "term_id", "pathway"),
}


_COMPATIBLE_TOKEN_KEYS = {"perturbation", "control", "contrast", "target"}
_UNKNOWN_VALUES = {"unknown", "not observed", "not observed in local files", "not available", "na", "n/a"}


def compare_scope(claim_scope: dict[str, Any], artifact_scope: dict[str, Any]) -> ScopeFit:
    """Compare claim constraints against artifact scope without trusting model labels."""

    manifest_fit = compare_manifest_scope(claim_scope, artifact_scope)
    if manifest_fit is not None:
        return manifest_fit

    canonical_fit = compare_canonical_scope(claim_scope, artifact_scope)
    if canonical_fit is not None:
        return canonical_fit

    claim_norm = _normalize_scope(claim_scope)
    artifact_norm = _normalize_scope(artifact_scope)
    if not claim_norm or not artifact_norm:
        return ScopeFit.unknown

    missing = False
    matched = False
    compatible = False
    for key, claim_value in claim_norm.items():
        if claim_value in (None, "") or _unknown_like(claim_value):
            continue
        artifact_value = artifact_norm.get(key)
        if artifact_value in (None, "") or _unknown_like(artifact_value):
            missing = True
            continue
        matched = True
        claim_normalized = _normalize_value(claim_value)
        artifact_normalized = _normalize_value(artifact_value)
        if claim_normalized == artifact_normalized:
            continue
        if _compatible_scope_value(key, claim_value, artifact_value):
            compatible = True
            continue
        return ScopeFit.mismatch

    if missing:
        return ScopeFit.weaker if matched else ScopeFit.unknown
    if compatible:
        return ScopeFit.compatible
    return ScopeFit.exact if matched else ScopeFit.unknown


def compatible_or_exact(scope_fit: ScopeFit) -> bool:
    return scope_fit in {ScopeFit.exact, ScopeFit.compatible, ScopeFit.weaker, ScopeFit.unknown}


def _normalize_scope(scope: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for canonical, aliases in _SCOPE_ALIASES.items():
        for alias in aliases:
            if alias in scope and scope[alias] not in (None, "") and not _unknown_like(scope[alias]):
                normalized[canonical] = scope[alias]
                break
    for key, value in scope.items():
        if not _unknown_like(value):
            normalized.setdefault(str(key), value)
    return normalized


def _normalize_value(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return "|".join(sorted(_normalize_value(item) for item in value))
    return str(value).strip().lower().replace(" ", "_")


def _compatible_scope_value(key: str, claim_value: Any, artifact_value: Any) -> bool:
    """Allow conservative alias-like matches for biological scope strings."""

    if key not in _COMPATIBLE_TOKEN_KEYS:
        return False
    claim_tokens = _scope_tokens(claim_value)
    artifact_tokens = _scope_tokens(artifact_value)
    if not claim_tokens or not artifact_tokens:
        return False
    if claim_tokens == artifact_tokens:
        return True
    if not claim_tokens.issubset(artifact_tokens):
        return False
    extra_tokens = artifact_tokens - claim_tokens
    if key == "perturbation":
        # A single-gene claim may match a target+control label such as
        # KLF1_NegCtrl0, but it must not match a true combinatorial label
        # such as CEBPE_RUNX1T1.
        return bool(extra_tokens) and all(_is_control_token(token) for token in extra_tokens)
    return True


def _scope_tokens(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        tokens: set[str] = set()
        for item in value:
            tokens.update(_scope_tokens(item))
        return tokens
    text = str(value).lower()
    raw_tokens = re.split(r"[^a-z0-9]+", text)
    ignored = {
        "perturbation",
        "crispri",
        "crispr",
        "crispra",
        "ko",
        "guide",
        "guides",
        "targeting",
        "non",
        "pooled",
        "pool",
        "control",
        "controls",
        "negative",
        "background",
        "vs",
        "and",
        "with",
    }
    return {token for token in raw_tokens if token and token not in ignored}


def _is_control_token(token: str) -> bool:
    return token.isdigit() or token.startswith("negctrl") or token in {"ntc", "mock", "vehicle"}


def _unknown_like(value: Any) -> bool:
    if value in (None, ""):
        return True
    text = str(value).strip().lower()
    return text in _UNKNOWN_VALUES


