"""The modules' calls to the field's packages (docs/adr/0022, section 15, point 4).

Each call is recorded in the run, in its result's diagnostics under package_calls: the package and its installed
version, the function, the data layer it was given, the design formula, the contrast, the covariates and the
parameters set. Before the call, input the package does not take is refused (check_layer): a test of raw counts
given log-normalised values, or the other way round.

    rec = packages.call("edgeR", "glmQLFit, glmQLFTest", data=counts, layer="raw_counts", formula="~ unit + group",
                        contrast="each target against the control cells", covariates=["sample"])
"""
import numpy as np
import scipy.sparse as sp

LAYERS = {
    "raw_counts": "raw counts: non-negative integers",
    "lognorm": "log-normalised expression (log1p of counts per 10,000): non-negative, not all integers",
}
ENVIRONMENT = {"edgeR": "r45", "limma": "r45", "MAST": "r45", "Seurat": "r45", "sceptre": "r45", "DESeq2": "r45",
               "glmGamPoi": "r45", "speckle": "r45", "clusterProfiler": "r45",
               "fgsea": "r45"}  # every other package is the Python environment's
SAMPLE = 50000  # values looked at to tell the layer


class PackageInputError(ValueError):
    pass


def _values(data):
    v = data.data if sp.issparse(data) else np.asarray(data, dtype=float).ravel()
    v = np.asarray(v, dtype=float)
    if len(v) > SAMPLE:
        v = v[np.random.default_rng(0).choice(len(v), SAMPLE, replace=False)]
    return v[np.isfinite(v)]


def check_layer(data, layer, what):
    """Refuse data that is not the layer `what` takes (PackageInputError)."""
    if layer not in LAYERS:
        raise ValueError(f"layer is one of {', '.join(LAYERS)}")
    v = _values(data)
    if not len(v):
        return
    integers = bool(np.allclose(v, np.round(v)))
    if (v < 0).any():
        raise PackageInputError(f"{what} takes {LAYERS[layer]}; these values have negatives (scaled data?)")
    if layer == "raw_counts" and not integers:
        raise PackageInputError(f"{what} takes {LAYERS[layer]}; these values are not integers (log-normalised?)")
    if layer == "lognorm" and integers and v.max() > 20:
        raise PackageInputError(f"{what} takes {LAYERS[layer]}; these values are integers up to {v.max():.0f} "
                                "(raw counts?)")


def version(package):
    """The installed version of a package, from its environment ('unknown' where it cannot be read here)."""
    from .. import envs
    try:
        return envs.versions(ENVIRONMENT.get(package, "python"), (package,)).get(package, "unknown")
    except (OSError, RuntimeError):
        return "unknown"


class seeded_draws:
    """Within the block, each numpy.random.default_rng() call made without a seed gets the next seed of a sequence
    started at `seed`, so a package that draws its permutations that way (pertpy's DistanceTest) gives the same
    result on a rerun. Its own code path is unchanged; calls that pass a seed are left alone."""

    def __init__(self, seed):
        self.seed = int(seed)

    def __enter__(self):
        import itertools
        self._orig = np.random.default_rng
        counter = itertools.count()
        orig = self._orig
        np.random.default_rng = lambda seed=None: orig(seed if seed is not None else [self.seed, next(counter)])
        return self

    def __exit__(self, *exc):
        np.random.default_rng = self._orig
        return False


def call(package, function, data=None, layer=None, formula=None, contrast=None, covariates=(), **params):
    """Check the data against the layer the function takes, and return the call's record."""
    if data is not None:
        check_layer(data, layer, f"{package} {function}")
    return {"package": package, "version": version(package), "function": function, "layer": layer,
            "formula": formula, "contrast": contrast, "covariates": list(covariates),
            "params": {k: v for k, v in params.items()}}
