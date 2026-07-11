from __future__ import annotations

import json
import re
import subprocess
import tomllib
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
from pertura_core.version import package_version
from pertura_workflow.environment import doctor_environment

EXPECTED_GITATTRIBUTES = (
    "* text=auto eol=lf",
    "*.ps1 text eol=crlf",
    "*.bat text eol=crlf",
    "*.cmd text eol=crlf",
    "*.h5ad -text",
    "*.h5 -text",
    "*.loom -text",
    "*.npz -text",
    "*.parquet -text",
    "*.png -text",
    "*.jpg -text",
    "*.jpeg -text",
    "*.gif -text",
    "*.pdf -text",
    "*.woff -text",
    "*.woff2 -text",
)

_WINDOWS_SEPARATOR = chr(92)
MACHINE_PATH_ALLOWLIST = {
    "tests/bench/test_benchmark_protocol.py": (
        "C:" + _WINDOWS_SEPARATOR + "data" + _WINDOWS_SEPARATOR + "raw.h5ad",
        "C:"
        + (_WINDOWS_SEPARATOR * 2)
        + "data"
        + (_WINDOWS_SEPARATOR * 2)
        + "raw.h5ad",
    ),
    "tests/bench/test_capability_bench_v03.py": tuple(
        "C:" + suffix
        for suffix in ("/private/fixture", "/run-a/output.json", "/run-a/data.csv")
    ),
}
MACHINE_PATH_PATTERN = re.compile(
    r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]+|(?<![A-Za-z0-9])/(?:home|Users)/[^/]+(?:/|$))"
)
TRACKED_DATA_SUFFIXES = frozenset(
    {
        ".h5ad",
        ".h5",
        ".loom",
        ".mtx",
        ".npz",
        ".parquet",
        ".rds",
        ".rda",
        ".whl",
        ".key",
        ".pem",
    }
)
TRACKED_BANNED_ROOTS = (
    "build/",
    "dist/",
    "ui/dist/",
    ".claude_runs/",
    ".pytest_cache/",
    ".tmp/",
    "benchmarks/cache/",
    "benchmarks/local/",
)


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


def _git_lines(root: Path, *arguments: str) -> tuple[int, list[str]]:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return completed.returncode, completed.stdout.splitlines()


def _clean_worktree_check(
    worktree: list[str], *, require_clean_worktree: bool
) -> ReleaseCheck:
    clean = not worktree
    enforced = require_clean_worktree
    detail = "worktree is clean" if clean else f"dirty paths: {len(worktree)}"
    if not enforced:
        detail += "; clean-worktree enforcement disabled for in-progress validation"
    return ReleaseCheck(
        "git_worktree_clean",
        clean or not enforced,
        detail,
        "repository",
    )


def _attribute_rules(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    return tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def _banned_tracked_paths(tracked: list[str]) -> list[str]:
    findings: list[str] = []
    for relative in tracked:
        normalized = relative.replace("\\", "/").lstrip("./")
        lowered = normalized.lower()
        parts = tuple(part for part in lowered.split("/") if part)
        suffix = Path(lowered).suffix
        if (
            any(lowered.startswith(prefix) for prefix in TRACKED_BANNED_ROOTS)
            or any(part in {"node_modules", "__pycache__"} for part in parts)
            or any(part.endswith(".egg-info") for part in parts)
            or suffix in TRACKED_DATA_SUFFIXES
            or lowered.endswith(".local.json")
        ):
            findings.append(relative)
    return sorted(set(findings))


def _machine_path_files(root: Path, tracked: list[str]) -> list[str]:
    text_suffixes = {
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".txt",
        ".py",
        ".R",
        ".ps1",
        ".csv",
        ".tsv",
    }
    findings: list[str] = []
    for relative in tracked:
        normalized = relative.replace("\\", "/")
        path = root / relative
        if path.suffix not in text_suffixes or not path.is_file():
            continue
        try:
            rendered = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for allowed in MACHINE_PATH_ALLOWLIST.get(normalized, ()):
            rendered = rendered.replace(allowed, "")
        if MACHINE_PATH_PATTERN.search(rendered):
            findings.append(normalized)
    return sorted(findings)


def _repository_checks(
    root: Path, *, require_clean_worktree: bool
) -> list[ReleaseCheck]:
    checks: list[ReleaseCheck] = []
    git_root_code, git_root_lines = _git_lines(root, "rev-parse", "--show-toplevel")
    observed_root = (
        Path(git_root_lines[0]).resolve()
        if git_root_code == 0 and git_root_lines
        else None
    )
    checks.append(
        ReleaseCheck(
            "authoritative_inner_repo",
            observed_root == root,
            f"observed git root: {observed_root}",
            "repository",
        )
    )

    attributes_path = root / ".gitattributes"
    observed_attributes = _attribute_rules(attributes_path)
    checks.append(
        ReleaseCheck(
            "line_ending_policy",
            attributes_path.is_file(),
            ".gitattributes must pin portable scientific text resources",
            "repository",
        )
    )
    checks.append(
        ReleaseCheck(
            "portable_attribute_rules",
            observed_attributes == EXPECTED_GITATTRIBUTES,
            (
                "rules match the frozen repository policy"
                if observed_attributes == EXPECTED_GITATTRIBUTES
                else f"expected {EXPECTED_GITATTRIBUTES}; observed {observed_attributes}"
            ),
            "repository",
        )
    )

    _, tracked = _git_lines(root, "ls-files")
    tracked_build = [
        item
        for item in tracked
        if item.replace("\\", "/").startswith(("build/", "dist/", "ui/dist/"))
    ]
    checks.append(
        ReleaseCheck(
            "generated_build_not_tracked",
            not tracked_build,
            f"tracked generated build paths: {tracked_build}",
            "repository",
        )
    )
    banned = _banned_tracked_paths(tracked)
    checks.append(
        ReleaseCheck(
            "banned_tracked_artifacts_absent",
            not banned,
            f"banned tracked paths: {banned}",
            "repository",
        )
    )
    _, worktree = _git_lines(root, "status", "--porcelain", "--untracked-files=all")
    checks.append(
        _clean_worktree_check(worktree, require_clean_worktree=require_clean_worktree)
    )
    machine_paths = _machine_path_files(root, tracked)
    checks.append(
        ReleaseCheck(
            "tracked_machine_paths_absent",
            not machine_paths,
            f"tracked files containing machine paths: {machine_paths}",
            "repository",
        )
    )
    return checks


def _package_version_check(root: Path) -> tuple[str, ReleaseCheck]:
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        source_version = str(project["project"]["version"])
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        source_version = f"invalid:{exc}"
    metadata_version = package_version()
    passed = metadata_version != "0+unknown" and metadata_version == source_version
    return metadata_version, ReleaseCheck(
        "package_version_parity",
        passed,
        f"package metadata={metadata_version}; pyproject={source_version}",
        "repository",
    )


def audit_v020(
    repo_root: str | Path, *, require_clean_worktree: bool = True
) -> dict[str, Any]:
    """Return the v3 pre-release audit (name retained for CLI compatibility)."""

    from pertura_bench.capability_bench import (
        coverage_matrix,
        server_benchmark_plan,
        validate_cases,
    )
    from pertura_bench.operations import require_repo_root
    from pertura_runtime.product_tools import PRODUCT_TOOL_NAMES

    root = require_repo_root(repo_root)
    checks = _repository_checks(root, require_clean_worktree=require_clean_worktree)
    build_version, version_check = _package_version_check(root)
    checks.append(version_check)

    domain_tools = [f"mcp__pertura__{name}" for name in PRODUCT_TOOL_NAMES]
    checks.append(
        ReleaseCheck(
            "default_domain_tool_count",
            len(domain_tools) == 5,
            f"observed {len(domain_tools)} tools",
            "runtime",
        )
    )

    from pertura_gate.core.policy import policy_for_profile

    legacy_trusted = set(policy_for_profile("strict").trusted_runner_methods)
    unsafe = sorted(
        {"pseudobulk", "pseudobulk_de", "exploratory_normal_approximation"}
        & legacy_trusted
    )
    checks.append(
        ReleaseCheck(
            "legacy_approximation_not_trusted",
            not unsafe,
            f"unsafe trusted methods: {unsafe}",
            "runtime",
        )
    )
    static = root / "src" / "pertura_runtime" / "dashboard_static"
    checks.append(
        ReleaseCheck(
            "dashboard_production_bundle",
            (static / "index.html").is_file() and any((static / "assets").glob("*.js")),
            str(static),
            "code",
        )
    )
    drift = freeze_contracts(root, check=True)
    checks.append(
        ReleaseCheck(
            "v020_compatibility_freeze",
            not drift,
            f"drift: {drift}",
            "runtime",
        )
    )

    from pertura_bench.skill_bench import skill_benchmark_matrix

    skill_state = skill_benchmark_matrix(root)
    checks.extend(
        (
            ReleaseCheck(
                "skill_bundle_ready",
                bool(skill_state["skill_bundle_ready"]),
                str(skill_state["static"]),
                "code",
            ),
            ReleaseCheck(
                "claude_skill_adapter_ready",
                bool(skill_state["claude_skill_adapter_ready"]),
                "bundled plugin, allowlist, and neutral tools are configured",
                "code",
            ),
            ReleaseCheck(
                "openai_adapter_ready",
                bool(skill_state["openai_adapter_ready"]),
                "OpenAI Agents SDK execution is intentionally not implemented",
                "agent_optional",
            ),
            ReleaseCheck(
                "skill_behavior_benchmark_ready",
                bool(skill_state["skill_behavior_benchmark_ready"]),
                str(skill_state["behavior_status"]),
                "agent_optional",
            ),
        )
    )
    case_validation = validate_cases()
    checks.append(
        ReleaseCheck(
            "candidate_case_catalog",
            bool(case_validation["ok"]) and case_validation["case_count"] == 120,
            f"{case_validation['case_count']} cases; problems: {case_validation['problems']}",
            "code",
        )
    )
    plan = server_benchmark_plan(root)
    rendered_plan = json.dumps(plan.model_dump(mode="json"), sort_keys=True)
    checks.append(
        ReleaseCheck(
            "server_plan_no_manual_placeholders",
            "<" not in rendered_plan and ">" not in rendered_plan,
            f"{len(plan.jobs)} jobs and {len(plan.artifacts)} artifacts",
            "code",
        )
    )

    capability_matrix = coverage_matrix(root)
    checks.append(
        ReleaseCheck(
            "candidate_capability_code",
            capability_matrix.code_ready,
            f"{sum(item.code_ready for item in capability_matrix.entries)}/{len(capability_matrix.entries)} candidate implementations ready",
            "code",
        )
    )
    checks.append(
        ReleaseCheck(
            "candidate_local_fixtures",
            capability_matrix.local_fixture_ready,
            f"{sum(item.local_fixture_ready for item in capability_matrix.entries)}/{len(capability_matrix.entries)} current synthetic verdict sets ready",
            "fixture",
        )
    )
    checks.append(
        ReleaseCheck(
            "candidate_real_benchmarks",
            capability_matrix.real_benchmark_ready,
            "real-data candidate benchmark verdicts must be generated on the server",
            "real",
        )
    )

    environment_profiles = (
        "edger-v1",
        "sceptre-v1",
        "composition-v1",
        "perturbseq-python-v1",
        "python-science-v1",
    )
    environments: dict[str, dict[str, Any]] = {}
    for profile in environment_profiles:
        try:
            environments[profile] = doctor_environment(profile)
        except (KeyError, ValueError) as exc:
            environments[profile] = {"ok": False, "problems": [str(exc)]}
    environment_ok = all(bool(item.get("ok")) for item in environments.values())
    checks.append(
        ReleaseCheck(
            "optional_scientific_environments",
            environment_ok,
            "; ".join(
                f"{name}={'ready' if status.get('ok') else 'missing'}"
                for name, status in environments.items()
            ),
            "environment",
        )
    )
    checks.append(
        ReleaseCheck(
            "edger_environment",
            bool(environments["edger-v1"].get("ok")),
            "; ".join(environments["edger-v1"].get("problems") or [])
            or str(environments["edger-v1"].get("versions") or {}),
            "environment",
        )
    )

    profiles, profile_problems = _validated_target_profiles(root)
    checks.append(
        ReleaseCheck(
            "validated_target_profiles",
            set(profiles) == {"crispri_screen_v1.yaml", "crispra_screen_v1.yaml"},
            f"validated profiles: {profiles}; problems: {profile_problems}",
            "release",
        )
    )
    golden_ok, golden_detail = _golden_status(root, environments["edger-v1"])
    checks.append(
        ReleaseCheck("edger_golden_tolerance", golden_ok, golden_detail, "release")
    )
    locks, lock_problems = _frozen_lock_status(root)
    checks.append(
        ReleaseCheck(
            "frozen_benchmark_locks",
            len(locks) == 4 and not lock_problems,
            f"validated datasets: {sorted(locks)}; problems: {lock_problems}",
            "real",
        )
    )

    repository_ready = all(
        item.passed for item in checks if item.category == "repository"
    )
    runtime_spine_ready = all(
        item.passed for item in checks if item.category == "runtime"
    )
    code_ready = all(item.passed for item in checks if item.category == "code")
    local_fixture_ready = all(
        item.passed for item in checks if item.category == "fixture"
    )
    optional_environment_ready = all(
        item.passed for item in checks if item.category == "environment"
    )
    real_benchmark_ready = all(
        item.passed for item in checks if item.category == "real"
    )
    release_specific_ready = all(
        item.passed for item in checks if item.category == "release"
    )
    release_ready = all(
        (
            repository_ready,
            runtime_spine_ready,
            code_ready,
            local_fixture_ready,
            optional_environment_ready,
            real_benchmark_ready,
            release_specific_ready,
        )
    )
    remaining: list[str] = []
    if not real_benchmark_ready:
        remaining.append("real-data artifact locks, subsets, and capability verdicts")
    if "crispri_screen_v1.yaml" not in profiles:
        remaining.append("expert-adjudicated CRISPRi profile")
    if "crispra_screen_v1.yaml" not in profiles:
        remaining.append("expert-adjudicated CRISPRa profile")
    if not optional_environment_ready:
        remaining.append(
            "optional scientific environments not installed on this machine"
        )
    return {
        "schema_version": "pertura-release-audit-v3",
        "target_version": "0.2.0",
        "build_version": build_version,
        "repository_ready": repository_ready,
        "runtime_spine_ready": runtime_spine_ready,
        "code_ready": code_ready,
        "local_fixture_ready": local_fixture_ready,
        "optional_environment_ready": optional_environment_ready,
        "local_environment_ready": optional_environment_ready,
        "real_benchmark_ready": real_benchmark_ready,
        "skill_bundle_ready": bool(skill_state["skill_bundle_ready"]),
        "claude_skill_adapter_ready": bool(
            skill_state["claude_skill_adapter_ready"]
        ),
        "openai_adapter_ready": bool(skill_state["openai_adapter_ready"]),
        "skill_behavior_benchmark_ready": bool(
            skill_state["skill_behavior_benchmark_ready"]
        ),
        "release_ready": release_ready,
        "ready": release_ready,
        "checks": [item.to_dict() for item in checks],
        "blocking_checks": [
            item.check_id
            for item in checks
            if not item.passed and item.category != "agent_optional"
        ],
        "remaining_blockers": remaining,
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
            if (
                not profile.get("validated")
                or profile.get("validation_class") != "expert_adjudicated"
            ):
                raise ValueError("profile is not expert_adjudicated")
            required_paths = {
                "verdict_set_path": profile.get("verdict_set_path"),
                "split_manifest_path": profile.get("split_manifest_path"),
                "evaluation_metrics_path": profile.get("evaluation_metrics_path"),
                "adjudication_manifest_path": profile.get("adjudication_manifest_path"),
            }
            if any(not value for value in required_paths.values()):
                raise ValueError("profile provenance paths are incomplete")
            verdicts = TargetVerdictSet.model_validate_json(
                _repo_file(root, required_paths["verdict_set_path"]).read_text(
                    encoding="utf-8"
                )
            )
            split = BenchmarkSplitManifest.model_validate_json(
                _repo_file(root, required_paths["split_manifest_path"]).read_text(
                    encoding="utf-8"
                )
            )
            metrics = json.loads(
                _repo_file(root, required_paths["evaluation_metrics_path"]).read_text(
                    encoding="utf-8"
                )
            )
            adjudication = json.loads(
                _repo_file(
                    root, required_paths["adjudication_manifest_path"]
                ).read_text(encoding="utf-8")
            )
            if (
                verdicts.label_source != "expert_adjudicated"
                or not verdicts.validated
                or len(verdicts.verdicts) < 50
            ):
                raise ValueError(
                    "verdict set is not a validated >=50-target expert set"
                )
            if (
                split.label_class != "expert_adjudicated"
                or verdicts.split_manifest_hash != split.canonical_hash
            ):
                raise ValueError("verdict set is not bound to the expert split")
            if profile.get("benchmark_hash") != verdicts.canonical_hash:
                raise ValueError("profile benchmark hash mismatch")
            if profile.get("evaluation_metrics_hash") != canonical_hash(metrics):
                raise ValueError("evaluation metrics hash mismatch")
            if profile.get("adjudication_manifest_hash") != canonical_hash(
                adjudication
            ):
                raise ValueError("adjudication manifest hash mismatch")
            if (
                metrics.get("label_source") != "expert_adjudicated"
                or metrics.get("verdict_set_hash") != verdicts.canonical_hash
            ):
                raise ValueError("metrics are not bound to expert verdicts")
            if (
                float(metrics.get("macro_f1", 0)) < 0.80
                or float(metrics.get("erroneous_block_rate", 1)) > 0.10
            ):
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
        runner = (
            root
            / "src"
            / "pertura_workflow"
            / "capabilities"
            / "runners"
            / "edger_ql.R"
        )
        if golden.reference_script_hash != file_sha256(
            reference
        ) or golden.runner_hash != file_sha256(runner):
            return False, "edgeR golden script hash drift"
        if environment.get("ok") and golden.environment_lock_hash != environment.get(
            "lock_hash"
        ):
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
            lock = BenchmarkArtifactLock.model_validate_json(
                path.read_text(encoding="utf-8")
            )
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
