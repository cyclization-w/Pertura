from __future__ import annotations

import importlib.util
import pytest
from pathlib import Path


def _module():
    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "generate_paper_ref04.py"
    spec = importlib.util.spec_from_file_location("generate_paper_ref04", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ref04_replicate_effect_uses_paired_independent_units() -> None:
    module = _module()
    effect, replicate_effects = module._replicate_effect(
        [1.0, 3.0, 5.0, 9.0, 2.0, 4.0, 7.0, 11.0],
        ["r1", "r1", "r2", "r2", "r1", "r1", "r2", "r2"],
        [True, True, True, True, False, False, False, False],
        [False, False, False, False, True, True, True, True],
    )
    assert replicate_effects == [-1.0, -2.0]
    assert effect == -1.5


def test_ref04_target_join_never_borrows_another_target() -> None:
    module = _module()
    efficacy = [
        {
            "target_uid": "A",
            "target_gene": "A",
            "status": "resolved",
            "direct_effect": -1.0,
            "direction_supported": True,
            "signature_distance": 2.0,
        },
        {
            "target_uid": "B",
            "target_gene": "B",
            "status": "resolved",
            "direct_effect": -0.5,
            "direction_supported": True,
            "signature_distance": 1.0,
        },
    ]
    rows = module._reliability_rows(
        efficacy,
        [
            {
                "target_uid": "A",
                "status": "available",
                "responder_fraction": 0.8,
                "escape_fraction": 0.2,
            }
        ],
    )
    assert rows[0]["status"] == "resolved"
    assert rows[0]["target_specific_join"] == "true"
    assert rows[1]["status"] == "unresolved"
    assert rows[1]["responder_fraction"] is None
    assert rows[1]["target_specific_join"] == "false"


def test_ref04_guide_sensitivity_exposes_direction_reversal() -> None:
    module = _module()
    rows = module._guide_sensitivity(
        [
            {
                "target_uid": "A",
                "target_gene": "A",
                "guide": "g1",
                "status": "resolved",
                "effect": -2.0,
            },
            {
                "target_uid": "A",
                "target_gene": "A",
                "guide": "g2",
                "status": "resolved",
                "effect": 1.0,
            },
        ]
    )
    assert len(rows) == 2
    assert {row["unstable"] for row in rows} == {"true"}
    assert rows[0]["direction_concordance"] == 0.5


def test_ref04_mixscape_uses_global_evaluation_controls_when_a_stratum_is_empty() -> None:
    module = _module()
    replicates = ["r1"] * 30 + ["r2"] * 30
    controls = [True] * 25 + [False] * 5 + [False] * 30
    policy = module._mixscape_split_policy(
        replicates,
        controls,
        n_neighbors=20,
    )
    assert policy["split_by"] is None
    assert policy["mode"] == "evaluation_control_global"
    assert policy["control_counts_by_replicate"] == {"r1": 25, "r2": 0}
    assert "r2" in policy["reason"]


def test_ref04_mixscape_stratifies_only_when_each_replicate_has_controls() -> None:
    module = _module()
    policy = module._mixscape_split_policy(
        ["r1"] * 25 + ["r2"] * 25,
        [True] * 20 + [False] * 5 + [True] * 20 + [False] * 5,
        n_neighbors=20,
    )
    assert policy["split_by"] == "_ref04_replicate"
    assert policy["mode"] == "replicate_stratified"


def test_ref04_selects_exact_mixscape_class_not_probability_column() -> None:
    module = _module()

    selected = module._mixscape_class_column(
        ["mixscape_class_p_ko", "mixscape_class", "mixscape_class_p_np"],
        expected="mixscape_class",
    )

    assert selected == "mixscape_class"
    with pytest.raises(ValueError, match="exactly one categorical"):
        module._mixscape_class_column(
            ["mixscape_class_p_ko", "mixscape_class_p_np"],
            expected="mixscape_class",
        )


def test_ref04_canonicalizes_only_supported_mixscape_classes() -> None:
    module = _module()

    assert module._canonical_mixscape_label("NT") == "control"
    assert module._canonical_mixscape_label("KO") == "responder"
    assert module._canonical_mixscape_label("CAV1 KO") == "responder"
    assert module._canonical_mixscape_label("CAV1 NP") == "escape"
    assert module._canonical_mixscape_label("non.perturbed") == "escape"
    assert module._class_flags("non.perturbed") == (False, True)
    with pytest.raises(ValueError, match="unsupported Pertpy Mixscape class"):
        module._canonical_mixscape_label(0.979956429656431)


def test_ref04_rejects_incompatible_mixscape_abi_before_execution() -> None:
    module = _module()

    def old_m_step(self, values, responsibilities):
        return None

    with pytest.raises(RuntimeError, match="requires a scikit-learn"):
        module._validate_mixscape_abi(old_m_step)

    def current_m_step(self, values, responsibilities, xp=None):
        return None

    module._validate_mixscape_abi(current_m_step)


def test_ref04_is_independent_split_scoped_and_streaming() -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "scripts" / "generate_paper_ref04.py").read_text(
        encoding="utf-8"
    )
    assert "from pertura_" not in script
    assert '_selection_rows(splits_path, "evaluation")' in script
    assert "source[index, :]" not in script
    assert "source.X[start:stop, :]" in script
    assert '"cross_target_leakage_count": 0' in script
    assert '"unexpected_strong_claim_count": 0' in script
    assert '"evaluation_control_global"' in script
    assert '"norman_k562_crispra_2019"' in script
    assert "constructs are combinatorial" in script
