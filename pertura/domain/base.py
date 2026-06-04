"""Public domain-pack contract.

`Domain` is the public package object for reusable Pertura analysis harnesses.
It bundles an AnalysisGraph, capability contracts, and review rubrics while
keeping legacy prompt fields available for older domain JSON files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Domain(BaseModel):
    """Bundle an analysis graph, capabilities, and rubrics for a domain.

    Preferred public API:

        Domain(name="my_domain").with_graph(graph).add_capability("run_de")

    Legacy fields such as `protocol`, `tools`, and `coding_guidelines` remain
    loadable so existing `.pertura/domain.json` files keep working.
    """

    name: str
    analysis_graph: dict | None = None
    capabilities: list[dict] = Field(default_factory=list)
    rubric: list[str] = Field(default_factory=list)
    critic_rubric: list[str] = Field(default_factory=list)
    condition_context: str = ""
    report_template: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Compatibility fields. These are no longer the preferred public authoring
    # surface, but runtime_context() still consumes them.
    agenda: list[str] = Field(default_factory=list)
    tools: str = ""
    validators: list[str] = Field(default_factory=list)
    protocol: str = ""
    coding_guidelines: str = ""
    audit_preamble: str = ""

    def with_graph(self, graph_or_spec) -> "Domain":
        """Attach an AnalysisGraph/AnalysisGraphSpec/dict to this domain."""
        from pertura.spec.models import spec_from_dict

        spec = graph_or_spec.to_spec() if hasattr(graph_or_spec, "to_spec") else spec_from_dict(graph_or_spec)
        if spec is None:
            raise ValueError("analysis graph cannot be empty")
        self.analysis_graph = spec.model_dump(mode="json")
        return self

    def add_capability(self, capability_id, **contract_fields) -> "Domain":
        """Register one capability contract and return this domain."""
        from pertura.capabilities import Capability, capability

        if isinstance(capability_id, Capability):
            if contract_fields:
                data = capability_id.model_dump(mode="json")
                data.update(contract_fields)
                cap = Capability(**data).model_dump(mode="json")
            else:
                cap = capability_id.model_dump(mode="json")
        else:
            cap = capability(capability_id, **contract_fields).model_dump(mode="json")
        self.capabilities.append(cap)
        return self

    def add_rubric(self, text: str, *, critic: bool = False) -> "Domain":
        """Add a human-readable rubric line used by planner/critic context."""
        if critic:
            self.critic_rubric.append(text)
        else:
            self.rubric.append(text)
        return self

    def registry(self):
        """Return the capability registry for this domain."""
        from pertura.capabilities import CapabilityRegistry

        return CapabilityRegistry.from_domain(self)

    def audit(self) -> dict:
        """Audit graph structure and capability coverage for this domain."""
        from pertura.spec.contracts import audit_analysis_graph
        from pertura.spec.models import spec_from_dict

        spec = spec_from_dict(self.analysis_graph)
        if spec is None:
            return {
                "ok": False,
                "errors": [{"code": "missing_analysis_graph", "message": "Domain has no analysis_graph."}],
                "warnings": [],
            }
        return audit_analysis_graph(spec, capabilities=self.registry())

    def describe(self, *, include_core_tools: bool = True) -> dict:
        """Return a compact browser payload for docs, CLI, GUI, and LLM context."""
        from pertura.domain.catalog import describe_domain

        return describe_domain(self, include_core_tools=include_core_tools)

    def runtime_context(self) -> dict[str, str]:
        """Return the stable prompt/runtime context consumed by the workbench.

        This is the single compatibility boundary between the new domain-pack
        API and older protocol/tools/coding_guidelines fields.
        """
        capability_lines = []
        for item in self.registry().summarize(limit=80):
            if item.get("missing"):
                continue
            outputs = ", ".join(
                [*item.get("expected_observations", []), *item.get("expected_artifacts", [])]
            )
            required = ", ".join(item.get("required_inputs", []))
            capability_lines.append(
                f"[{item.get('id')}] {item.get('description', '')}"
                + (f" Requires: {required}." if required else "")
                + (f" Expects: {outputs}." if outputs else "")
            )
        rubric_text = "\n".join(self.rubric or [])
        critic_text = "\n".join(self.critic_rubric or [])
        condition_context = self.condition_context or "\n\n".join(
            item for item in [self.protocol, rubric_text, critic_text] if item
        )
        protocol = self.protocol or "\n".join(
            item for item in [
                f"Domain: {self.name}",
                condition_context[:2000],
                "Rubric:\n" + rubric_text if rubric_text else "",
            ]
            if item
        )
        coding_guidelines = "\n\n".join(
            item for item in [
                self.coding_guidelines,
                "Domain rubric:\n" + rubric_text if rubric_text else "",
                "Critic rubric:\n" + critic_text if critic_text else "",
            ]
            if item
        )
        tools = "\n\n".join(
            item for item in [
                self.tools,
                "Capability contracts:\n" + "\n".join(capability_lines) if capability_lines else "",
            ]
            if item
        )
        return {
            "protocol": protocol,
            "coding_guidelines": coding_guidelines,
            "tools": tools,
            "audit_preamble": self.audit_preamble,
            "condition_context": condition_context,
            "report_template": self.report_template,
        }

    def to_json(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        return out

    @classmethod
    def from_json(cls, path: str | Path) -> "Domain":
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))
