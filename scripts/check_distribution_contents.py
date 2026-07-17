from __future__ import annotations

import argparse
import json
import tarfile
import zipfile
from pathlib import Path


WHEEL_REQUIRED = (
    "pertura_workflow/capabilities/specs/association.sceptre.v1.yaml",
    "pertura_workflow/capabilities/planner_routes.json",
    "pertura_workflow/capabilities/runners/edger_ql.R",
    "pertura_workflow/capabilities/runners/environment_worker.py",
    "pertura_workflow/capabilities/runners/gsea_prerank_runner.py",
    "pertura_workflow/capabilities/runners/ulm_runner.py",
    "pertura_workflow/environments/edger-v1.yml",
    "pertura_workflow/environments/interpretation-v1.yml",
    "pertura_workflow/environments/virtual-eval-v1.yml",
    "pertura_workflow/knowledge_resource_profiles/pathway-knowledge-v1.json",
    "pertura_workflow/knowledge_resource_profiles/regulator-knowledge-v1.json",
    "pertura_runtime/dashboard_static/index.html",
    "pertura_runtime/agent_bundle/bundle.json",
    "pertura_runtime/agent_bundle/.claude-plugin/plugin.json",
    "pertura_runtime/agent_bundle/skills/operate-pertura-workflow/SKILL.md",
    "pertura_runtime/agent_bundle/skills/inspect-perturb-seq-design/SKILL.md",
    "pertura_runtime/agent_bundle/skills/diagnose-perturb-seq-screen/SKILL.md",
    "pertura_runtime/agent_bundle/skills/interpret-perturb-seq-results/SKILL.md",
    "pertura_runtime/agent_bundle/skills/evaluate-virtual-perturb-seq-model/SKILL.md",
    "pertura_runtime/agent_bundle/skills/interpret-perturb-seq-results/references/claim-language.md",
    "pertura_runtime/agent_bundle/skills/run-replicate-aware-pseudobulk-de/SKILL.md",
    "pertura_runtime/agent_bundle/skills/run-replicate-aware-pseudobulk-de/agents/openai.yaml",
    "pertura_runtime/agent_bundle/skills/run-replicate-aware-pseudobulk-de/scripts/materialize_pseudobulk.py",
    "pertura_runtime/agent_bundle/skills/run-replicate-aware-pseudobulk-de/scripts/run_edger_ql.R",
    "pertura_runtime/agent_bundle/skills/run-replicate-aware-pseudobulk-de/scripts/run_locked.sh",
    "pertura_runtime/agent_bundle/skills/run-design-preserving-null-calibration/SKILL.md",
    "pertura_runtime/agent_bundle/skills/run-design-preserving-null-calibration/agents/openai.yaml",
    "pertura_runtime/agent_bundle/skills/run-design-preserving-null-calibration/scripts/run_paired_label_null.R",
    "pertura_runtime/agent_bundle/skills/run-design-preserving-null-calibration/scripts/run_locked.sh",
    "pertura_bench/cases/capability_cases.v1.json",
    "pertura_bench/cases/skill_cases.v1.json",
    "pertura_bench/cases/agent_workflow_cases.v1.json",
    "pertura_bench/cases/agent_workflow_verdicts.v1.json",
    "pertura_bench/cases/server_agent_cases.v1.json",
    "pertura_bench/cases/real_parameters.v1.json",
    "pertura_bench/cases/design_confirmations.v1.json",
    "pertura_bench/cases/metric_references.v1.json",
    "pertura_bench/cases/reference_generators.v1.json",
    "pertura_bench/schemas/CapabilityBenchmarkCase.schema.json",
    "pertura_bench/schemas/ProjectLifecycle.schema.json",
    "pertura_bench/schemas/AgentBenchmarkResult.schema.json",
    "pertura_bench/schemas/AgentWorkflowCase.schema.json",
    "pertura_bench/schemas/AgentWorkflowVerdict.schema.json",
    "pertura_bench/schemas/JudgeManifest.schema.json",
    "pertura_bench/runners/edger_reference.R",
    "pertura_bench/runners/propeller_reference.R",
    "pertura_bench/runners/sceptre_reference.R",
    "pertura_bench/runners/seurat_mixscape_reference.R",
    "pertura_core/compatibility/v0.2/tool-surface.json",
)

SDIST_REQUIRED = (
    "README.md",
    "pyproject.toml",
    "MANIFEST.in",
    "ui/package.json",
    "ui/package-lock.json",
    "ui/vite.config.ts",
    "benchmarks/README.md",
    "docs/README.md",
    "scripts/freeze_v020_contracts.py",
    "scripts/export_h5ad_benchmark_tables.py",
    "scripts/export_papalexi_guide_assets.R",
    *(f"src/{path}" for path in WHEEL_REQUIRED),
)

FORBIDDEN_PARTS = (
    "/node_modules/",
    "/build/",
    "/dist/",
    "/.claude_runs/",
    "/.pytest_cache/",
    "/legacy/",
    "/pertura_gate/",
)
FORBIDDEN_ACTIVE_FILES = (
    "pertura_runtime/claude/finalizer.py",
    "pertura_runtime/claude/mcp_server.py",
    "pertura_runtime/claude/tools/evidence_tools.py",
    "pertura_bench/p07_harness.py",
    "pertura_bench/p21_classic_workflow.py",
    "pertura_bench/stage_benchmark.py",
)
FORBIDDEN_REMOVED_SKILL_PREFIXES = (
    "pertura_runtime/agent_bundle/skills/execute-task-scoped-plan/",
    "pertura_runtime/agent_bundle/skills/finalize-scientific-task/",
)
FORBIDDEN_AUTHORITY_TOKENS = (
    b"Evidence" + b"Artifact",
    b"Evidence" + b"Registry",
    b"mcp__pertura_" + b"evidence__",
    b"from pertura_" + b"gate",
    b"import pertura_" + b"gate",
)
FORBIDDEN_SUFFIXES = (
    ".h5ad",
    ".h5",
    ".loom",
    ".mtx",
    ".npz",
    ".parquet",
    ".rds",
    ".rda",
    ".whl",
)


def _wheel_names(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return {name.replace("\\", "/") for name in archive.namelist()}


def _sdist_names(path: Path) -> set[str]:
    with tarfile.open(path, "r:*") as archive:
        names = {name.replace("\\", "/") for name in archive.getnames()}
    return {name.split("/", 1)[1] for name in names if "/" in name}


def _forbidden(names: set[str], *, artifact: Path) -> list[str]:
    del artifact  # the same payload policy applies to wheels and source distributions
    findings = []
    for name in sorted(names):
        framed = f"/{name.strip('/')}/"
        if any(part in framed for part in FORBIDDEN_PARTS):
            findings.append(name)
        if name.lower().endswith(FORBIDDEN_SUFFIXES):
            findings.append(name)
        if name in FORBIDDEN_ACTIVE_FILES:
            findings.append(name)
        if name.startswith(FORBIDDEN_REMOVED_SKILL_PREFIXES):
            findings.append(name)
    return sorted(set(findings))


def _authority_token_findings(path: Path, *, kind: str, names: set[str]) -> list[str]:
    findings: list[str] = []
    source_names = sorted(
        name
        for name in names
        if name.endswith(".py")
        and name.startswith(("pertura_runtime/", "pertura_workflow/", "pertura_bench/"))
    )
    if kind == "wheel":
        with zipfile.ZipFile(path) as archive:
            for name in source_names:
                payload = archive.read(name)
                if any(token in payload for token in FORBIDDEN_AUTHORITY_TOKENS):
                    findings.append(name)
    else:
        with tarfile.open(path, "r:*") as archive:
            members = {
                member.name.split("/", 1)[1]: member
                for member in archive.getmembers()
                if "/" in member.name
            }
            for name in source_names:
                extracted = archive.extractfile(members[name])
                payload = extracted.read() if extracted is not None else b""
                if any(token in payload for token in FORBIDDEN_AUTHORITY_TOKENS):
                    findings.append(name)
    return findings


def check_distribution(path: str | Path) -> dict[str, object]:
    artifact = Path(path)
    if artifact.suffix == ".whl":
        kind = "wheel"
        names = _wheel_names(artifact)
        required = WHEEL_REQUIRED
    elif artifact.name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        kind = "sdist"
        names = _sdist_names(artifact)
        required = SDIST_REQUIRED
    else:
        raise ValueError(f"unsupported distribution artifact: {artifact}")

    missing = sorted(item for item in required if item not in names)
    forbidden = _forbidden(names, artifact=artifact)
    forbidden.extend(_authority_token_findings(artifact, kind=kind, names=names))
    forbidden = sorted(set(forbidden))
    return {
        "artifact": artifact.name,
        "kind": kind,
        "passed": not missing and not forbidden,
        "missing": missing,
        "forbidden": forbidden,
        "member_count": len(names),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify Pertura wheel/sdist package data."
    )
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args(argv)
    verdicts = [check_distribution(path) for path in args.artifacts]
    print(json.dumps(verdicts, indent=2, sort_keys=True))
    return 0 if verdicts and all(item["passed"] for item in verdicts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
