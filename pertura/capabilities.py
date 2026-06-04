"""Capability registry for public analysis graph authoring.

Capabilities are the stable action vocabulary exposed to users and LLMs.
They sit above concrete tools/templates: a node can allow ``run_de`` without
requiring the user to know whether the implementation is scanpy, pertpy, or a
workspace script.
"""

from __future__ import annotations

from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field

CapabilityKind = Literal["read", "execute", "review", "report", "external"]
CapabilityRisk = Literal["low", "medium", "high"]


class CapabilityRef(BaseModel):
    """Typed public reference to a capability id.

    Runtime specs still serialize capabilities as strings. This small wrapper is
    for developer ergonomics: users can write ``pt.caps.run_de`` instead of the
    bare string ``"run_de"`` and still get a stable JSON capability id.
    """

    capability_id: str
    title: str = ""
    description: str = ""
    stage: str = ""
    kind: CapabilityKind = "execute"
    aliases: list[str] = Field(default_factory=list)

    @property
    def id(self) -> str:
        return self.capability_id

    def __str__(self) -> str:
        return self.capability_id

    def compact(self) -> dict[str, Any]:
        return {
            "id": self.capability_id,
            "title": self.title or self.capability_id.replace("_", " "),
            "description": self.description,
            "stage": self.stage,
            "kind": self.kind,
        }

    def to_capability(self, **overrides: Any) -> "Capability":
        fields = {
            "title": self.title,
            "description": self.description,
            "stage": self.stage,
            "kind": self.kind,
            "aliases": self.aliases,
        }
        fields.update({key: value for key, value in overrides.items() if value is not None})
        return capability(self.capability_id, **fields)


class Capability(BaseModel):
    capability_id: str
    title: str = ""
    description: str = ""
    stage: str = ""
    kind: CapabilityKind = "execute"
    tool_names: list[str] = Field(default_factory=list)
    template_ids: list[str] = Field(default_factory=list)
    packages: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)
    analysis_modes: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    expected_observations: list[str] = Field(default_factory=list)
    required_inputs: list[str] = Field(default_factory=list)
    risk: CapabilityRisk = "low"
    backend: str = "kernel"
    contract: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)

    @property
    def id(self) -> str:
        return self.capability_id

    def compact(self) -> dict[str, Any]:
        return {
            "id": self.capability_id,
            "title": self.title or self.capability_id.replace("_", " "),
            "description": self.description,
            "stage": self.stage,
            "kind": self.kind,
            "tools": self.tool_names,
            "packages": self.packages,
            "functions": self.functions,
            "analysis_modes": self.analysis_modes,
            "expected_artifacts": self.expected_artifacts,
            "expected_observations": self.expected_observations,
            "required_inputs": self.required_inputs,
            "risk": self.risk,
            "backend": self.backend,
        }


class CapabilityRegistry:
    def __init__(self, capabilities: Iterable[Capability | dict] = ()):
        self._items: dict[str, Capability] = {}
        self._aliases: dict[str, str] = {}
        for item in capabilities:
            self.register(item)

    def register(self, item: Capability | dict) -> Capability:
        cap = item if isinstance(item, Capability) else _capability_from_dict(item)
        self._items[cap.capability_id] = cap
        for alias in cap.aliases:
            self._aliases[alias] = cap.capability_id
        return cap

    def get(self, capability_id: str | Capability | CapabilityRef) -> Capability | None:
        cap_id = to_capability_id(capability_id)
        resolved = self._aliases.get(cap_id, cap_id)
        return self._items.get(resolved)

    def has(self, capability_id: str | Capability | CapabilityRef) -> bool:
        return self.get(capability_id) is not None

    def ids(self) -> list[str]:
        return sorted(self._items)

    def to_list(self) -> list[dict[str, Any]]:
        return [self._items[key].model_dump(mode="json") for key in self.ids()]

    def summarize(self, capability_ids: Iterable[str] | None = None, *, limit: int = 20) -> list[dict[str, Any]]:
        ids = list(capability_ids or self.ids())
        out = []
        for cap_id in ids:
            cap = self.get(cap_id)
            if cap is None:
                out.append({"id": cap_id, "missing": True})
            else:
                out.append(cap.compact())
            if len(out) >= limit:
                break
        return out

    def missing_from_spec(self, spec: Any | None) -> list[str]:
        from pertura.spec.models import spec_from_dict

        graph = spec_from_dict(spec)
        if graph is None:
            return []
        missing = set()
        for node in graph.nodes:
            for cap_id in node.allowed_capabilities:
                if not self.has(cap_id):
                    missing.add(cap_id)
        return sorted(missing)

    @classmethod
    def from_domain(cls, domain) -> "CapabilityRegistry":
        from pertura.spec.models import spec_from_dict

        graph = spec_from_dict(getattr(domain, "analysis_graph", None))
        registry = cls(getattr(domain, "capabilities", []) or [])
        if graph is not None:
            for node in graph.nodes:
                for cap_id in node.allowed_capabilities:
                    if not registry.has(cap_id):
                        registry.register(_default_capability(cap_id, stage=node.node_id))
        return registry


def to_capability_id(value: str | Capability | CapabilityRef) -> str:
    """Return the stable string id for a capability-like object."""
    if isinstance(value, Capability):
        return value.capability_id
    if isinstance(value, CapabilityRef):
        return value.capability_id
    if hasattr(value, "capability_id"):
        return str(getattr(value, "capability_id"))
    return str(value)


def capability_ref(
    capability_id: str,
    *,
    title: str = "",
    description: str = "",
    stage: str = "",
    kind: CapabilityKind = "execute",
    aliases: list[str] | None = None,
) -> CapabilityRef:
    return CapabilityRef(
        capability_id=capability_id,
        title=title,
        description=description,
        stage=stage,
        kind=kind,
        aliases=aliases or [],
    )


def capability(
    capability_id: str | CapabilityRef,
    *,
    title: str = "",
    description: str = "",
    stage: str = "",
    kind: CapabilityKind = "execute",
    tool_names: list[str] | None = None,
    template_ids: list[str] | None = None,
    packages: list[str] | None = None,
    functions: list[str] | None = None,
    analysis_modes: list[str] | None = None,
    expected_artifacts: list[str] | None = None,
    expected_observations: list[str] | None = None,
    required_inputs: list[str] | None = None,
    risk: CapabilityRisk = "low",
    backend: str = "kernel",
    contract: dict[str, Any] | None = None,
    aliases: list[str] | None = None,
) -> Capability:
    if isinstance(capability_id, CapabilityRef):
        title = title or capability_id.title
        description = description or capability_id.description
        stage = stage or capability_id.stage
        kind = capability_id.kind if kind == "execute" else kind
        aliases = aliases or capability_id.aliases
    capability_id = to_capability_id(capability_id)
    defaults = _default_capability_defaults(capability_id, stage=stage, kind=kind)
    return Capability(
        capability_id=capability_id,
        title=title,
        description=description,
        stage=stage,
        kind=kind,
        tool_names=tool_names or defaults["tool_names"],
        template_ids=template_ids or [],
        packages=packages or defaults["packages"],
        functions=functions or defaults["functions"],
        analysis_modes=analysis_modes or defaults["analysis_modes"],
        expected_artifacts=expected_artifacts or defaults["expected_artifacts"],
        expected_observations=expected_observations or defaults["expected_observations"],
        required_inputs=required_inputs or defaults["required_inputs"],
        risk=risk,
        backend=backend,
        contract=contract or {},
        aliases=aliases or [],
    )


def build_capability_registry(domain) -> CapabilityRegistry:
    return CapabilityRegistry.from_domain(domain)


def _capability_from_dict(data: dict[str, Any]) -> Capability:
    if "capability_id" not in data and "id" in data:
        data = {**data, "capability_id": data["id"]}
    stage = data.get("stage", "")
    kind = data.get("kind", "execute")
    defaults = _default_capability_defaults(data.get("capability_id", ""), stage=stage, kind=kind)
    for key, value in defaults.items():
        if not data.get(key):
            data = {**data, key: value}
    return Capability(**data)


def _default_capability(capability_id: str, *, stage: str = "") -> Capability:
    title = capability_id.replace("_", " ")
    kind: CapabilityKind = "execute"
    tool_names = ["execute_code"]
    backend = "kernel"
    risk: CapabilityRisk = "low"
    if capability_id.startswith(("inspect", "query", "trace", "compare")) or capability_id in {
        "load_dataset", "search_web",
    }:
        kind = "read" if capability_id != "search_web" else "external"
        tool_names = [capability_id] if capability_id in {
            "load_dataset", "trace_upstream", "compare_branches", "search_web",
        } else []
    if capability_id in {"search_web"}:
        risk = "high"
    return capability(
        capability_id,
        title=title,
        description=f"Domain capability: {title}.",
        stage=stage,
        kind=kind,
        tool_names=tool_names,
        backend=backend,
        risk=risk,
    )


def _default_capability_defaults(capability_id: str, *, stage: str = "", kind: CapabilityKind = "execute") -> dict[str, list[str]]:
    stage_map: dict[str, dict[str, list[str]]] = {
        "workspace_inspection": {
            "packages": ["pathlib", "pandas", "scanpy"],
            "functions": ["Path.iterdir", "scanpy.read_h5ad", "pandas.read_csv"],
            "analysis_modes": ["workspace_inventory", "data_loading"],
            "expected_observations": ["workspace_file", "schema"],
            "expected_artifacts": [],
            "required_inputs": [],
        },
        "experimental_design": {
            "packages": ["pandas", "numpy"],
            "functions": ["DataFrame.value_counts", "DataFrame.unique"],
            "analysis_modes": ["design_audit", "control_validation"],
            "expected_observations": ["design", "control_audit"],
            "expected_artifacts": ["control_audit"],
            "required_inputs": ["adata.obs"],
        },
        "scrna_qc": {
            "packages": ["scanpy", "pandas", "numpy"],
            "functions": ["scanpy.pp.calculate_qc_metrics", "scanpy.pp.filter_cells"],
            "analysis_modes": ["qc_metrics", "filtering_review"],
            "expected_observations": ["qc_metric", "filtering_decision"],
            "expected_artifacts": ["qc_table", "figure"],
            "required_inputs": ["adata"],
        },
        "guide_assignment": {
            "packages": ["pandas", "numpy"],
            "functions": ["DataFrame.value_counts", "Series.map"],
            "analysis_modes": ["guide_assignment", "mapping_audit"],
            "expected_observations": ["guide_assignment", "guide_count"],
            "expected_artifacts": ["guide_assignment_table", "mapping_table"],
            "required_inputs": ["guide_column"],
        },
        "perturbation_validation": {
            "packages": ["scanpy", "scipy", "statsmodels"],
            "functions": ["scanpy.tl.rank_genes_groups", "scipy.stats.mannwhitneyu"],
            "analysis_modes": ["validation", "contrast_analysis"],
            "expected_observations": ["perturbation_validation", "logFC"],
            "expected_artifacts": ["validation_table"],
            "required_inputs": ["adata", "control_labels"],
        },
        "target_qc": {
            "packages": ["pandas", "numpy"],
            "functions": ["DataFrame.groupby", "DataFrame.value_counts"],
            "analysis_modes": ["target_qc", "coverage_review"],
            "expected_observations": ["target_coverage", "guide_concordance"],
            "expected_artifacts": ["coverage_table"],
            "required_inputs": ["target_column", "guide_column"],
        },
        "state_reference": {
            "packages": ["scanpy", "matplotlib", "seaborn"],
            "functions": ["scanpy.pp.pca", "scanpy.pp.neighbors", "scanpy.tl.umap", "scanpy.tl.leiden"],
            "analysis_modes": ["state_reference", "embedding", "clustering"],
            "expected_observations": ["embedding_summary", "cluster_summary"],
            "expected_artifacts": ["embedding", "cluster_table"],
            "required_inputs": ["adata"],
        },
        "effect_exploration": {
            "packages": ["scanpy", "scipy", "statsmodels"],
            "functions": ["scanpy.tl.rank_genes_groups", "scipy.stats.mannwhitneyu", "statsmodels.stats.multitest.multipletests"],
            "analysis_modes": ["effect_exploration", "differential_expression", "composition_shift"],
            "expected_observations": ["logFC", "p_value"],
            "expected_artifacts": ["de_result", "effect_figure"],
            "required_inputs": ["adata", "control_labels"],
        },
        "target_discovery": {
            "packages": ["pandas", "sklearn", "networkx"],
            "functions": ["sklearn.cluster.KMeans", "sklearn.metrics.pairwise.cosine_similarity", "networkx.from_pandas_edgelist"],
            "analysis_modes": ["target_discovery", "ranking", "network_inference"],
            "expected_observations": ["target_rank", "driver_score"],
            "expected_artifacts": ["target_ranking", "network_table"],
            "required_inputs": ["effect_table"],
        },
        "biology_story": {
            "packages": ["pertura.core.observation_memory", "pertura.core.graph", "openai"],
            "functions": ["query_observation_memory", "trace_upstream", "search_web"],
            "analysis_modes": ["story_synthesis", "provenance_audit", "literature_context"],
            "expected_observations": ["story_conclusion"],
            "expected_artifacts": [],
            "required_inputs": ["supported_observations"],
        },
        "report": {
            "packages": ["networkx", "pandas", "matplotlib"],
            "functions": ["trace_upstream", "compare_branches"],
            "analysis_modes": ["reporting", "evidence_compilation"],
            "expected_observations": [],
            "expected_artifacts": ["report"],
            "required_inputs": ["conclusions", "artifacts"],
        },
    }
    defaults = stage_map.get(stage, {
        "packages": [],
        "functions": [],
        "analysis_modes": [],
        "expected_observations": [],
        "expected_artifacts": [],
        "required_inputs": [],
    }).copy()
    capability_overrides: dict[str, dict[str, list[str]]] = {
        "load_dataset": {
            "packages": ["scanpy", "anndata", "pandas"],
            "functions": ["scanpy.read_h5ad", "scanpy.read_10x_h5", "pandas.read_csv"],
            "analysis_modes": ["data_loading", "schema_recovery"],
            "expected_observations": ["schema"],
            "expected_artifacts": [],
            "required_inputs": ["workspace_files"],
        },
        "inspect_schema": {
            "packages": ["anndata", "pandas"],
            "functions": ["AnnData.obs", "AnnData.var", "DataFrame.dtypes"],
            "analysis_modes": ["schema_audit"],
            "expected_observations": ["schema", "design"],
            "expected_artifacts": [],
            "required_inputs": ["adata"],
        },
        "run_de": {
            "packages": ["scanpy", "scipy", "statsmodels"],
            "functions": ["scanpy.tl.rank_genes_groups", "scipy.stats.mannwhitneyu", "statsmodels.stats.multitest.multipletests"],
            "analysis_modes": ["differential_expression", "effect_validation"],
            "expected_observations": ["logFC", "p_value"],
            "expected_artifacts": ["de_result"],
            "required_inputs": ["adata", "control_labels"],
        },
        "compare_methods": {
            "packages": ["pandas", "scipy", "statsmodels"],
            "functions": ["statsmodels.stats.multitest.multipletests", "scipy.stats.mannwhitneyu"],
            "analysis_modes": ["method_sensitivity"],
            "expected_observations": ["method_sensitivity"],
            "expected_artifacts": ["method_comparison"],
            "required_inputs": ["effect_result"],
        },
        "composition_test": {
            "packages": ["scanpy", "pandas"],
            "functions": ["DataFrame.groupby", "Series.value_counts"],
            "analysis_modes": ["composition_shift"],
            "expected_observations": ["composition_shift"],
            "expected_artifacts": ["composition_table"],
            "required_inputs": ["state_labels", "target_column"],
        },
        "trajectory_analysis": {
            "packages": ["scanpy", "pandas"],
            "functions": ["scanpy.tl.dpt", "scanpy.tl.diffmap"],
            "analysis_modes": ["trajectory_bias"],
            "expected_observations": ["trajectory_effect"],
            "expected_artifacts": ["trajectory_table"],
            "required_inputs": ["state_reference"],
        },
        "co_regulated_modules": {
            "packages": ["scanpy", "pandas"],
            "functions": ["scanpy.tl.rank_genes_groups", "DataFrame.corr"],
            "analysis_modes": ["module_discovery"],
            "expected_observations": ["co_regulated_module"],
            "expected_artifacts": ["module_table"],
            "required_inputs": ["effect_result"],
        },
        "rank_targets": {
            "packages": ["pandas", "sklearn"],
            "functions": ["DataFrame.sort_values", "sklearn.metrics.pairwise.cosine_similarity"],
            "analysis_modes": ["target_ranking"],
            "expected_observations": ["target_rank"],
            "expected_artifacts": ["target_ranking"],
            "required_inputs": ["effect_table"],
        },
        "query_observation_memory": {
            "packages": ["pertura.core.observation_memory"],
            "functions": ["build_observation_memory_view"],
            "analysis_modes": ["provenance_lookup"],
            "expected_observations": [],
            "expected_artifacts": [],
            "required_inputs": ["observation_memory"],
        },
        "trace_upstream": {
            "packages": ["pertura.core.graph"],
            "functions": ["trace_upstream"],
            "analysis_modes": ["provenance_trace"],
            "expected_observations": [],
            "expected_artifacts": [],
            "required_inputs": ["node_id"],
        },
        "compare_branches": {
            "packages": ["pertura.core.graph"],
            "functions": ["build_branch_view", "compare_branches"],
            "analysis_modes": ["branch_comparison"],
            "expected_observations": [],
            "expected_artifacts": [],
            "required_inputs": ["branches"],
        },
        "search_web": {
            "packages": ["openai"],
            "functions": ["responses.create"],
            "analysis_modes": ["literature_context"],
            "expected_observations": [],
            "expected_artifacts": [],
            "required_inputs": ["query"],
        },
        "generate_report": {
            "packages": ["networkx", "pandas", "matplotlib"],
            "functions": ["trace_upstream", "compare_branches"],
            "analysis_modes": ["reporting"],
            "expected_observations": [],
            "expected_artifacts": ["report"],
            "required_inputs": ["conclusions", "artifacts"],
        },
    }
    override = capability_overrides.get(capability_id)
    if override:
        defaults = override
    if kind == "read" and not defaults["analysis_modes"]:
        defaults["analysis_modes"] = [stage or "read"]
    tool_names: list[str] = []
    if kind in {"execute", "review"}:
        tool_names = ["execute_code"]
    elif capability_id in {"load_dataset", "trace_upstream", "compare_branches", "search_web"}:
        tool_names = [capability_id]
    return {**defaults, "tool_names": tool_names}


__all__ = [
    "Capability",
    "CapabilityRef",
    "CapabilityRegistry",
    "CapabilityKind",
    "CapabilityRisk",
    "capability",
    "capability_ref",
    "to_capability_id",
    "build_capability_registry",
]
