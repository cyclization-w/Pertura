from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pertura_bench.compatibility import freeze_contracts
from pertura_bench.models import (
    BenchmarkArtifactLock,
    BenchmarkSplitManifest,
    GoldenComparison,
    TargetVerdictSet,
)
from pertura_core.hashing import canonical_hash, file_sha256
from pertura_runtime.claude.options import DEFAULT_CODEACT_ALLOWED_TOOLS
from pertura_workflow.environment import doctor_environment


@dataclass(frozen=True)
class ReleaseCheck:
    check_id: str
    passed: bool
    detail: str
    category: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "passed": self.passed,
            "detail": self.detail,
            "category": self.category,
            "external": self.category != "code",
        }


def audit_v020(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    checks: list[ReleaseCheck] = []
    domain_tools = [item for item in DEFAULT_CODEACT_ALLOWED_TOOLS if item.startswith("mcp__pertura__")]
    checks.append(ReleaseCheck("default_domain_tool_count", len(domain_tools) == 5, f"observed {len(domain_tools)} tools", "code"))

    from pertura_gate.core.policy import policy_for_profile

    legacy_trusted = set(policy_for_profile("strict").trusted_runner_methods)
    unsafe = sorted({"pseudobulk", "pseudobulk_de", "exploratory_normal_approximation"} & legacy_trusted)
    checks.append(ReleaseCheck("legacy_approximation_not_trusted", not unsafe, f"unsafe trusted methods: {unsafe}", "code"))
    static = root / "src" / "pertura_runtime" / "dashboard_static"
    checks.append(ReleaseCheck(
        "dashboard_production_bundle",
        (static / "index.html").is_file() and any((static / "assets").glob("*.js")),
        str(static),
        "code",
    ))
    drift = freeze_contracts(root, check=True)
    checks.append(ReleaseCheck("v020_compatibility_freeze", not drift, f"drift: {drift}", "code"))

    from pertura_bench.capability_bench import coverage_matrix

    capability_matrix = coverage_matrix()
    checks.append(ReleaseCheck(
        "candidate_capability_code",
        capability_matrix.code_ready,
        f"{sum(item.code_ready for item in capability_matrix.entries)}/{len(capability_matrix.entries)} candidate implementations ready",
        "code",
    ))
    checks.append(ReleaseCheck(
        "candidate_local_fixtures",
        capability_matrix.local_fixture_ready,
        f"{sum(item.local_fixture_ready for item in capability_matrix.entries)}/{len(capability_matrix.entries)} local fixture protocols ready",
        "code",
    ))
    checks.append(ReleaseCheck(
        "candidate_real_benchmarks",
        capability_matrix.real_benchmark_ready,
        "real-data candidate benchmark verdicts must be generated on the server",
        "release",
    ))
    environment = doctor_environment("edger-v1")
    checks.append(ReleaseCheck(
        "edger_environment",
        bool(environment["ok"]),
        "; ".join(environment["problems"]) or str(environment["versions"]),
        "local",
    ))

    profiles, profile_problems = _validated_target_profiles(root)
    checks.append(ReleaseCheck(
        "validated_target_profiles",
        set(profiles) == {"crispri_screen_v1.yaml", "crispra_screen_v1.yaml"},
        f"validated profiles: {profiles}; problems: {profile_problems}",
        "release",
    ))

    golden_ok, golden_detail = _golden_status(root, environment)
    checks.append(ReleaseCheck("edger_golden_tolerance", golden_ok, golden_detail, "release"))
    locks, lock_problems = _frozen_lock_status(root)
    checks.append(ReleaseCheck(
        "frozen_benchmark_locks",
        len(locks) == 4 and not lock_problems,
        f"validated datasets: {sorted(locks)}; problems: {lock_problems}",
        "release",
    ))

    code_ready = all(item.passed for item in checks if item.category == "code")
    local_ready = all(item.passed for item in checks if item.category in {"code", "local"})
    release_ready = all(item.passed for item in checks)
    return {
        "schema_version": "pertura-release-audit-v2",
        "target_version": "0.2.0",
        "build_version": "0.2.0a3",
        "local_fixture_ready": capability_matrix.local_fixture_ready,
        "real_benchmark_ready": capability_matrix.real_benchmark_ready,
        "code_ready": code_ready,
        "local_environment_ready": local_ready,
        "release_ready": release_ready,
        "ready": release_ready,
        "checks": [item.to_dict() for item in checks],
        "blocking_checks": [item.check_id for item in checks if not item.passed],
    }


def _validated_target_profiles(root: Path) -> tuple[list[str], list[str]]:
    profiles = root / "src" / "pertura_workflow" / "capabilities" / "profiles"
    validated: list[str] = []
    problems: list[str] = []
    for name in ("crispri_screen_v1.yaml", "crispra_screen_v1.yaml"):
        path = profiles / name
        if not path.is_file():
            problems.append(f"missing {name}")
            continue
        try:
            profile = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not profile.get("validated") or profile.get("validation_class") != "expert_adjudicated":
                raise ValueError("profile is not expert_adjudicated")
            required_paths = {
                "verdict_set_path": profile.get("verdict_set_path"),
                "split_manifest_path": profile.get("split_manifest_path"),
                "evaluation_metrics_path": profile.get("evaluation_metrics_path"),
                "adjudication_manifest_path": profile.get("adjudication_manifest_path"),
            }
            if any(not value for value in required_paths.values()):
                raise ValueError("profile provenance paths are incomplete")
            verdicts = TargetVerdictSet.model_validate_json(_repo_file(root, required_paths["verdict_set_path"]).read_text(encoding="utf-8"))
            split = BenchmarkSplitManifest.model_validate_json(_repo_file(root, required_paths["split_manifest_path"]).read_text(encoding="utf-8"))
            metrics = json.loads(_repo_file(root, required_paths["evaluation_metrics_path"]).read_text(encoding="utf-8"))
            adjudication = json.loads(_repo_file(root, required_paths["adjudication_manifest_path"]).read_text(encoding="utf-8"))
            if verdicts.label_source != "expert_adjudicated" or not verdicts.validated or len(verdicts.verdicts) < 50:
                raise ValueError("verdict set is not a validated >=50-target expert set")
            if split.label_class != "expert_adjudicated" or verdicts.split_manifest_hash != split.canonical_hash:
                raise ValueError("verdict set is not bound to the expert split")
            if profile.get("benchmark_hash") != verdicts.canonical_hash:
                raise ValueError("profile benchmark hash mismatch")
            if profile.get("evaluation_metrics_hash") != canonical_hash(metrics):
                raise ValueError("evaluation metrics hash mismatch")
            if profile.get("adjudication_manifest_hash") != canonical_hash(adjudication):
                raise ValueError("adjudication manifest hash mismatch")
            if metrics.get("label_source") != "expert_adjudicated" or metrics.get("verdict_set_hash") != verdicts.canonical_hash:
                raise ValueError("metrics are not bound to expert verdicts")
            if float(metrics.get("macro_f1", 0)) < 0.80 or float(metrics.get("erroneous_block_rate", 1)) > 0.10:
                raise ValueError("profile evaluation metrics miss release thresholds")
            validated.append(name)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            problems.append(f"{name}: {exc}")
    return validated, problems


def _repo_file(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if root != path and root not in path.parents:
        raise ValueError("profile provenance path escapes the repository")
    if not path.is_file():
        raise ValueError(f"profile provenance file is missing: {relative}")
    return path


def _golden_status(root: Path, environment: dict[str, Any]) -> tuple[bool, str]:
    path = root / "benchmarks" / "golden" / "edger_v1_verdict.json"
    if not path.is_file():
        return False, "missing edgeR golden verdict"
    try:
        golden = GoldenComparison.model_validate_json(path.read_text(encoding="utf-8"))
        reference = root / "src" / "pertura_bench" / "runners" / "edger_reference.R"
        runner = root / "src" / "pertura_workflow" / "capabilities" / "runners" / "edger_ql.R"
        if golden.reference_script_hash != file_sha256(reference) or golden.runner_hash != file_sha256(runner):
            return False, "edgeR golden script hash drift"
        if environment.get("ok") and golden.environment_lock_hash != environment.get("lock_hash"):
            return False, "edgeR golden environment lock drift"
        maximum = max(golden.maximum_errors.values(), default=1.0)
        return golden.passed and maximum <= 1e-7, f"maximum_error={maximum}"
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return False, f"invalid edgeR golden: {exc}"


def _frozen_lock_status(root: Path) -> tuple[set[str], list[str]]:
    expected = {
        "replogle_k562_essential_2022",
        "papalexi_thp1_eccite",
        "norman_k562_crispra_2019",
        "kang18_8vs8_pbmc",
    }
    observed: set[str] = set()
    problems: list[str] = []
    for path in sorted((root / "benchmarks" / "locks").glob("*.json")):
        try:
            lock = BenchmarkArtifactLock.model_validate_json(path.read_text(encoding="utf-8"))
            if lock.dataset_id not in expected:
                raise ValueError("unexpected dataset")
            if lock.license_status != "reviewed":
                raise ValueError("license review is incomplete")
            if lock.dataset_id in observed:
                raise ValueError("duplicate frozen lock")
            observed.add(lock.dataset_id)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            problems.append(f"{path.name}: {exc}")
    return observed, problems
