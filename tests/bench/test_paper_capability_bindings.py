from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(repo: Path, root: Path) -> tuple[Path, Path]:
    catalog = json.loads(
        (repo / "benchmarks" / "paper_v1" / "reference_catalog.v1.json").read_text(
            encoding="utf-8"
        )
    )
    references = root / "references"
    index_packs = []
    for pack in catalog["reference_packs"]:
        pack_id = pack["reference_pack_id"]
        pack_root = references / pack_id
        pack_root.mkdir(parents=True)
        table = pack_root / "reference.tsv"
        table.write_text("id\tvalue\ncase\t1\n", encoding="utf-8")
        manifest = {
            "schema_version": f"fixture-{pack_id.lower()}",
            "reference_pack_id": pack_id,
            "completed_jobs": [job["job_id"] for job in pack["generator_jobs"]],
            "pending_jobs": [],
            "readiness": "generated",
            "output_files": {"reference.tsv": _sha256(table)},
        }
        manifest_path = pack_root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        index_packs.append(
            {
                "reference_pack_id": pack_id,
                "manifest_sha256": _sha256(manifest_path),
                "pack_tree_sha256": "sha256:" + pack_id[-2:] * 32,
                "git_commit": None,
                "readiness": "generated",
                "completed_jobs": manifest["completed_jobs"],
            }
        )
    index = {
        "schema_version": "pertura-paper-reference-pack-index-v1",
        "passed": True,
        "reference_pack_count": 10,
        "reference_packs": index_packs,
    }
    index_path = root / "reference-pack-index.json"
    index_path.write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return references, index_path


def test_paper_capability_bindings_cover_every_capability_once_and_are_deterministic(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    references, index = _fixture(repo, tmp_path)
    output = tmp_path / "capability-reference-bindings.json"
    command = [
        sys.executable,
        str(repo / "scripts" / "build_paper_capability_bindings.py"),
        "--reference-index",
        str(index),
        "--references-root",
        str(references),
        "--output",
        str(output),
    ]
    first = subprocess.run(command, text=True, capture_output=True, check=False)
    assert first.returncode == 0, first.stdout + first.stderr
    first_hash = _sha256(output)
    second = subprocess.run(command, text=True, capture_output=True, check=False)
    assert second.returncode == 0, second.stdout + second.stderr
    assert _sha256(output) == first_hash

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["problems"] == []
    assert payload["status"] == "bound"
    assert payload["scenario_count"] == 10
    assert payload["capability_count"] == 44
    assert payload["reference_pack_count"] == 10
    bindings = [
        binding
        for scenario in payload["scenarios"]
        for binding in scenario["capability_bindings"]
    ]
    assert len(bindings) == 44
    assert len({binding["capability_id"] for binding in bindings}) == 44
    assert all(binding["metrics"] for binding in bindings)
    assert all(
        set(binding["metrics"])
        == set(binding["reference_catalog_metrics"])
        | set(binding["execution_or_hard_gate_metrics"])
        for binding in bindings
    )
    assert set(payload["scoring_route_counts"]) == {
        "planted_fixture_comparison",
        "protocol_hard_gate",
        "real_artifact_comparison",
    }
    cap10 = next(
        scenario for scenario in payload["scenarios"] if scenario["scenario_id"] == "CAP-10"
    )
    assert {
        binding["release_scope"] for binding in cap10["capability_bindings"]
    } == {"optional_supplemental"}
    ref05_bindings = [
        binding for binding in bindings if binding["reference_pack_id"] == "REF-05"
    ]
    assert ref05_bindings
    assert {binding["release_scope"] for binding in ref05_bindings} == {
        "supplemental"
    }
    assert payload["metric_rule"]["missing_metrics_are_not_passed"] is True


def test_binding_builder_does_not_run_capabilities_or_mutate_references() -> None:
    repo = Path(__file__).resolve().parents[2]
    script = (repo / "scripts" / "build_paper_capability_bindings.py").read_text(
        encoding="utf-8"
    )
    assert "from pertura_" not in script
    assert "import pertura_" not in script
    assert "subprocess" not in script
    assert "unlink(" not in script
    assert "rmtree(" not in script
    assert "optional/supplemental" in script
