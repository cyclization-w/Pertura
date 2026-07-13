from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata, resources
from typing import Any, Iterable

import yaml

from pertura_core import CapabilitySpec, CapabilityTrust
from pertura_core.hashing import canonical_hash


class CapabilityRegistryError(ValueError):
    pass


def capability_scientific_hash(spec: CapabilitySpec) -> str:
    """Hash executable scientific semantics while excluding display-only phase."""

    payload = spec.model_dump(mode="json", exclude={"canonical_hash", "phase"})
    return canonical_hash(payload)

_DEPENDENCY_SCOPE_MODES = {
    "exact",
    "dataset",
    "same_dataset_context",
    "compatible",
}
_DEPENDENCY_USAGE_MODES = {
    "scientific_input",
    "row_filter",
    "validation_gate",
    "parameter_source",
    "provenance_only",
}


@dataclass(frozen=True)
class CapabilitySummary:
    capability_id: str
    version: str
    phase: int
    kind: str
    summary: str
    trust_level: str
    input_requirements: tuple[str, ...]
    implemented: bool
    validation_status: str | None = None
    deprecated: bool = False
    broker_executable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "phase": self.phase,
            "kind": self.kind,
            "summary": self.summary,
            "trust_level": self.trust_level,
            "input_requirements": list(self.input_requirements),
            "implemented": self.implemented,
            "validation_status": self.validation_status,
            "deprecated": self.deprecated,
            "broker_executable": self.broker_executable,
        }


class CapabilityRegistry:
    """Validated capability discovery without loading scientific implementations."""

    def __init__(self, specs: Iterable[CapabilitySpec] = ()) -> None:
        self._specs: dict[tuple[str, str], CapabilitySpec] = {}
        for spec in specs:
            self.register(spec)
        self.validate()

    @classmethod
    def load_default(cls, *, include_external: bool = False) -> "CapabilityRegistry":
        specs = list(_load_bundled_specs())
        if include_external:
            specs.extend(_load_entry_point_specs())
        return cls(specs)

    def register(self, spec: CapabilitySpec) -> None:
        key = (spec.capability_id, spec.version)
        if key in self._specs:
            raise CapabilityRegistryError(f"duplicate capability: {spec.capability_id}@{spec.version}")
        self._specs[key] = spec

    def get(self, capability_id: str, version: str | None = None) -> CapabilitySpec:
        candidates = [spec for (cid, _), spec in self._specs.items() if cid == capability_id]
        if version is not None:
            candidates = [spec for spec in candidates if spec.version == version]
        if not candidates:
            suffix = f"@{version}" if version else ""
            raise CapabilityRegistryError(f"unknown capability: {capability_id}{suffix}")
        if version is None:
            candidates.sort(key=lambda item: item.version)
        return candidates[-1]

    def list(
        self,
        *,
        kind: str | None = None,
        phase: int | None = None,
        include_deprecated: bool = False,
    ) -> list[CapabilitySummary]:
        specs = sorted(self._specs.values(), key=lambda item: (item.phase, item.capability_id, item.version))
        superseded_ids = {
            str(spec.metadata.get("supersedes_placeholder"))
            for spec in specs
            if spec.metadata.get("supersedes_placeholder")
        }
        result = []
        for spec in specs:
            if not include_deprecated and (
                bool(spec.metadata.get("deprecated", False))
                or spec.capability_id in superseded_ids
            ):
                continue
            if kind and spec.kind != kind:
                continue
            if phase is not None and spec.phase != phase:
                continue
            result.append(CapabilitySummary(
                capability_id=spec.capability_id,
                version=spec.version,
                phase=spec.phase,
                kind=spec.kind,
                summary=spec.summary,
                trust_level=spec.trust_level.value,
                input_requirements=spec.input_requirements,
                implemented=spec.implemented,
                validation_status=spec.metadata.get("validation_status"),
                deprecated=bool(spec.metadata.get("deprecated", False)),
                broker_executable=bool(
                    spec.implemented
                    and not spec.metadata.get("installed_external", False)
                ),
            ))
        return result

    def specs(self) -> tuple[CapabilitySpec, ...]:
        """Return immutable specs for compatibility and audit tooling."""

        return tuple(sorted(self._specs.values(), key=lambda item: (item.phase, item.capability_id, item.version)))

    def validate(self) -> None:
        from pertura_workflow.capabilities.executors import has_executor, has_validator

        by_id = {spec.capability_id: spec for spec in self._specs.values()}
        for spec in self._specs.values():
            installed_external = bool(spec.metadata.get("installed_external", False))
            if not installed_external and not has_executor(spec.executor):
                raise CapabilityRegistryError(f"missing executor {spec.executor!r} for {spec.capability_id}")
            if not installed_external and not has_validator(spec.validator):
                raise CapabilityRegistryError(f"missing validator {spec.validator!r} for {spec.capability_id}")
            for dependency in spec.depends_on:
                if dependency not in by_id:
                    if installed_external:
                        continue
                    raise CapabilityRegistryError(f"unknown dependency {dependency!r} for {spec.capability_id}")
            if not installed_external:
                _validate_dependency_policy(spec)
            if spec.trust_level != CapabilityTrust.builtin_trusted and spec.claim_permissions:
                raise CapabilityRegistryError(
                    f"untrusted capability {spec.capability_id} cannot self-authorize claim permissions"
                )
        _validate_acyclic(by_id)


def _validate_dependency_policy(spec: CapabilitySpec) -> None:
    """Validate scientific dependency semantics independently of display phase."""

    policy = spec.metadata.get("dependency_policy") or {}
    if not isinstance(policy, dict):
        raise CapabilityRegistryError(
            f"dependency_policy must be an object for {spec.capability_id}"
        )
    expected = set(spec.depends_on)
    observed = set(policy)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unknown " + ", ".join(extra))
        raise CapabilityRegistryError(
            f"dependency_policy keys do not match depends_on for {spec.capability_id}: "
            + "; ".join(details)
        )
    for dependency, rules in policy.items():
        if not isinstance(rules, dict):
            raise CapabilityRegistryError(
                f"dependency policy for {spec.capability_id} -> {dependency} must be an object"
            )
        scope = str(rules.get("scope") or "")
        usage = str(rules.get("usage") or "")
        statuses = rules.get("accepted_statuses")
        if scope not in _DEPENDENCY_SCOPE_MODES:
            raise CapabilityRegistryError(
                f"invalid dependency scope {scope!r} for {spec.capability_id} -> {dependency}"
            )
        if usage not in _DEPENDENCY_USAGE_MODES:
            raise CapabilityRegistryError(
                f"invalid dependency usage {usage!r} for {spec.capability_id} -> {dependency}"
            )
        if not isinstance(statuses, list) or not statuses or not all(
            isinstance(item, str) and item for item in statuses
        ):
            raise CapabilityRegistryError(
                f"accepted_statuses must be a non-empty string list for {spec.capability_id} -> {dependency}"
            )


def _load_bundled_specs() -> Iterable[CapabilitySpec]:
    directory = resources.files("pertura_workflow.capabilities").joinpath("specs")
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if entry.name.endswith((".yaml", ".yml")):
            payload = yaml.safe_load(entry.read_text(encoding="utf-8"))
            yield CapabilitySpec.model_validate(payload)


def _load_entry_point_specs() -> Iterable[CapabilitySpec]:
    try:
        selected = metadata.entry_points(group="pertura.capabilities")
    except TypeError:  # pragma: no cover - Python 3.10 compatibility
        selected = metadata.entry_points().get("pertura.capabilities", [])
    for point in selected:
        loaded = point.load()
        values = loaded() if callable(loaded) else loaded
        if isinstance(values, (dict, CapabilitySpec)):
            values = [values]
        for value in values:
            spec = value if isinstance(value, CapabilitySpec) else CapabilitySpec.model_validate(value)
            payload = spec.model_dump(mode="json", exclude={"canonical_hash"})
            payload["trust_level"] = CapabilityTrust.installed_untrusted.value
            payload["claim_permissions"] = []
            payload["implemented"] = False
            payload["metadata"] = dict(payload.get("metadata") or {}) | {
                "installed_external": True,
                "broker_executable": False,
                "discovery_only": True,
            }
            payload["capability_spec_id"] = ""
            yield CapabilitySpec.model_validate(payload)


def _validate_acyclic(by_id: dict[str, CapabilitySpec]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(capability_id: str) -> None:
        if capability_id in visited:
            return
        if capability_id in visiting:
            raise CapabilityRegistryError(f"capability dependency cycle at {capability_id}")
        visiting.add(capability_id)
        for dependency in by_id[capability_id].depends_on:
            if dependency in by_id:
                visit(dependency)
        visiting.remove(capability_id)
        visited.add(capability_id)

    for capability_id in by_id:
        visit(capability_id)
