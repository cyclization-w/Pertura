from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pertura_runtime.claude.workspace import ClaudeRunWorkspace
from pertura_runtime.project.models import (
    AnalysisRunRecord,
    ConversationRecord,
    ProjectRecord,
)
from pertura_runtime.project.store import ProjectStore


@dataclass(frozen=True)
class ProjectWorkspace:
    root: Path
    state_dir: Path
    runs_dir: Path
    objects_dir: Path
    store: ProjectStore
    project: ProjectRecord

    @classmethod
    def initialize(cls, root: Path, *, logical_name: str | None = None) -> "ProjectWorkspace":
        root = Path(root).expanduser().resolve()
        state_dir = root / ".pertura"
        state_dir.mkdir(parents=True, exist_ok=True)
        store = ProjectStore(state_dir / "project.sqlite")
        projects = store.list_projects()
        project = projects[0] if projects else ProjectRecord(logical_name=logical_name or root.name)
        if not projects:
            store.put_project(project)
        workspace = cls(root=root, state_dir=state_dir, runs_dir=state_dir / "runs", objects_dir=state_dir / "objects", store=store, project=project)
        workspace.runs_dir.mkdir(parents=True, exist_ok=True)
        workspace.objects_dir.mkdir(parents=True, exist_ok=True)
        return workspace

    @classmethod
    def open(cls, root: Path) -> "ProjectWorkspace":
        root = Path(root).expanduser().resolve()
        database = root / ".pertura" / "project.sqlite"
        if not database.is_file():
            raise FileNotFoundError(f"not a Pertura project: {root}")
        return cls.initialize(root)

    def create_run(self, *, logical_name: str = "analysis", run_id: str | None = None) -> AnalysisRunRecord:
        run = AnalysisRunRecord(project_id=self.project.project_id, logical_name=logical_name, **({"run_id": run_id} if run_id else {}))
        self.store.put_run(run)
        self.store.put_project(self.project.model_copy(update={"active_run_id": run.run_id}))
        return run

    def active_run(self) -> AnalysisRunRecord:
        project = self.store.get_project(self.project.project_id) or self.project
        if project.active_run_id:
            run = self.store.get_run(project.active_run_id)
            if run:
                return run
        runs = self.store.list_runs(project.project_id)
        if runs:
            return runs[-1]
        return self.create_run(logical_name="current", run_id="current")

    def create_conversation(self, run_id: str, *, title: str = "Pertura analysis") -> ConversationRecord:
        if not self.store.get_run(run_id):
            raise KeyError(f"unknown analysis run: {run_id}")
        conversation = ConversationRecord(project_id=self.project.project_id, run_id=run_id, title=title)
        self.store.put_conversation(conversation)
        return conversation

    def run_workspace(self, run_id: str, *, input_source: Path | None = None) -> ClaudeRunWorkspace:
        run_root = self.runs_dir / run_id
        return ClaudeRunWorkspace.open(run_root, input_source=input_source) if run_root.exists() else ClaudeRunWorkspace.create(root=self.runs_dir, input_source=input_source, run_id=run_id)
