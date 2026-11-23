"""Readers of a screen deposit: what files it holds, how they pair up, and their contents. Formats only: no file's
content is interpreted from its names.

Recognised files (in the deposit folder and two levels below it):
- 10x matrices: '*.h5' files (Cell Ranger HDF5), and matrix/features/barcodes triplets sharing a prefix
  ('<prefix>matrix.mtx.gz', '<prefix>features.tsv.gz' or genes.tsv.gz, '<prefix>barcodes.tsv.gz'). Triplets with the
  same barcodes are one set of cells read twice (e.g. a gene-expression and a guide matrix): the one with more
  features is the sample, the others are extra readouts of it;
- guide calls: Cell Ranger's protospacer_calls_per_cell.csv(.gz) and call tables with the same columns (comma- or
  tab-separated), and cells_per_protospacer.json(.gz);
- feature references: CSVs with Cell Ranger's feature reference columns (id, name, feature_type, ...);
- Cell Ranger configurations: multi configs ('[gene-expression]', '[libraries]', '[samples]' sections) and library
  CSVs (fastqs, sample, library_type columns), recognised by their content;
- other tables (sample sheets, cell metadata): CSV and TSV files with a header, listed for reading;
- processed objects: '*.h5ad';
- archives: '*.tar' and '*.tar.gz', listed but not opened (they need extracting first);
- GEO metadata: '*family.soft.gz'.
A Mac's metadata files (AppleDouble '._*' files, __MACOSX folders, .DS_Store) are skipped.
Layouts: per_sample_h5, per_sample_mtx, combined_mtx (one matrix, possibly of several samples told apart by barcode
suffixes), processed_h5ad, archive_only, or mixed.
Matrices are paired with call files by name, or, for one combined matrix and several call files, by the share of
each call file's barcodes the matrix holds (a call file's '-1' suffixes may stand for another suffix of the matrix).
Features without a declared type take the type the feature reference declares for them (Cell Ranger's default for
two-column feature files is gene expression).
"""
import gzip
import hashlib
import io
import json
import os
import re

import numpy as np
import pandas as pd
import scipy.io
import scipy.sparse as sp

GUIDE = "CRISPR Guide Capture"
ANTIBODY = "Antibody Capture"
NON_TARGETING = "Non-Targeting"  # Cell Ranger's documented target_gene_name for non-targeting guides
CALL_COLUMNS = {"cell_barcode", "feature_call", "num_features", "num_umis"}
MIN_CALL_OVERLAP = 0.9  # pairing by barcodes: share of a call file's barcodes the matrix must hold


def _open(path):
    return gzip.open(path, "rt", errors="replace") if path.endswith(".gz") else open(path, errors="replace")


def _text(path):
    """A text file's content with every line ending (\\r\\n, \\r) made \\n."""
    with _open(path) as fh:
        return fh.read().replace("\r\n", "\n").replace("\r", "\n")


MAC_FOLDER = "__MACOSX"


def mac_metadata(name):
    """True for the metadata archives made on a Mac carry beside the real files: AppleDouble files ('._matrix.mtx.gz',
    holding only the file's attributes, not its content), __MACOSX folders and .DS_Store."""
    parts = name.replace("\\", "/").split("/")
    return MAC_FOLDER in parts or parts[-1].startswith("._") or parts[-1] == ".DS_Store"


def _files(root, depth=3):
    out = []
    root = os.path.abspath(root)
    for d, dirs, files in os.walk(root):
        if d[len(root):].count(os.sep) >= depth:
            dirs[:] = []
        dirs[:] = [x for x in dirs if x != MAC_FOLDER]
        out += [os.path.join(d, f) for f in files if not mac_metadata(f)]
    return sorted(out)


def _stem(path, suffixes):
    b = os.path.basename(path)
    for s in suffixes:
        if b.endswith(s):
            return b[: -len(s)]
    return b


def _sample_name(stem):
    """A readable sample name: the file stem without GEO sample IDs and trailing separators."""
    s = re.sub(r"^GSM\d+_", "", stem)
    s = re.sub(r"^GSE\d+_", "", s)
    return s.strip("_.-") or stem


EVERY_DROPLET = 1_000_000  # barcodes in one lane above which a matrix holds every droplet: Cell Ranger's raw matrix
# holds its barcode list's (3.6 million or more since v3), its filtered one the called cells (tens of thousands)
CELLRANGER_FOLDERS = {"filtered_feature_bc_matrix", "raw_feature_bc_matrix", "sample_filtered_feature_bc_matrix",
                      "sample_raw_feature_bc_matrix", "outs", "count", "per_sample_outs", "multi"}


def _folder_name(path, root):
    """The sample folder of a file with a generic name (Cell Ranger's <sample>/outs/filtered_feature_bc_matrix/
    matrix.mtx.gz, or <sample>/filtered_feature_bc_matrix.h5): the nearest parent folder that is not one of Cell
    Ranger's own, below the deposit's root; None if there is none."""
    d = os.path.dirname(os.path.abspath(path))
    root = os.path.abspath(root)
    while d.startswith(root) and d != root:
        if os.path.basename(d) not in CELLRANGER_FOLDERS:
            return os.path.basename(d)
        d = os.path.dirname(d)
    return None


def _lines(path, n=3):
    try:
        with _open(path) as fh:
            return [fh.readline().strip() for _ in range(n)]
    except (OSError, UnicodeDecodeError, EOFError):
        return []


def _fingerprint_of(barcodes):
    return hashlib.sha256("\n".join(sorted(barcodes)).encode()).hexdigest()


def _barcode_fingerprint(path):
    """The set of a barcodes file's barcodes, whatever their order, as a hash."""
    with _open(path) as fh:
        return _fingerprint_of(line.strip() for line in fh if line.strip())


def _n_lines(path):
    with _open(path) as fh:
        return sum(1 for _ in fh)


MTX = ("matrix.mtx.gz", "matrix.mtx")
H5_SUFFIXES = ("_filtered_feature_bc_matrix.h5", "filtered_feature_bc_matrix.h5", ".count_matrix.h5", ".h5")
CALL_SUFFIXES = ("_protospacer_calls_per_cell.csv.gz", "protospacer_calls_per_cell.csv.gz",
                 ".protospacer_calls_per_cell.csv.gz", "_protospacer_calls_per_cell.csv", ".csv.gz", ".txt.gz",
                 "_cells_per_protospacer.json.gz", ".json.gz")
TABLE_SUFFIXES = (".csv", ".csv.gz", ".tsv", ".tsv.gz", ".txt", ".txt.gz")


def _kind_of_table(path):
    """'multi_config', 'library_csv', 'feature_reference', 'calls' or 'table' for a CSV/TSV file, from its content."""
    head = _lines(path, 3)
    if not head or not head[0]:
        return None
    if any(h.startswith("[") and h.rstrip(",").endswith("]") for h in head):
        return "multi_config"
    cols = {c.strip().lower() for c in re.split(r"[,\t;]", head[0])}
    if {"fastqs", "sample", "library_type"} <= cols:
        return "library_csv"
    if {"id", "name", "feature_type"} <= cols:
        return "feature_reference"
    if CALL_COLUMNS <= cols:
        return "calls"
    return "table"


def _mtx_shape(path):
    """(rows, columns) from a Matrix Market file's size line, without reading its entries; None if unreadable."""
    try:
        with _open(path) as fh:
            for line in fh:
                if not line.startswith("%"):
                    r, c = line.split()[:2]
                    return int(r), int(c)
    except (OSError, ValueError, EOFError):
        return None
    return None


def _shared_features(m, files, own):
    """The features file a matrix without its own shares with others (one deposit lists a features file once for
    several matrices): among the folder's features files, those with as many lines as the matrix has features, and
    of those, the ones no other matrix claims by name. None unless exactly one is left."""
    shape = _mtx_shape(m)
    if shape is None:
        return None
    folder = [f for f in files if os.path.dirname(f) == os.path.dirname(m)
              and f.endswith(("features.tsv.gz", "features.tsv", "genes.tsv.gz", "genes.tsv"))]
    fits = [f for f in folder if _n_lines(f) in shape]
    free = [f for f in fits if f not in own]
    pick = free if free else fits
    return pick[0] if len(pick) == 1 else None


ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar")
EXTRACTED_MARKER = ".extracted"


def _archive_stem(path):
    b = os.path.basename(path)
    for s in ARCHIVE_SUFFIXES:
        if b.endswith(s):
            b = b[: -len(s)]
            break
    for s in ("_filtered_feature_bc_matrix", "_raw_feature_bc_matrix"):  # Cell Ranger's folder names
        if b.endswith(s):
            b = b[: -len(s)]
    return b


def already_extracted(archive, files):
    """True when nearly all of the archive's files already sit in the deposit (files: its paths), so extracting it
    again would read the same samples twice."""
    import tarfile
    with tarfile.open(archive) as tf:
        members = [os.path.basename(m.name) for m in tf.getmembers() if m.isfile() and not mac_metadata(m.name)]
    present = {os.path.basename(f) for f in files}
    return bool(members) and sum(m in present for m in members) >= 0.9 * len(members)


def extract_archives(archives, dest):
    """Each archive into dest/<archive stem>/, and the archives found inside into folders of their own, one level deep
    (per-sample archives often hold files with the same generic names, which would overwrite each other). Members
    with unsafe paths are refused (tarfile's data filter), and a Mac's metadata files are left out. A folder already
    holding the marker is not extracted again. Returns the folders."""
    import tarfile

    def unpack(path, to):
        with tarfile.open(path) as tf:
            tf.extractall(to, members=[m for m in tf.getmembers() if not mac_metadata(m.name)], filter="data")

    out = []
    for a in archives:
        folder = os.path.join(dest, _archive_stem(a))
        marker = os.path.join(folder, EXTRACTED_MARKER)
        if not os.path.exists(marker):
            os.makedirs(folder, exist_ok=True)
            unpack(a, folder)
            inner = [os.path.join(d, f) for d, _, fs in os.walk(folder) for f in fs if f.endswith(ARCHIVE_SUFFIXES)]
            for i in inner:
                sub = os.path.join(os.path.dirname(i), _archive_stem(i))
                os.makedirs(sub, exist_ok=True)
                unpack(i, sub)
            with open(marker, "w") as fh:
                fh.write(json.dumps({"archive": a, "inner": [os.path.relpath(i, folder) for i in inner]}) + "\n")
        out.append(folder)
    return out


def open_deposit(root, extract_to=None, extract=True, cell_tables=None):
    """(discover's result, folders extracted into extract_to, archives whose files already sit in the deposit).
    Archives are extracted into extract_to only when given, and only those not already extracted beside them; with
    extract False, only the archives extracted there before are read and none is extracted now. A deposit read into
    cell tables (cell_tables) is read from them, and nothing is extracted."""
    from .. import cell_table
    if cell_table.samples(cell_tables):
        return discover(root, cell_tables=cell_tables), [], []
    dep = discover(root)
    extracted, beside = [], []
    if dep["archives"] and extract_to:
        here = _files(root, depth=4)
        beside = [a for a in dep["archives"] if already_extracted(a, here)]
        todo = [a for a in dep["archives"] if a not in beside]
        if not extract:
            todo = [a for a in todo if os.path.exists(os.path.join(extract_to, _archive_stem(a), EXTRACTED_MARKER))]
        if todo:
            extracted = extract_archives(todo, extract_to)
            dep = discover(root, also=extracted)
        dep["archives"] = [a for a in dep["archives"] if a not in beside]
    return dep, extracted, beside


def discover(root, also=(), cell_tables=None):
    """The deposit's files by role, its samples (matrix with its calls, when they pair), and its layout. also: other
    folders read as part of the deposit (its extracted archives); archives already extracted there are left out.
    cell_tables: the project's cell-table folder (pertura.cell_table); when it holds a manifest, its samples are the
    deposit's samples (layout 'cell_tables'), and the deposit's own files are listed by name only. A matrix whose
    files cannot be opened is listed under 'unreadable' with the error, not read."""
    from .. import cell_table
    tabled = cell_table.samples(cell_tables)
    if tabled:
        return _listed_by_name(root, also, tabled)
    files = _files(root)
    roots = [os.path.abspath(root)]
    extracted = set()
    for extra in also:
        files += [f for f in _files(extra, depth=4) if not f.endswith(EXTRACTED_MARKER)]
        roots.append(os.path.abspath(extra))
        marker = os.path.join(extra, EXTRACTED_MARKER)
        if os.path.exists(marker):
            with open(marker) as fh:
                info = json.loads(fh.readline())
            extracted.add(os.path.abspath(info["archive"]))
            extracted |= {os.path.abspath(os.path.join(extra, i)) for i in info.get("inner", [])}
    files = [f for f in files if os.path.abspath(f) not in extracted]
    h5 = [f for f in files if f.endswith(".h5")]
    mtx = [f for f in files if f.endswith(MTX)]
    triplets, own, unreadable = [], set(), []
    for m in mtx:  # the features files matrices claim by name
        prefix = m[: -len("matrix.mtx.gz")] if m.endswith(".gz") else m[: -len("matrix.mtx")]
        own |= {prefix + x for x in ("features.tsv.gz", "genes.tsv.gz", "features.tsv", "genes.tsv")
                if os.path.exists(prefix + x)}
    for m in mtx:
        prefix = m[: -len("matrix.mtx.gz")] if m.endswith(".gz") else m[: -len("matrix.mtx")]
        feats = next((prefix + x for x in ("features.tsv.gz", "genes.tsv.gz", "features.tsv", "genes.tsv")
                      if os.path.exists(prefix + x)), None)
        shared = False
        if feats is None:  # a features file listed once for several matrices, matched by its number of features
            try:
                feats, shared = _shared_features(m, files, own), True
            except Exception as e:  # a file the readers cannot open
                unreadable.append({"file": m, "error": f"{type(e).__name__}: {e}"})
                continue
        bcs = next((prefix + x for x in ("barcodes.tsv.gz", "barcodes.tsv") if os.path.exists(prefix + x)), None)
        if feats and bcs:
            triplets.append({"matrix": m, "features": feats, "barcodes": bcs, "prefix": prefix,
                             **({"features_shared": True} if shared else {})})
    # triplets with the same barcodes are one set of cells: the largest is the sample, the others extra readouts
    by_cells = {}
    for t in triplets:
        try:
            fp, t["n_features"] = _barcode_fingerprint(t["barcodes"]), _n_lines(t["features"])
        except Exception as e:  # a file the readers cannot open
            unreadable.append({"file": t["matrix"], "error": f"{type(e).__name__}: {e}"})
            continue
        by_cells.setdefault(fp, []).append(t)
    merged = []
    for group in by_cells.values():
        group = sorted(group, key=lambda t: -t["n_features"])
        main = dict(group[0])
        if len(group) > 1:
            main["extra"] = [{k: t[k] for k in ("matrix", "features", "barcodes")} for t in group[1:]]
        merged.append(main)
    used = {f for t in merged for f in (t["features"], t["barcodes"])} | \
        {f for t in merged for e in t.get("extra", []) for f in (e["features"], e["barcodes"])}
    named_calls = [f for f in files if re.search(r"(protospacer_calls|guidecalls|guide_calls|cells_per_protospacer)",
                                                 os.path.basename(f).lower())]
    refs = [f for f in files if re.search(r"feature_?ref", os.path.basename(f).lower()) and f.endswith((".csv", ".csv.gz"))]
    refs += [f for f in files if re.search(r"(gdo|guide)_?(features|template)", os.path.basename(f).lower())
             and f not in refs]
    refs = [f for f in refs if f not in used]  # a guide matrix's own features file is not a feature reference
    configs, tables, calls = [], [], list(named_calls)
    for f in files:
        if not f.endswith(TABLE_SUFFIXES) or f in used or f in refs or f in named_calls:
            continue
        kind = _kind_of_table(f)
        if kind in ("multi_config", "library_csv"):
            configs.append(f)
        elif kind == "feature_reference":
            refs.append(f)
        elif kind == "calls":
            calls.append(f)
        elif kind == "table":
            tables.append(f)
    objects = [f for f in files if f.endswith(".h5ad")]
    archives = [f for f in files if f.endswith((".tar", ".tar.gz", ".tgz"))]
    soft = [f for f in files if f.endswith("family.soft.gz")]
    tables = [t for t in tables if t not in soft]
    def name(stem, path):  # a generic file name ('matrix.mtx.gz') is named by its sample folder
        if stem:
            return _sample_name(stem)
        home = next((r for r in roots if os.path.abspath(path).startswith(r + os.sep)), roots[0])
        return _sample_name(_folder_name(path, home) or "matrix")
    samples = [{"name": name(_stem(f, H5_SUFFIXES).rstrip("_.-"), f), "kind": "h5", "matrix": f} for f in h5]
    samples += [{"name": name(os.path.basename(t["prefix"]).rstrip("_.-"), t["matrix"]), "kind": "mtx",
                 "matrix": t["prefix"], "features": t["features"], "barcodes": t["barcodes"],
                 **({"extra": t["extra"]} if t.get("extra") else {})} for t in merged]
    taken = [s["name"] for s in samples]
    for s in samples:  # two samples of one name (T/Library1, UT/Library1) are named by their folders from the root
        if taken.count(s["name"]) > 1:
            m = os.path.abspath(s["matrix"])
            home = next((r for r in roots if m.startswith(r + os.sep)), roots[0])
            folders = os.path.relpath(os.path.dirname(m) if s["kind"] == "h5" else m, home).split(os.sep)
            s["name"] = _sample_name("_".join(f for f in folders if f not in CELLRANGER_FOLDERS and f != os.curdir))
    duplicates = []  # a triplet holding the same cells as an h5 file is the same sample read twice
    # a capture's matrix of every droplet beside its filtered matrix (Cell Ranger's raw_feature_bc_matrix and
    # filtered_feature_bc_matrix): the filtered one holds its called cells, the raw one is the same capture (GSE335887:
    # 3.6 million barcodes read as two more samples, and the build ran past 170 GB)
    paths = {os.path.abspath(s["matrix"]) for s in samples}
    raws = [s for s in samples if "raw_feature_bc_matrix" in os.path.abspath(s["matrix"]) and
            os.path.abspath(s["matrix"]).replace("raw_feature_bc_matrix", "filtered_feature_bc_matrix") in paths]
    duplicates += [{"matrix": os.path.basename(s["matrix"].rstrip("/\\")) or s["matrix"],
                    "same_capture_as": "its filtered_feature_bc_matrix, which holds the called cells"} for s in raws]
    samples = [s for s in samples if s not in raws]
    every = []  # a matrix of every droplet with no matrix of its called cells beside it: not read as cells
    for s in samples:
        n = _most_barcodes_in_one_lane(s) if s["kind"] in ("h5", "mtx") else 0
        if n > EVERY_DROPLET:
            every.append({"matrix": s["matrix"], "barcodes_in_one_lane": n})
    samples = [s for s in samples if s["matrix"] not in {e["matrix"] for e in every}]
    h5_cells = {}
    for smp in samples:
        if smp["kind"] == "h5":
            try:
                h5_cells[_fingerprint_of(_sample_barcodes(smp))] = smp["name"]
            except (OSError, KeyError):
                pass
    if h5_cells:
        keep = []
        for smp in samples:
            fp = _barcode_fingerprint(smp["barcodes"]) if smp["kind"] == "mtx" else None
            if fp in h5_cells:
                duplicates.append({"matrix": os.path.basename(smp["matrix"]), "same_cells_as": h5_cells[fp]})
                continue
            keep.append(smp)
        samples = keep
    _pair_calls(samples, calls)
    if objects and not samples:
        layout = "processed_h5ad"
    elif samples and all(s["kind"] == "h5" for s in samples):
        layout = "per_sample_h5" if len(samples) > 1 else "single_h5"
    elif samples and all(s["kind"] == "mtx" for s in samples):
        layout = "per_sample_mtx" if len(samples) > 1 else "combined_mtx"
    elif samples:
        layout = "mixed"
    else:
        layout = "archive_only" if archives else "unrecognised"
    return {"root": os.path.abspath(root), "layout": layout, "samples": samples, "calls": calls,
            "feature_references": refs, "configs": configs, "tables": tables, "objects": objects,
            "archives": archives, "geo_soft": soft, "duplicates": duplicates, "unreadable": unreadable,
            "every_droplet": every}


def _listed_by_name(root, also, tabled):
    """discover's answer for a deposit read into cell tables: the tables' samples, and the deposit's other files by
    name (sample sheets, feature references, GEO metadata), none of them opened as matrices."""
    files = _files(root) + [f for e in also for f in _files(e, depth=4) if not f.endswith(EXTRACTED_MARKER)]
    refs = [f for f in files if re.search(r"feature_?ref", os.path.basename(f).lower())
            and f.endswith((".csv", ".csv.gz"))]
    soft = [f for f in files if f.endswith("family.soft.gz")]
    tables = [f for f in files if f.endswith(TABLE_SUFFIXES) and f not in refs and f not in soft
              and not re.search(r"(protospacer_calls|guidecalls|guide_calls)", os.path.basename(f).lower())]
    return {"root": os.path.abspath(root), "layout": "cell_tables", "samples": tabled,
            "calls": [s["calls"] for s in tabled if s.get("calls")], "feature_references": refs, "configs": [],
            "tables": tables, "objects": [], "archives": [], "geo_soft": soft, "duplicates": [], "unreadable": []}


def _sample_barcodes(sample):
    """A sample's barcodes as a set (an h5 file's, a cell table's or a triplet's barcodes file)."""
    if sample["kind"] == "cell_table":
        import anndata as ad
        return set(map(str, ad.read_h5ad(sample["matrix"], backed="r").obs_names))
    if sample["kind"] == "h5":
        import h5py
        with h5py.File(sample["matrix"], "r") as fh:
            return {x.decode() if isinstance(x, bytes) else str(x) for x in fh["matrix/barcodes"][:]}
    with _open(sample["barcodes"]) as fh:
        return {line.strip() for line in fh if line.strip()}


def _most_barcodes_in_one_lane(sample):
    """A matrix's barcodes in its largest lane (the suffix of Cell Ranger's barcodes: -1, -2); 0 where unread."""
    try:
        barcodes = pd.Series(list(_sample_barcodes(sample)), dtype=str)
    except (OSError, KeyError):  # an h5 file of another layout: its reader says what it holds
        return 0
    return int(barcodes.str.extract(r"-(\d+)$", expand=False).fillna("").value_counts().max()) if len(barcodes) else 0


def _pair_calls(samples, calls):
    """Pair each matrix with its call file(s): a single matrix with a single call file; otherwise each call file with
    the matrix holding its barcodes (directly, with another barcode suffix, or with a prefix the matrix adds); a call
    file no matrix holds is paired by the longest shared name after removing GEO sample IDs (a deposit may give the
    matrix and its calls different GSM IDs)."""
    if not calls:
        return
    if len(samples) == 1 and len(calls) == 1:
        samples[0]["calls"] = calls[0]
        return
    bcs = [_sample_barcodes(s) for s in samples]
    left = []
    for c in calls:
        cb = _call_barcodes(c)
        hit = None
        if cb:
            for i, b in enumerate(bcs):
                m = _match_calls(c, cb, b)
                if m:
                    hit = (i, m)
                    break
        if hit is None:
            left.append(c)
            continue
        i, m = hit
        samples[i].setdefault("calls", []).append(m)
        samples[i]["calls_pairing"] = "by barcodes"
    for s in samples:  # one call file whose barcodes match as they are: its path, as for a single pair
        c = s.get("calls")
        if isinstance(c, list) and len(c) == 1 and not c[0].get("suffix") and not c[0].get("prefix"):
            s["calls"] = c[0]["path"]
    names = {c: _sample_name(_stem(c, CALL_SUFFIXES)) for c in left}
    for s in samples:
        if s.get("calls"):
            continue
        exact = [c for c, n in names.items() if n == s["name"]]
        if len(exact) == 1:
            s["calls"] = exact[0]
            continue
        best, best_len = None, 0
        for c, n in names.items():
            k = len(os.path.commonprefix([s["name"], n]))
            if k > best_len and k >= max(3, int(0.8 * min(len(s["name"]), len(n)))):
                best, best_len = c, k
        if best:
            s["calls"] = best
            s["calls_pairing"] = "proposed (shared name prefix)"


def _call_barcodes(path):
    if path.endswith((".json", ".json.gz")):
        with _open(path) as fh:
            return {bc for bcs in json.load(fh).values() for bc in bcs}
    return set(read_call_table(path)["cell_barcode"].astype(str))


MIN_PREFIX_COVER = 0.9  # pairing by a barcode prefix: share of the matrix's cells with that prefix the call file holds
MAX_OTHER_COVER = 0.1   # ... while it holds at most this share of any other prefix's cells (chance barcode clashes)


def _best_prefix(cb, bcs):
    """The prefix that turns a call file's barcodes into the matrix's ('D1_' + 'AAAC...-1'), measured from the matrix
    barcodes that end with the call barcodes: the file must hold nearly all of that prefix's cells (the matrix may
    keep fewer cells than the calls cover) and few of any other's. None otherwise."""
    lengths = {len(b) for b in cb}
    if len(lengths) != 1:
        return None
    n = lengths.pop()
    by_end, size = {}, {}
    for b in bcs:
        if len(b) > n:
            p = b[:-n]
            by_end.setdefault(b[-n:], []).append(p)
            size[p] = size.get(p, 0) + 1
    counts = {}
    for b in cb:
        for p in by_end.get(b, ()):
            counts[p] = counts.get(p, 0) + 1
    if not counts:
        return None
    cover = sorted(((k / size[p], p) for p, k in counts.items()), reverse=True)
    best, second = cover[0], (cover[1][0] if len(cover) > 1 else 0.0)
    return best[1] if best[0] >= MIN_PREFIX_COVER and second <= MAX_OTHER_COVER else None


def _match_calls(path, cb, bcs):
    """{path, suffix, prefix} when the matrix (barcodes bcs) holds the call file's barcodes cb: at least
    MIN_CALL_OVERLAP of them directly, once their '-1' suffix is read as one of the matrix's suffixes, or once a
    barcode prefix the matrix adds is put in front (a combined matrix of several captures); else None."""
    if len(cb & bcs) >= MIN_CALL_OVERLAP * len(cb):
        return {"path": path, "suffix": None}
    suffixes = sorted({b.rsplit("-", 1)[1] for b in bcs if "-" in b})
    stems = {b.rsplit("-", 1)[0] for b in cb}
    best = max(suffixes, key=lambda x: len({f"{y}-{x}" for y in stems} & bcs), default=None)
    if best is not None and len({f"{y}-{best}" for y in stems} & bcs) >= MIN_CALL_OVERLAP * len(stems):
        return {"path": path, "suffix": best}
    prefix = _best_prefix(cb, bcs)
    return {"path": path, "suffix": None, "prefix": prefix} if prefix is not None else None


def _pair_by_barcodes(sample, calls):
    """[{path, suffix, prefix}] for the call files whose barcodes the matrix holds (_match_calls); [] if any file
    fails."""
    bcs = _sample_barcodes({"kind": "mtx", **sample} if "kind" not in sample else sample)
    out = []
    for c in calls:
        cb = _call_barcodes(c)
        m = _match_calls(c, cb, bcs) if cb else None
        if m is None:
            return []
        out.append(m)
    return out


def matrix_features(sample):
    """(names, declared types or None for files without a type column) of a matrix's features, without its counts."""
    if sample["kind"] == "cell_table":
        import anndata as ad
        var = ad.read_h5ad(sample["matrix"], backed="r").var
        return var["name"].astype(str).tolist(), var["feature_types"].astype(str).tolist()
    if sample["kind"] == "h5":
        import h5py
        with h5py.File(sample["matrix"], "r") as fh:
            names = [x.decode() if isinstance(x, bytes) else str(x) for x in fh["matrix/features/name"][:]]
            types = [x.decode() if isinstance(x, bytes) else str(x) for x in fh["matrix/features/feature_type"][:]]
        return names, types
    t = pd.read_csv(sample["features"], sep="\t", header=None)
    return t[1].astype(str).tolist(), (t[2].astype(str).tolist() if t.shape[1] > 2 else None)


def declared_types(feature_ref):
    """{feature id or name: feature_type} from a feature reference (empty without one)."""
    if feature_ref is None or "feature_type" not in feature_ref.columns:
        return {}
    out = {}
    for col in ("id", "name"):
        if col in feature_ref.columns:
            out.update(zip(feature_ref[col].astype(str), feature_ref["feature_type"].astype(str)))
    return out


def _read_triplet(matrix, features, barcodes, declared, conflicts=None):
    """A matrix triplet. conflicts, when given, collects features whose own type column says gene expression while
    the feature reference declares them guides: those are read as guides."""
    if not matrix.endswith(MTX):  # a prefix: its matrix file, compressed or not
        matrix = next((matrix + m for m in MTX if os.path.exists(matrix + m)), matrix + MTX[0])
    X = sp.csr_matrix(scipy.io.mmread(matrix).T)
    feats = pd.read_csv(features, sep="\t", header=None)
    bcs = pd.read_csv(barcodes, sep="\t", header=None)[0].astype(str).to_numpy()
    ids, names = feats[0].astype(str).to_numpy(), feats[1].astype(str).to_numpy()
    if feats.shape[1] > 2:
        types = feats[2].astype(str).to_numpy()
        # by feature ID only: a gene may share its name with a guide the reference declares (a guide named 'SYVN1')
        ref = np.array([declared.get(i, "") for i in ids])
        wrong = (types == "Gene Expression") & (ref == GUIDE)
        if wrong.any():
            types = np.where(wrong, GUIDE, types)
            if conflicts is not None:
                conflicts.append({"file": os.path.basename(features), "n": int(wrong.sum()),
                                  "examples": names[wrong][:5].tolist()})
    else:  # no type column: the reference's declared type, else Cell Ranger's default (gene expression)
        types = np.array([declared.get(i, declared.get(n, "Gene Expression")) for i, n in zip(ids, names)])
    return X, bcs, ids, names, types


def _sample_files(sample):
    """The files a sample's matrix is read from: its h5 file, or its triplet's (and its extra readouts')."""
    if sample["kind"] == "h5":
        return [sample["matrix"]]
    m = sample["matrix"]
    if not m.endswith(MTX):
        m = next((m + x for x in MTX if os.path.exists(m + x)), m + MTX[0])
    out = [m, sample["features"], sample["barcodes"]]
    for e in sample.get("extra", []):
        out += [e["matrix"], e["features"], e["barcodes"]]
    return out


def parsed_path(sample, declared, parsed):
    """Where a sample's parsed matrix is kept in the folder `parsed`: named for its files (paths, sizes and times) and
    the declared types, so a changed file is parsed again."""
    stamp = [[os.path.abspath(f), os.path.getsize(f), int(os.path.getmtime(f))] for f in _sample_files(sample)]
    key = hashlib.sha256(json.dumps([stamp, sorted((declared or {}).items())]).encode()).hexdigest()[:16]
    return os.path.join(parsed, f"{_sample_name(sample['name'])}-{key}.h5ad")


def read_matrix(sample, declared=None, parsed=None):
    """A sample's matrix as AnnData with var 'feature_types', 'gene_ids' and 'name' (the name before making names
    unique: a guide may share a gene's name). Extra readouts of the same cells are added as further features.
    declared: {feature id or name: type} from the feature reference, for features files without a type column.
    parsed: a folder where the matrix is kept once parsed and read from afterwards (text matrices take most of a
    deposit's reading time)."""
    import anndata as ad
    if sample["kind"] == "cell_table":  # read as written (pertura.cell_table): counts, var and the cells' columns
        a = ad.read_h5ad(sample["matrix"])
        for c in a.var.columns:
            a.var[c] = a.var[c].astype(str)
        a.X = sp.csr_matrix(a.X)
        return a
    if sample["kind"] != "mtx":  # an h5 file reads fast as it is
        parsed = None
    if parsed:
        path = parsed_path(sample, declared, parsed)
        if os.path.exists(path):
            a = ad.read_h5ad(path)
            for c in a.var.columns:  # h5ad keeps text columns as categories; as parsed, they are text
                a.var[c] = a.var[c].astype(str)
            notes = a.uns.pop("type_conflicts_json", None)
            a.uns = {"type_conflicts": json.loads(notes)} if notes else {}
            return a
    a = _parse_matrix(sample, declared or {})
    if parsed:
        os.makedirs(parsed, exist_ok=True)
        keep = a.copy()
        conflicts = keep.uns.pop("type_conflicts", None)
        keep.uns = {"type_conflicts_json": json.dumps(conflicts)} if conflicts else {}
        tmp = f"{path}.{os.getpid()}.tmp"  # written whole, then moved into place: a reader never sees half a file
        keep.write_h5ad(tmp)
        os.replace(tmp, path)
    return a


def _parse_into(args):
    """A worker's task: parse one sample's matrix into the parsed folder."""
    sample, declared, parsed = args
    try:  # one thread per worker: the workers share the thread limit (the cores are pinned in any case)
        from threadpoolctl import threadpool_limits
        threadpool_limits(1)
    except Exception:  # not installed, or unable to read this machine's libraries
        pass
    read_matrix(sample, declared, parsed)
    return sample["name"]


PARALLEL_FROM_BYTES = 100_000_000  # text matrices this large in all are parsed by several processes at once


def parse_matrices(samples, declared, parsed, workers):
    """Parse the samples' text matrices not yet in `parsed`, `workers` at a time in processes of their own when they
    are large, so the readers that follow read each from there. Returns the samples parsed now."""
    todo = [s for s in samples if s["kind"] == "mtx" and not os.path.exists(parsed_path(s, declared, parsed))]
    if not todo:
        return []
    size = sum(os.path.getsize(f) for s in todo for f in _sample_files(s))
    if workers <= 1 or len(todo) == 1 or size < PARALLEL_FROM_BYTES:
        for s in todo:
            read_matrix(s, declared, parsed)
        return [s["name"] for s in todo]
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(min(workers, len(todo)), mp_context=multiprocessing.get_context("spawn")) as ex:
        return list(ex.map(_parse_into, [(s, declared, parsed) for s in todo]))


def _parse_matrix(sample, declared):
    import anndata as ad
    if sample["kind"] == "h5":
        import scanpy as sc
        a = sc.read_10x_h5(sample["matrix"], gex_only=False)
        a.var["name"] = np.asarray(a.var_names).astype(str)
        a.var_names_make_unique()
        if declared:
            ids = a.var["gene_ids"].astype(str).to_numpy() if "gene_ids" in a.var else a.var["name"].to_numpy()
            ref = np.array([declared.get(i, "") for i in ids])  # by feature ID only, as for triplets
            wrong = (a.var["feature_types"].astype(str).to_numpy() == "Gene Expression") & (ref == GUIDE)
            if wrong.any():
                a.var["feature_types"] = np.where(wrong, GUIDE, a.var["feature_types"].astype(str))
                a.uns["type_conflicts"] = [{"file": os.path.basename(sample["matrix"]), "n": int(wrong.sum()),
                                            "examples": a.var["name"].to_numpy()[wrong][:5].tolist()}]
        return a
    conflicts = []
    X, bcs, ids, names, types = _read_triplet(sample["matrix"], sample["features"], sample["barcodes"], declared,
                                              conflicts)
    blocks, all_ids, all_names, all_types = [X], [ids], [names], [types]
    order = pd.Index(bcs)
    for e in sample.get("extra", []):
        Xe, be, ie, ne, te = _read_triplet(e["matrix"], e["features"], e["barcodes"], declared, conflicts)
        pos = pd.Index(be).get_indexer(order)  # the same cells, possibly in another order
        blocks.append(Xe[pos])
        all_ids.append(ie)
        all_names.append(ne)
        all_types.append(te)
    var = pd.DataFrame({"gene_ids": np.concatenate(all_ids), "feature_types": np.concatenate(all_types)},
                       index=np.concatenate(all_names))
    a = ad.AnnData(sp.hstack(blocks).tocsr(), obs=pd.DataFrame(index=bcs), var=var)
    a.var["name"] = np.asarray(a.var_names).astype(str)
    a.var_names_make_unique()
    if conflicts:
        a.uns["type_conflicts"] = conflicts
    return a


def read_call_table(path):
    """A call file as a table (cell_barcode, feature_call, num_features, num_umis), whatever its separator, or for
    cells_per_protospacer.json a table with one row per (cell, protospacer) and no UMIs."""
    if path.endswith((".json", ".json.gz")):
        with _open(path) as fh:
            d = json.load(fh)
        rows = [(bc, g) for g, bcs in d.items() for bc in bcs]
        t = pd.DataFrame(rows, columns=["cell_barcode", "feature"])
        g = t.groupby("cell_barcode")["feature"]
        return pd.DataFrame({"cell_barcode": g.size().index, "feature_call": g.agg("|".join).to_numpy(),
                             "num_features": g.size().to_numpy(), "num_umis": None})
    with _open(path) as fh:
        head = fh.readline()
    sep = "\t" if head.count("\t") > head.count(",") else ","
    t = pd.read_csv(path, sep=sep)
    missing = CALL_COLUMNS - set(t.columns)
    if missing:
        raise ValueError(f"{path}: not a call table (missing {sorted(missing)})")
    return t


def read_calls(calls):
    """One call table from a sample's call file, or from several paired by barcodes ([{path, suffix}]), with each
    file's '-1' suffixes rewritten to its paired suffix."""
    if isinstance(calls, str):
        return read_call_table(calls)
    parts = []
    for c in calls:
        t = read_call_table(c["path"])
        if c.get("suffix"):
            t = t.assign(cell_barcode=t["cell_barcode"].astype(str).str.rsplit("-", n=1).str[0] + f"-{c['suffix']}")
        if c.get("prefix"):
            t = t.assign(cell_barcode=c["prefix"] + t["cell_barcode"].astype(str))
        parts.append(t)
    return pd.concat(parts, ignore_index=True)


def split_calls(text, n, known):
    """Cell Ranger joins a cell's calls with '|', which some guide names also contain (e.g. 'rs199524|rs199523_1').
    Split text into exactly n known guide names; None if that is impossible."""
    tokens = text.split("|")

    def parts(i, left):
        if i == len(tokens):
            return [] if left == 0 else None
        for j in range(len(tokens), i, -1):
            name = "|".join(tokens[i:j])
            if name in known:
                rest = parts(j, left - 1)
                if rest is not None:
                    return [name] + rest
        return None
    return parts(0, n)


def read_feature_reference(path):
    """A feature reference as a table with at least 'name' and, when present, 'id', 'feature_type' and
    'target_gene_name'. Any line ending is read. Other guide tables (a 'guide template') are read as they are."""
    t = pd.read_csv(io.StringIO(_text(path)), sep=None, engine="python")
    t.columns = [str(c).strip() for c in t.columns]
    if "name" not in t.columns:
        for c in t.columns:
            if c.lower() in ("guide", "sgrna", "guide_name", "sgrna_name", "id"):
                t = t.rename(columns={c: "name"})
                break
    return t


def read_cellranger_config(path):
    """A Cell Ranger configuration as {section: rows}: a multi config's sections ('gene-expression' key-value rows;
    'libraries', 'samples' and other tables as lists of dicts), or a library CSV as {'libraries': rows}."""
    text = _text(path)
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if lines and not lines[0].startswith("["):
        sep = "\t" if lines[0].count("\t") > lines[0].count(",") else ","
        return {"libraries": pd.read_csv(io.StringIO(text), sep=sep).to_dict("records")}
    sections, name = {}, None
    for ln in lines:
        s = ln.strip().rstrip(",")
        if s.startswith("[") and s.endswith("]"):
            name = s[1:-1].strip().lower()
            sections[name] = []
        elif name is not None:
            sections[name].append(ln)
    out = {}
    for name, rows in sections.items():
        first = [c.strip() for c in rows[0].split(",")] if rows else []
        if name in ("libraries", "samples") or (len(first) > 2 and all(first)):
            out[name] = pd.read_csv(io.StringIO("\n".join(rows)), sep=",").to_dict("records") if rows else []
        else:  # key,value rows (e.g. [gene-expression] reference,/path)
            out[name] = [dict(zip(("key", "value"), [c.strip() for c in r.split(",", 1)])) for r in rows]
    return out


def read_geo_soft(path):
    """GEO family SOFT: the series' title, summary and overall design, and each sample's title, characteristics
    (key: value) and descriptions."""
    series, samples, cur = {"title": "", "summary": [], "overall_design": []}, {}, None
    with gzip.open(path, "rt", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("^SAMPLE"):
                cur = line.split("=", 1)[1].strip()
                samples[cur] = {"title": "", "characteristics": {}, "description": []}
            elif line.startswith("!Series_title") and cur is None:
                series["title"] = line.split("=", 1)[1].strip()
            elif line.startswith("!Series_summary") and cur is None:
                series["summary"].append(line.split("=", 1)[1].strip())
            elif line.startswith("!Series_overall_design") and cur is None:
                series["overall_design"].append(line.split("=", 1)[1].strip())
            elif cur and line.startswith("!Sample_title"):
                samples[cur]["title"] = line.split("=", 1)[1].strip()
            elif cur and line.startswith("!Sample_characteristics"):
                kv = line.split("=", 1)[1].strip()
                k, _, v = kv.partition(":")
                samples[cur]["characteristics"].setdefault(k.strip().lower(), []).append(v.strip())
            elif cur and line.startswith("!Sample_description"):
                samples[cur]["description"].append(line.split("=", 1)[1].strip())
    return {"series": series, "samples": samples}
