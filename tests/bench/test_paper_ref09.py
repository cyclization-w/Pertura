from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _ref08(root: Path) -> Path:
    path = root / "REF-08"
    path.mkdir()
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "reference_pack_id": "REF-08",
                "readiness": "generated",
                "pending_jobs": [],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_ref09_is_deterministic_offline_and_preserves_source_classes(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    ref08 = _ref08(tmp_path)
    output = tmp_path / "REF-09"
    command = [
        sys.executable,
        str(repo / "scripts" / "generate_paper_ref09.py"),
        "--ref08",
        str(ref08),
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
    assert manifest["completed_jobs"] == ["REF-09-A", "REF-09-B", "REF-09-C"]
    assert manifest["pending_jobs"] == []
    assert manifest["counts"]["literature_records"] == 5
    assert manifest["counts"]["evidence_records"] == 9
    assert manifest["counts"]["accepted_evidence_records"] == 5
    assert manifest["counts"]["rejected_evidence_records"] == 4
    assert manifest["metrics"]["citation_completeness"] == 1.0
    assert manifest["metrics"]["source_class_accuracy"] == 1.0
    assert manifest["metrics"]["strong_overclaim_count"] == 0
    assert manifest["metrics"]["constraint_satisfaction"] == 1.0
    assert manifest["metrics"]["coverage"] >= 0.75
    assert manifest["metrics"]["uncertainty_capture"] >= 0.80

    snapshot = json.loads(
        (output / "europepmc_snapshot.json").read_text(encoding="utf-8")
    )
    assert snapshot["offline_replay_only"] is True
    assert "not a systematic-review" in snapshot["coverage_claim"]
    assert {row["pmid"] for row in snapshot["records"]} == {
        "35688146",
        "33649593",
        "31395745",
        "27984732",
        "28099430",
    }

    with (output / "literature_record_reference.tsv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        literature = list(csv.DictReader(handle, delimiter="\t"))
    assert all(row["source_class"] == "curated_prior" for row in literature)
    assert all(row["citation_complete"] == "True" for row in literature)

    evidence = json.loads(
        (output / "evidence_map_truth.json").read_text(encoding="utf-8")
    )
    rejected = {
        row["case_id"]: row["reasons"]
        for row in evidence["records"]
        if row["expected"] == "rejected"
    }
    assert rejected == {
        "evidence_06": ["prediction_reclassified_as_measurement"],
        "evidence_07": ["stale_result"],
        "evidence_08": ["cross_target_evidence"],
        "evidence_09": ["prior_provenance_required"],
    }

    panel = json.loads(
        (output / "next_panel_reference.json").read_text(encoding="utf-8")
    )
    assert panel["source_class"] == "hypothesis"
    assert panel["selected_oracle"]["cost"] <= panel["budget"]
    assert panel["claim_boundary"].endswith("not measurements")


def test_ref09_does_not_add_live_network_or_pubmed_adapter() -> None:
    repo = Path(__file__).resolve().parents[2]
    script = (repo / "scripts" / "generate_paper_ref09.py").read_text(
        encoding="utf-8"
    )
    assert "urllib" not in script
    assert "requests" not in script
    assert "eutils" not in script.lower()
    assert "A direct PubMed adapter is intentionally deferred" in script
    assert "not systematic-review recall" in script
