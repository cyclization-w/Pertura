from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.bind_paper_agent_catalogs import bind_assets, bind_task_references
from pertura_bench.paper_tasks import (
    load_paper_task_catalog,
    validate_paper_asset_catalog,
)


ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_task_reference_binding_freezes_every_reference_source(
    tmp_path: Path,
) -> None:
    candidate = ROOT / "benchmarks/paper_v1/task_references.v1.json"
    reference_ids = [f"REF-{index:02d}" for index in range(1, 11)]
    manifest_hashes = {}
    for reference_id in reference_ids:
        manifest_path = tmp_path / "references" / reference_id / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "reference_pack_id": reference_id,
                    "readiness": "generated",
                    "pending_jobs": [],
                }
            ),
            encoding="utf-8",
        )
        manifest_hashes[reference_id] = _sha256(manifest_path)
    index_path = tmp_path / "reference-index.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": "pertura-paper-reference-pack-index-v1",
                "passed": True,
                "reference_pack_count": 10,
                "reference_packs": [
                    {
                        "reference_pack_id": reference_id,
                        "manifest_sha256": manifest_hashes[reference_id],
                        "pack_tree_sha256": "sha256:" + f"{index + 10:064x}",
                        "git_commit": f"{index:040x}",
                    }
                    for index, reference_id in enumerate(reference_ids, start=1)
                ],
            }
        ),
        encoding="utf-8",
    )

    task_root = tmp_path / "task-references"
    p06_reference = task_root / "PAPA-06/reference/trans_de_reference.tsv"
    p06_design = task_root / "PAPA-06/reference/design_matrices.tsv"
    p06_eligibility = (
        task_root / "PAPA-06/neutral_inputs/target_eligibility.tsv"
    )
    p07_evidence = task_root / "PAPA-07/global_effect_evidence.tsv"
    for path in (p06_reference, p06_design, p06_eligibility, p07_evidence):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("id\tvalue\nfixture\t1\n", encoding="utf-8")
    manifest = {
        "schema_version": "pertura-paper-task-reference-pack-v1",
        "readiness": "generated",
        "pending_jobs": [],
        "problems": [],
        "passed": True,
        "output_files": {
            path.relative_to(task_root).as_posix(): _sha256(path)
            for path in (p06_reference, p06_design, p06_eligibility, p07_evidence)
        },
    }
    (task_root / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    candidate_payload = json.loads(candidate.read_text(encoding="utf-8"))
    for binding in candidate_payload["bindings"]:
        for evaluator in binding.get("evaluator_templates") or ():
            reference = (
                tmp_path
                / "references"
                / evaluator["reference_source"]
                / evaluator["reference_output"]
            )
            reference.parent.mkdir(parents=True, exist_ok=True)
            reference.write_text("id\tvalue\nfixture\t1\n", encoding="utf-8")
    output = tmp_path / "bound.json"
    result = bind_task_references(
        candidate_path=candidate,
        reference_index_path=index_path,
        task_reference_root=task_root,
        paper_root=tmp_path,
        output_path=output,
    )
    assert result["passed"] is True
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "bound"
    assert len(payload["bindings"]) == 21
    assert all(binding["bound_reference_sources"] for binding in payload["bindings"])
    assert all(
        len(binding["bound_reference_sources"])
        == len(binding["reference_sources"])
        for binding in payload["bindings"]
    )
    assert payload["bindings"][0]["bound_reference_sources"][0][
        "manifest_sha256"
    ].startswith("sha256:")
    assert all(
        binding.get("bound_evaluator")
        or binding.get("evaluators")
        or binding.get("protocol_evaluator")
        for binding in payload["bindings"]
    )


def test_asset_binding_requires_every_external_task_input(tmp_path: Path) -> None:
    task_catalog = ROOT / "benchmarks/paper_v1/agent_tasks.v2.json"
    catalog = json.loads(task_catalog.read_text(encoding="utf-8"))
    workflows = {}
    for workflow in catalog["workflows"]:
        produced = set()
        external = set()
        for task in workflow["turns"]:
            for role in task["required_input_roles"]:
                if role not in produced and not (
                    task.get("role") == "optional"
                    and role == "prediction_manifest_optional"
                ):
                    external.add(role)
            produced.update(task["required_artifact_roles"])
        assets = []
        for role in sorted(external):
            path = tmp_path / "cache" / workflow["workflow_id"] / role
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(role, encoding="utf-8")
            assets.append(
                {
                    "role": role,
                    "root": "cache",
                    "relative_path": f"{workflow['workflow_id']}/{role}",
                }
            )
        workflows[workflow["workflow_id"]] = {"assets": assets}
    template = tmp_path / "template.json"
    template.write_text(
        json.dumps(
            {
                "schema_version": "pertura-paper-agent-assets-template-v1",
                "workflows": workflows,
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "bound-assets.json"
    result = bind_assets(
        template_path=template,
        task_catalog_path=task_catalog,
        cache=tmp_path / "cache",
        paper_root=tmp_path / "paper",
        output_path=output,
    )
    assert result["passed"] is True
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["task_catalog_sha256"] == _sha256(task_catalog)

    # An external asset must never be allowed to impersonate an artifact that
    # an earlier task in the same workflow is responsible for producing.
    masked = json.loads(output.read_text(encoding="utf-8"))
    repl_assets = masked["workflows"]["WF-REPL"]["assets"]
    repl_assets.append(
        {
            "role": "retained_cell_manifest",
            "root": "cache",
            "relative_path": "WF-REPL/retained_cell_manifest",
            "content_sha256": "sha256:" + "1" * 64,
        }
    )
    loaded = load_paper_task_catalog(task_catalog)
    problems = validate_paper_asset_catalog(masked, loaded)
    assert any("could mask dependencies" in problem for problem in problems)
