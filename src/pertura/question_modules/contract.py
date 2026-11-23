"""The module contract and registry (docs/adr/0020, section 2)."""
import inspect
import json
import os
from dataclasses import asdict, dataclass, field

FIELDS = ("serves", "applies_when", "needs", "returns", "fails_when", "tested_against")
STATUSES = ("detected", "not_detected", "not_enough_data", "measured")
# the modules' inputs that are tables, with the columns each module reads from them: a table given by its object ID
# or its path is checked for them before the run starts, so a missing column is named (docs/adr/0027, rule 7)
TABLE_INPUTS = {("E.program_axis", "effects"): ("target", "score", "effect"),
                ("E.co_functional_sets", "effects"): ("target", "score", "effect"),
                ("E.combinations", "de"): ("gene", "group", "avg_logFC", "DE"),
                ("G.direct_targets", "de"): ("target", "gene", "effect", "q"),
                ("G.direct_targets", "net"): ("source", "target", "weight"),
                ("C.within_states", "cell_states"): ("barcode", "state"),
                ("C.programs", "cell_values"): ("barcode",),
                ("G.meaning", "de"): ("target", "gene", "effect"),
                ("C.genes", "de"): ("target", "gene", "effect", "q"),
                ("E.similar_targets", "de"): ("target", "gene", "effect", "q"),
                ("G.meaning", "calibration"): ("target", "gene", "effect"),
                ("C.within_states", "composition"): ("target", "state", "mean_difference"),
                ("A.screen_overview", "abundance"): ("target", "log2_vs_control_guides"),
                ("A.screen_overview", "efficacy"): ("guide", "target", "log2fc"),
                ("A.screen_overview", "states"): ("target", "state", "mean_difference"),
                ("A.screen_overview", "similar"): ("target", "other", "correlation"),
                ("A.screen_overview", "guides"): ("target", "median_correlation", "control_pairs_as_high"),
                ("A.screen_overview", "replicates"): ("target", "r"),
                ("A.screen_overview", "genes"): ("target", "genes_changed"),
                ("A.screen_overview", "common"): ("target", "e_distance"),
                ("A.screen_overview", "previous"): ("target", "e_distance", "effect_line")}
# the table inputs a module may take, when they are left out, from the run another of its inputs names: a
# C.states_and_composition run holds both each cell's state and the composition. Any other table input left out
# stays out, since a table with the right columns from another module's run is not that input.
TABLES_FROM_ANOTHER_INPUT = {("C.within_states", "cell_states"), ("C.within_states", "composition")}
# a table input that, given by no input's run, is taken from the build's latest finished run of the module that makes
# it (the development runs on 0adebaa: a worker tried four calls to give E.co_functional_sets its effects)
TABLES_FROM_THE_LATEST_RUN = {("E.co_functional_sets", "effects"): "C.programs",
                              ("C.within_states", "cell_states"): "C.states_and_composition",
                              ("E.program_axis", "effects"): "C.programs"}
# where a table input comes from, named in the error when its columns are missing (session 4, 1h: the route to
# regulatory_modes existed, and the error named only the columns)
TABLE_PRODUCERS = {("E.combinations", "de"): "the genes tool (C.genes) with method seurat3_lr and groups holding the "
                                              "pair and each of its singles ({name: [labels]}): that run's de table "
                                              "(<run>:de)",
                   ("G.direct_targets", "de"): "a C.genes run, given by its ID or <run>:changed_genes; the run's "
                                               "own test of every gene is read",
                   ("G.meaning", "de"): "a C.genes run, given by its ID or <run>:changed_genes; the run's own test "
                                        "of every gene is read, and its calibration",
                   ("C.genes", "de"): "an earlier C.genes run, given by its ID or <run>:changed_genes; the run's own "
                                      "test of every gene is read"}
# table inputs that need every gene a test tested, not only the changed ones: given a module run of genes (its ID or one
# of its tables' IDs), the run's own pt.genes table is read in its place
FULL_GENE_TABLES = {("G.direct_targets", "de"), ("G.meaning", "de"), ("C.genes", "de"), ("E.similar_targets", "de")}
# "measured": the module reports its numbers and has no test to give a status (docs/adr/0022, section 2); the agent
# closes the question from the numbers
# which of a module's tests are corrected together for multiple testing: a q-value controls the false discoveries
# within that family only, and targets chosen before a run (by an earlier test) are a selection the q-values do not
# cover (Benjamini and Bogomolov 2014); each result says its family, its tests and how its targets were chosen
CORRECTED_TOGETHER = {
    "C.genes": "each target's genes, within the target (Benjamini-Hochberg; Seurat's adjusted p, Bonferroni within "
               "the target, for the field method)",
    "C.any_effect": "the targets tested (Benjamini-Hochberg over them)",
    "C.states_and_composition": "every target and state tested (Benjamini-Hochberg over them)",
    "C.cell_cycle": "every target and phase tested (Benjamini-Hochberg over them)",
    "C.programs": "every target and program tested (Benjamini-Hochberg over them)",
    "E.condition_x_perturbation": "every target and program tested (Benjamini-Hochberg over them)",
    "E.program_axis": "every two programs (Benjamini-Hochberg over them)",
    "G.meaning": "each target's gene sets, its genes up and down apart (the package's Benjamini-Hochberg)",
    "G.direct_targets": "one row per target, its own factor's known targets; the activities and Fisher's tests "
                        "apart (Benjamini-Hochberg over each)"}
# where a method corrects in its own way: (module, choice, value) -> its family, in place of the module's
CORRECTED_BY_METHOD = {
    ("C.genes", "method", "rank_test"): "each target's genes within each unit (Benjamini-Hochberg within the target "
                                        "and unit), the units then combined",
    ("C.genes", "method", "seurat3_lr"): "each group's genes (Seurat's adjusted p, Bonferroni within the group)",
    ("C.states_and_composition", "test", "sccoda"): "the states of each target (scCODA's own false discovery rate, "
                                                    "no Benjamini-Hochberg)",
    ("C.cell_cycle", "test", "sccoda"): "the phases of each target (scCODA's own false discovery rate, no "
                                        "Benjamini-Hochberg)",
    ("G.meaning", "method", "gsea"): "each target's gene sets, its genes ranked (GSEA's permutation false discovery "
                                     "rate)"}
# where a method tests the groups it is given rather than every target with enough cells
GROUPS_GIVEN = {("C.genes", "method", "seurat3_lr"): "groups"}


@dataclass
class Contract:
    serves: str
    applies_when: str
    needs: str
    returns: str
    fails_when: list            # [(how it misleads, the check that catches it)]
    tested_against: str
    choices: dict = field(default_factory=dict)  # choice -> [alternatives]; the first is the primary
    inputs: dict = field(default_factory=dict)   # input -> what it is (given by the agent, not varied)
    # a choice or input that only some methods use -> (the choice that picks the method, [the values that use it]);
    # given with another method it is refused, not ignored (docs/adr/0024, section 2)
    scope: dict = field(default_factory=dict)
    # what each target is compared with when the module has no reference choice, and where the comparison is made
    # within parts of the screen (each state, each condition), for what its result says it compared (item 24.4)
    reference: str = "control_guides"
    within: str = ""

    def missing(self):
        return [f for f in FIELDS if not getattr(self, f)]


@dataclass
class Result:
    status: str                 # one of STATUSES
    table: object               # a pandas DataFrame
    diagnostics: dict
    figure: str                 # a PNG path
    figure_checks: dict         # reference_shown, zero_centred, direction_labelled (True/False/None)
    summary: str = ""
    tables: dict = field(default_factory=dict)  # further tables by name, written as <name>.tsv
    # the result's biology in lines: which genes, states, conditions or pairs, as the reply gives them to the agent;
    # every module replied with counts and identifiers, and what sat in a table went unread (2026-11-07)
    biology: list = field(default_factory=list)

    def write(self, out_dir):
        os.makedirs(out_dir, exist_ok=True)
        self.table.to_csv(os.path.join(out_dir, "result.tsv"), sep="\t", index=False)
        for name, t in self.tables.items():
            t.to_csv(os.path.join(out_dir, f"{name}.tsv"), sep="\t", index=False)
        with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"status": self.status, "diagnostics": self.diagnostics, "figure": self.figure,
                       "figure_checks": self.figure_checks, "summary": self.summary,
                       **({"biology": self.biology} if self.biology else {}),
                       "tables": ["result"] + sorted(self.tables)}, fh, indent=1, default=str)
        return out_dir


def _at_default(value, default):
    """Whether a given value is the default (None, or equal to it); a table or an array never is."""
    if value is None:
        return True
    if hasattr(value, "shape"):
        return False
    try:
        return bool(value == default)
    except (TypeError, ValueError):
        return False


class Module:
    def __init__(self, name, contract, fn):
        self.name, self.contract, self.fn = name, contract, fn
        self.recipe = None  # a module written as blocks: the recipe its run calls (docs/adr/0031, D2)

    def default(self, name):
        """A choice's value when it is not given: the function's own default."""
        p = inspect.signature(self.fn).parameters.get(name)
        return None if p is None or p.default is inspect.Parameter.empty else p.default

    def check(self, given):
        """Refuse what would do nothing or be misread: an unknown name; a value outside a choice's list (a number
        where the list is numbers); a choice or input its method does not use. The error names what is allowed."""
        c = self.contract
        bad = [k for k in given if k not in c.choices and k not in c.inputs]
        if bad:
            raise ValueError(f"{self.name}: no choice or input {bad}; its choices are {c.choices} and its inputs "
                             f"{sorted(c.inputs)}")
        for k, v in given.items():
            alts = c.choices.get(k) or []
            if alts and all(isinstance(a, str) for a in alts) and v not in alts:
                raise ValueError(f"{self.name}: {k} is one of {alts}, not {v!r}")
            numbers = alts and all(isinstance(a, (int, float)) and not isinstance(a, bool) for a in alts)
            if numbers and (isinstance(v, bool) or not isinstance(v, (int, float))):
                raise ValueError(f"{self.name}: {k} is a number (such as {alts[0]}), not {v!r}")
        for k, v in given.items():
            if k in c.scope and not _at_default(v, self.default(k)):  # at its default it changes nothing
                by, values = c.scope[k]
                used = given.get(by, self.default(by))
                if used not in values:
                    raise ValueError(f"{self.name}: {k} is used only with {by} {values}; this run has {by} "
                                     f"{used!r}, which does not use it")

    def run(self, screen, design, out_dir, **choices):
        """The module on the screen. `policy` ({name: value}, pertura.policy) changes Pertura's numbers for this run;
        a choice given here is the run's own (a default is not a permission: it runs wherever it can be computed)."""
        from .. import policy
        changes = choices.pop("policy", None)
        self.check(choices)
        os.makedirs(out_dir, exist_ok=True)
        own = {k for k, v in choices.items() if not (isinstance(v, str) and v == "auto")
               and not _at_default(v, self.default(k))}
        with policy.using(changes, explicit_choices=own):
            r = self.fn(screen, design, out_dir, **choices)
            used = policy.used()
        if isinstance(r.diagnostics, dict):
            r.diagnostics["policy"] = used
            if used["notes"]:
                r.summary = (r.summary or "") + "; " + "; ".join(used["notes"])
        if r.status not in STATUSES:
            raise ValueError(f"{self.name}: status {r.status!r} is not one of {STATUSES}")
        if isinstance(r.diagnostics, dict) and "compared" not in r.diagnostics:
            said = compared(screen, r.table, choices.get("reference") or self.default("reference")
                            or self.contract.reference, self.contract.within)
            if said:
                r.diagnostics["compared"] = said
        if isinstance(r.diagnostics, dict) and self.name in CORRECTED_TOGETHER:
            mt = multiple_testing(self.name, r, choices.get("targets"),
                                  {k: choices.get(k, self.default(k)) for k in ("method", "test", "groups")})
            if mt:
                r.diagnostics["multiple_testing"] = mt
        r.write(out_dir)
        return r

    def describe(self):
        """The contract, and where the module is a recipe of blocks, the recipe's source, to read or copy into the
        notebook and change one step (docs/adr/0031, D3)."""
        out = {"name": self.name, **asdict(self.contract)}
        out["inputs"] = {**out["inputs"], "policy": "{name: value}: Pertura's numbers for this run (min_cells, "
                         "min_cells_profile, min_replicates, q, min_effect; pertura.policy). A choice you give runs "
                         "wherever it can be computed, and the result says how it differs from the default"}
        if self.name in CORRECTED_TOGETHER:
            out["corrected_together"] = CORRECTED_TOGETHER[self.name]
        if self.recipe is not None:
            out["recipe"] = inspect.getsource(self.recipe)
        return out


def multiple_testing(name, result, targets=None, choices=None):
    """A result's multiple testing: the family its q-values are corrected over (the method's own where it corrects
    in its own way, CORRECTED_BY_METHOD), the number of tests (the module's own count where it gives one, else the
    table's p-values, else the q-values') and how its targets were chosen. None where the run tested nothing."""
    import pandas as pd
    if result.status == "not_enough_data":
        return None
    choices = choices or {}
    said = result.diagnostics if isinstance(result.diagnostics, dict) else {}

    def chosen(c):  # the method that ran: the choice, or where it was auto, the one the result names
        v = choices.get(c)
        return said.get(c) if v in (None, "auto") and isinstance(said.get(c), str) else v
    out = dict(said.get("multiple_testing") or {})
    family = next((f for (m, c, v), f in CORRECTED_BY_METHOD.items() if m == name and chosen(c) == v), None)
    out.setdefault("corrected_together", family or CORRECTED_TOGETHER[name])
    table = result.table
    for col in ("p", "q"):
        if "tests" not in out and hasattr(table, "columns") and col in table.columns:
            out["tests"] = int(pd.to_numeric(table[col], errors="coerce").notna().sum())
    given = next((choices.get(i) for (m, c, v), i in GROUPS_GIVEN.items() if m == name and chosen(c) == v), None)
    if given:
        out.setdefault("targets", f"the {len(given)} groups given, chosen before this run")
    out.setdefault("targets", f"the {len(list(targets))} targets given, chosen before this run" if targets else
                   "every target with enough cells")
    return out


def multiple_testing_words(mt):
    """A result's multiple testing in words, for a report's methods; "" where the result gives none."""
    if not mt:
        return ""
    tests = f"{mt['tests']} tests, " if mt.get("tests") is not None else ""
    return f"{tests}corrected together: {mt.get('corrected_together', '')}; its targets: {mt.get('targets', '')}"


COMPARED_SHOWN = 40  # targets whose comparison a result names, the first in its table


def compared(screen, table, reference="control_guides", within=""):
    """What a result compared (docs/build_goal_session6_fixes.md, item 24.4): for the targets of its table, each one's
    cells, the reference cells its own comparison takes (its arm's control cells where the design matches references;
    every control cell for a combination whose members span arms) and the units holding both; where the module
    compares within parts of the screen, the counts are the whole screen's, said so. {} where the table names no
    target or the screen cannot say."""
    if reference not in ("control_guides", "other_targets") or not hasattr(screen, "comparison"):
        return {}
    try:
        names = [str(t) for t in dict.fromkeys(table["target"].astype(str))] if "target" in table else []
    except (AttributeError, KeyError, TypeError):
        return {}
    out = {}
    for t in names[:COMPARED_SHOWN]:
        if not (screen.label == t).any():
            continue
        try:
            c = screen.comparison(t, reference)
        except ValueError as e:  # a target with no one arm
            out[t] = {"refused": str(e)}
            continue
        held = sorted(set(screen.unit[c["cells"]]) & set(screen.unit[c["reference"]]))
        arm = ", ".join(json.loads(c["arm"])) if str(c["arm"]).startswith("[") else c["arm"]  # its attributes' values
        whose = ("the control cells" if reference == "control_guides" else "every other target's cells")
        out[t] = {**c["counts"], "reference": (f"{whose} of every arm, as its members span arms" if arm == "every arm"
                                               else whose + (f" of its arm ({arm})" if arm else "")),
                  "units": len(held)}
        if within:
            out[t]["within"] = f"{within}: these counts are the whole screen's; the table gives each part's"
    if len(names) > COMPARED_SHOWN:
        out["more"] = f"{len(names) - COMPARED_SHOWN} more targets, each compared the same way"
    return out


RENAMED = {"C.programmes": "C.programs", "E.programme_axis": "E.program_axis"}  # earlier name -> name


class _Registry(dict):
    """The modules by name. An earlier name of a renamed module still finds it, so the records of earlier sessions
    read; listing gives each module once, under its name."""

    def __getitem__(self, key):
        return super().__getitem__(RENAMED.get(key, key))

    def __contains__(self, key):
        return super().__contains__(RENAMED.get(key, key))

    def get(self, key, default=None):
        return super().get(RENAMED.get(key, key), default)


REGISTRY = _Registry()


def register(name, contract):
    """A decorator: register fn as the module `name` with its contract."""
    def deco(fn):
        if contract.missing():
            raise ValueError(f"module {name} lacks {contract.missing()}")
        REGISTRY[name] = Module(name, contract, fn)
        return fn
    return deco
