from __future__ import annotations

from pathlib import Path

from pertura_core import (
    AnalysisStatus,
    CapabilityTrust,
    ResultEnvelope,
    ScopeKey,
    SourceClass,
)
from pertura_core.hashing import path_sha256
from pertura_runtime.verifier.broker import _publish_outputs


def test_verifier_publishes_directory_artifact_without_changing_hash(
    tmp_path: Path,
) -> None:
    staging = tmp_path / "staging"
    bundle = staging / "prediction_bundle.zarr"
    (bundle / "c").mkdir(parents=True)
    (bundle / "zarr.json").write_text("metadata", encoding="utf-8")
    (bundle / "c" / "0").write_bytes(b"chunk")
    digest = path_sha256(bundle)
    result = ResultEnvelope(
        run_id="run-directory",
        request_id="request-directory",
        capability_id="virtual.prediction.ingest.v1",
        capability_version="0.1.0",
        capability_trust=CapabilityTrust.exploratory,
        contract_id="contract-directory",
        contract_hash="sha256:" + "1" * 64,
        scope=ScopeKey(dataset_id="dataset-directory"),
        status=AnalysisStatus.completed_with_caution,
        result_kind="prediction_bundle",
        source_class=SourceClass.prediction,
        summary="Chunked prediction bundle.",
        output_paths=(bundle.name,),
        output_hashes={bundle.name: digest},
    )

    published = _publish_outputs(
        result,
        staging,
        tmp_path / "outputs",
        tmp_path,
    )

    destination = tmp_path / published.output_paths[0]
    assert destination.is_dir()
    assert path_sha256(destination) == digest
    assert published.output_hashes[bundle.name] == digest
