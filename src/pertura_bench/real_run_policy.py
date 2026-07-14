from __future__ import annotations

import json
from importlib import resources
from typing import Any

from pertura_core.hashing import canonical_hash


_RESOURCE = "real_run_policy.v1.json"
_ALLOWED_VARIANTS = {
    ("frozen_subset", "calibration"),
    ("frozen_subset", "evaluation"),
    ("full_dataset", "evaluation"),
}


def load_real_run_policy() -> tuple[dict[str, Any], str]:
    resource = resources.files("pertura_bench").joinpath("cases", _RESOURCE)
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "pertura-real-run-policy-v1":
        raise ValueError("unsupported real run policy schema")
    named_sets = (
        "secondary_capabilities",
        "excluded_capabilities",
        "calibration_capabilities",
        "full_dataset_capabilities",
    )
    values: dict[str, set[str]] = {}
    for name in named_sets:
        raw = payload.get(name)
        if not isinstance(raw, list) or len(raw) != len(set(raw)):
            raise ValueError(f"real run policy {name} must be a unique list")
        values[name] = {str(item) for item in raw}
    if values["secondary_capabilities"] & values["excluded_capabilities"]:
        raise ValueError("excluded real capabilities cannot also be secondary")
    return payload, canonical_hash(payload)


def validate_real_run_policy(specs: Any) -> str:
    policy, digest = load_real_run_policy()
    known = {str(spec.capability_id) for spec in specs}
    configured = {
        name: set(policy[name])
        for name in (
            "secondary_capabilities",
            "excluded_capabilities",
            "calibration_capabilities",
            "full_dataset_capabilities",
        )
    }
    unknown = sorted(set().union(*configured.values()) - known)
    if unknown:
        raise ValueError(
            "real run policy references unknown benchmark capabilities: "
            + ", ".join(unknown)
        )
    excluded = configured["excluded_capabilities"]
    conflicting = sorted(
        excluded
        & (
            configured["calibration_capabilities"]
            | configured["full_dataset_capabilities"]
        )
    )
    if conflicting:
        raise ValueError(
            "excluded capabilities cannot have real run variants: "
            + ", ".join(conflicting)
        )
    return digest

def real_runs_for_spec(spec: Any) -> tuple[dict[str, str], ...]:
    policy, _ = load_real_run_policy()
    capability_id = str(spec.capability_id)
    excluded = set(policy["excluded_capabilities"])
    if capability_id in excluded:
        return ()
    track = (
        "secondary"
        if capability_id in set(policy["secondary_capabilities"])
        else "primary"
    )
    runs: list[dict[str, str]] = []
    for dataset_id in spec.required_real_datasets:
        runs.append(
            {
                "dataset_id": str(dataset_id),
                "tier": "frozen_subset",
                "split": "evaluation",
                "track": track,
            }
        )
        if capability_id in set(policy["calibration_capabilities"]):
            runs.append(
                {
                    "dataset_id": str(dataset_id),
                    "tier": "frozen_subset",
                    "split": "calibration",
                    "track": track,
                }
            )
        if capability_id in set(policy["full_dataset_capabilities"]):
            runs.append(
                {
                    "dataset_id": str(dataset_id),
                    "tier": "full_dataset",
                    "split": "evaluation",
                    "track": track,
                }
            )
    identities = {
        (item["dataset_id"], item["tier"], item["split"]) for item in runs
    }
    if len(identities) != len(runs):
        raise ValueError(f"real run policy duplicates {capability_id}")
    if any((item["tier"], item["split"]) not in _ALLOWED_VARIANTS for item in runs):
        raise ValueError(f"real run policy contains an invalid variant for {capability_id}")
    return tuple(runs)