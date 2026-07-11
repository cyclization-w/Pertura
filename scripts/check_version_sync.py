from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path


def _distribution_to_semver(version: str) -> str:
    return re.sub(r"a(\d+)$", r"-alpha.\1", version)


def check_versions(repo_root: str | Path) -> list[str]:
    root = Path(repo_root).resolve()
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    python_version = str(project["project"]["version"])
    expected_semver = _distribution_to_semver(python_version)
    ui_version = str(
        json.loads((root / "ui" / "package.json").read_text(encoding="utf-8"))[
            "version"
        ]
    )
    plugin_version = str(
        json.loads(
            (
                root
                / "src"
                / "pertura_runtime"
                / "agent_bundle"
                / ".claude-plugin"
                / "plugin.json"
            ).read_text(encoding="utf-8")
        )["version"]
    )
    drift = []
    if ui_version != expected_semver:
        drift.append(
            f"UI version {ui_version} does not match Python {python_version}"
        )
    if plugin_version != expected_semver:
        drift.append(
            "Claude plugin version "
            f"{plugin_version} does not match Python {python_version}"
        )
    return drift


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check Python, UI, and bundled agent-plugin version parity."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args(argv)
    drift = check_versions(args.repo)
    if drift:
        for item in drift:
            print(item)
        return 1
    print("Pertura Python, UI, and agent-plugin versions are synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
