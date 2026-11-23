"""Which cells form the reference, each group and each construct after a specification's cell choices: vector calls
(tools.assignment), QC (tools.qc) and the reference kind. Cells are integer positions in the dataset.

Groups come from the perturbation labels: every non-control label is a group made of its constructs. A dataset may
also carry a group map (research universes, pertura_research.retired.universe):
    {"reference": [construct, ...], "groups": {name: [construct, ...]}}
Then the reference is made of the listed control constructs only, and the listed groups are added; they may share
constructs (every possible decoy made of the same control constructs).

Each construct is tested once, whatever the number of groups it belongs to. Re-called constructs map back to groups
through the provided labels, so a decoy's constructs stay that decoy's. None of this looks at which groups are decoys.
"""
import hashlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .tools.assignment import recall_constructs
from .tools.qc import cell_metrics, qc_keep  # noqa: F401 (cell_metrics re-exported for data.py)

GROUPS_VERSION = "groups/2"  # part of every cache key: bump when group building changes
MIN_CELLS_GROUP = 3  # smallest group (or construct) any tool will test


@dataclass
class GroupSet:
    reference: np.ndarray  # positions of reference cells
    groups: dict           # group -> positions of its cells (pooled)
    members: dict          # group -> labels of its testable constructs
    constructs: dict       # construct label -> positions (each construct once)
    home: dict = field(default_factory=dict)  # construct label -> the group label its cells carry in the data
    dropped: int = 0       # cells removed by QC or re-calling

    def fingerprint(self):
        h = hashlib.sha256(np.sort(self.reference).astype(np.int64).tobytes())
        for g in sorted(self.groups):
            h.update(g.encode())
            h.update(np.sort(self.groups[g]).astype(np.int64).tobytes())
            h.update("|".join(self.members.get(g, ())).encode())
        for c in sorted(self.constructs):
            h.update(c.encode())
            h.update(np.sort(self.constructs[c]).astype(np.int64).tobytes())
        return h.hexdigest()


def _majority(values, by):
    return pd.Series(values).groupby(by).agg(lambda s: s.mode().iloc[0])


def cell_calls(obs, design, spec, metrics=None, guide_counts=None, sgrna_names=None, assignment_calls=None):
    """Per cell, after the specification's cell choices: group label, construct, control kind (or None) and kept.
    assignment_calls: construct per cell from a registered assignment fit (pertura.assignments), for those choices."""
    pert = obs[design.perturbation_col].astype(str).to_numpy()
    cons = obs[design.construct_col].astype(str).to_numpy()
    kind = obs[design.control_kind_col].astype(str).to_numpy() if design.control_kind_col else None
    keep = qc_keep(spec["qc"], metrics) if spec["qc"] != "provided" else np.ones(len(obs), bool)
    if spec["assignment"] == "provided":
        group, construct = pert, cons
    else:
        if spec["assignment"].startswith("min_umi:"):
            t = int(spec["assignment"].split(":", 1)[1])
            construct = recall_constructs(guide_counts, sgrna_names, design.sgrna_suffix, t)
        else:
            if assignment_calls is None:
                raise ValueError(f"assignment {spec['assignment']!r} needs its registered calls")
            construct = np.asarray(assignment_calls, dtype=object)
        group = pd.Series(construct).map(_majority(pert, cons)).fillna("").to_numpy()
        if kind is not None:
            kind = pd.Series(construct).map(_majority(kind, cons)).fillna("").to_numpy()
        keep &= group != ""
    return group, construct, kind, keep


def build_groups(obs, design, spec, metrics=None, guide_counts=None, sgrna_names=None, group_map=None,
                 assignment_calls=None):
    """The GroupSet a specification analyses. obs: cell metadata in dataset order."""
    group, construct, kind, keep = cell_calls(obs, design, spec, metrics, guide_counts, sgrna_names,
                                              assignment_calls)
    ctrl = group == design.control_label
    ref_kind = spec["reference"].split(":", 1)[1] if ":" in spec["reference"] else None
    ref = ctrl & keep & ((kind == ref_kind) if ref_kind else True)
    if group_map is not None:
        ref &= np.isin(construct, list(group_map["reference"]))
    usable = keep & (construct != "")
    idx = np.flatnonzero(usable)
    by_con = pd.Series(construct[usable]).groupby(construct[usable]).indices
    constructs = {c: idx[ix] for c, ix in sorted(by_con.items()) if len(ix) >= MIN_CELLS_GROUP}
    home = {c: group[p[0]] for c, p in constructs.items()}
    members = {g: [] for g in sorted(set(group[usable & ~ctrl]))}
    for c in sorted(constructs):
        if home[c] in members:
            members[home[c]].append(c)
    if group_map is not None:
        ref_set = set(group_map["reference"])
        for g, cs in group_map["groups"].items():
            if g in members:
                raise ValueError(f"group map name {g!r} is also a perturbation label")
            if ref_set & set(cs):
                raise ValueError(f"group map group {g!r} uses reference constructs")
            members[g] = sorted(c for c in cs if c in constructs)
    groups = {}
    for g in sorted(members):
        # a group's cells are its constructs' cells, so constructs below MIN_CELLS_GROUP still count towards it
        pos = np.flatnonzero(usable & ((group == g) if group_map is None or g not in group_map["groups"]
                                       else np.isin(construct, list(group_map["groups"][g]))))
        if len(pos) >= MIN_CELLS_GROUP:
            groups[g] = pos
    members = {g: tuple(members[g]) for g in groups}
    used = set().union(*members.values()) if members else set()
    constructs = {c: p for c, p in constructs.items() if c in used}
    home = {c: home[c] for c in constructs}
    return GroupSet(reference=np.flatnonzero(ref), groups=groups, members=members, constructs=constructs, home=home,
                    dropped=int(len(obs) - keep.sum()))
