"""Run one crispat guide-assignment method. Executed with the crispat environment's interpreter (envs 'py'), not
imported by pertura, so it only needs crispat, anndata and pandas.

Usage: python crispat_runner.py <guide_counts.h5ad> <work dir> <method> [threshold]
  method: umi, max, ratio, quantiles (rule-based; threshold where crispat takes one), 2beta, 3beta, gauss,
  poisson_gauss, poisson, binomial, negative_binomial (models; serial, so one thread)
The input's obs must hold 'batch' as integer codes (crispat's count models need them).
Writes <work dir>/assignments.csv (cell, gRNA, ...) as crispat does, plus version.txt.
"""
import os
import sys

import crispat

IN, WD, METHOD = sys.argv[1], sys.argv[2], sys.argv[3]
T = float(sys.argv[4]) if len(sys.argv) > 4 else None
os.makedirs(WD, exist_ok=True)
for sub in ("fitted_model_plots", "loss_plots"):  # crispat saves plots here without creating the folders
    os.makedirs(os.path.join(WD, sub), exist_ok=True)
d = WD.rstrip("/") + "/"
if METHOD == "umi":
    crispat.ga_umi(IN, thresholds=[int(T)], output_dir=d)
    os.replace(f"{d}assignments_t{int(T)}.csv", f"{d}assignments.csv")
elif METHOD == "max":
    crispat.ga_max(IN, output_dir=d, UMI_threshold=int(T or 0))
elif METHOD in ("ratio", "quantiles"):
    getattr(crispat, f"ga_{METHOD}")(IN, thresholds=[T], output_dir=d)
    os.replace(f"{d}assignments_t{T:g}.csv", f"{d}assignments.csv")
elif METHOD in ("2beta", "3beta"):
    getattr(crispat, f"ga_{METHOD}")(IN, output_dir=d, n_iter=500)
elif METHOD == "gauss":
    crispat.ga_gauss(IN, output_dir=d, n_iter=250, n_jobs=int(os.environ.get("PERTURA_CRISPAT_JOBS", "1")),
                     make_plots=False)
elif METHOD == "poisson_gauss":
    crispat.ga_poisson_gauss(IN, output_dir=d, n_iter=500, n_jobs=1, make_plots=False)
elif METHOD in ("poisson", "binomial", "negative_binomial"):
    getattr(crispat, f"ga_{METHOD}")(IN, output_dir=d, parallelize=False)
else:
    sys.exit(f"unknown crispat method {METHOD!r}")
with open(f"{d}version.txt", "w") as fh:
    fh.write(getattr(crispat, "__version__", "unknown"))
