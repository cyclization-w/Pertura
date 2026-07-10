from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    return repr(value)


@dataclass(frozen=True)
class ClaudeRunWorkspace:
    """Isolated workspace for a single Claude SDK run."""

    root: Path
    input_dir: Path
    outputs_dir: Path
    logs_dir: Path
    artifacts_dir: Path
    reports_dir: Path
    task_dir: Path
    input_source: Path | None = None

    @classmethod
    def create(
        cls,
        *,
        root: Path,
        input_source: Path | None = None,
        run_id: str | None = None,
    ) -> "ClaudeRunWorkspace":
        run_name = run_id or f"claude_{_utc_stamp()}_{uuid4().hex[:8]}"
        run_root = Path(root).expanduser().resolve() / run_name
        workspace = cls(
            root=run_root,
            input_dir=run_root / "input",
            outputs_dir=run_root / "outputs",
            logs_dir=run_root / "logs",
            artifacts_dir=run_root / "artifacts",
            reports_dir=run_root / "reports",
            task_dir=run_root / "task",
            input_source=Path(input_source).expanduser().resolve() if input_source else None,
        )
        workspace.initialize()
        return workspace

    @classmethod
    def open(cls, root: Path, *, input_source: Path | None = None) -> "ClaudeRunWorkspace":
        run_root = Path(root).expanduser().resolve()
        manifest_path = run_root / "manifest.json"
        manifest: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                manifest = {}
        recorded_source = manifest.get("input_source")
        source = input_source or (Path(recorded_source) if recorded_source else None)
        workspace = cls(
            root=run_root,
            input_dir=run_root / "input",
            outputs_dir=run_root / "outputs",
            logs_dir=run_root / "logs",
            artifacts_dir=run_root / "artifacts",
            reports_dir=run_root / "reports",
            task_dir=run_root / "task",
            input_source=Path(source).expanduser().resolve() if source else None,
        )
        for path in (workspace.root, workspace.input_dir, workspace.outputs_dir, workspace.logs_dir, workspace.artifacts_dir, workspace.reports_dir, workspace.task_dir):
            path.mkdir(parents=True, exist_ok=True)
        return workspace

    def initialize(self) -> None:
        for path in [
            self.root,
            self.input_dir,
            self.outputs_dir,
            self.logs_dir,
            self.artifacts_dir,
            self.reports_dir,
            self.task_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)
        if self.input_source is not None:
            self._stage_input_reference(self.input_source)
        self.write_json(
            self.root / "manifest.json",
            {
                "runtime": "claude_agent_sdk",
                "run_root": str(self.root),
                "input_source": str(self.input_source) if self.input_source else None,
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "status": "created",
            },
        )

    def _stage_input_reference(self, source: Path) -> None:
        self.write_text(
            self.input_dir / "README.md",
            "\n".join(
                [
                    "# Pertura input",
                    "",
                    "The source dataset is read-only for this run.",
                    f"Absolute source path: `{source}`",
                    "",
                    "If `input/project` exists, it is a filesystem link to the source.",
                    "Write all generated files under `outputs/`.",
                    "",
                ]
            ),
        )
        self.write_text(self.input_dir / "source_path.txt", str(source) + "\n")
        link = self.input_dir / "project"
        if link.exists():
            return
        try:
            if source.is_dir():
                os.symlink(source, link, target_is_directory=True)
            else:
                os.symlink(source, link)
        except OSError:
            self.write_text(
                self.input_dir / "LINK_NOT_CREATED.txt",
                "Could not create input/project symlink. Use source_path.txt.\n",
            )

    def write_text(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")

    def append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=_json_default, ensure_ascii=False) + "\n")

    def update_manifest(self, updates: dict[str, Any]) -> None:
        path = self.root / "manifest.json"
        existing: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = {}
        existing.update(updates)
        self.write_json(path, existing)

    def write_task_files(self, *, task: str, system_prompt: str, output_contract: str) -> None:
        self.write_text(self.task_dir / "PERTURA_TASK.md", task)
        self.write_text(self.task_dir / "PERTURA_SYSTEM_PROMPT.md", system_prompt)
        self.write_text(self.task_dir / "PERTURA_OUTPUT_CONTRACT.md", output_contract)

    def summarize_outputs(self) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        for base in [self.outputs_dir, self.logs_dir, self.artifacts_dir, self.reports_dir]:
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if path.is_file():
                    files.append(
                        {
                            "path": str(path.relative_to(self.root)),
                            "size_bytes": path.stat().st_size,
                        }
                    )
        return {"files": sorted(files, key=lambda item: item["path"])}

    def finalize(self, *, status: str, result: str | None = None, error: str | None = None) -> None:
        payload: dict[str, Any] = {
            "status": status,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "outputs": self.summarize_outputs(),
        }
        if result is not None:
            payload["result_preview"] = result[:2000]
        if error is not None:
            payload["error"] = error
        self.update_manifest(payload)

    def copy_for_debug(self, source: Path, relative_dest: str) -> Path:
        dest = self.root / relative_dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        return dest
