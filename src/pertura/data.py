"""A dataset on disk: an AnnData of raw counts, its machine-readable design, and optional per-cell guide counts.

The manifest (JSON) holds: name, h5ad, design (path to the design file), guide_counts ('' if none), group_map ('' if
none; research universes, see pertura.groups) and fingerprint. The fingerprint is computed once when the manifest is
made, so it can key caches without re-reading the data: the sha256 of the h5ad file, combined with the sha256 of the
guide counts and of the group map when there are any. Data loads lazily and keeps derived values (gene detection, cell
metrics) for its lifetime: one job.

Cells with a non-empty 'copy_of' column are modified copies (spike-in carriers in research universes). Gene detection
ignores them, so gene filters are the same with and without spike-ins.
"""
import hashlib
import json
import os

import numpy as np
import scipy.sparse as sp

from .design import Design
from .groups import build_groups, cell_metrics


def sha256_file(path, chunk=1 << 24):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def make_manifest(name, h5ad, design, guide_counts="", out=None, group_map=""):
    """Write (if out) and return a dataset manifest. Paths are stored as given, normally absolute server paths."""
    fp = sha256_file(h5ad)
    extra = [sha256_file(f) for f in (guide_counts, group_map) if f]
    if extra:
        fp = hashlib.sha256("|".join([fp] + extra).encode()).hexdigest()
    m = {"name": name, "h5ad": h5ad, "design": design, "guide_counts": guide_counts, "group_map": group_map,
         "fingerprint": fp}
    if out:
        with open(out, "w") as fh:
            json.dump(m, fh, indent=1)
    return m


class Data:
    def __init__(self, manifest):
        if isinstance(manifest, str):
            with open(manifest) as fh:
                manifest = json.load(fh)
        self.manifest = manifest
        self.name = manifest["name"]
        self.fingerprint = manifest["fingerprint"]
        self.design = Design.load(manifest["design"])
        self._adata = None
        self._detected = {}
        self._metrics = None
        self._guides = None
        self._group_map = None
        self._usage = {}
        self._states = {}

    @property
    def adata(self):
        if self._adata is None:
            import anndata as ad
            a = ad.read_h5ad(self.manifest["h5ad"])
            a.X = sp.csr_matrix(a.X)
            self._adata = a
        return self._adata

    @property
    def X(self):
        return self.adata.X

    @property
    def obs(self):
        return self.adata.obs

    @property
    def genes(self):
        return np.asarray(self.adata.var_names)

    def originals(self):
        """Positions of the cells that are not modified copies."""
        if "copy_of" not in self.obs.columns:
            return np.arange(self.X.shape[0])
        return np.flatnonzero(self.obs["copy_of"].astype(str).to_numpy() == "")

    def detected(self, frac):
        """Genes detected (count > 0) in at least frac of the original cells; does not look at groups."""
        if frac not in self._detected:
            orig = self.originals()
            X = self.X if len(orig) == self.X.shape[0] else self.X[orig]
            n = np.asarray((X > 0).sum(0)).ravel()
            self._detected[frac] = n >= frac * X.shape[0]
        return self._detected[frac]

    def program_usage(self, set_id):
        """(usage matrix cells x programs, in dataset cell order; program names) of a registered program set."""
        if set_id not in self._usage:
            sets = self.manifest.get("programs", {})
            if set_id not in sets:
                raise KeyError(f"program set {set_id!r} is not registered for {self.name} (have {sorted(sets)})")
            from .programs import load_usage
            U, names = load_usage(sets[set_id])
            if U.shape[0] != self.X.shape[0]:
                raise ValueError(f"program set {set_id} has {U.shape[0]} cells, the dataset {self.X.shape[0]}")
            self._usage[set_id] = (U, names)
        return self._usage[set_id]

    def state_labels(self, set_id):
        """(state label per cell, in dataset cell order; state names) of a registered state set."""
        if set_id not in self._states:
            sets = self.manifest.get("states", {})
            if set_id not in sets:
                raise KeyError(f"state set {set_id!r} is not registered for {self.name} (have {sorted(sets)})")
            from .states import load_labels
            labels, names = load_labels(sets[set_id])
            if len(labels) != self.X.shape[0]:
                raise ValueError(f"state set {set_id} has {len(labels)} cells, the dataset {self.X.shape[0]}")
            self._states[set_id] = (labels, names)
        return self._states[set_id]

    def group_map(self):
        """The research group map ({'reference': [...], 'groups': {...}}) or None."""
        path = self.manifest.get("group_map", "")
        if path and self._group_map is None:
            with open(path) as fh:
                self._group_map = json.load(fh)
        return self._group_map

    def metrics(self):
        if self._metrics is None:
            m = cell_metrics(self.X, self.genes)
            m["original"] = False
            m.loc[self.originals(), "original"] = True
            if self.manifest.get("doublets"):
                from .doublets import load_doublets
                m["doublet"] = load_doublets(self.manifest["doublets"])
            self._metrics = m
        return self._metrics

    def guide_counts(self):
        """(cells x sgRNAs matrix in dataset cell order, sgRNA names); requires guide_counts in the manifest."""
        if self._guides is None:
            path = self.manifest.get("guide_counts") or self.design.guide_counts
            if not path or not os.path.exists(path):
                raise FileNotFoundError("this dataset has no guide counts")
            import anndata as ad
            g = ad.read_h5ad(path)
            if list(g.obs_names) != list(self.adata.obs_names):
                g = g[self.adata.obs_names]
            self._guides = (sp.csr_matrix(g.X), np.asarray(g.var_names))
        return self._guides

    def assignment_calls(self, choice):
        """Construct call per cell from a registered assignment fit (pertura.assignments)."""
        sets = self.manifest.get("assignments", {})
        if choice not in sets:
            raise KeyError(f"assignment {choice!r} is not registered for {self.name} (have {sorted(sets)})")
        from .assignments import load_calls
        calls = load_calls(sets[choice])
        if len(calls) != self.X.shape[0]:
            raise ValueError(f"assignment {choice} has {len(calls)} cells, the dataset {self.X.shape[0]}")
        return calls

    def groupset(self, spec):
        a = spec["assignment"]
        gc, names = self.guide_counts() if a.startswith("min_umi:") else (None, None)
        calls = self.assignment_calls(a) if a != "provided" and not a.startswith("min_umi:") else None
        metrics = self.metrics() if spec["qc"] != "provided" else None
        return build_groups(self.obs, self.design, spec, metrics=metrics, guide_counts=gc, sgrna_names=names,
                            group_map=self.group_map(), assignment_calls=calls)


class DataWithoutUnit(Data):
    """The dataset without one unit's cells, for the skeptic's leave-one-unit-out reruns (pertura_research.retired.checks). Its name
    and fingerprint say which unit is left out. Guide counts align by cell name, so min_umi assignments work;
    registrations aligned by position (assignment fits, program and state sets) are not available on the view."""

    def __init__(self, manifest, unit):
        super().__init__(manifest)
        if not self.design.unit_col:
            raise ValueError(f"{self.name} has no unit column")
        self.unit = str(unit)
        self.name = f"{self.name}__without_{self.unit}"
        self.fingerprint = hashlib.sha256(f"{self.fingerprint}|without:{self.unit}".encode()).hexdigest()

    @property
    def adata(self):
        if self._adata is None:
            import anndata as ad
            a = ad.read_h5ad(self.manifest["h5ad"])
            keep = a.obs[self.design.unit_col].astype(str).to_numpy() != self.unit
            if keep.all():
                raise ValueError(f"no cell belongs to unit {self.unit!r}")
            self._keep = keep
            a = a[keep].copy()
            a.X = sp.csr_matrix(a.X)
            self._adata = a
        return self._adata

    def metrics(self):
        """The full dataset's per-cell metrics on the view's cells: doublet calls are stored for every cell of the
        dataset, so they are subset like the cells (found by an analyst agent's session, 2026-10-08)."""
        if self._metrics is None:
            m = cell_metrics(self.X, self.genes)
            m["original"] = False
            m.loc[self.originals(), "original"] = True
            if self.manifest.get("doublets"):
                from .doublets import load_doublets
                m["doublet"] = load_doublets(self.manifest["doublets"])[self._keep]
            self._metrics = m
        return self._metrics

    def assignment_calls(self, choice):
        raise NotImplementedError("assignment fits are aligned by position; leave-one-unit-out uses provided or "
                                  "min_umi assignments")

    def program_usage(self, set_id):
        raise NotImplementedError("program sets are aligned by position")

    def state_labels(self, set_id):
        raise NotImplementedError("state sets are aligned by position")
