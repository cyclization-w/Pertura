from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_ref01b_completes_ref01_manifest_deterministically(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "REF-01"
    output.mkdir()
    profiles = output / "dataset_profiles.json"
    tabulations = output / "design_tabulations.json"
    profiles.write_text('{"profiles": true}\n', encoding="utf-8")
    tabulations.write_text('{"tabulations": true}\n', encoding="utf-8")
    manifest = {
        "schema_version": "pertura-paper-ref01-v1",
        "reference_pack_id": "REF-01",
        "completed_jobs": ["REF-01-A"],
        "pending_jobs": ["REF-01-B"],
        "readiness": "generated_partial",
        "generator_script_sha256": "sha256:" + "1" * 64,
        "output_files": {
            profiles.name: _sha256(profiles),
            tabulations.name: _sha256(tabulations),
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    command = [
        sys.executable,
        str(root / "scripts" / "generate_paper_ref01b.py"),
        "--output",
        str(output),
    ]
    first = subprocess.run(command, text=True, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    first_manifest = (output / "manifest.json").read_bytes()
    second = subprocess.run(command, text=True, capture_output=True, check=False)
    assert second.returncode == 0, second.stderr
    assert (output / "manifest.json").read_bytes() == first_manifest

    updated = json.loads(first_manifest)
    assert updated["completed_jobs"] == ["REF-01-A", "REF-01-B"]
    assert updated["pending_jobs"] == []
    assert updated["readiness"] == "generated"
    assert set(updated["output_files"]) == {
        "dataset_profiles.json",
        "design_tabulations.json",
        "intake_failure_truth.json",
        "design_failure_truth.json",
    }

    intake = json.loads(
        (output / "intake_failure_truth.json").read_text(encoding="utf-8")
    )
    design = json.loads(
        (output / "design_failure_truth.json").read_text(encoding="utf-8")
    )
    assert {case["case_id"] for case in intake["cases"]} == {
        "wrong_expression_layer",
        "duplicate_cell_identity",
    }
    assert {case["case_id"] for case in design["cases"]} == {
        "missing_replicate",
        "condition_batch_confounding",
    }


def test_ref01b_rejects_ref01a_output_drift(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "REF-01"
    output.mkdir()
    profiles = output / "dataset_profiles.json"
    profiles.write_text('{}\n', encoding="utf-8")
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "pertura-paper-ref01-v1",
                "reference_pack_id": "REF-01",
                "completed_jobs": ["REF-01-A"],
                "output_files": {
                    profiles.name: "sha256:" + "0" * 64,
                },
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "generate_paper_ref01b.py"),
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "REF-01-A output hash drift" in completed.stderr
