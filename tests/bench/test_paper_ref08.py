from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _make_ref07(repo: Path, root: Path) -> Path:
    dataset_ids = (
        "replogle_k562_essential_2022",
        "papalexi_thp1_eccite",
        "norman_k562_crispra_2019",
    )
    datasets = root / "datasets.json"
    datasets.write_text(
        json.dumps({"datasets": {name: {} for name in dataset_ids}}),
        encoding="utf-8",
    )
    ref01 = root / "REF-01"
    ref01.mkdir()
    (ref01 / "manifest.json").write_text(
        json.dumps(
            {
                "reference_pack_id": "REF-01",
                "readiness": "generated",
                "pending_jobs": [],
            }
        ),
        encoding="utf-8",
    )
    (ref01 / "dataset_profiles.json").write_text(
        json.dumps(
            {
                "datasets": {
                    name: {"shape": [100, 50], "artifact_sha256": "sha256:" + str(index + 1) * 64}
                    for index, name in enumerate(dataset_ids)
                }
            }
        ),
        encoding="utf-8",
    )
    ref07 = root / "REF-07"
    result = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts" / "generate_paper_ref07.py"),
            "--datasets",
            str(datasets),
            "--ref01",
            str(ref01),
            "--output",
            str(ref07),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return ref07


def test_ref08_is_deterministic_complete_and_preserves_claim_boundaries(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    ref07 = _make_ref07(repo, tmp_path)
    output = tmp_path / "REF-08"
    command = [
        sys.executable,
        str(repo / "scripts" / "generate_paper_ref08.py"),
        "--ref07",
        str(ref07),
        "--output",
        str(output),
    ]
    first = subprocess.run(command, text=True, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    manifest_hash = _sha256(output / "manifest.json")
    second = subprocess.run(command, text=True, capture_output=True, check=False)
    assert second.returncode == 0, second.stderr
    assert _sha256(output / "manifest.json") == manifest_hash

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["completed_jobs"] == ["REF-08-A", "REF-08-B", "REF-08-C"]
    assert manifest["pending_jobs"] == []
    assert manifest["counts"]["perturbations"] == 12
    assert manifest["counts"]["genes"] == 48
    assert manifest["counts"]["signed_programs"] == 4
    assert manifest["counts"]["regulator_activity_rows"] == 48
    assert manifest["counts"]["ora_tests"] > 0
    assert manifest["counts"]["gsea_tests"] == 48
    assert manifest["metrics"]["ari"] >= 0.90
    assert manifest["metrics"]["cluster_stability"] >= 0.80
    assert manifest["metrics"]["provenance_completeness"] == 1.0
    assert manifest["metrics"]["causal_overclaim_count"] == 0

    protocol = json.loads(
        (output / "ranking_protocol_truth.json").read_text(encoding="utf-8")
    )
    assert protocol["valid_full_observed_ranking"] == "accepted"
    assert protocol["significant_only_truncated_ranking"] == "blocked"
    assert protocol["duplicate_gene_ranking"] == "blocked"
    assert protocol["missing_tested_universe_for_ora"] == "blocked"

    edge_text = (output / "perturbation_regulator_reference.tsv").read_text(
        encoding="utf-8"
    )
    assert "derived_hypothesis" in edge_text
    assert "\tfalse\n" in edge_text
    assert "causal_measurement" not in edge_text


def test_ref08_is_independent_of_pertura_results() -> None:
    repo = Path(__file__).resolve().parents[2]
    script = (repo / "scripts" / "generate_paper_ref08.py").read_text(
        encoding="utf-8"
    )
    assert "from pertura_" not in script
    assert "import pertura_" not in script
    assert "causal interpretation is prohibited" in script
    assert "full observed signed ranking" in script
