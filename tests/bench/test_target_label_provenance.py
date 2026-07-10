from __future__ import annotations

from pathlib import Path

import pytest

from pertura_bench.models import TargetVerdict, TargetVerdictSet
from pertura_bench.operations import stable_target_split
from pertura_bench.target_labels import evaluate_target_predictions, published_proxy_verdict_set
from pertura_workflow.capabilities.target_reliability import _load_profile


def test_published_proxy_is_traceable_but_never_production_eligible() -> None:
    split = stable_target_split(["T1", "T2"], "crispri")
    verdicts = published_proxy_verdict_set(
        modality="crispri",
        split=split,
        importer_version="1",
        importer_hash="sha256:" + "1" * 64,
        rows=[
            {"dataset_id": "d", "target_id": "T1", "expected_direction": "down", "verdict": "screen_passed", "reason_codes": ["table"], "doi": "10.1/test", "supplement": "S1", "row": 1},
            {"dataset_id": "d", "target_id": "T2", "expected_direction": "down", "verdict": "blocked", "reason_codes": ["table"], "doi": "10.1/test", "supplement": "S1", "row": 2},
        ],
    )
    report = evaluate_target_predictions(verdicts, {"T1": "screen_passed", "T2": "blocked"})
    assert report["macro_f1"] == 1.0
    assert report["proxy_only"] is True
    assert report["production_eligible"] is False
    assert _load_profile("crispri_published_proxy_v0")["validated"] is False


def test_proxy_set_cannot_be_spoofed_as_validated() -> None:
    verdict = TargetVerdict(
        modality="crispra", dataset_id="d", target_id="T", expected_direction="up",
        verdict="caution", reason_codes=("published",), label_source="published_proxy",
        doi="10.1/test", supplement="S1", importer_version="1", importer_hash="sha256:" + "1" * 64,
    )
    with pytest.raises(ValueError, match="cannot be production validated"):
        TargetVerdictSet(
            modality="crispra", label_source="published_proxy",
            split_manifest_hash="sha256:" + "2" * 64, verdicts=(verdict,), validated=True,
        )
