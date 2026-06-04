"""Deterministic checks for whether attempts satisfied capability outputs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def capability_output_gaps(capability, observations, artifacts) -> dict[str, list[str]]:
    """Return expected observations/artifacts not registered by an attempt."""
    missing_observations = [
        expected for expected in list(getattr(capability, "expected_observations", []) or [])
        if not matches_observation_contract(expected, observations)
    ]
    missing_artifacts = [
        expected for expected in list(getattr(capability, "expected_artifacts", []) or [])
        if not matches_artifact_contract(expected, artifacts)
    ]
    return {
        "missing_observations": missing_observations,
        "missing_artifacts": missing_artifacts,
    }


def matches_observation_contract(expected: str, observations) -> bool:
    fields = []
    for obs in observations:
        fields.append([
            getattr(obs, "type", ""),
            getattr(obs, "metric", ""),
            getattr(obs, "target", ""),
            getattr(obs, "variable_key", ""),
        ])
    return any(_matches_expected(expected, item) for item in fields)


def matches_artifact_contract(expected: str, artifacts) -> bool:
    fields = []
    for artifact in artifacts:
        metadata = getattr(artifact, "metadata", {}) or {}
        fields.append([
            getattr(artifact, "kind", ""),
            getattr(artifact, "summary", ""),
            getattr(artifact, "path", ""),
            Path(str(getattr(artifact, "path", ""))).name,
            metadata.get("kind", ""),
            metadata.get("artifact_type", ""),
            metadata.get("output_type", ""),
            metadata.get("capability_output", ""),
            metadata.get("tags", []),
        ])
    return any(_matches_expected(expected, item) for item in fields)


def _matches_expected(expected: str, fields: list[Any]) -> bool:
    expected_n = _normalize(expected)
    if not expected_n:
        return False
    normalized_fields = [_normalize(field) for field in fields if field not in (None, "", [], {})]
    if any(expected_n == field or expected_n in field for field in normalized_fields):
        return True
    blob = " ".join(normalized_fields)
    tokens = [token for token in expected_n.split(" ") if token]
    return bool(tokens) and all(token in blob for token in tokens)


def _normalize(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        value = " ".join(str(item) for item in value)
    text = str(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()
