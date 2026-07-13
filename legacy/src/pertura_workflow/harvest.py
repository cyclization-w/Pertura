from __future__ import annotations

from pathlib import Path

from pertura_workflow.models import HarvestMode, HarvestReport
from pertura_workflow.preflight import preflight_workspace


def harvest_artifacts_from_workspace(
    workspace: str | Path,
    *,
    mode: HarvestMode | str = HarvestMode.candidate_only,
    registry_path: str | Path | None = None,
) -> HarvestReport:
    harvest_mode = mode if isinstance(mode, HarvestMode) else HarvestMode(str(mode))
    preflight = preflight_workspace(workspace)
    reasons: list[str] = []
    registered: list[str] = []
    if harvest_mode == HarvestMode.candidate_only:
        reasons.append("candidate_only mode never writes the evidence registry")
    elif harvest_mode == HarvestMode.auto_register_strict:
        reasons.append("auto_register_strict skipped registration because no P2.0 candidate has validator_passed=true")
    elif harvest_mode == HarvestMode.interactive_confirm:
        reasons.append("interactive_confirm is reserved for identity/design metadata confirmation; no automatic evidence registration was performed")
    return HarvestReport(
        workspace=preflight.workspace,
        mode=harvest_mode,
        candidates=preflight.candidate_artifacts,
        registered_artifact_ids=registered,
        registry_path=str(registry_path) if registry_path else None,
        reasons=reasons,
    )
