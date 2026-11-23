"""Environment layer: which interpreter runs each tool, and the package versions it ran with.

Tools declare an environment ('python' or 'r45') and the packages that determine their results. Those versions are
read once per process, stored with every result, and part of the cache key, so an upgraded package never serves stale
results. A third environment, 'py', holds crispat (guide assignment), which runs in its own interpreter.

The exported specs of the server environments are in envs/specs/ at the repository root, for reinstallation:
- pertpy: Python (scanpy, pertpy, decoupler, pydeseq2);
- r45-singlecell: R 4.5 (edgeR, limma, DESeq2, glmGamPoi, MAST, sceptre, sccomp, ...).
"""
import functools
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys

BASE = os.path.expanduser(os.environ.get("PERTURA_BASE", "~/pertura"))  # where the environments and resources live
R_HOME = os.environ.get("PERTURA_R_HOME", f"{BASE}/envs/r45-singlecell")
PY_ENV = os.environ.get("PERTURA_PY_ENV", f"{BASE}/envs/py/bin/python")
ENVIRONMENTS = {
    "python": {"kind": "python", "executable": sys.executable},
    "r45": {"kind": "R", "home": R_HOME, "rscript": f"{R_HOME}/bin/Rscript"},
    "py": {"kind": "python", "executable": PY_ENV},
}
_VERSIONS_CODE = """import importlib.metadata as m, json, sys
out = {}
for p in sys.argv[1:]:
    try:
        out[p] = m.version(p)
    except m.PackageNotFoundError:
        out[p] = "missing"
print(json.dumps(out))
"""


def rscript():
    return ENVIRONMENTS["r45"]["rscript"]


MAGECK = os.environ.get("PERTURA_MAGECK", f"{BASE}/envs/mageck_on_pertpy/bin/mageck")


def mageck():
    """MAGeCK's command (installed in a virtual environment of its own on the pertpy interpreter)."""
    return MAGECK


def mageck_env(extra=None):
    """Process environment for MAGeCK: its bin directory first on PATH, where its compiled RRA step lives."""
    return {**os.environ, "PATH": os.path.dirname(MAGECK) + ":" + os.environ.get("PATH", ""), **(extra or {})}


CNMF_PYTHON = os.environ.get("PERTURA_CNMF_PYTHON", f"{BASE}/envs/cnmf_on_pertpy/bin/python")


def cnmf_python():
    """The interpreter of cNMF's virtual environment (on the pertpy interpreter, with cnmf installed in it)."""
    return CNMF_PYTHON


R_LIBS_EXTRA = os.environ.get("PERTURA_R_LIBS", f"{BASE}/r_libs")  # packages Pertura added


def counts_for_r(path, X):
    """Cells x genes counts written as R's Matrix::readMM reads them (genes x cells, Matrix Market, gzip), at gzip's
    fastest level: R reads the same counts whatever the level, and a large screen's matrix is written in minutes."""
    import gzip
    import scipy.io
    import scipy.sparse as sp
    with gzip.open(path, "wb", compresslevel=1) as fh:
        scipy.io.mmwrite(fh, sp.csr_matrix(X).T.tocoo())


def r_env(extra=None):
    """Process environment for R: its bin directory first on PATH (the conda compilers and libraries live there);
    the library of packages Pertura added (GeneNMF) before the environment's own, so the shared environment is left
    as it is. data.table's threads follow the process's thread cap (OMP_NUM_THREADS), since its own default is half
    the cores; a cap given in extra (OMP_NUM_THREADS, an R process of several run side by side) holds for BLAS and
    data.table too, unless extra sets them."""
    extra = dict(extra or {})
    cap = extra.get("OMP_NUM_THREADS")
    threads = cap or os.environ.get("R_DATATABLE_NUM_THREADS") or os.environ.get("OMP_NUM_THREADS") or "1"
    capped = {v: cap for v in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")} if cap else {}
    libs = {}
    if os.path.isdir(R_LIBS_EXTRA):
        libs["R_LIBS"] = R_LIBS_EXTRA + (os.pathsep + os.environ["R_LIBS"] if os.environ.get("R_LIBS") else "")
    return {**os.environ, "PATH": f"{R_HOME}/bin:" + os.environ.get("PATH", ""), "R_DATATABLE_NUM_THREADS": threads,
            **libs, **capped, **extra}


@functools.lru_cache(maxsize=None)
def versions(environment, packages):
    """{package: version} for a tuple of packages in an environment ('missing' if not installed)."""
    if not packages:
        return {}
    if ENVIRONMENTS[environment]["kind"] == "python" and ENVIRONMENTS[environment]["executable"] != sys.executable:
        r = subprocess.run([ENVIRONMENTS[environment]["executable"], "-c", _VERSIONS_CODE, *packages],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"could not read package versions in {environment}: {r.stderr[-500:]}")
        return json.loads(r.stdout)
    if ENVIRONMENTS[environment]["kind"] == "python":
        out = {}
        for p in packages:
            try:
                out[p] = importlib.metadata.version(p)
            except importlib.metadata.PackageNotFoundError:
                out[p] = "missing"
        return out
    pkgs = ",".join(f'"{p}"' for p in packages)
    code = (f"for (p in c({pkgs})) cat(p, tryCatch(as.character(packageVersion(p)), error = function(e) 'missing'), "
            "'\\n')")
    r = subprocess.run([rscript(), "-e", code], env=r_env(), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"could not read R package versions: {r.stderr[-500:]}")
    out = dict(line.split() for line in r.stdout.splitlines() if line.strip())
    out["R"] = subprocess.run([rscript(), "-e", "cat(R.version.string)"], env=r_env(), capture_output=True,
                              text=True).stdout.strip()
    return out


# the field's packages by what they are used for (docs/build_goal_session6_fixes.md, item 14), in the Python
# environment the notebook runs in and the R one its cells reach
FIELD_PACKAGES = {
    "tests of genes": {"python": ("scanpy", "pydeseq2", "statsmodels", "scipy"),
                       "r45": ("edgeR", "limma", "DESeq2", "MAST", "glmGamPoi", "Seurat", "sceptre")},
    "batches": {"python": ("harmonypy", "bbknn", "scanorama"), "r45": ("harmony",)},
    "states and programs": {"python": ("scanpy", "leidenalg", "scikit-learn"), "r45": ()},
    "TF and pathway activity": {"python": ("decoupler",), "r45": ()},
    "responses to a perturbation": {"python": ("pertpy",), "r45": ()},
    "gene sets and enrichment": {"python": ("gseapy",), "r45": ("clusterProfiler", "msigdbr")},
    "composition": {"python": (), "r45": ("speckle", "sccomp")},
}


@functools.lru_cache(maxsize=1)
def field_packages():
    """{use: {"installed": ["name version", ...], "missing": [...]}} for FIELD_PACKAGES, read once per process; an
    environment that cannot be read is said so, never guessed."""
    found, unread = {}, []
    for env in ("python", "r45"):
        names = sorted({p for use in FIELD_PACKAGES.values() for p in use[env]})
        try:
            found[env] = versions(env, tuple(names))
        except (OSError, RuntimeError, ValueError) as e:
            found[env] = {}
            unread.append(f"{'R' if env == 'r45' else 'Python'}: {str(e)[:120]}")
    out = {}
    for use, by_env in FIELD_PACKAGES.items():
        have, lack = [], []
        for env, names in by_env.items():
            for p in names:
                v = found.get(env, {}).get(p)
                if v and v != "missing":
                    have.append(f"{p} {v}" + (" (R)" if env == "r45" else ""))
                elif v == "missing":
                    lack.append(p + (" (R)" if env == "r45" else ""))
        out[use] = {"installed": have, **({"missing": lack} if lack else {})}
    if unread:
        out["not read"] = {"installed": [], "missing": [], "why": unread}
    return out


def fingerprint(environment, packages):
    return hashlib.sha256(json.dumps(versions(environment, tuple(packages)), sort_keys=True).encode()).hexdigest()
