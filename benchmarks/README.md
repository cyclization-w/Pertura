# Pertura benchmark data

Large upstream datasets are never committed to this repository. The JSON
manifests pin the official source, upstream checksum when published, intended
use and license-review URL. The pertura_bench fetch command and
scripts/fetch_benchmark.py are explicit network operations: they verify size
and the supplied MD5, compute SHA-256, and write a portable lock plus an ignored
local-path sidecar. Analysis capabilities never invoke them automatically.

Calibration and final-evaluation subset manifests must be generated separately.
Threshold profiles may read calibration subsets only; a release audit must reject
profiles whose benchmark hash is absent from the frozen final-evaluation manifest.

The checked-in edgeR golden is synthetic and small; it is not a substitute for
the four real-data locks. Published-paper proxy labels remain validated=false
and cannot satisfy the expert-adjudicated release gate.
