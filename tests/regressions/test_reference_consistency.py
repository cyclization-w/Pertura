"""M1 of the bug register: the declared matched comparison is used for counts, effects and feature selection."""
import numpy as np
import pandas as pd
import pytest

from regression_helpers import guide
from pertura.question_modules import b, c, e
from pertura.steps import genes, profiles, similarity


# BR26: a target whose guides span arms is refused and proposed for a split
def test_one_dominant_guide_does_not_discard_another_matched_stratum(make_screen, observe):
    gs = [guide("t1", "T", vector="v1"), guide("t2", "T", vector="v2"),
          guide("n1", "NT1", vector="v1"), guide("n2", "NT2", vector="v2")]
    s = make_screen([("T", "t1", "u", [1, 2, 3], 20), ("T", "t2", "u", [3, 2, 1], 21),
                     ("control", "n1", "u", [1, 2, 3], 20), ("control", "n2", "u", [3, 2, 1], 20)], gs,
                    matches=["vector"])
    try:
        ref = s.reference(target="T")
    except ValueError:
        return
    vectors = s.attribute("vector")
    target_strata = {vectors[g] for g in s.top_guide[s.cells_of("T")]}
    reference_strata = {vectors[g] for g in s.top_guide[ref]}
    observe(target_strata=sorted(target_strata), reference_strata=sorted(reference_strata))
    assert target_strata <= reference_strata


# BR03: B.escape's own-gene path reads the member's own arm
def test_escape_own_gene_uses_matched_controls(matched_screen, tmp_path, observe):
    s = matched_screen
    result = b.escape(s, s.design, str(tmp_path), method="own_gene")
    actual = float(result.table.loc[result.table.unit == "u1", "detected_reference"].iloc[0])
    expected = float((s.gene("g0") > 0)[s.reference(target="T")].mean())
    observe(actual_detection=actual, matched_detection=expected)
    assert actual == expected


# BR05: C.genes' seurat3_lr path reads each group's own arm
def test_public_seurat3_lr_uses_matched_controls(matched_screen, tmp_path, observe):
    s = matched_screen
    result = c.genes(s, s.design, str(tmp_path), method="seurat3_lr")
    actual = int(result.table.iloc[0]["reference cells"])
    expected = int(s.reference(target="T").sum())
    observe(reported_reference_cells=actual, matched_reference_cells=expected)
    assert actual == expected


# BR02: C.within_states counts the control cells its test uses
def test_within_state_eligibility_counts_matched_controls(make_screen, monkeypatch, tmp_path, observe):
    s = make_screen([("T", "t", "u1", [1, 2, 3], 12), ("control", "ni", "u1", [1, 2, 3], 2),
                     ("control", "na", "u1", [1, 2, 3], 20)],
                    [guide("t", "T", gene="g0"), guide("ni", "NTi"), guide("na", "NTa", "CRISPRa")], matches=["mode"])
    monkeypatch.setattr(c, "genes_by_leads", lambda *a, **kw: (
        pd.DataFrame(columns=["target", "gene", "effect", "q", "test"]), []))
    states = pd.DataFrame({"barcode": s.adata.obs_names, "state": "S"})
    result = c.within_states(s, s.design, str(tmp_path), cell_states=states, pairs=[["T", "S"]])
    row = result.table.iloc[0]
    observe(control_cells=int(row.control_cells), tested=bool(row.tested), status=result.status,
            matched_controls=int(s.reference(target="T").sum()))
    assert (int(row.control_cells), bool(row.tested), result.status) == (2, False, "not_enough_data")


# BR01: E.condition_x_perturbation compares with the target's own arm
@pytest.mark.parametrize("mode", ["CRISPRa", "CRISPRi"])
def test_condition_interaction_is_not_changed_by_other_arm(make_screen, monkeypatch, tmp_path, observe, mode):
    gs = [guide("t", "T", mode, gene="g0"), guide("n", "NT", mode),
          guide("other", "NTother", "CRISPRi" if mode == "CRISPRa" else "CRISPRa")]
    rows, scores, conditions = [], [], []
    for unit in ["u1", "u2"]:
        for cond, base, delta, other in [("rest", 0., 1., 0.), ("stim", 0., 2.2, 100.)]:
            for label, g, score in [("T", "t", base + delta), ("control", "n", base), ("control", "other", other)]:
                rows.append((label, g, unit, [1, 2, 3], 12))
                scores += [score] * 12
                conditions += [cond] * 12
    s = make_screen(rows, gs, matches=["mode"])
    s.condition = np.array(conditions)
    s.adata.obs["condition"] = conditions
    monkeypatch.setattr(e, "scanpy_scores", lambda *a, **kw: ({"P": np.array(scores)}, None, None))
    result = e.condition_x_perturbation(s, s.design, str(tmp_path), gene_sets="given", gene_lists={"P": ["g0"]})
    actual = float(result.table.iloc[0]["interaction"])
    observe(actual_interaction=actual, expected_interaction=1.2)
    assert actual == pytest.approx(1.2)


# BR06: each similarity profile subtracts its own arm's control cells
def test_similarity_profile_is_invariant_to_unmatched_arm(make_screen, observe):
    gs = [guide("t1", "T1"), guide("t2", "T2"), guide("ni", "NTi"), guide("na", "NTa", "CRISPRa")]
    s = make_screen([("T1", "t1", "u1", [10, 20, 30, 10], 40), ("T2", "t2", "u1", [10, 20, 30, 10], 40),
                     ("control", "ni", "u1", [10, 20, 30, 10], 40), ("control", "na", "u1", [30, 10, 10, 20], 40)],
                    gs, matches=["mode"])
    arm = genes.arms(s, ["T1", "T2"], "control_guides")[0][2]

    def value(view):
        table, _, _ = similarity.pairs(view, targets=["T1", "T2"], genes="all", mask_targets=False)
        values = table.loc[table.kind == "different targets", "correlation"]
        return float(values.iloc[0]) if len(values) else np.nan
    full, isolated = value(s), value(arm)
    observe(full_screen_r=full, matched_arm_r=isolated)
    assert np.isclose(full, isolated, equal_nan=True)


# BR04: profiles choose their genes on the arms profiled
def test_profile_gene_filter_uses_the_same_matched_reference(make_screen, observe):
    s = make_screen([("T", "t", "u1", [10, 20, 30, 10], 20), ("control", "ni", "u1", [10, 20, 30, 10], 20),
                     ("control", "na", "u1", [0, 20, 30, 10], 400)],
                    [guide("t", "T"), guide("ni", "NTi"), guide("na", "NTa", "CRISPRa")], matches=["mode"])
    arm = genes.arms(s, ["T"], "control_guides")[0][2]
    full = profiles.profiles(s, ["T"], min_cells=10)["u1"]
    isolated = profiles.profiles(arm, ["T"], min_cells=10)["u1"]
    observe(full_screen_genes=list(full.columns), matched_arm_genes=list(isolated.columns))
    assert set(full.columns) == set(isolated.columns)
