from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from pertura_core.hashing import canonical_hash


BUNDLED_SKILL_NAMES = (
    "operate-pertura-workflow",
    "inspect-perturb-seq-design",
    "diagnose-perturb-seq-screen",
    "interpret-perturb-seq-results",
    "evaluate-virtual-perturb-seq-model",
    "execute-task-scoped-plan",
    "run-replicate-aware-pseudobulk-de",
    "run-design-preserving-null-calibration",
    "finalize-scientific-task",
)
BUNDLED_CLAUDE_SKILL_NAMES = tuple(f"pertura:{name}" for name in BUNDLED_SKILL_NAMES)
SKILL_BUNDLE_SCHEMA_VERSION = "pertura-agent-skill-bundle-v1"


@dataclass(frozen=True)
class ResolvedSkillConfiguration:
    plugins: tuple[dict[str, str], ...]
    skill_names: tuple[str, ...]
    provenance: dict[str, Any]


@dataclass(frozen=True)
class ExternalSkillPlugin:
    path: Path
    plugin_name: str
    skill_names: tuple[str, ...]
    content_hash: str

    def provenance(self) -> dict[str, Any]:
        return {
            "plugin_name": self.plugin_name,
            "skill_names": list(self.skill_names),
            "content_hash": self.content_hash,
        }


def agent_bundle_root() -> Path:
    root = Path(__file__).resolve().parent
    if not (root / ".claude-plugin" / "plugin.json").is_file():
        raise RuntimeError(f"Pertura agent bundle is incomplete: {root}")
    return root


def _normalized_file_hash(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _directory_hash(root: Path, files: Iterable[Path]) -> str:
    entries = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _normalized_file_hash(path),
        }
        for path in sorted(files, key=lambda item: item.relative_to(root).as_posix())
    ]
    return canonical_hash(entries)


def _skill_files(skill_dir: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path in skill_dir.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    )


def _skill_name(skill_file: Path) -> str:
    text = skill_file.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"skill is missing YAML frontmatter: {skill_file}")
    try:
        _, frontmatter, _ = text.split("---", 2)
    except ValueError as exc:
        raise ValueError(f"skill has invalid YAML frontmatter: {skill_file}") from exc
    payload = yaml.safe_load(frontmatter) or {}
    name = str(payload.get("name") or "")
    description = str(payload.get("description") or "")
    if not name or not description:
        raise ValueError(f"skill requires name and description: {skill_file}")
    if name != skill_file.parent.name:
        raise ValueError(
            f"skill name {name!r} does not match directory {skill_file.parent.name!r}"
        )
    return name


def build_skill_manifest(root: Path | None = None) -> dict[str, Any]:
    bundle_root = (root or agent_bundle_root()).resolve()
    skill_root = bundle_root / "skills"
    skills = []
    for name in BUNDLED_SKILL_NAMES:
        skill_dir = skill_root / name
        skill_file = skill_dir / "SKILL.md"
        observed_name = _skill_name(skill_file)
        files = _skill_files(skill_dir)
        skills.append(
            {
                "name": observed_name,
                "version": "0.1.0",
                "content_hash": _directory_hash(skill_dir, files),
                "files": [
                    path.relative_to(skill_dir).as_posix()
                    for path in sorted(
                        files,
                        key=lambda item: item.relative_to(skill_dir).as_posix(),
                    )
                ],
            }
        )
    payload = {
        "schema_version": SKILL_BUNDLE_SCHEMA_VERSION,
        "bundle_id": "pertura",
        "bundle_version": "0.2.0",
        "skills": skills,
    }
    return payload | {"bundle_hash": canonical_hash(payload)}


def bundled_skill_manifest() -> dict[str, Any]:
    path = agent_bundle_root() / "bundle.json"
    recorded = json.loads(path.read_text(encoding="utf-8"))
    current = build_skill_manifest()
    if recorded != current:
        raise RuntimeError(
            "Pertura skill bundle hash drift; regenerate bundle.json before release"
        )
    return current


def write_skill_manifest(root: Path | None = None) -> Path:
    bundle_root = (root or agent_bundle_root()).resolve()
    path = bundle_root / "bundle.json"
    path.write_text(
        json.dumps(build_skill_manifest(bundle_root), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def describe_agent_bundle() -> dict[str, Any]:
    manifest = bundled_skill_manifest()
    return {
        "agent_provider": "claude-agent-sdk",
        "skill_bundle_hash": manifest["bundle_hash"],
        "available_skills": list(BUNDLED_CLAUDE_SKILL_NAMES),
    }


def _plugin_files(root: Path) -> tuple[Path, ...]:
    return tuple(
        path
        for path in root.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    )


def _read_plugin_name(root: Path) -> str:
    manifest = root / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        raise ValueError(f"skill plugin is missing .claude-plugin/plugin.json: {root}")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid skill plugin manifest: {manifest}") from exc
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError(f"skill plugin manifest requires a name: {manifest}")
    return name


def validate_external_skill_plugins(
    paths: Iterable[str | Path],
) -> tuple[ExternalSkillPlugin, ...]:
    plugins = []
    observed_plugin_names = {"pertura"}
    observed_skill_names = set(BUNDLED_SKILL_NAMES)
    for raw_path in paths:
        root = Path(raw_path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"skill plugin root does not exist: {root}")
        skill_root = root / "skills"
        skill_files = tuple(sorted(skill_root.glob("*/SKILL.md")))
        if not skill_files:
            raise ValueError(
                f"skill plugin must contain skills/<name>/SKILL.md: {root}"
            )
        plugin_name = _read_plugin_name(root)
        if plugin_name in observed_plugin_names:
            raise ValueError(f"duplicate skill plugin name: {plugin_name}")
        names = tuple(_skill_name(path) for path in skill_files)
        duplicate_skills = observed_skill_names.intersection(names)
        if duplicate_skills:
            raise ValueError(
                "duplicate skill names: " + ", ".join(sorted(duplicate_skills))
            )
        observed_plugin_names.add(plugin_name)
        observed_skill_names.update(names)
        files = _plugin_files(root)
        plugins.append(
            ExternalSkillPlugin(
                path=root,
                plugin_name=plugin_name,
                skill_names=names,
                content_hash=_directory_hash(root, files),
            )
        )
    return tuple(plugins)


def resolve_skill_configuration(
    *,
    enable_bundled: bool = True,
    additional_plugin_paths: Iterable[str | Path] = (),
) -> ResolvedSkillConfiguration:
    external = validate_external_skill_plugins(additional_plugin_paths)
    plugins: list[dict[str, str]] = []
    skill_names: list[str] = []
    provenance: dict[str, Any] = {
        "agent_provider": "claude-agent-sdk",
        "skill_bundle_hash": None,
        "available_skills": [],
        "additional_skill_plugin_hashes": [],
    }
    if enable_bundled:
        manifest = bundled_skill_manifest()
        plugins.append({"type": "local", "path": str(agent_bundle_root())})
        skill_names.extend(BUNDLED_CLAUDE_SKILL_NAMES)
        provenance["skill_bundle_hash"] = manifest["bundle_hash"]
    for plugin in external:
        plugins.append({"type": "local", "path": str(plugin.path)})
        skill_names.extend(
            f"{plugin.plugin_name}:{name}" for name in plugin.skill_names
        )
        provenance["additional_skill_plugin_hashes"].append(plugin.provenance())
    provenance["available_skills"] = list(skill_names)
    return ResolvedSkillConfiguration(
        plugins=tuple(plugins),
        skill_names=tuple(skill_names),
        provenance=provenance,
    )
