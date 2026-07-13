from __future__ import annotations

import json
from pathlib import Path

from pertura_bench.agent_models import (
    AgentWorkflowCase,
    AgentWorkflowVerdict,
    JudgeManifest,
)
from pertura_bench.capability_models import (
    CapabilityBenchmarkCase,
    CapabilityBenchmarkMatrix,
    CapabilityBenchmarkMetric,
    CapabilityBenchmarkSpec,
    CapabilityBenchmarkVerdict,
    CapabilityCoverageEntry,
    ScientificResultDigest,
    ServerBenchmarkPlan,
)
from pertura_bench.models import (
    BenchmarkArtifactLock,
    BenchmarkSourceManifest,
    BenchmarkSplitManifest,
    BenchmarkSubsetLock,
    BenchmarkSubsetSpec,
    GoldenComparison,
    TargetVerdict,
    TargetVerdictSet,
)


BENCHMARK_MODELS = (
    AgentWorkflowCase,
    AgentWorkflowVerdict,
    JudgeManifest,
    BenchmarkSourceManifest,
    BenchmarkArtifactLock,
    BenchmarkSubsetSpec,
    BenchmarkSubsetLock,
    BenchmarkSplitManifest,
    TargetVerdict,
    TargetVerdictSet,
    GoldenComparison,
    CapabilityBenchmarkMetric,
    CapabilityBenchmarkCase,
    CapabilityBenchmarkSpec,
    CapabilityBenchmarkVerdict,
    CapabilityCoverageEntry,
    CapabilityBenchmarkMatrix,
    ScientificResultDigest,
    ServerBenchmarkPlan,
)


def export_benchmark_schemas(destination: str | Path, *, check: bool = False) -> list[str]:
    root = Path(destination)
    drift = []
    for model in BENCHMARK_MODELS:
        name = f"{model.__name__}.schema.json"
        rendered = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        path = root / name
        if check:
            if not path.is_file() or path.read_text(encoding="utf-8") != rendered:
                drift.append(name)
        else:
            root.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered, encoding="utf-8", newline="\n")
    return drift
