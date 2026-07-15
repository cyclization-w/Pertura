from __future__ import annotations

import importlib.util
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
    assert '"norman_k562_crispra_2019"' in script
    assert "constructs are combinatorial" in script
