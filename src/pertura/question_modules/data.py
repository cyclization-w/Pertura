"""The data every module reads: a screen object's counts and cells (pertura.screen_object.load), with its guides
and the confirmed design."""
import json
import os
from functools import cached_property

import numpy as np
import pandas as pd
import scipy.sparse as sp

from ..most_common import most_common_value

NOT_TARGETS =("none", "multi", "ambiguous", "control", "incomplete", "two sets", "nan", "", "excluded", "failed QC")
# "excluded": a cell outside a cell set that the robustness engine keeps beside it (pertura.cell_sets)
REFERENCES = ("control_guides", "other_targets", "no_guide")
# a unit holds a condition where at least this many of its cells carry it, so one mislabelled cell does not make a
# screen's samples hold several conditions (Screen.crossed)
CROSSED_CELLS = 10
# the build's unit for a cell whose unit it could not tell (no value in the unit column): "<sample>:unassigned"
UNASSIGNED = ":unassigned"


def value(design, key):
    """A design fact's value, or None."""
    from ..design_gate import node
    return (node(design or {}, key) or {}).get("value")


def units_are_replicates(design, meta):
    """Whether a screen object's units (its summary, screen.json) hold the confirmed replicate: the design names one
    (not none), and the unit column it names was found. How the units were told apart does not change what the design
    confirmed they are: donors told apart by genotype are donors, and lanes of one pool are a design whose replicate
    is none."""
    from ..design_gate import no_replicate
    if no_replicate(value(design, "units.independent_unit")):
        return False
    column = value(design, "units.unit_column")
    return not (column and any(str(column) in (f.get("columns") or []) for f in (meta or {}).get("flags") or []
                               if f.get("flag") == "columns not found"))


def same_sample_controls(unit, target, control, min_cells=10, seed=0):
    """A target's control cells from its own samples, in its mix (unit: each cell's sample; target and control:
    masks). From each sample that holds the target's cells, that sample's control cells in proportion to the target's
    cells there, at the one ratio every such sample allows (so the sample that allows least gives all its control
    cells). The target's cells in a sample with fewer than min_cells control cells are left out. Returns (the target's
    cells kept, the matched control cells, the target's cells left out)."""
    unit = np.asarray(unit).astype(str)
    target, control = np.asarray(target, bool), np.asarray(control, bool)
    samples = sorted(set(unit[target]))
    kept = [u for u in samples if (control & (unit == u)).sum() >= min_cells]
    tm = target & np.isin(unit, kept)
    matched = np.zeros(len(unit), bool)
    if not kept:
        return tm, matched, int(target.sum())
    ratio = min((control & (unit == u)).sum() / (tm & (unit == u)).sum() for u in kept)
    rng = np.random.default_rng(seed)
    for u in kept:
        idx = np.flatnonzero(control & (unit == u))
        k = min(len(idx), int(round(ratio * (tm & (unit == u)).sum())))
        matched[rng.choice(idx, k, replace=False) if k < len(idx) else idx] = True
    return tm, matched, int(target.sum() - tm.sum())


def seed_of(label):
    """A target's own seed for its draw of control cells, from its name: a call on some targets draws for each what
    the whole call draws."""
    import zlib
    return zlib.crc32(str(label).encode("utf-8"))


def _text(obs, column):
    """A cell column as text, '' where it is missing or the column is absent (categorical columns included)."""
    if column not in obs:
        return np.full(len(obs), "", dtype=object).astype(str)
    s = obs[column].astype(object)
    return s.where(s.notna(), "").astype(str).to_numpy()


def key_of(values):
    """An arm's key from its attributes' values, kept apart whatever they hold (a value with '|' or ':' stays one
    value): JSON of the list."""
    return json.dumps([str(v) for v in values])


class Screen:
    def __repr__(self):
        """What the cells hold, from pt.describe(): an agent's first look at the object prints it (the fair
        comparison's workers printed an object's address, then searched)."""
        try:
            from .. import blocks
            d = blocks.describe(self)
        except Exception:  # outside a notebook's context: the shape alone
            return f"Screen: {len(self.label)} cells x {self.adata.n_vars} genes (pt.describe() in a notebook)"
        top = ", ".join(f"{k} {v}" for k, v in list(d["labels"].items())[:12])
        cond = "; ".join(f"{k}: {', '.join(v)}" for k, v in d["conditions"].items()) or "none"
        return (f"Screen: {d['n_cells']} cells x {d['genes']} genes, {len(d['per_sample'])} samples (independent "
                f"units: {d['independent_units']}); {d['targets_with_10_cells']} targets with 10 or more cells\n"
                f"labels (label, after the confirmed cells' rule, as every block reads it; 'failed QC' for a cell "
                f"left out; the build's is label_before_qc): {top}\n"
                f"conditions: {cond}\ncell columns: {', '.join(d['cell_columns'])}\n"
                ".adata: the raw counts and the cells' columns; .lognorm: log1p of counts per 10,000. "
                "pt.describe() gives all of this as a dict; pt.help() lists the blocks.")

    def __init__(self, screen_dir, design=None, adata=None):
        from ..screen_object import load
        self.dir = screen_dir
        self.design = design or {}
        self.adata = adata if adata is not None else load(screen_dir)
        self.guides = pd.read_csv(os.path.join(screen_dir, "guides.tsv"), sep="\t")
        # a feature named as one of the library's guides stays a gene: the build's reading checks name such features,
        # and the reading retypes those that are guide counts (docs/adr/0041, step 6; the old drop is in
        # pertura_research.retired.features_named_as_guides_dropped)
        self.X = sp.csr_matrix(self.adata.X)
        self.genes = np.asarray(self.adata.var_names).astype(str)
        self.gene_index = {g: i for i, g in enumerate(self.genes)}
        obs = self.adata.obs
        self.label = obs["label"].astype(str).to_numpy()
        # the screen object's unit (a sample, lane, hashtag or object column); units.merge maps those that are one
        # biological replicate (two channels of one mouse) to it. A screen made again from this one's cells reads the
        # build's from 'capture', which this one writes
        self.capture = _text(obs, "capture") if "capture" in obs else obs["unit"].astype(str).to_numpy()
        merge = value(self.design, "units.merge") or {}
        self.unit = np.array([str(merge.get(u, u)) for u in self.capture], dtype=object).astype(str)
        self.sample = _text(obs, "sample")
        self.condition = _text(obs, "condition")
        self.guides_text = _text(obs, "guides")
        self.control_group = _text(obs, "control_group")
        # in a capture of pairs, whether a single-target cell's second guide was called ("seen", "not seen")
        self.partner = _text(obs, "partner")
        with open(os.path.join(screen_dir, "screen.json"), encoding="utf-8") as fh:
            self.meta = json.load(fh)
        self.libsize = np.asarray(self.X.sum(1)).ravel()
        # each cell's label under the other calling methods (screen_object.OTHER_CALLS), where the object has them
        self.other_calls = {name: obs[f"label_{name}"].astype(str).to_numpy()
                            for name in (self.meta.get("calls") or {}).get("others", []) if f"label_{name}" in obs}
        self.barcodes = np.asarray(self.adata.obs_names).astype(str)
        # how the build told the cell's unit: one hashtag, several, none ('' where it was not told by hashtags)
        self.unit_call = _text(obs, "unit_call")
        # quality control (the design's data.qc, pertura.quality): a cell that fails the confirmed rule, or whose unit
        # could not be told, is labelled "failed QC" with its reason, so every module and block leaves it out
        from .. import quality
        self.qc_reason, self.qc_measures = quality.failures(self, value(self.design, "data.qc"))
        self.label = np.where(self.qc_reason != "", quality.FAILED, self.label)
        # the rule's outcome among the cells' columns too, so code that reads the AnnData leaves out the cells every
        # module leaves out: qc_reason ('' for a kept cell), and label (and label_after_qc) the label the modules read;
        # label_before_qc keeps the build's. Likewise unit is the merged unit the modules count, capture the build's
        if self.adata.is_view:  # an object of its own, holding the counts the screen holds, before columns are added
            import anndata as ad
            self.adata = ad.AnnData(self.X, obs=self.adata.obs.copy(), var=self.adata.var.copy())
        self.adata.obs["capture"] = self.capture
        self.adata.obs["unit"] = self.unit
        self.adata.obs["qc_reason"] = self.qc_reason
        if "label_before_qc" not in self.adata.obs:
            self.adata.obs["label_before_qc"] = self.adata.obs["label"].astype(str).to_numpy()
        self.adata.obs["label"] = self.label
        self.adata.obs["label_after_qc"] = self.label

    def with_labels(self, labels):
        """The same screen with the given labels (a shallow copy)."""
        import copy
        s = copy.copy(self)
        s.label = np.asarray(labels).astype(str)
        return s

    def with_calls(self, name):
        """The same screen with every cell labelled by another calling method (a shallow copy: the counts, units and
        guides are shared)."""
        import copy
        if name not in self.other_calls:
            raise ValueError(f"no calls {name!r} in this screen object; it has {sorted(self.other_calls)}")
        s = copy.copy(self)
        s.label = self.other_calls[name]
        return s

    def with_genes(self, names):
        """The same screen with only these genes' counts (a shallow copy): each cell keeps its whole library size, so
        its log-normalised values are the whole screen's. For a test of each gene on its own (a finding's gene)."""
        import copy
        idx = [self.gene_index[g] for g in dict.fromkeys(map(str, names)) if g in self.gene_index]
        s = copy.copy(self)
        s.__dict__.pop("lognorm", None)
        s.adata, s.X, s.genes = self.adata[:, idx], self.X[:, idx], self.genes[idx]
        s.gene_index = {g: i for i, g in enumerate(s.genes)}
        return s

    def subset(self, cells=None, labels=None, **columns):
        """The same screen cut to some cells (docs/adr/0024, section 3): cells as a mask or as barcodes, and columns as
        values of the cells' columns (condition_tissue="tumour", or a list of values); labels replace the kept cells'
        labels ({barcode: label}, or one per kept cell in order). Everything per cell is cut alike."""
        import copy
        n = len(self.label)
        names = np.asarray(self.adata.obs_names).astype(str)
        keep = np.ones(n, bool)
        if cells is not None:
            c = np.asarray(cells)
            if c.dtype == bool:
                if len(c) != n:
                    raise ValueError(f"a mask of cells has one value per cell ({n}), not {len(c)}")
                keep &= c
            else:
                c = c.astype(str)
                unknown = c[~np.isin(c, names)]
                if len(unknown):
                    raise ValueError(f"{len(unknown)} of the {len(c)} barcodes are not cells of this screen (such as "
                                     f"{', '.join(unknown[:3])}; its cells are named like {names[0]})")
                keep &= np.isin(names, c)
        obs = self.adata.obs
        for col, want in columns.items():
            if col not in obs:
                raise ValueError(f"no cell column {col!r}; the columns are {sorted(map(str, obs.columns))}")
            want = [want] if isinstance(want, (str, int, float)) else list(want)
            keep &= np.isin(_text(obs, col), [str(w) for w in want])
        if not keep.any():
            raise ValueError("no cell is left")
        idx = np.flatnonzero(keep)
        s = copy.copy(self)
        for k in ("guide_lists", "top_guide", "lognorm", "match_key_of_cells", "arms_of_cells", "_in_arm",
                  "capture_kind", "crossed", "strata"):  # recomputed (a condition's cells are not crossed)
            s.__dict__.pop(k, None)
        s.adata = self.adata[idx]
        s.X = self.X[idx]
        for k in ("label", "capture", "unit", "sample", "condition", "guides_text", "control_group", "partner",
                  "libsize", "barcodes", "unit_call", "qc_reason"):
            setattr(s, k, getattr(self, k)[idx])
        s.qc_measures = None if self.qc_measures is None else self.qc_measures.iloc[idx].reset_index(drop=True)
        s.other_calls = {k: v[idx] for k, v in self.other_calls.items()}
        for k in ("guide_lists", "top_guide"):
            if k in self.__dict__:
                v = self.__dict__[k]
                s.__dict__[k] = [v[i] for i in idx] if isinstance(v, list) else v[idx]
        if labels is not None:
            kept = names[idx]
            if isinstance(labels, (dict, pd.Series)):
                lab = pd.Series(labels).astype(str)
                missing = ~pd.Index(kept).isin(lab.index)
                if missing.any():
                    raise ValueError(f"{int(missing.sum())} kept cells have no label (such as {kept[missing][0]})")
                s.label = lab.reindex(kept).to_numpy().astype(str)
            else:
                lab = np.asarray(labels).astype(str)
                if len(lab) != len(idx):
                    raise ValueError(f"labels: one per kept cell ({len(idx)}), not {len(lab)}")
                s.label = lab
        return s

    # ---- groups
    def targets(self, min_cells=1):
        """Labels of single perturbations with at least min_cells cells. A set delivered together ('a+b', an
        injection set) is one perturbation; a pair of separately delivered perturbations ('a|b') is not (its
        questions are E's)."""
        s = pd.Series(self.label)
        s = s[~s.isin(NOT_TARGETS) & ~s.str.contains(r"\|")]
        vc = s.value_counts()
        return [str(t) for t, n in vc.items() if n >= min_cells]

    def perturbations(self, min_cells=1):
        """Labels of single perturbations and of combinations ('a+b' sets, 'a|b' pairs) with min_cells cells."""
        s = pd.Series(self.label)
        vc = s[~s.isin(NOT_TARGETS)].value_counts()
        return [str(t) for t, n in vc.items() if n >= min_cells]

    @staticmethod
    def members(label):
        """The targets of a label: itself, or the members of a combination ('a+b', 'a|b')."""
        import re
        return [m for m in re.split(r"[+|]", label) if m]

    def cells_of(self, target):
        return self.label == target

    def single(self):
        """Cells labelled with one perturbation (a set delivered together counts as one; a pair does not)."""
        lab = pd.Series(self.label)
        return (~lab.isin(NOT_TARGETS) & ~lab.str.contains(r"\|")).to_numpy()

    def reference(self, kind="control_guides", target=None):
        """The cells a target is compared with: control-guide cells; every other target's cells; or cells with no
        guide (they include perturbed cells whose guide was not captured). With a target and the design's
        perturbation.reference_matches (ADR 0026, D3), only the cells whose guides share those attributes with the
        target's (a CRISPRa target against the activation arm's controls)."""
        if kind == "control_guides":
            m = self.label == "control"
        elif kind == "other_targets":
            m = self.single()
            m = m & (self.label != target) if target is not None else m
        elif kind == "no_guide":
            return self.label == "none"
        else:
            raise ValueError(f"reference is one of {REFERENCES}")
        if target is not None and self.reference_matches:
            m = m & self.in_arm(self.arm_of(target))
        return m

    def arm_of(self, label):
        """The arm a label is compared within: the values of the reference-matching attributes its guides share ('' in a
        design that matches nothing). A label whose guides differ in a matched attribute has no one arm: it is refused,
        and the design check proposes to split it (bug register, BR26, option A)."""
        if not self.reference_matches:
            return ""
        keys = self.label_keys(label)
        if len(keys) > 1:
            raise ValueError(f"{label}: its guides differ in {', '.join(self.reference_matches)} "
                             f"({'; '.join(sorted(keys))}), so no one arm's control cells are its reference; split it "
                             "into one target per value at the design check (perturbation.target_map)")
        return next(iter(keys)) if keys else self.match_key(label)

    def label_keys(self, label):
        """The arm keys of a label: of its own guides its cells carry (else of its own guides, else of the guides its
        cells carry); a combination's, its members' together."""
        members = self.members(label)
        if len(members) > 1:
            return set().union(*(self.label_keys(m) for m in members))
        own = set(self.guides.loc[self.guides["target"].astype(str) == str(label), "guide"].astype(str))
        seen = set(self.top_guide[self.label == label]) - {""}
        guides = (own & seen) or own or seen
        maps = [self.attribute(a) for a in self.reference_matches]
        return {key_of([m.get(g, "") for m in maps]) for g in guides}

    def own_reference(self, label, kind="control_guides"):
        """The reference a label is compared with: its arm's control cells; a combination whose members span arms (a
        pair of a CRISPRi and a CRISPRa guide) every control cell of the screen, as the user decided (bug register,
        BR34); a single target whose guides differ in a matched attribute is refused (BR26)."""
        try:
            return self.reference(kind, target=label)
        except ValueError:
            if len(self.members(label)) < 2:
                raise
            ref = self.reference(kind)
            return ref & (self.label != label) if kind == "other_targets" else ref

    def arm_or_every(self, label):
        """A label's arm, or 'every arm' for a combination whose members span arms."""
        try:
            return self.arm_of(label)
        except ValueError:
            if len(self.members(label)) < 2:
                raise
            return "every arm"

    def arm_words(self, label):
        """A label's arm in words: its matched attributes' values ('CRISPRi'), or 'every arm' for a combination whose
        members span arms; '' in a design that matches nothing."""
        a = self.arm_or_every(label)
        return ", ".join(map(str, json.loads(a))) if str(a).startswith("[") else a

    def comparison(self, target, kind="control_guides", unit=None, condition=None, state=None, every_arm=False):
        """The one comparison of a target (docs/build_goal_session6_fixes.md, item 24.1): its cells and its reference
        within a unit, a condition and a state (each optional, a value or a mask), its arm, and the counts. every_arm:
        every reference cell whatever its arm, said so (a pair of two modes, a figure of every control cell)."""
        where = np.ones(len(self.label), bool)
        for given, values in ((unit, self.unit), (condition, self.condition)):
            if given is not None:
                where &= values == given
        if state is not None:
            where &= np.asarray(state, bool)
        cells = self.cells_of(target) & where
        ref = (self.reference(kind) if every_arm else self.own_reference(target, kind)) & where & ~cells
        return {"target": target, "cells": cells, "reference": ref,
                "arm": "every arm" if every_arm else self.arm_or_every(target) if kind != "no_guide" else "",
                "counts": {"cells": int(cells.sum()), "reference_cells": int(ref.sum())}}

    # ---- who a target is compared with: one place for the design's facts on matching (item 9)
    @cached_property
    def crossed(self):
        """Whether a unit holds cells of two or more conditions (four hashtag conditions in each lane). A target whose
        cells lean to one condition would read that condition's difference as its own against the unit's controls."""
        told = self.condition != ""
        if not told.any():
            return False
        held = pd.crosstab(self.unit[told], self.condition[told]) >= CROSSED_CELLS  # the conditions each unit holds
        return bool((held.sum(axis=1) > 1).any())

    @cached_property
    def strata(self):
        """Each cell's group that control cells are matched within: its unit and its condition where the screen is
        crossed, its unit otherwise (the same array, so a screen whose units hold one condition each draws as before)."""
        if not self.crossed:
            return self.unit
        return np.array([f"{u}|{c}" for u, c in zip(self.unit, self.condition)], dtype=object).astype(str)

    @property
    def matching_words(self):
        """The control cells a drawn comparison takes, in a reply's words."""
        return ("the control cells of its own samples and conditions, in its mix" if self.crossed
                else "the control cells of its own samples, in its mix")

    @property
    def model_terms(self):
        """The cell columns a model on cells adjusts for beside the group: the unit, and the condition where the screen
        is crossed (a model keeps every control cell and takes the conditions as a term)."""
        return ["unit", "condition"] if self.crossed else ["unit"]

    def matched(self, target, control, min_cells=10, seed=0):
        """A target's control cells drawn within its strata, in its mix (same_sample_controls on Screen.strata):
        (the target's cells kept, the matched control cells, the target's cells left out)."""
        return same_sample_controls(self.strata, target, control, min_cells, seed)

    def baseline(self, values, cells, reference, min_reference=1):
        """The reference's mean of values (cells x features) for some cells: in a crossed screen, each stratum's
        reference mean weighted by the cells there, over the strata with at least min_reference reference cells (the
        cells in the others are left out); otherwise the reference's own mean. Returns (the mean, the cells kept)."""
        cells, reference = np.asarray(cells, bool), np.asarray(reference, bool)
        if not self.crossed:
            return np.asarray(values[np.flatnonzero(reference)].mean(0)).ravel(), cells
        parts = [(cells & (self.strata == s), reference & (self.strata == s)) for s in sorted(set(self.strata[cells]))]
        parts = [(a, b) for a, b in parts if b.sum() >= min_reference]
        kept = np.logical_or.reduce([a for a, _ in parts]) if parts else np.zeros(len(cells), bool)
        if not kept.any():
            return None, kept
        mean = sum(a.sum() * np.asarray(values[np.flatnonzero(b)].mean(0)).ravel() for a, b in parts) / kept.sum()
        return mean, kept

    # ---- the guides' attributes (ADR 0026): from the screen object's guide table
    @cached_property
    def reference_matches(self):
        v = value(self.design, "perturbation.reference_matches") or []
        return [str(a) for a in (v if isinstance(v, list) else [v]) if str(a) in self.guides.columns]

    def attribute(self, attr):
        """{guide: value} of one attribute of the guide table ('' where a guide has none)."""
        if attr not in self.guides.columns:
            return {}
        return dict(zip(self.guides["guide"].astype(str), self.guides[attr].fillna("").astype(str)))

    def match_key(self, label):
        """The values of the reference-matching attributes for a label: those of the guide its cells carry most."""
        g = self.label_guide(label)
        return key_of([self.attribute(a).get(g, "") for a in self.reference_matches])

    @cached_property
    def match_key_of_cells(self):
        """Per cell, the values of the reference-matching attributes of the guide its label rests on."""
        maps = [self.attribute(a) for a in self.reference_matches]
        return np.array([key_of([m.get(g, "") for m in maps]) for g in self.top_guide], dtype=object)

    @cached_property
    def arms_of_cells(self):
        """Per cell, the arms it is compared within: its label's guide's; a control cell that carries control guides
        of several arms (a non-targeting guide of each mode, as captures of guide pairs deliver) is the reference of
        each of them, as dual-guide screens take their all-control constructs as the control of every single
        perturbation (Boettcher et al. 2018; Horlbeck et al. 2018; Norman et al. 2019)."""
        keys = self.match_key_of_cells
        if not self.reference_matches:
            return [frozenset([k]) for k in keys]
        maps = [self.attribute(a) for a in self.reference_matches]
        kind = self.guides["kind"].astype(str) if "kind" in self.guides else pd.Series("targeting", self.guides.index)
        control = set(self.guides.loc[~kind.isin(["targeting", "unknown"]), "guide"].astype(str))
        out = []
        for lab, k, cell in zip(self.label, keys, self.guide_lists):
            more = {key_of([m.get(g, "") for m in maps]) for g, _ in cell if g in control} if lab == "control" else ()
            out.append(frozenset({k, *more}))
        return out

    def in_arm(self, key):
        """The cells compared within the arm `key` (arms_of_cells); every cell for 'every arm'."""
        if key == "every arm":
            return np.ones(len(self.label), bool)
        cache = self.__dict__.setdefault("_in_arm", {})
        if key not in cache:
            cache[key] = np.array([key in s for s in self.arms_of_cells], dtype=bool)
        return cache[key]

    # ---- memberships (ADR 0026, D2): a cell belongs to every target whose guide it carries (high MOI)
    def carriers(self, target):
        """Cells that carry a guide of the target, from each cell's called guides."""
        own = set(self.guides.loc[self.guides["target"].astype(str) == str(target), "guide"].astype(str))
        return np.array([any(g in own for g, _ in cell) for cell in self.guide_lists], dtype=bool)

    def non_carriers(self, target):
        """Cells with a called guide, none of them the target's: a membership's reference."""
        has = np.array([bool(cell) for cell in self.guide_lists], dtype=bool)
        return has & ~self.carriers(target)

    def units(self):
        """The units, without the cells whose unit the build could not tell (UNASSIGNED: no clear hashtag, no value in
        the unit column), which are no replicate."""
        return sorted(u for u in set(self.unit) if not str(u).endswith(UNASSIGNED))

    def unassigned(self):
        """Per cell, whether the build could not tell its unit."""
        return np.char.endswith(np.asarray(self.unit).astype(str), UNASSIGNED)

    @cached_property
    def capture_kind(self):
        """Per cell, its kind of capture: the delivery (perturbation.delivery: singles or pairs), the batch
        (units.batches) and the design's other factor that groups the captures (units.capture_factor), joined by
        ' · '; '' where the design gives none of them. A profile is pooled only within one kind
        (pertura.steps.profiles.pooled_within_kinds)."""
        from ..design_gate import delivery_of

        def by(key):
            v = value(self.design, key)
            if not isinstance(v, dict):
                return {}
            return {str(k): x for k, x in (v.get("by_unit") or v.get("by_sample") or {}).items()}
        batch, factor = by("units.batches"), by("units.capture_factor")
        named = value(self.design, "units.capture_factor")
        name = str(named.get("name") or "") if isinstance(named, dict) else ""
        seen, out = {}, []
        for c, u, s in zip(self.capture, self.unit, self.sample):
            if (c, u, s) not in seen:
                d = delivery_of(self.design, unit=c, sample=s) or delivery_of(self.design, unit=u)
                b = next((batch[x] for x in (c, u, s) if x in batch), None)
                f = next((factor[x] for x in (c, u, s) if x in factor), None)
                parts = [d, None if b in (None, "") else str(b) if str(b).lower().startswith("batch")
                         else f"batch {b}", None if f in (None, "") else f"{name} {f}".strip()]
                seen[(c, u, s)] = " · ".join(p for p in parts if p)
            out.append(seen[(c, u, s)])
        return np.array(out, dtype=object).astype(str)

    def kind_of_units(self):
        """{unit: its kind of capture}. A unit whose cells come from captures of different kinds (two captures pooled
        into one donor) is of a kind naming them all ('mixed: a; b'), so it is pooled with no unit of one kind; a unit
        the design does not place, beside units it does, is of the kind 'not recorded'."""
        t = pd.DataFrame({"unit": self.unit, "kind": self.capture_kind})
        kinds = {str(u): sorted(set(map(str, k))) for u, k in t.groupby("unit")["kind"]}
        named = any(ks != [""] for ks in kinds.values())
        out = {}
        for u, ks in kinds.items():
            ks = [k or "not recorded" for k in ks] if named else ks
            out[u] = ks[0] if len(ks) == 1 else "mixed: " + "; ".join(ks)
        return out

    def independent_units(self):
        """Whether the screen object's units are the confirmed biological replicates (units_are_replicates)."""
        return units_are_replicates(self.design, self.meta)

    # ---- guides
    @cached_property
    def guide_lists(self):
        """Per cell, [(guide, umis)] from the screen object's guides column."""
        out = []
        for text in self.guides_text:
            cell = []
            for part in str(text).split(";"):
                if ":" in part:
                    g, u = part.rsplit(":", 1)
                    try:
                        cell.append((g, int(float(u))))
                    except ValueError:
                        cell.append((g, 1))
            out.append(cell)
        return out

    @cached_property
    def top_guide(self):
        """Per cell, the guide its label rests on, for the analyses that read a cell's guide: of the guides that carry
        the screen object's label for the cell (its target's guides; a control cell's control group, else its control
        guides), the one with the most UMIs, so a cell labelled by its target whose control guide has more UMIs counts
        under its target's guide; for a cell of no single label (a pair, none, ambiguous), its guide with the most
        UMIs. A tie goes to the guides' order, not to the order they are listed in; '' without a guide."""
        g = self.guides.astype({"guide": str, "target": str})
        target_of = dict(zip(g["guide"], g["target"]))
        control = set(g.loc[~g["kind"].isin(["targeting", "unknown"]), "guide"]) if "kind" in g else set()
        labels = (self.adata.obs["label_before_qc"].astype(str).to_numpy() if "label_before_qc" in self.adata.obs
                  else self.label)
        out = []
        for lab, group, cell in zip(labels, self.control_group, self.guide_lists):
            if lab == "control":
                # its control group is the control guides' target in the map
                own = ([x for x in cell if group and target_of.get(x[0], x[0]) == group]
                       or [x for x in cell if x[0] in control])
            else:
                own = [x for x in cell if target_of.get(x[0]) == lab]
            pick = own or cell
            out.append(min(pick, key=lambda x: (-x[1], x[0]))[0] if pick else "")
        return np.array(out, dtype=object)

    # ---- expression
    def gene(self, name):
        j = self.gene_index.get(name)
        return None if j is None else self._columns()[:, j].toarray().ravel()

    def _columns(self):
        """The counts column-compressed, made once for the matrix the screen holds: a gene's values are read from its
        own column, not from a scan of every cell's entries. A copy that holds another matrix makes its own."""
        held = self.__dict__.get("_by_column")
        if held is None or held[0] is not self.X:
            held = (self.X, sp.csc_matrix(self.X))
            self.__dict__["_by_column"] = held
        return held[1]

    def pseudobulk(self, mask, min_cells=10):
        """unit -> (counts summed over the unit's masked cells, cells) for units with min_cells cells."""
        out = {}
        for u in self.units():
            m = mask & (self.unit == u)
            n = int(m.sum())
            if n >= min_cells:
                out[u] = (np.asarray(self.X[np.flatnonzero(m)].sum(0)).ravel(), n)
        return out

    @cached_property
    def lognorm(self):
        """log1p of counts per 10,000, every cell (CSR)."""
        Y = sp.csr_matrix(sp.diags(1e4 / np.maximum(self.libsize, 1)) @ self.X)
        Y.data = np.log1p(Y.data)
        return Y

    def detected(self, mask, fraction):
        """Genes detected in at least `fraction` of the masked cells."""
        X = self.X[np.flatnonzero(mask)]
        return np.asarray((X > 0).sum(0)).ravel() >= fraction * max(X.shape[0], 1)

    def label_guide(self, label):
        """The guide a label's cells carry most ('' when none does); of guides carried by as many cells, the first
        in their order, whatever the cells' order."""
        g = pd.Series(self.top_guide[self.label == label])
        g = g[g != ""]
        return str(most_common_value(g)) if len(g) else ""

    def own_gene_of(self, target):
        """The gene a target's guides act on: the target's name when it is a gene of the matrix, else the target of the
        guide its cells carry most (a label that names a mode or an element too, such as 'GATA1-i')."""
        if target in self.gene_index:
            return target
        g = self.label_guide(target)
        gene = self.attribute("gene").get(g)  # the guide table's gene, where the design gives it (ADR 0026)
        if gene and gene in self.gene_index:
            return gene
        own = dict(zip(self.guides["guide"].astype(str), self.guides["target"].astype(str))).get(g)
        return own if own in self.gene_index else None
