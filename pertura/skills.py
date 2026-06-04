"""SKILL.md loader, similar to Claude Code's skill system.

Skills are directories or single SKILL.md files with frontmatter:
  ---
  name: skill_name
  description: What this skill does
  allowed_tools: [tool_a, tool_b]
  ---
  Skill instructions injected into LLM context when relevant.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_skill(path: Path) -> dict | None:
    """Load a SKILL.md file. Returns {name, description, body, allowed_tools}."""
    if path.is_dir():
        path = path / "SKILL.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    return parse_skill(text, str(path))


def parse_skill(text: str, source: str = "") -> dict | None:
    """Parse frontmatter and body from SKILL.md text."""
    fm = _parse_frontmatter(text)
    if not fm:
        return None
    body = text.split("---", 2)[-1].strip() if text.count("---") >= 2 else ""
    return {
        "name": fm.get("name", ""),
        "description": fm.get("description", ""),
        "allowed_tools": fm.get("allowed_tools", []),
        "body": body,
        "source": source,
    }


def load_skills_dir(directory: Path) -> list[dict]:
    """Load all skills from a directory tree."""
    skills = []
    if not directory.exists():
        return skills
    for item in sorted(directory.iterdir()):
        skill = load_skill(item)
        if skill:
            skills.append(skill)
    return skills


def _parse_frontmatter(text: str) -> dict:
    """Extract simple YAML-like frontmatter between --- markers."""
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    fm_text = parts[1].strip()
    result = {}
    current_key = None
    current_list = []
    for line in fm_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if ":" in line and not line.startswith("-"):
            if current_key and current_list:
                result[current_key] = current_list
                current_list = []
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value:
                result[key] = value.strip('"').strip("'")
                current_key = None
            else:
                current_key = key
        elif line.startswith("-") and current_key:
            current_list.append(line[1:].strip())
    if current_key and current_list:
        result[current_key] = current_list
    return result


def init_pertura_dir(project_path: Path) -> Path:
    """Create a .pertura/ directory with editable starter files."""
    pertura_dir = project_path / ".pertura"
    pertura_dir.mkdir(parents=True, exist_ok=True)

    instructions_path = pertura_dir / "PERTURA.md"
    if not instructions_path.exists():
        instructions_path.write_text(
            """# Project instructions for the analysis agent

## Data description
<!-- Describe your data: format, columns, experimental design -->

## Analysis goals
<!-- What questions should the agent answer? -->

## Preferences
<!-- Methods, thresholds, reporting style, output format -->

## First checks
- Run `pertura spec audit .pertura/analysis_graph.json --domain perturbseq`
- Inspect a node with `pertura spec contract .pertura/analysis_graph.json --domain perturbseq --node effect_exploration`
""",
            encoding="utf-8",
        )

    graph_path = pertura_dir / "analysis_graph.json"
    domain_path = pertura_dir / "domain.json"
    if not graph_path.exists() or not domain_path.exists():
        from pertura.domain import perturbseq

        domain = perturbseq.default_domain()
        if not graph_path.exists():
            graph_path.write_text(
                json.dumps(domain.analysis_graph, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if not domain_path.exists():
            domain_path.write_text(
                json.dumps(domain.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    settings_path = pertura_dir / "settings.json"
    if not settings_path.exists():
        settings_path.write_text(
            json.dumps(
                {
                    "domain": ".pertura/domain.json",
                    "analysis_graph": ".pertura/analysis_graph.json",
                    "provider": "openai",
                    "max_attempts": 30,
                    "web_research": "ask",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    (pertura_dir / "skills").mkdir(exist_ok=True)
    (pertura_dir / "hooks").mkdir(exist_ok=True)

    return pertura_dir


def init_blackboard_dir(project_path: Path) -> Path:
    """Backward-compatible alias for older callers."""
    return init_pertura_dir(project_path)
