from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest


def _module():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "generate_paper_ref03.py"
    spec = importlib.util.spec_from_file_location("generate_paper_ref03", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ref03_vote_mapping_has_probability_and_distance_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    import sklearn.neighbors

    class DeterministicNeighbors:
        def __init__(self, *, n_neighbors: int, n_jobs: int):
            assert n_neighbors == 3
            assert n_jobs == 1

        def fit(self, controls: np.ndarray):
            assert controls.shape == (6, 2)
            return self

        def kneighbors(self, points: np.ndarray, *, return_distance: bool):
            assert return_distance is True
            assert points.shape == (2, 2)
            return (
                np.asarray([[0.02, 0.04, 0.08], [130.0, 134.0, 140.0]]),
                np.asarray([[0, 1, 2], [3, 4, 5]]),
            )

    monkeypatch.setattr(
        sklearn.neighbors, "NearestNeighbors", DeterministicNeighbors
    )
    controls = np.asarray(
        [[0.0, 0.0], [0.1, 0.0], [0.0, 0.1], [5.0, 5.0], [5.1, 5.0], [5.0, 5.1]]
    )
    labels = np.asarray(["state_a"] * 3 + ["state_b"] * 3)
    mapped = module._vote_assignments(
        np.asarray([[0.02, 0.02], [100.0, 100.0]]),
        controls,
        labels,
        n_neighbors=3,
        probability_threshold=0.60,
        distance_threshold=1.0,
    )
    assert mapped[0]["technical_state_id"] == "state_a"
    assert mapped[0]["rejected"] is False
    assert mapped[1]["technical_state_id"] == "unresolved_state"
    assert mapped[1]["rejected"] is True


def test_ref03_dense_budget_is_checked_before_materialization() -> None:
    module = _module()

    class NeverMaterialize:
        def toarray(self):
            raise AssertionError("matrix was materialized before budget check")

    with pytest.raises(MemoryError, match="exceeding max_memory_gb"):
        module._dense(
            NeverMaterialize(),
            rows=1_000_000,
            columns=2_000,
            max_memory_gb=0.01,
            label="planted budget attack",
        )


def test_ref03_is_independent_and_enforces_protocol_boundaries() -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "scripts" / "generate_paper_ref03.py").read_text(
        encoding="utf-8"
    )
    assert "from pertura_" not in script
    assert 'if str(row["is_control"]).lower() == "true"' in script
    assert 'evaluation_cell_count_used_for_fit": 0' in script
    assert '"strong_claim_allowed": "false"' in script
    assert '"technical_id_overwritten": "false"' in script
    assert "distance_rejection_quantile" in script
    assert "np.sort(index)" in script
