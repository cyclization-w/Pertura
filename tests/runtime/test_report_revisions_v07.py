from pathlib import Path

from pertura_runtime.product import PerturaProductRuntime
from pertura_runtime.project.workspace import ProjectWorkspace


def test_report_revision_is_idempotent_until_content_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PERTURA_AUTHORITY_ROOT", str(tmp_path / "authority"))
    project = ProjectWorkspace.initialize(tmp_path / "study")
    run = project.create_run(logical_name="screen")
    workspace = project.run_workspace(run.run_id)
    runtime = PerturaProductRuntime(workspace, project_workspace=project, run_id=run.run_id)
    try:
        first = runtime.finalize_report()
        second = runtime.finalize_report()
    finally:
        runtime.close()

    assert first["revision"] == second["revision"] == 1
    assert first["report_digest"] == second["report_digest"]
    assert len(project.store.list_report_revisions(run.run_id)) == 1
    assert (workspace.reports_dir / "revisions" / "0001" / "report.json").is_file()
    assert (workspace.reports_dir / "latest.md").is_file()
