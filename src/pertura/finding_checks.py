"""A recorded finding's checks (docs/adr/0025, section 3): the field's checks, chosen from the confirmed design
(design_fit.checks) and run when the finding is recorded, each reported in its own terms with its n. A check without
the data for it says why and never counts against the finding. There is no verdict: the critic lists a finding whose
checks disagree (its guides disagree, or a re-test gives the other direction) and the report states it.

A finding is a target, a feature and a direction, of a kind:
- gene: the feature is a gene; the lead is C.genes' test for the target (design_fit.gene_lead), or the test the
  finding came from;
- gene_set: the feature names a gene set (genes); its score is scanpy's score_genes, tested as C.programs tests it;
- score: the feature names a per-cell score the agent's notebook saved (pt.save_table: barcode and score), tested as a
  gene set's score is;
- state: the feature names a state of a labelling (state_table: barcode, state), tested as C.states_and_composition
  tests it (Fisher's exact against the control cells of the same samples);
- interaction, custom: not checked here (E.combinations' own agreement between units; the agent's own code).

A claim on several targets (targets) is checked target by target, each with the checks above, and each check recorded
with its target.

The checks, each the lead's own test on other cells or by the design's other test. For a gene, each re-test tests
the finding's gene alone (its q is its p), where the test allows it: the tests on cells and Seurat's; only the lead
(its changed genes) and the calibration need every gene:
- lead: the finding under its lead test;
- guides_agree: the lead on each guide's cells; they agree when at least MIN_GUIDES go the finding's way and none is
  significant the other way (one weak guide does not veto; each shown with its own-gene change);
- tests: each of the field's other tests the design admits for the kind (design_fit.field_tests) on the finding's
  gene, score or state, each beside the same test on groups of control guides (how many groups it calls, and the
  95th percentile of their effects); the agent or the user may report the finding by another of them, and the
  report names the tests tried;
- calibration: the lead's test on groups of control guides, against the rest of the control cells (for a gene, its
  changed genes against the groups' discoveries; for a gene set or a state, its effect against the groups' effects);
- half: the lead on each half of the units (a target with one guide, with design_fit.HALF_UNITS units);
- (other_test and depth_matched: in records written before; depth matching stays in the notebook, pt.match_depth);
- deposit_calls: the lead on the deposit's own guide calls (a finding on the agent's calling rule).

    python -m pertura.finding_checks run --record <folder> --finding <id> --spec <spec.json> --screen <screen object>
        --design <design.yaml> --out <folder>
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

from . import design_fit as fit

SIGNIFICANT = 0.05  # one test (a score, a state's share): its p
CHECK_NAMES = ("lead", "guides_agree", "tests", "other_test", "calibration", "depth_matched", "half", "deposit_calls")
WORDS = {"lead": "the lead test", "guides_agree": "each guide alone", "tests": "the field's other tests",
         "other_test": "the other test",
         "calibration": "control-guide groups through the same test", "depth_matched": "depth matched",
         "half": "each half of the data", "deposit_calls": "the deposit's own guide calls"}


def _q_level():  # a gene's level: C.genes' own
    from .question_modules.c import GENES_Q
    return GENES_Q


# ---- one measurement: the finding's feature in some target cells against the reference, by one test --------------
class Measure:
    """The finding's feature measured on a screen: measure(screen, targets, method) gives, per target label, the
    effect, p and q (q: within the target's genes for a gene; p for one score or state), the cells and the test.
    alone: a gene finding's gene tested by itself (q over that gene, so its p), for a re-test."""

    def __init__(self, spec, workdir):
        import itertools
        self.spec, self.work, self.kind = spec, workdir, spec["kind"]
        self.reference = spec.get("reference") or "control_guides"
        self._count = itertools.count(1)  # each measurement its own folder, also when checks run side by side

    def __call__(self, screen, targets, method, alone=False):
        n = next(self._count)
        if self.kind == "gene":
            return self._gene(screen, targets, method, n, alone)
        if self.kind in ("gene_set", "score"):
            return self._score(screen, targets, method)
        return self._state(screen, targets, method)

    def _gene(self, screen, targets, method, n=0, alone=False):
        from .question_modules import c
        out, run = {}, []
        for t in targets:
            tm, rm = fit.sides(screen, t, self.reference)
            if tm.sum() < fit.MIN_CELLS or rm.sum() < fit.MIN_CELLS:
                out[t] = {"effect": np.nan, "p": np.nan, "q": np.nan, "test": method, "cells": int(tm.sum()),
                          "why": f"fewer than {fit.MIN_CELLS} cells on a side ({int(tm.sum())} and {int(rm.sum())})"}
            else:
                run.append(t)
        if not run:
            return out
        leads = (c.leads_of(screen, run, self.reference) if method == "auto" else
                 {t: {"method": method} for t in run})
        g = self.spec["feature"]
        # the targets' genes through the lead are the same for every gene claim on them: kept, as the calibration is
        path = None if alone else _kept_path(screen, self.spec, "lead\x1e" + json.dumps(
            [sorted(leads.items()), self.reference], sort_keys=True, default=str))
        table = _read_kept(path)
        if table is None:
            table, _ = c.genes_by_leads(screen, leads, self.reference, os.path.join(self.work, f"m{n}"),
                                        genes=[g] if alone else None)
            _keep(path, table)
        for t in run:
            row = table[(table["target"] == t) & (table["gene"] == g)]
            n_changed = None if alone else int(((table["target"] == t) & (table["q"] < _q_level())).sum())
            tm, rm = fit.sides(screen, t, self.reference)
            base = {"test": leads[t]["method"], "cells": int(tm.sum()), "genes_changed": n_changed,
                    **({"gene_alone": True} if alone else {}), **_units(screen, tm, rm, leads[t]["method"])}
            if len(row):
                out[t] = {**base, "effect": float(row["effect"].iat[0]), "p": float(row["p"].iat[0]),
                          "q": float(row["q"].iat[0])}
            else:
                out[t] = {**base, "effect": np.nan, "p": np.nan, "q": np.nan,
                          "why": "not tested by this test here (too few cells or samples, or the gene filtered out)"}
        return out

    def _score(self, screen, targets, method):
        from .question_modules import c
        if self.kind == "score":  # the agent's own score, read by barcode on these cells
            score = self.spec["values"].reindex(np.asarray(screen.adata.obs_names).astype(str)).to_numpy(float)
            if not np.isfinite(score).any():
                return {t: {"effect": np.nan, "p": np.nan, "q": np.nan, "test": method,
                            "why": "the score's barcodes are none of these cells"} for t in targets}
        else:
            name = self.spec["feature"]
            per_cell, used, _ = c.scanpy_scores(screen, {name: self.spec["genes"]}, by_columns=False)  # one set
            if name not in per_cell:
                return {t: {"effect": np.nan, "p": np.nan, "q": np.nan, "test": method,
                            "why": "the set has too few detected genes"} for t in targets}
            score = np.asarray(per_cell[name])
        scored = np.isfinite(score)  # a cell the score leaves out is on neither side
        out = {}
        for t in targets:
            tm, rm = fit.sides(screen, t, self.reference)
            tm, rm = tm & scored, rm & scored
            if tm.sum() < fit.MIN_CELLS or rm.sum() < fit.MIN_CELLS:
                out[t] = {"effect": np.nan, "p": np.nan, "q": np.nan, "test": method, "cells": int(tm.sum()),
                          "why": f"fewer than {fit.MIN_CELLS} cells on a side"}
                continue
            m = fit.score_lead(screen, t, self.reference)["method"] if method == "auto" else method
            if m == "linear_model" and len(set(screen.unit[tm | rm])) < 2 and not screen.crossed:
                out[t] = {"effect": np.nan, "p": np.nan, "q": np.nan, "test": m, "cells": int(tm.sum()),
                          "why": "one sample: no sample term"}
                continue
            effect, p, test = c.score_test(screen.unit, score, tm, rm, m,
                                           screen.condition if screen.crossed else None, screen.strata)
            out[t] = {"effect": effect, "p": p, "q": p, "test": m, "cells": int(tm.sum()),
                      "reference_cells": int(rm.sum()), **_units(screen, tm, rm, m)}
        return out

    def _state(self, screen, targets, method="fisher_same_samples"):
        """A state's share by the lead's backend: Fisher's exact test against the control cells of the same samples
        (fisher_same_samples), propeller or the Cochran-Mantel-Haenszel test (pertura.steps.composition)."""
        from .question_modules import c
        from .steps import composition
        method = "fisher_same_samples" if method in (None, "auto", "fisher") else method
        lab = np.asarray(self.spec["state_labels"]).astype(str)
        state = str(self.spec.get("state") or self.spec["feature"])
        states = sorted(set(lab) - {""})
        if state not in states:
            names = self.spec.get("state_names") or {}
            why = (f"no state {state!r} in the labelling; its states are "
                   f"{[f'{s} ({names[s]})' if s in names else s for s in states[:20]]}")
            return {t: {"effect": np.nan, "p": np.nan, "q": np.nan, "test": method, "why": why} for t in targets}
        out = {}
        for t in targets:
            tm, rm = fit.sides(screen, t, self.reference)
            tm, rm = tm & (lab != ""), rm & (lab != "")  # cells the labelling leaves out are on neither side
            if method == "fisher_same_samples":
                r = c.composition_same_samples(screen.unit, lab, tm, rm, state, fit.MIN_CELLS, strata=screen.strata)
                out[t] = {"effect": r["mean_difference"], "p": r["p"], "q": r["p"], "test": method,
                          "cells": r["cells"], "reference_cells": r["reference_cells"],
                          **_units(screen, tm, rm, method)}
                if not np.isfinite(r["p"]):
                    out[t]["why"] = r["test"]
                continue
            n = next(self._count)
            per, _, calls = composition.compare(screen.with_labels(np.where(tm, t, np.where(rm, screen.label,
                                                                                            "excluded"))),
                                                [t], self.reference, np.where(lab == "", "nan", lab), method,
                                                os.path.join(self.work, f"m{n}"))
            row = per[(per["target"] == t) & (per["state"].astype(str) == state)]
            if len(row):
                x = row.iloc[0]
                out[t] = {"effect": float(x["mean_difference"]), "p": float(x["p"]), "q": float(x["p"]),
                          "test": method, "cells": int(x["cells"]), "reference_cells": int(x["reference_cells"]),
                          **_units(screen, tm, rm, method)}
            else:
                why = (calls[0].get("not_fitted_why") or {}).get(t) if calls else None
                out[t] = {"effect": np.nan, "p": np.nan, "q": np.nan, "test": method,
                          "why": why or f"{method} gave no row for this state"}
        return out


def _units(screen, target_mask, reference_mask, test):
    """The units a test used: the target's cells in each unit, and for a test by replicate the units it took as its
    replicates (those holding both sides)."""
    held = pd.Series(np.asarray(screen.unit)[np.asarray(target_mask, bool)]).astype(str).value_counts().sort_index()
    out = {"units": {u: int(n) for u, n in held.items()}}
    if test == "edger_ql":
        out["replicates"] = fit.shared_units(screen, target_mask, reference_mask)
    return out


def _level(kind):
    return _q_level() if kind == "gene" else SIGNIFICANT


def outcome(r, direction, level):
    """One re-test against the finding's direction: agrees (its way, significant), its way but not significant,
    the other way (significant), the other way but not significant, or not run."""
    if r is None or not np.isfinite(r.get("effect", np.nan)) or not np.isfinite(r.get("q", np.nan)):
        return "not run"
    if r["effect"] == 0:
        return "no difference"
    same = np.sign(r["effect"]) == np.sign(direction)
    sig = r["q"] < level
    return ("agrees" if sig else "its way, not significant") if same else (
        "other direction" if sig else "other way, not significant")


def why_not(r):
    """Why a measurement gave no result."""
    if r is None:
        return "no result"
    if r.get("why"):
        return r["why"]
    if np.isfinite(r.get("effect", np.nan)) and not np.isfinite(r.get("q", np.nan)):
        return "the test gave no p-value"
    return "no result"


def parts_outcome(parts, direction, level):
    """Several parts (guides, halves): they agree when at least MIN_GUIDES are significant the finding's way and none
    the other way; partly when at least MIN_GUIDES go its way but fewer are significant, none against; they disagree
    when one is significant the other way or fewer than MIN_GUIDES go its way. One weak part the other way does not
    veto."""
    runs = {k: v for k, v in parts.items() if outcome(v, direction, level) != "not run"}
    if len(runs) < fit.MIN_GUIDES:
        return "not run"
    said = [outcome(v, direction, level) for v in runs.values()]
    ways = sum(np.sign(v["effect"]) == np.sign(direction) for v in runs.values())
    if "other direction" in said or ways < fit.MIN_GUIDES:
        return "disagree"
    return "agree" if said.count("agrees") >= fit.MIN_GUIDES else "partly"


def parts_counts(parts, direction, level):
    """The parts' outcomes counted, in words: 'k of n significant its way, m its way not significant, ...'."""
    said = [outcome(v, direction, level) for v in parts.values()]
    n = len(said)
    order = ("agrees", "its way, not significant", "other way, not significant", "other direction", "no difference",
             "not run")
    words = {"agrees": "significant its way", "its way, not significant": "its way, not significant",
             "other way, not significant": "the other way, not significant",
             "other direction": "significant the other way", "no difference": "no difference",
             "not run": "not run"}
    counts = [(said.count(o), words[o]) for o in order if said.count(o)]
    return ", ".join(f"{k} of {n} {w}" if i == 0 else f"{k} {w}" for i, (k, w) in enumerate(counts))


def own_gene_change(screen, target, cells, reference="control_guides"):
    """The target's own gene in some of its cells against the reference: the log2 fold change of pooled counts per
    million, B.efficacy's formula (steps.on_target; the knockdown a guide is shown with), or None."""
    from .steps import on_target
    own = screen.own_gene_of(target)
    if own is None or screen.gene(own) is None:
        return None
    rm = screen.reference(reference, target=target) & ~screen.cells_of(target)
    if screen.crossed:  # the control cells drawn within the cells' samples and conditions, in their mix
        from .question_modules.data import seed_of
        cells, rm, _ = screen.matched(cells, rm, 10, seed_of(target))
    return on_target.change(screen, own, cells, rm, "pooled_cpm")


def _guide_screen(screen, target, guide):
    """The screen with one guide's cells of the target as the target, the target's other cells left out (so that
    against the other targets, its sibling guides are not part of the reference)."""
    tm = screen.cells_of(target)
    lab = np.where(tm & (screen.top_guide == guide), guide, np.where(tm, "excluded", screen.label))
    return screen.with_labels(lab)


def _half_screen(screen, mask):
    """The screen cut to one half: every cell outside it left out, the target's and the reference's alike."""
    return screen.with_labels(np.where(mask, screen.label, "excluded"))


def run_checks(screen, spec, workdir, deposit=None, seed=0):
    """The finding's lead and its checks, each in its own terms. Returns (lead, [check results])."""
    kind, target, direction = spec["kind"], spec["target"], spec["direction"]
    reference = spec.get("reference") or "control_guides"
    planned = fit.checks(kind, screen, target, reference, spec.get("lead"), calling_rule=deposit is not None)
    if kind in ("interaction", "custom"):
        return None, [{"check": "all", "outcome": "not run", "why": planned[0]["not_run"]}]
    if spec.get("only"):  # the checks asked for (the lead always runs): a finding's calibration alone, for example
        planned = [c for c in planned if c["check"] in spec["only"]]
    measure, level = Measure(spec, workdir), _level(kind)
    lead_used = spec.get("lead") or fit.lead(kind, screen, target, reference)
    method = lead_used["method"]
    started = time.time()
    lead = {**measure(screen, [target], method)[target], "reason": lead_used.get("reason", ""),
            "seconds": round(time.time() - started, 1)}  # each check's time, to see where a finding's checks go
    lead["outcome"] = outcome(lead, direction, level)
    # a part (a guide, a half) by the design's test for it: pseudobulk needs its replicates, which a part may lack
    part_method = "auto" if method == "edger_ql" else method
    if lead["outcome"] == "not run":
        lead["why"] = why_not(lead)
    other = next((c for c in planned if c["check"] == "other_test" and not c.get("not_run")), None)

    def one(c):
        if c.get("not_run"):
            return {"check": c["check"], "outcome": "not run", "why": c["not_run"]}
        started = time.time()
        try:  # a check that fails says so; it never stops the others or the record
            out = _one_check(c, screen, spec, measure, method, part_method, lead, other, deposit, workdir, seed,
                             level)
        except Exception as e:
            out = {"check": c["check"], "outcome": "not run", "why": f"it failed: {type(e).__name__}: {e}"[:500]}
        return {**out, "seconds": round(time.time() - started, 1)}

    # the checks are independent: side by side, in the order planned (R processes stay within c.R_SLOTS)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max(1, len(planned))) as ex:
        out = list(ex.map(one, planned))
    return lead, out


def _single(name, r, direction, level, **more):
    o = outcome(r, direction, level)
    return {"check": name, **more, **r, "outcome": o, **({"why": why_not(r)} if o == "not run" else {})}


def _parts(name, per, direction, level, word):
    for v in per.values():
        v["outcome"] = outcome(v, direction, level)
        if v["outcome"] == "not run":
            v["why"] = why_not(v)
    o = parts_outcome(per, direction, level)
    out = {"check": name, "outcome": o, "parts": per}
    if o == "not run":
        out["why"] = (f"fewer than {fit.MIN_GUIDES} {word} gave a result: "
                      + "; ".join(f"{k}: {v.get('why') or v['outcome']}" for k, v in per.items()))
    return out


def side_by_side(fn, items):
    """fn on each item at the same time, in threads, the results in the items' order: a check's parts (its guides,
    its halves) are independent measurements, and each R process among them holds one of c.R_SLOTS."""
    from concurrent.futures import ThreadPoolExecutor
    items = list(items)
    if len(items) < 2:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(len(items)) as ex:
        return list(ex.map(fn, items))


def _one_check(c, screen, spec, measure, method, part_method, lead, other, deposit, workdir, seed, level):
    from . import blocks
    name, target, direction = c["check"], spec["target"], spec["direction"]
    reference = spec.get("reference") or "control_guides"
    if name == "guides_agree":
        def guide(g):
            r = measure(_guide_screen(screen, target, g), [g], part_method, alone=True)[g]
            r["own_gene"] = own_gene_change(screen, target, screen.cells_of(target) & (screen.top_guide == g),
                                            reference)
            return r
        per = dict(zip(c["guides"], side_by_side(guide, c["guides"])))
        return _parts(name, per, direction, level, "guides")
    if name == "tests":
        return _tests(c, screen, spec, measure, level, seed)
    if name == "other_test":
        return _single(name, measure(screen, [target], c["method"], alone=True)[target], direction, level,
                       method=c["method"], label=c["label"])
    if name == "calibration":
        if spec["kind"] == "gene":
            return _calibration(screen, spec, method, lead, workdir, seed, other)
        return _calibrate_effect(screen, spec, measure, method, lead, seed)
    if name == "depth_matched":
        tm, rm = fit.sides(screen, target, reference)
        pair = screen.subset(cells=tm | rm)
        pt_, pr = pair.cells_of(target), ~pair.cells_of(target)
        dt, dr = np.median(pair.libsize[pt_]), np.median(pair.libsize[pr])
        deep, factor = (pt_, dr / dt) if dt > dr else (pr, dt / dr)
        thin = blocks.thinned(pair, deep, factor, np.random.default_rng(seed))
        return _single(name, measure(thin, [target], method, alone=True)[target], direction, level,
                       depth_ratio=c["ratio"], factor=round(float(min(1.0, factor)), 3))
    if name == "half":
        units = fit.replicates(screen, target, reference)["shared"]
        units = units if len(units) >= fit.HALF_UNITS else []  # halves of the units, or of the cells
        halves = side_by_side(lambda h: measure(_half_screen(screen, fit.halves(screen, h, units, seed)), [target],
                                                part_method, alone=True)[target], (1, 2))
        per = {f"half {h}": r for h, r in zip((1, 2), halves)}
        return _parts(name, per, direction, level, "halves")
    if name == "deposit_calls":
        if target not in set(deposit.label):
            return {"check": name, "outcome": "not run", "why": "the deposit's calls have no such target"}
        return _single(name, measure(deposit, [target], method, alone=True)[target], direction, level)
    return {"check": name, "outcome": "not run", "why": "no such check"}


def _tests(c, screen, spec, measure, level, seed):
    """The field's other tests on the finding's feature alone, on its target: the outcome is agree (each significant
    its way), partly (none against) or other direction (one significant the other way). The control-guide groups are
    the calibration check's, against the lead test (2026-11-18: no published screen runs each test on them)."""
    target, direction = spec["target"], spec["direction"]
    rows = []
    for m in c["methods"]:
        try:
            r = measure(screen, [target], m["method"], alone=True)[target]
        except Exception as e:  # a test that cannot run here says why, and the others still run
            r = {"effect": np.nan, "p": np.nan, "q": np.nan, "why": f"it failed: {type(e).__name__}: {e}"[:300]}
        row = {"method": m["method"], "label": m["label"], **{k: r.get(k) for k in ("effect", "p", "q", "cells",
                                                                                   "reference_cells", "why")},
               "outcome": outcome(r, direction, level)}
        rows.append(row)
    said = [r["outcome"] for r in rows if r["outcome"] != "not run"]
    out = ("not run" if not said else "other direction" if "other direction" in said else
           "agree" if all(o == "agrees" for o in said) else "partly")
    return {"check": "tests", "outcome": out, "rows": rows,
            **({"why": "; ".join(f"{r['label']}: {r.get('why') or 'no result'}" for r in rows)}
               if out == "not run" else {})}


def _kept_path(screen, spec, what):
    """Where a measurement many claims share is kept for reuse: one file per screen as the job built it (its build,
    design and code, which the job gives as spec["_kept"]; its cells, labels and guides) and what was measured (a
    gene calibration's test, seed and q level; a lead's targets, tests and reference). None where the job gives no
    folder."""
    kept = spec.get("_kept")
    if not kept:
        return None
    import hashlib
    h = hashlib.sha256(str(kept["base"]).encode())
    for values in (screen.adata.obs_names, screen.label, screen.top_guide):
        h.update("\x1f".join(map(str, np.asarray(values))).encode())
    h.update(("\x1e" + what).encode())
    return os.path.join(kept["folder"], h.hexdigest()[:32] + ".pkl")


def _read_kept(path):
    if path and os.path.exists(path):
        try:
            return pd.read_pickle(path)
        except Exception as e:  # a kept measurement that cannot be read is computed again
            print(f"the kept measurement {path} could not be read ({type(e).__name__}); computed again",
                  file=sys.stderr)
    return None


def _keep(path, value):
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        pd.to_pickle(value, tmp)
        os.replace(tmp, path)  # whole or absent: a reader never sees half a table


def _calibrate_one(screen, spec, method, workdir, seed):
    """One test on groups of control guides (C.genes' calibration groups), each against the rest of the control
    cells: their discoveries, the 95th percentile, and how many groups call the finding's gene. The groups' table is
    computed once for the screen, test and seed, and read back for later claims (_kept_path)."""
    from .question_modules import c
    g = spec["feature"]
    path = _kept_path(screen, spec, f"calibration\x1e{method}\x1e{seed}\x1e{_q_level()}")
    kept = _read_kept(path)
    if kept is None:
        seen = {}

        def run(s, groups):
            t, calls = c.genes_by_leads(s, {x: {"method": method} for x in groups}, "control_guides",
                                        os.path.join(workdir, f"calibration_{method}"))
            seen["table"] = t
            return t, calls

        cal, extra = c.calibrate_method(screen, run, "control_guides", _q_level(), seed, method=method)
        if cal.get("not_run"):
            return {"method": method, "not_run": cal["not_run"]}
        t = seen["table"]
        kept = {"cal": cal, "discoveries": extra["calibration_groups"]["discoveries"].to_numpy(),
                "called": t.loc[t["q"] < _q_level(), ["gene", "target"]].reset_index(drop=True)}
        _keep(path, kept)
    cal, called = kept["cal"], kept["called"]
    return {"method": method, "groups": int(cal["groups"]), "groups_not_fitted": int(cal.get("groups_not_fitted", 0)),
            "discoveries": int(cal["discoveries"]),
            "groups_95th_percentile": float(np.percentile(kept["discoveries"], 95)),
            "groups_calling_the_gene": int(called.loc[called["gene"] == g, "target"].nunique())}


def _calibration(screen, spec, method, lead, workdir, seed, other=None):
    """The calibration check of the lead's test, and of the other test where it runs (docs/adr/0025, section 3):
    the target's changed genes against the lead's control groups' 95th percentile."""
    first = _calibrate_one(screen, spec, method, workdir, seed)
    out = {"check": "calibration"}
    if other is not None and other.get("method") and other["method"] != method:
        out["other_test"] = _calibrate_one(screen, spec, other["method"], workdir, seed)
    if first.get("not_run"):
        return {**out, "outcome": "not run", "why": first["not_run"]}
    beyond = (lead.get("genes_changed") or 0) > first["groups_95th_percentile"]
    return {**out, **{k: v for k, v in first.items() if k != "method"}, "target_genes_changed": lead.get("genes_changed"),
            "outcome": "beyond the control groups" if beyond else "within the control groups' range"}


def _calibrate_effect(screen, spec, measure, method, lead, seed):
    """The calibration check of a gene-set or state finding: the lead's own measure on groups of control guides (C.genes'
    calibration groups, sized like the library's targets), each against the rest of the control cells; the finding's
    effect against theirs: how many go as far its way, and whether it passes their 95th percentile."""
    from .question_modules import c
    groups = c.calibration_groups(screen, seed)
    lab = screen.label.copy()
    for g, guides in groups.items():
        lab = np.where((screen.label == "control") & np.isin(screen.top_guide, guides), g, lab)
    names = [g for g in groups if (lab == g).sum() >= fit.MIN_CELLS]
    if not names:
        return {"check": "calibration", "outcome": "not run", "why": "too few control guides for a group and its reference"}
    per = measure(screen.with_labels(lab), names, method)
    effects = np.asarray([per[g].get("effect", np.nan) for g in names], dtype=float)
    effects = effects[np.isfinite(effects)]
    target = lead.get("effect", np.nan)
    if not len(effects) or not np.isfinite(target):
        return {"check": "calibration", "outcome": "not run",
                "why": "no control-guide group or the finding itself could be measured"}
    sign = 1 if spec["direction"] > 0 else -1
    top = float(np.percentile(np.abs(effects), 95))
    beyond = sign * target > 0 and abs(target) > top
    return {"check": "calibration", "groups": int(len(effects)),
            "groups_as_far": int((sign * effects >= sign * target).sum()), "groups_95th_percentile_effect": top,
            "target_effect": float(target),
            "outcome": "beyond the control groups" if beyond else "within the control groups' range"}


# ---- words, figure, record -------------------------------------------------------------------------------------
TEST_WORDS = {"edger_ql": "edgeR-QL pseudobulk", "wilcoxon": "Wilcoxon",
              "wilcoxon_same_samples": "Wilcoxon against the control cells of the same samples",
              "linear_model": "the linear model with the sample", "fisher_same_samples": "Fisher's exact test",
              "propeller": "propeller (speckle) on the samples' proportions",
              "cmh": "the Cochran-Mantel-Haenszel test over the samples"}


def _num(v):
    return f"{v:.2g}" if isinstance(v, float) and np.isfinite(v) else "n/a"


def disagrees(lead, checks):
    """Whether the finding's checks disagree with it: the lead does not show it, its guides or halves disagree, or a
    re-test gives the other direction."""
    if lead is None:
        return False
    if lead.get("outcome") in ("other direction", "other way, not significant", "no difference"):
        return True
    return any(c["outcome"] in ("disagree", "other direction") for c in checks)


def _units_words(r):
    """The units a result names, in words: its cells per unit, and its replicates for a test by replicate."""
    units = r.get("units") or {}
    words = (f" in {len(units)} units: " + ", ".join(f"{u} {n}" for u, n in list(units.items())[:8])
             + (", ..." if len(units) > 8 else "")) if units else ""
    reps = r.get("replicates")
    return words + (f"; replicates {', '.join(reps)}" if reps else "")


def sentence(spec, lead, checks):
    """The checks in the field's words, with the n of each."""
    if lead is None:
        return f"Not checked here: {checks[0]['why']}."
    if lead["outcome"] == "not run":
        return (f"{spec['feature']} in {spec['target']}'s cells: the lead test gave no result ({lead.get('why')}); "
                + "; ".join(f"{WORDS.get(c['check'], c['check'])}: {c['outcome']}" for c in checks) + ".")
    way = "up" if spec["direction"] > 0 else "down"
    p_word = "q" if spec["kind"] == "gene" else "p"
    parts = [f"{spec['feature']} {way} in {spec['target']}'s cells ({lead.get('cells', 'n/a')} cells"
             f"{_units_words(lead)}; "
             f"{TEST_WORDS.get(lead['test'], lead['test'])}, effect {_num(lead['effect'])}, {p_word} "
             f"{_num(lead['q'])}: {lead['outcome']})"]
    for c in checks:
        if c["outcome"] == "not run":
            parts.append(f"{WORDS.get(c['check'], c['check'])}: not run ({c['why']})")
        elif c["check"] in ("guides_agree", "half"):
            each = "; ".join(f"{k} {_num(v['effect'])} ({v.get('cells', 'n/a')} cells"
                             + (f" in {len(v['units'])} units" if v.get("units") else "")
                             + (f", own gene {_num(v['own_gene'])}" if v.get("own_gene") is not None else "")
                             + f", {v.get('outcome', 'n/a')})"
                             for k, v in c["parts"].items())
            counted = parts_counts(c["parts"], spec["direction"], _level(spec["kind"]))
            parts.append(f"{WORDS[c['check']]}: {counted} ({each})")
        elif c["check"] == "tests":
            each = "; ".join(f"{r['label']}: effect {_num(r.get('effect'))}, "  # each on the feature alone: its p
                             f"p {_num(r['p'] if r.get('p') is not None else r.get('q'))}, {r['outcome']}"
                             for r in c["rows"])
            parts.append(f"{WORDS['tests']}: {c['outcome']} ({each})")
        elif c["check"] == "calibration" and "target_effect" in c:  # a gene-set or state finding: effects, not genes
            parts.append(f"{WORDS['calibration']}: {c['groups']} groups (95th percentile of their effects "
                         f"{_num(c['groups_95th_percentile_effect'])}; {c['groups_as_far']} go as far its way; the "
                         f"finding's effect {_num(c['target_effect'])}: {c['outcome']})")
        elif c["check"] == "calibration":
            o = c.get("other_test") or {}
            also = (f"; under the other test, {o['discoveries']} discoveries in {o['groups']} groups" if o.get("groups")
                    else f"; under the other test: not run ({o['not_run']})" if o.get("not_run") else "")
            parts.append(f"{WORDS['calibration']}: {c['discoveries']} discoveries in {c['groups']} groups (95th "
                         f"percentile {c['groups_95th_percentile']:.0f} genes; the target changes "
                         f"{c['target_genes_changed']}: {c['outcome']}; {c['groups_calling_the_gene']} groups call "
                         f"{spec['feature']}{also})")
        else:
            label = c.get("label") or WORDS.get(c["check"], c["check"])
            reps = f"; replicates {', '.join(c['replicates'])}" if c.get("replicates") else ""
            word = "p" if c.get("gene_alone") else p_word
            parts.append(f"{label}: {c['outcome']} (effect {_num(c.get('effect'))}, {word} {_num(c.get('q'))}"
                         f"{reps})")
    if any(c.get("gene_alone") or any(v.get("gene_alone") for v in (c.get("parts") or {}).values())
           for c in checks):
        parts.append(f"each re-test tested {spec['feature']} alone, so its q is its p")
    return "; ".join(parts) + "."


def figure(spec, lead, checks, path):
    """The supplementary panel: the effect under the lead and each check, filled where significant by its own
    test."""
    from .question_modules import figures as fg
    rows = [("lead: " + TEST_WORDS.get(lead["test"], lead["test"]), lead)]
    for c in checks:
        if c["outcome"] == "not run" or c["check"] == "calibration":
            continue
        if "parts" in c:
            rows += [(k, v) for k, v in c["parts"].items()]
        elif "rows" in c:
            rows += [(r["label"], r) for r in c["rows"]]
        else:
            rows.append((c.get("label") or WORDS.get(c["check"], c["check"]), c))
    rows = [(n, r) for n, r in rows if np.isfinite(r.get("effect", np.nan))]
    level = _level(spec["kind"])
    plt = fg._plt()
    fig, ax = plt.subplots(figsize=(fg.WIDTHS_MM[0] / 25.4, 0.25 * len(rows) + 1.0))
    for i, (name, r) in enumerate(rows):
        sig = np.isfinite(r.get("q", np.nan)) and r["q"] < level
        ax.plot([r["effect"]], [i], "o", mfc="black" if sig else "white", mec="black", ms=4)
    ax.axvline(0, color="grey", lw=0.6, ls="--")
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([n for n, _ in rows])
    ax.invert_yaxis()
    unit = {"gene": "log2 fold change", "gene_set": "score, target minus reference",
            "state": "share of cells in the state, target minus reference"}[spec["kind"]]
    ax.set_xlabel(f"{spec['feature']}: {unit}")
    fg._save(plt, fig, path, f"{spec['target']} {spec['feature']}: its checks")
    return path


def _plain(x):
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return float(x) if np.isfinite(x) else None
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def run(record, screen, spec, out_dir, finding_id, deposit=None, run_id=None, seed=0):
    """The finding's checks: result.json, checks.json and figure.png in out_dir, and one check_recorded event per
    check (the lead first). A claim on several targets: each target's in a folder of its own, each check recorded
    with its target, and a result.json over them in out_dir."""
    targets = list(spec.get("targets") or [spec["target"]])
    if len(targets) == 1:
        return _run_one(record, screen, {**spec, "target": targets[0]}, out_dir, finding_id, deposit, run_id, seed)
    os.makedirs(out_dir, exist_ok=True)
    # shortcut: a gene claim's calibration (the control-guide groups, the same for every target) runs once per target,
    # as separate findings did; share it across targets if claims on several genes' targets prove slow
    each = {t: _run_one(record, screen, {**spec, "target": t}, os.path.join(out_dir, "".join(
        ch if ch.isalnum() or ch in "._-" else "_" for ch in t)), finding_id, deposit, run_id, seed, target=t)
        for t in targets}
    summary = {"status": "measured" if any(x["status"] == "measured" for x in each.values()) else "not_enough_data",
               "summary": " ".join(x["summary"] for x in each.values()),
               "disagrees": [t for t, x in each.items() if x["disagrees"]],
               "figure": next((x["figure"] for x in each.values() if x["figure"]), None), "targets": each}
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(_plain(summary), fh, indent=1, allow_nan=False)
    return summary


def _run_one(record, screen, spec, out_dir, finding_id, deposit=None, run_id=None, seed=0, target=None):
    """One target's checks (target: named where the claim has several)."""
    from . import record as rc
    os.makedirs(out_dir, exist_ok=True)
    lead, checks = run_checks(screen, spec, out_dir, deposit, seed)
    if record is not None:  # recorded first: a figure or a sentence that fails cannot cost the checks
        items = ([{"check": "lead", **lead}] if lead is not None else []) + checks
        for c in items:
            name = c["check"] if c["check"] in CHECK_NAMES else "lead"
            reason = c.get("why") or c.get("reason") or ("no result" if c["outcome"] == "not run" else "")
            record.append("check_recorded", {"finding_id": finding_id, "check": name, "outcome": c["outcome"],
                                             "reason": reason, "run_id": run_id,
                                             **({"target": target} if target else {}),
                                             "numbers": _plain({k: v for k, v in c.items()
                                                                if k not in ("check", "outcome", "why", "reason")})},
                          rc.TOOL)
    words = sentence(spec, lead, checks)
    path = None
    if lead is not None and lead["outcome"] != "not run":
        try:
            path = figure(spec, lead, checks, os.path.join(out_dir, "figure.png"))
        except Exception as e:  # said in the result, never hidden
            words += f" (The checks' figure could not be drawn: {type(e).__name__}: {e}.)"
    ran = lead is not None and lead["outcome"] != "not run"
    # the checks report numbers and give no verdict of their own: a lead that ran is measured, whatever its q
    summary = {"status": "measured" if ran else "not_enough_data", "summary": words, "lead": lead, "checks": checks,
               "disagrees": disagrees(lead, checks), "figure": path,
               "figure_checks": {"reference_shown": True, "zero_centred": None, "direction_labelled": True}}
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(_plain(summary), fh, indent=1, allow_nan=False)
    return summary


def state_names(state_table):
    """{state ID: its name}, where the run that wrote the labelling named its states (C.states_and_composition's
    state_names, in the result.json beside its cell_states table), else {}."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(state_table)), "result.json"), encoding="utf-8") as fh:
            names = (json.load(fh).get("diagnostics") or {}).get("state_names") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {str(k): str(v) for k, v in names.items()} if isinstance(names, dict) else {}


def score_values(path):
    """A per-cell score the notebook saved (pt.save_table): its values by barcode, from the table's barcode column and
    its score (a column named score, or its only numeric column)."""
    t = pd.read_csv(path, sep="\t")
    if "barcode" not in t:
        raise ValueError("the score's table has no barcode column: save one row per cell, its barcode and its score")
    nums = [c for c in t.columns if c != "barcode" and pd.api.types.is_numeric_dtype(t[c])]
    col = "score" if "score" in nums else nums[0] if len(nums) == 1 else None
    if col is None:
        raise ValueError(f"the score's table names its score: a column named score, or one numeric column (it has "
                         f"{nums or 'none'})")
    values = pd.Series(t[col].to_numpy(float), index=t["barcode"].astype(str))
    if values.index.duplicated().any():
        raise ValueError("the score's table gives a cell twice: one row per barcode")
    return values


def load_spec(spec, screen):
    """A spec's per-cell parts read from their tables, on the screen's cells; a state given by its name (as the states
    table names it, such as "S09 FOXP3 BATF") is read as its ID."""
    if spec["kind"] == "score" and spec.get("score_table"):
        spec["values"] = score_values(spec["score_table"])
    if spec["kind"] == "state" and spec.get("state_table"):
        t = pd.read_csv(spec["state_table"], sep="\t", dtype=str).set_index("barcode")
        # a module's cell_states, a pt.states call's states, or a pt.cell_cycle call's phases (S_score comes first)
        st = t["state"] if "state" in t else t["phase"] if "phase" in t else t.iloc[:, 0]
        spec["state_labels"] = (st.astype(str).reindex(np.asarray(screen.adata.obs_names).astype(str))
                                .fillna("").to_numpy())
        names = state_names(spec["state_table"])
        spec["state_names"] = names
        given = " ".join(str(spec.get("feature", "")).split()).casefold()
        named = [s for s, n in names.items() if " ".join(n.split()).casefold() == given]
        if named:
            spec["state"] = named[0]
    return spec


def _built_from(screen_dir, design_path):
    """What a job's screen object comes from beyond its cells: the build (screen.json), the design file, and this
    code's file (a release's own folder)."""
    import hashlib
    h = hashlib.sha256(os.path.abspath(__file__).encode())
    for path in (os.path.join(screen_dir, "screen.json"), design_path):
        try:
            with open(path, "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(b"\x00absent")
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m pertura.finding_checks")
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    for a in ("--record", "--finding", "--spec", "--screen", "--design", "--out"):
        r.add_argument(a, required=True)
    r.add_argument("--run-id", default=None)
    r.add_argument("--at-once", type=int, default=1, help="how many checks jobs of a record run together")
    a = ap.parse_args(argv)
    from . import record as rc
    from .question_modules.data import Screen
    from .screen_object import load_design
    with open(a.spec, encoding="utf-8") as fh:
        spec = json.load(fh)
    spec.pop("_queue", None)  # the service's own note on the queued spec
    run_id = a.run_id or os.path.basename(os.path.abspath(a.out))  # a job runs in its own folder, named by its run
    # the slot first: a job that waits holds no screen object
    with _slots(os.path.join(a.record, "checks.lock"), a.at_once):
        screen = Screen(a.screen, load_design(a.design))
        deposit = None
        if spec.get("cells"):  # the finding's cell set; a calling rule's with the deposit's calls beside it
            from . import cell_sets
            _, info = cell_sets.load(a.screen, spec["cells"])
            if info.get("calls"):
                screen = cell_sets.for_engine(screen, a.screen, spec["cells"])
                deposit = screen.with_calls("deposit")
            else:
                screen = cell_sets.apply(screen, a.screen, spec["cells"])
        spec = load_spec(spec, screen)
        spec["_kept"] = {"folder": os.path.join(os.path.dirname(os.path.abspath(a.record)), "checks",
                                                "kept_measurements"),
                         "base": _built_from(a.screen, a.design)}
        out = run(rc.Record(a.record), screen, spec, a.out, a.finding, deposit, run_id)
    print(f"{a.finding}: {out['summary']}")
    return 0


class _slots:
    """At most n of a record's checks jobs at once (each loads the whole screen): a job takes the first free of n
    lock files, prefix.0 to prefix.<n-1>, where the system has locks (fcntl), and waits while all are taken;
    elsewhere, no wait."""

    def __init__(self, prefix, n=1, poll=2.0):
        self.prefix, self.n, self.poll, self.fh = prefix, max(1, int(n)), poll, None

    def __enter__(self):
        try:
            import fcntl
        except ImportError:
            return self
        import time
        while True:
            for k in range(self.n):
                fh = open(f"{self.prefix}.{k}", "w")
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    fh.close()
                    continue
                self.fh = fh
                return self
            time.sleep(self.poll)

    def __exit__(self, *exc):
        if self.fh is not None:
            import fcntl
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()
            self.fh = None
        return False


if __name__ == "__main__":
    sys.exit(main())
