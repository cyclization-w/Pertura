from __future__ import annotations

import pandas as pd
import pytest

from pertura_bench.metric_evaluators import (
    _compare_classification,
    _compare_cluster_agreement,
)


def test_classification_canonicalizes_declared_scientific_aliases() -> None:
    observed = pd.DataFrame(
        [{"id": "a", "label": "responder"}, {"id": "b", "label": "escape"}]
    )
    reference = pd.DataFrame(
        [{"id": "a", "label": "KO"}, {"id": "b", "label": "NP"}]
    )
    spec = {
        "key_columns": ["id"],
        "observed_label_column": "label",
        "reference_label_column": "label",
        "allowed_labels": ["control", "responder", "escape"],
        "label_aliases": {
            "control": ["NT"],
            "responder": ["KO", "perturbed"],
            "escape": ["NP", "non.perturbed"],
        },
        "minimum_accuracy": 1.0,
        "minimum_macro_f1": 1.0,
    }

    passed, metrics = _compare_classification(observed, reference, spec)

    assert passed is True
    assert metrics["accuracy"] == 1.0
    observed.loc[0, "label"] = "unknown"
    with pytest.raises(ValueError, match="unknown classification label"):
        _compare_classification(observed, reference, spec)


def test_boolean_classification_is_strict_and_encoding_invariant() -> None:
    observed = pd.DataFrame(
        [{"id": "a", "supported": "false"}, {"id": "b", "supported": "true"}]
    )
    reference = pd.DataFrame(
        [{"id": "a", "supported": False}, {"id": "b", "supported": True}]
    )
    spec = {
        "key_columns": ["id"],
        "observed_label_column": "supported",
        "reference_label_column": "supported",
        "label_type": "boolean",
        "minimum_accuracy": 1.0,
        "minimum_macro_f1": 1.0,
    }

    passed, metrics = _compare_classification(observed, reference, spec)

    assert passed is True
    assert metrics["accuracy"] == 1.0
    observed.loc[0, "supported"] = "not-a-bool"
    with pytest.raises(ValueError, match="invalid boolean value"):
        _compare_classification(observed, reference, spec)


def test_cluster_rejection_does_not_treat_false_string_as_true() -> None:
    observed = pd.DataFrame(
        [
            {"id": "a", "cluster": "0", "rejected": "false"},
            {"id": "b", "cluster": "1", "rejected": "true"},
        ]
    )
    reference = pd.DataFrame(
        [
            {"id": "a", "cluster": "A", "rejected": False},
            {"id": "b", "cluster": "B", "rejected": True},
        ]
    )
    spec = {
        "key_columns": ["id"],
        "observed_label_column": "cluster",
        "reference_label_column": "cluster",
        "rejection_column": "rejected",
        "reference_rejection_column": "rejected",
        "minimum_ari": 1.0,
        "minimum_rejection_accuracy": 1.0,
    }

    passed, metrics = _compare_cluster_agreement(observed, reference, spec)

    assert passed is True
    assert metrics["mapping_rejection_accuracy"] == 1.0
    observed.loc[0, "rejected"] = "not-a-bool"
    with pytest.raises(ValueError, match="invalid boolean value"):
        _compare_cluster_agreement(observed, reference, spec)
