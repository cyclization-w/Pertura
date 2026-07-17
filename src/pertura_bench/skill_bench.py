from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from pertura_core.hashing import canonical_hash
from pertura_runtime.agent_bundle import BUNDLED_SKILL_NAMES
from pertura_runtime.agent_bundle.bundle import bundled_skill_manifest


CASE_SCHEMA_VERSION = "pertura-skill-case-catalog-v1"
_FORBIDDEN_PROVIDER_TOKENS = (
    "claude",
    "openai",
    "anthropic",
    "mcp__",
    "claudeagentoptions",
    "responses api",
)
_REFERENCE = re.compile(r"\[[^\]]+\]\((references/[^)]+)\)")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def skill_case_path() -> Path:
    return Path(__file__).resolve().parent / "cases" / "skill_cases.v1.json"


def load_skill_cases() -> dict[str, Any]:
    return json.loads(skill_case_path().read_text(encoding="utf-8"))


def validate_skill_cases() -> dict[str, Any]:
    payload = load_skill_cases()
    problems: list[str] = []
    cases = list(payload.get("cases") or [])
    if payload.get("schema_version") != CASE_SCHEMA_VERSION:
        problems.append("skill case schema version mismatch")
    if len(cases) != 37:
        problems.append(f"expected 37 cases, observed {len(cases)}")
    ids = [str(item.get("case_id") or "") for item in cases]
    if len(ids) != len(set(ids)):
        problems.append("duplicate skill case IDs")
    counts = {
        kind: sum(item.get("kind") == kind for item in cases)
        for kind in ("single_positive", "multi_positive", "negative")
    }
    if counts != {
        "single_positive": 17,
        "multi_positive": 9,
        "negative": 11,
    }:
        problems.append(f"unexpected skill case mix: {counts}")
    allowed = set(BUNDLED_SKILL_NAMES)
    for item in cases:
        expected = set(item.get("expected_skills") or [])
        forbidden = set(item.get("forbidden_skills") or [])
        if not expected.issubset(allowed) or not forbidden.issubset(allowed):
            problems.append(f"{item.get('case_id')}: unknown skill name")
        if expected.intersection(forbidden):
            problems.append(f"{item.get('case_id')}: expected/forbidden overlap")
        if not str(item.get("prompt") or "").strip():
            problems.append(f"{item.get('case_id')}: missing prompt")
        if not str(item.get("expected_first_action") or "").strip():
            problems.append(f"{item.get('case_id')}: missing first action")
    return {
        "ok": not problems,
        "case_count": len(cases),
        "counts": counts,
        "catalog_hash": canonical_hash(payload),
        "problems": problems,
    }


def validate_skill_bundle_static(repo_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    skill_root = root / "src" / "pertura_runtime" / "agent_bundle" / "skills"
    problems: list[str] = []
    for name in BUNDLED_SKILL_NAMES:
        skill_dir = skill_root / name
        skill_file = skill_dir / "SKILL.md"
        text = skill_file.read_text(encoding="utf-8")
        if len(text.splitlines()) >= 500:
            problems.append(f"{name}: SKILL.md must be under 500 lines")
        parts = text.split("---", 2)
        if len(parts) != 3:
            problems.append(f"{name}: invalid frontmatter")
            continue
        metadata = yaml.safe_load(parts[1]) or {}
        if set(metadata) != {"name", "description"}:
            problems.append(f"{name}: frontmatter must contain only name/description")
        provider_texts = [text]
        for reference in sorted((skill_dir / "references").glob("*.md")):
            provider_texts.append(reference.read_text(encoding="utf-8"))
        lowered = "\n".join(provider_texts).lower()
        for token in _FORBIDDEN_PROVIDER_TOKENS:
            if token in lowered:
                problems.append(f"{name}: provider-specific token {token!r}")
        for relative in _REFERENCE.findall(text):
            if not (skill_dir / relative).is_file():
                problems.append(f"{name}: missing referenced resource {relative}")
    try:
        manifest = bundled_skill_manifest()
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        manifest = {}
        problems.append(f"bundle manifest: {exc}")
    cases = validate_skill_cases()
    problems.extend(f"cases: {item}" for item in cases["problems"])
    return {
        "ok": not problems,
        "bundle_hash": manifest.get("bundle_hash"),
        "skills": list(BUNDLED_SKILL_NAMES),
        "case_catalog_hash": cases["catalog_hash"],
        "case_count": cases["case_count"],
        "problems": problems,
    }


def skill_benchmark_matrix(repo_root: str | Path | None = None) -> dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    static = validate_skill_bundle_static(root)
    verdict = root / "benchmarks" / "skills" / "skill_behavior_verdict.json"
    behavior_ready = False
    behavior_reason = "not_run_environment_missing"
    if verdict.is_file():
        try:
            payload = json.loads(verdict.read_text(encoding="utf-8"))
            behavior_ready = bool(
                payload.get("passed")
                and payload.get("bundle_hash") == static.get("bundle_hash")
                and payload.get("case_catalog_hash") == static.get("case_catalog_hash")
            )
            behavior_reason = "current" if behavior_ready else "verdict_hash_drift"
        except json.JSONDecodeError:
            behavior_reason = "invalid_verdict"
    return {
        "schema_version": "pertura-skill-benchmark-matrix-v1",
        "skill_bundle_ready": bool(static["ok"]),
        "claude_skill_adapter_ready": bool(static["ok"]),
        "openai_adapter_ready": False,
        "skill_behavior_benchmark_ready": behavior_ready,
        "behavior_status": behavior_reason,
        "static": static,
    }
