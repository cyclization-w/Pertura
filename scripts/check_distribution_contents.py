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
    "pertura_workflow/environments/edger-v1.yml",
    "pertura_runtime/dashboard_static/index.html",
    "pertura_runtime/agent_bundle/bundle.json",
    "pertura_runtime/agent_bundle/.claude-plugin/plugin.json",
    "pertura_runtime/agent_bundle/skills/operate-pertura-workflow/SKILL.md",
    "pertura_runtime/agent_bundle/skills/inspect-perturb-seq-design/SKILL.md",
    "pertura_runtime/agent_bundle/skills/diagnose-perturb-seq-screen/SKILL.md",
    "pertura_runtime/agent_bundle/skills/interpret-perturb-seq-results/SKILL.md",
    "pertura_runtime/agent_bundle/skills/interpret-perturb-seq-results/references/claim-language.md",
    "pertura_bench/cases/capability_cases.v1.json",
    "pertura_bench/cases/skill_cases.v1.json",
    "pertura_bench/schemas/CapabilityBenchmarkCase.schema.json",
    "pertura_bench/runners/edger_reference.R",
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
    "docs/legacy/README.md",
    "docs/legacy/06_registrar_tool_surface.md",
    "scripts/freeze_v020_contracts.py",
    *(f"src/{path}" for path in WHEEL_REQUIRED),
)

FORBIDDEN_PARTS = (
    "/node_modules/",
    "/build/",
    "/dist/",
    "/.claude_runs/",
    "/.pytest_cache/",
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
