"""Item 24.1 of docs/build_goal_session6_fixes.md: the arm-invariance test. In a library of two modes whose design
matches each target with its own mode's control cells, a CRISPRi target's numbers do not change when the CRISPRa
arm's control cells change. Each module and block that compares targets with control cells runs on two screens that
differ only in the CRISPRa control cells' counts, and the CRISPRi targets' rows must be the same. The steps that use
every cell on purpose (a PCA or programmes fitted on all cells) are named, with the reason, and left out."""
import copy
import warnings

import anndata as ad
import numpy as np
import pandas as pd
import pytest
import scipy.sparse as sp

from regression_helpers import design_for, guide
from pertura.question_modules.data import Screen

GENES = 60  # more than scanpy's 50 components, so the response methods read the PCA, as on a real screen
# every cell on purpose: these fit their space on the whole screen, so another arm's cells move every number a little
EVERY_CELL = {"C.any_effect": "its PCA is fitted on every cell", "C.programs": "its programmes are found on every cell",
              "C.states_and_composition": "its states are found on every cell"}
EVERY_CELL_BLOCKS = {"scores": "scanpy's score_genes draws each set's reference genes from bins of mean expression "
                               "over every cell"}


def screen_pair(tmp_path, opposite=False, shared=False, changed=("na1", "na2", "na3")):
    """Two screens alike but for the CRISPRa control cells' counts (those of the `changed` guides): CRISPRi targets A
    and B, CRISPRa target C, three control guides in each arm (so control-guide pairs form in each); with opposite, a
    gene g3 both inhibited (Xi) and activated (Xa); with shared, A moves B's genes too."""
    rng = np.random.default_rng(0)
    guides = [guide("a1", "A", gene="g0"), guide("a2", "A", gene="g0"), guide("b1", "B", gene="g1"),
              guide("b2", "B", gene="g1"), guide("c1", "C", "CRISPRa", gene="g2"), guide("c2", "C", "CRISPRa",
                                                                                         gene="g2"),
              guide("ni1", "NTi1"), guide("ni2", "NTi2"), guide("ni3", "NTi3"), guide("na1", "NTa1", "CRISPRa"),
              guide("na2", "NTa2", "CRISPRa"), guide("na3", "NTa3", "CRISPRa")]
    if opposite:
        guides += [guide("xi1", "Xi", gene="g3"), guide("xi2", "Xi", gene="g3"),
                   guide("xa1", "Xa", "CRISPRa", gene="g3"), guide("xa2", "Xa", "CRISPRa", gene="g3")]
    target_of = {g["guide"]: g["target"] for g in guides}
    rows = []
    for unit in ("u1", "u2", "u3"):
        for g in guides:
            label = "control" if g["kind"] != "targeting" else g["target"]
            for _ in range(30):
                rows.append((label, g["guide"], unit))
    base = rng.poisson(5, size=(len(rows), GENES)).astype(float)
    for i, (label, g, unit) in enumerate(rows):
        if label == "A":
            base[i, 0] = rng.poisson(1)
        if label == "B" or (shared and label == "A"):
            base[i, 1] = rng.poisson(1) if label == "B" else base[i, 1]
            base[i, 5:10] += rng.poisson(4, 5)
        if label == "C":
            base[i, 2] += rng.poisson(15)
        if label == "Xi":
            base[i, 3] = rng.poisson(1)
        if label == "Xa":
            base[i, 3] += rng.poisson(15)
    other = base.copy()
    for i, (label, g, unit) in enumerate(rows):
        if g in changed:
            other[i, :20] = rng.poisson(12, 20)  # the CRISPRa arm's control cells, changed
    out = []
    for k, X in enumerate((base, other)):
        folder = tmp_path / f"s{k}"
        folder.mkdir()
        obs = pd.DataFrame({"label": [r[0] for r in rows], "guides": [f"{r[1]}:10" for r in rows],
                            "unit": [r[2] for r in rows], "sample": [r[2] for r in rows],
                            "control_group": [target_of[r[1]] if r[0] == "control" else "" for r in rows]},
                           index=[f"c{i}" for i in range(len(rows))])
        a = ad.AnnData(sp.csr_matrix(X), obs=obs, var=pd.DataFrame(index=[f"g{i}" for i in range(GENES)]))
        pd.DataFrame(guides).to_csv(folder / "guides.tsv", sep="\t", index=False)
        (folder / "screen.json").write_text('{"unit_source": "column", "flags": []}', encoding="utf-8")
        design = design_for(guides, ["mode"])
        design["experiment"] = {"organism": {"value": "human", "status": "confirmed", "source": "test"}}
        design["perturbation"]["modality"] = {"value": "mixed", "status": "confirmed", "source": "test"}
        out.append(Screen(str(folder), copy.deepcopy(design), adata=a))
    return out


def rows_of(result, targets=("A", "B")):
    """The result's rows of the CRISPRi targets alone (a pair's row when both its targets are)."""
    t = result.table
    key = next((c for c in ("target", "perturbation", "group") if t is not None and c in t), None)
    if key is None or not len(t):
        return pd.DataFrame()
    t = t[t[key].astype(str).isin(targets)]
    for other in ("other_target", "other"):  # a row of two targets: both CRISPRi
        if other in t:
            t = t[t[other].astype(str).isin(targets)]
    num = t.select_dtypes("number")
    names = ("target", "group", "gene", "unit", "score", "state")
    return t[[c for c in t.columns if c in num.columns or c in names]] \
        .sort_values([c for c in names if c in t.columns]).reset_index(drop=True)


MODULES = [("B.efficacy", {}), ("B.escape", {"method": "own_gene"}), ("B.escape", {"method": "mixscape"}),
           ("C.genes", {"method": "rank_test"}), ("C.genes", {"method": "seurat3_lr"}), ("E.similar_targets", {}),
           ("D.common_response", {}), ("F.guides_agree", {}), ("F.replicates_agree", {})]


@pytest.mark.parametrize("name,choices", MODULES)
def test_a_crispri_targets_numbers_do_not_move_with_the_crispra_arms_control_cells(tmp_path, name, choices):
    from pertura import question_modules as qm
    s1, s2 = screen_pair(tmp_path)
    got = []
    for k, s in enumerate((s1, s2)):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                got.append(qm.REGISTRY[name].run(s, s.design, str(tmp_path / f"{name}-{k}"), **choices))
            except ImportError as e:  # a package this machine lacks: the server's suite runs it
                pytest.skip(f"{name} needs {e.name}")
    a, b = rows_of(got[0]), rows_of(got[1])
    assert len(a), f"{name} gave no row for the CRISPRi targets"
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-9, atol=1e-12)


def test_the_similarity_reads_a_pair_against_its_own_arms_control_guide_pairs(tmp_path):
    """A and B share a programme, so their r is high; two of the CRISPRa control guides change together on the second
    screen, so a CRISPRa control pair correlates as much: pooled with every arm's pairs, A-B's share would move."""
    from pertura.steps import similarity as sim
    a, b = (sim.pairs(s, targets=["A", "B", "C"], min_cells=10, genes="all", mask_targets=False)[0]
            for s in screen_pair(tmp_path, shared=True, changed=("na1", "na2")))
    pick = [t[t["group"].isin(["A", "B"]) & t["other"].isin(["A", "B"])] for t in (a, b)]
    assert pick[0]["control_pairs_as_high"].tolist() == pick[1]["control_pairs_as_high"].tolist()
    pooled = [float(np.mean(t.loc[t["kind"] == "control guides", "correlation"].to_numpy() >= p["correlation"].iloc[0]))
              for t, p in zip((a, b), pick)]
    assert pooled[0] != pooled[1], pooled  # the test can fail: every arm's pairs together would move A-B's share


def test_each_member_of_a_pair_of_opposite_modes_is_read_against_its_own_arm(tmp_path):
    from pertura.question_modules.e import _members_act
    s, s2 = screen_pair(tmp_path, opposite=True)  # s2: the CRISPRa control cells changed
    got = _members_act(s, s.design, "Xa / Xi", ["Xa", "Xi"], s.units(), in_pair=False)
    assert got["members_act"], got
    assert got["single_change_Xa"] > 0 > got["single_change_Xi"]
    again = _members_act(s2, s2.design, "Xa / Xi", ["Xa", "Xi"], s2.units(), in_pair=False)
    assert again["single_change_Xi"] == got["single_change_Xi"]  # Xi read against the CRISPRi arm alone


def test_a_control_cell_rests_on_the_guide_of_its_control_group(tmp_path):
    s, _ = screen_pair(tmp_path)
    i = int(np.flatnonzero(s.top_guide == "ni1")[0])
    s.adata.obs.iloc[i, s.adata.obs.columns.get_loc("guides")] = "na1:4;ni1:12"
    assert Screen(s.dir, s.design, adata=s.adata).top_guide[i] == "ni1"  # its control group NTi1's guide
    s.adata.obs.iloc[i, s.adata.obs.columns.get_loc("guides")] = "na1:14;ni1:12"
    assert Screen(s.dir, s.design, adata=s.adata).top_guide[i] == "ni1"  # even beside more of another arm's


def test_a_control_cell_with_a_control_guide_of_each_arm_is_the_reference_of_both(tmp_path):
    """Captures of guide pairs give control cells a non-targeting guide of each mode: such a cell is the reference of
    both arms (the user's decision of 2026-10-28, after the field's dual-guide screens)."""
    s, _ = screen_pair(tmp_path)
    i = int(np.flatnonzero(s.top_guide == "ni1")[0])
    j = int(np.flatnonzero(s.top_guide == "na2")[0])
    s.adata.obs.iloc[i, s.adata.obs.columns.get_loc("guides")] = "ni1:10;na1:10"
    s2 = Screen(s.dir, s.design, adata=s.adata)
    assert len(s2.arms_of_cells[i]) == 2
    assert s2.reference(target="A")[i] and s2.reference(target="C")[i]
    assert s2.reference(target="C")[j] and not s2.reference(target="A")[j]  # one arm's control guide: that arm's


def test_a_set_whose_members_span_arms_is_read_against_every_control_cell(tmp_path):
    """BR34: 'A+C', a CRISPRi and a CRISPRa guide in one cell, is compared with every control cell; no step refuses it."""
    from pertura.question_modules import c as cmod
    from pertura.steps import genes, similarity as sim
    s, _ = screen_pair(tmp_path)
    obs = s.adata.obs
    pick = np.flatnonzero(obs["label"].astype(str).to_numpy() == "C")[:60]
    obs.iloc[pick, obs.columns.get_loc("label")] = "A+C"
    obs.iloc[pick, obs.columns.get_loc("guides")] = "a1:10;c1:10"
    s = Screen(s.dir, s.design, adata=s.adata)
    assert s.arm_or_every("A+C") == "every arm"
    assert "every arm" in [k for k, _, _ in genes.arms(s, ["A", "A+C"], "control_guides")]
    t = cmod.genes_rank_test(s, ["A+C"], "control_guides", min_cells=10)
    assert len(t) and set(t["target"]) == {"A+C"}
    sim.pairs(s, targets=["A", "A+C"], min_cells=10, genes="all", mask_targets=False)


def test_seurat3_lr_with_other_targets_reads_its_arms_other_targets_without_its_own_cells(tmp_path):
    from pertura.question_modules import c as cmod
    s, _ = screen_pair(tmp_path)
    out = tmp_path / "s3"
    out.mkdir()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = cmod.genes_seurat3_lr(s, str(out), reference="other_targets")
    ref = r.table.set_index("group")["reference cells"]
    assert ref["A"] == int((s.label == "B").sum())  # B alone: not A's own cells, not the CRISPRa target C
    assert "C" in r.diagnostics["not tested"]  # the CRISPRa arm holds no other target


def test_the_steps_that_use_every_cell_say_why():
    from pertura import blocks as pt
    from pertura import question_modules as qm
    assert set(EVERY_CELL) <= set(qm.REGISTRY)
    assert all(callable(getattr(pt, b, None)) for b in EVERY_CELL_BLOCKS)


BLOCKS = {"on_target": {}, "similarity": {"genes": "all", "mask_targets": False}, "genes": {"method": "wilcoxon"}}


@pytest.mark.parametrize("name", sorted(BLOCKS))
def test_a_block_call_does_not_move_with_the_other_arms_control_cells(tmp_path, name):
    from pertura import blocks as pt
    s1, s2 = screen_pair(tmp_path)
    got = []
    for k, s in enumerate((s1, s2)):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                with pt.inside(s, str(tmp_path / f"{name}-{k}")):
                    got.append(getattr(pt, name)(targets=["A", "B"], **BLOCKS[name]))
            except ImportError as e:
                pytest.skip(f"pt.{name} needs {e.name}")
    a, b = rows_of(got[0]), rows_of(got[1])
    assert len(a), f"pt.{name} gave no row for the CRISPRi targets"
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-9, atol=1e-12)
