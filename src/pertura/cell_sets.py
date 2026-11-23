"""Cell sets (docs/adr/0024, section 3): a named set of a screen object's cells, with, where the agent changes them,
each cell's label (its guide call).

The agent makes one in its notebook (`save_cells`, which writes it into the notebook's own cell_sets folder); the
analysis service checks it against the screen object and keeps it in the object's folder (cell_sets/), where every
notebook can read it (`load_cells`). Every block, runner, robustness check and null takes a kept set by name, and the
screen is cut to its cells, and relabelled, before the analysis starts (`apply`), so no analysis has its own way to
leave cells out.

A set that decides guide calls (it relabels cells, or leaves cells out by their guide evidence: calls=True) is the
screen's calling rule, confirmed by the user before an analysis uses it; it may name the set it is cut from (base, a
population). A set that only leaves cells out for another reason (a tissue, a paper's quality thresholds) is stated
with its n. The robustness engine runs a calling rule's finding on its population with the deposit's own calls as a
variant (`for_engine`), so the check reports what the rule moved.
"""
import hashlib
import json
import os
import re
import shutil

import numpy as np
import pandas as pd

FOLDER = "cell_sets"
SIDECAR = ".cells.json"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
SHOWN = 20          # labels listed in a set's summary
EXCLUDED = "excluded"  # the label of a cell outside a calling rule, kept beside it for the engine


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def write(folder, name, barcodes, labels=None, reason="", calls=False, base=None):
    """Write a set into folder (the notebook's own cell_sets folder) for the service to check and keep. barcodes: the
    cells, as the screen object names them; labels: one per cell in order, or {barcode: label}, where the set
    changes guide calls; reason: why; calls: the set decides guide calls; base: the kept set it is cut from."""
    if not NAME.match(str(name)):
        raise ValueError("a cell set's name is letters, digits, '_', '.' or '-' (64 at most), starting with a letter "
                         "or a digit")
    if not str(reason).strip():
        raise ValueError("give the reason for the set (reason=...)")
    bc = pd.Series(list(barcodes), dtype=object).astype(str)
    if bc.empty:
        raise ValueError("the set has no cells")
    if bc.duplicated().any():
        raise ValueError(f"{int(bc.duplicated().sum())} barcodes appear twice")
    table = pd.DataFrame({"barcode": bc.to_numpy()})
    if labels is not None:
        if isinstance(labels, (dict, pd.Series)):
            lab = pd.Series(labels).astype(str)
            missing = ~bc.isin(lab.index)
            if missing.any():
                raise ValueError(f"{int(missing.sum())} cells have no label (such as {bc[missing].iloc[0]})")
            table["label"] = lab.reindex(bc).to_numpy()
        else:
            lab = np.asarray(list(labels)).astype(str)
            if len(lab) != len(bc):
                raise ValueError(f"labels: one per cell ({len(bc)}), not {len(lab)}")
            table["label"] = lab
        if (table["label"] == EXCLUDED).any():
            raise ValueError(f"'{EXCLUDED}' is Pertura's own label; leave such cells out of the set instead")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{name}.tsv")
    table.to_csv(path, sep="\t", index=False)
    meta = {"name": str(name), "reason": str(reason), "calls": bool(calls), "base": base, "cells": int(len(table)),
            "labelled": labels is not None, "sha256": _sha(path)}
    with open(os.path.join(folder, f"{name}{SIDECAR}"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    return {"name": str(name), "cells": int(len(table)),
            "next": "the tool that ran this cell checks the set against the screen object and keeps it"}


def pending(folder):
    """The sets written in folder: {name: (the table's sha256, its sidecar)}."""
    out = {}
    if not os.path.isdir(folder):
        return out
    for f in sorted(os.listdir(folder)):
        if f.endswith(SIDECAR):
            try:
                with open(os.path.join(folder, f), encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, ValueError):
                continue
            out[meta.get("name")] = (meta.get("sha256"), meta)
    return out


def screen_cells(screen_dir):
    """The screen object's cells (barcode, sample, unit, label, guides), without the counts."""
    cells = pd.read_csv(os.path.join(screen_dir, "cells.tsv.gz"), sep="\t", dtype=str, keep_default_na=False,
                        usecols=lambda c: c in ("barcode", "sample", "unit", "label", "guides"))
    return cells if "guides" in cells else cells.assign(guides="")


def unsupported_labels(screen_dir, barcodes, labels, cells=None):
    """The labels each cell's called guides do not support (ADR 0026, D2), as [(barcode, label)]: a target's label
    needs a guide of that target; a pair 'a|b' or a set 'a+b' a guide of each member; 'control' control guides only;
    the not-a-target labels (none, multi and the like) need nothing."""
    import re
    from .question_modules.data import NOT_TARGETS
    cells = screen_cells(screen_dir) if cells is None else cells
    guides = pd.read_csv(os.path.join(screen_dir, "guides.tsv"), sep="\t", dtype=str, keep_default_na=False)
    target_of = dict(zip(guides["guide"], guides["target"]))
    control = set(guides.loc[~guides["kind"].isin(["targeting", "unknown"]), "guide"])
    called = dict(zip(cells["barcode"], cells["guides"]))
    bad = []
    for bc, lab in zip(barcodes, labels):
        lab = str(lab)
        if lab in NOT_TARGETS and lab != "control":
            continue
        gs = [x.rsplit(":", 1)[0] for x in str(called.get(bc, "")).split(";") if x]  # a guide's own ':' stays
        if lab == "control":
            ok = bool(gs) and all(g in control for g in gs)
        else:
            have = {target_of.get(g) for g in gs}
            ok = all(m in have for m in re.split(r"[+|]", lab) if m)
        if not ok:
            bad.append((bc, lab))
    return bad


def names(screen_dir):
    folder = os.path.join(screen_dir, FOLDER)
    return sorted(f[:-5] for f in os.listdir(folder) if f.endswith(".json")) if os.path.isdir(folder) else []


def enter(screen_dir, source, name, run_id):
    """Check the set `name` written in the folder `source` against the screen object and keep it in the object's
    folder. Returns its summary (cells per sample and per label, the cells it relabels, whether it is a calling
    rule); raises ValueError naming its problems."""
    with open(os.path.join(source, f"{name}{SIDECAR}"), encoding="utf-8") as fh:
        meta = json.load(fh)
    src = os.path.join(source, f"{name}.tsv")
    table = pd.read_csv(src, sep="\t", dtype=str, keep_default_na=False)
    cells = screen_cells(screen_dir)
    problems = []
    known = table["barcode"].isin(cells["barcode"])
    if not known.all():
        problems.append(f"{int((~known).sum())} of its {len(table)} barcodes are not cells of the screen object (such "
                        f"as {table.loc[~known, 'barcode'].iloc[0]}; the object's cells are named like "
                        f"{cells['barcode'].iloc[0]})")
    if "label" in table:  # each label must follow from the cell's called guides, whatever words it uses
        bad = unsupported_labels(screen_dir, table["barcode"], table["label"], cells)
        if bad:
            problems.append(f"{len(bad)} cells' labels are not supported by their called guides (such as "
                            + "; ".join(f"{b} labelled {lab!r}" for b, lab in bad[:3]) + ")")
    base = meta.get("base")
    if base:
        if base not in names(screen_dir):
            problems.append(f"its base {base!r} is not a kept cell set (kept: {names(screen_dir)})")
        else:
            outside = ~table["barcode"].isin(load(screen_dir, base)[0]["barcode"])
            if outside.any():
                problems.append(f"{int(outside.sum())} of its cells are not in its base {base!r}")
    if problems:
        raise ValueError("; ".join(problems))
    dest = os.path.join(screen_dir, FOLDER)
    os.makedirs(dest, exist_ok=True)
    kept = os.path.join(dest, f"{name}.tsv")
    if os.path.exists(kept) and _sha(kept) != _sha(src):
        raise ValueError(f"a different cell set named {name!r} is kept already; give this one another name")
    shutil.copyfile(src, kept)
    own = cells.set_index("barcode").reindex(table["barcode"])
    labels = table["label"] if "label" in table else own["label"]
    relabelled = int((table["label"].to_numpy() != own["label"].to_numpy()).sum()) if "label" in table else 0
    summary = {"name": name, "reason": meta.get("reason", ""), "cells": int(len(table)), "of": int(len(cells)),
               "per_sample": {str(k): int(v) for k, v in own["sample"].value_counts().items()},
               "per_label": {str(k): int(v) for k, v in labels.value_counts().head(SHOWN).items()},
               "labels": int(labels.nunique()), "relabelled": relabelled,
               "calls": bool(meta.get("calls")) or relabelled > 0, "base": base, "run_id": run_id}
    with open(os.path.join(dest, f"{name}.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1)
    return summary


def load(screen_dir, name):
    """A kept set: (its table: barcode, and label where it gives them; its summary)."""
    path = os.path.join(screen_dir, FOLDER, f"{name}.tsv")
    if not os.path.exists(path):
        raise ValueError(f"no kept cell set {name!r}; the kept sets are {names(screen_dir)}")
    with open(os.path.join(screen_dir, FOLDER, f"{name}.json"), encoding="utf-8") as fh:
        info = json.load(fh)
    return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False), info


def apply(screen, screen_dir, name):
    """The screen cut to a kept set's cells, relabelled where the set gives labels."""
    table, info = load(screen_dir, name)
    labels = dict(zip(table["barcode"], table["label"])) if "label" in table else None
    s = screen.subset(cells=table["barcode"].to_numpy(), labels=labels)
    s.cell_set = {"name": name, "cells": int(len(table)), "calls": bool(info.get("calls"))}
    return s


def for_engine(screen, screen_dir, name):
    """The robustness engine's screen for a finding on a kept set. A population: its cells. A calling rule: its base
    population (every cell where it has none), the rule's labels on its own cells and 'excluded' on the rest (neither
    target nor reference), and the screen object's own labels as another calling method, 'deposit'."""
    table, info = load(screen_dir, name)
    if not info.get("calls"):
        return apply(screen, screen_dir, name)
    base = apply(screen, screen_dir, info["base"]) if info.get("base") else screen
    names_b = np.asarray(base.adata.obs_names).astype(str)
    own = pd.Series(base.label, index=names_b)
    rule = (pd.Series(table["label"].to_numpy(), index=table["barcode"].to_numpy()) if "label" in table
            else own.reindex(table["barcode"].to_numpy()))
    s = base.with_labels(rule.reindex(names_b).fillna(EXCLUDED).to_numpy())
    s.other_calls = {**base.other_calls, "deposit": base.label}
    s.cell_set = {"name": name, "cells": int(len(table)), "calls": True, "base": info.get("base")}
    return s
