from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.request
from importlib import resources
from pathlib import Path
from typing import Any

from pertura_core.hashing import canonical_hash, file_sha256


PROFILE = "edger-v1"
PYTHON_PROFILE = "python-science-v1"
PERTURBSEQ_PYTHON_PROFILE = "perturbseq-python-v1"
SCEPTRE_PROFILE = "sceptre-v1"
COMPOSITION_PROFILE = "composition-v1"
INTERPRETATION_PROFILE = "interpretation-v1"
VIRTUAL_EVAL_PROFILE = "virtual-eval-v1"
SUPPORTED_PROFILES = (
    PROFILE,
    PYTHON_PROFILE,
    PERTURBSEQ_PYTHON_PROFILE,
    SCEPTRE_PROFILE,
    COMPOSITION_PROFILE,
    INTERPRETATION_PROFILE,
    VIRTUAL_EVAL_PROFILE,
)
MICROMAMBA_VERSION = "2.6.2-1"
EXPECTED_EDGER_VERSIONS = {
    "R": "4.5.3",
    "Bioconductor": "3.22",
    "edgeR": "4.8.2",
    "limma": "3.66.0",
}
EXPECTED_SCEPTRE_VERSIONS = {"R": "4.5.3", "sceptre": "0.99.0"}
EXPECTED_COMPOSITION_VERSIONS = {
    "R": "4.5.3",
    "Bioconductor": "3.22",
    "speckle": "1.10.0",
    "limma": "3.66.0",
    "edgeR": "4.8.2",
}
EXPECTED_PERTURBSEQ_PYTHON_VERSIONS = {
    "anndata": "0.11.4",
    "scanpy": "1.12.1",
    "mudata": "0.3.2",
    "igraph": "0.11.8",
    "leidenalg": "0.10.2",
    "scikit-misc": "0.5.2",
    "pertpy": "1.1.1",
    "scrublet": "0.2.3",
    "scikit-learn": "1.8.0",
}
EXPECTED_INTERPRETATION_VERSIONS = {
    "gseapy": "1.3.0",
    "decoupler": "2.1.6",
}
R_EXPECTED_VERSIONS = {
    PROFILE: EXPECTED_EDGER_VERSIONS,
    SCEPTRE_PROFILE: EXPECTED_SCEPTRE_VERSIONS,
    COMPOSITION_PROFILE: EXPECTED_COMPOSITION_VERSIONS,
}
PYTHON_EXPECTED_VERSIONS = {
    PERTURBSEQ_PYTHON_PROFILE: EXPECTED_PERTURBSEQ_PYTHON_VERSIONS,
    INTERPRETATION_PROFILE: EXPECTED_INTERPRETATION_VERSIONS,
}
R_INSTALLERS = {
    PROFILE: "install-edger-v1.R",
    SCEPTRE_PROFILE: "install-sceptre-v1.R",
    COMPOSITION_PROFILE: "install-composition-v1.R",
}
R_IMPORT_SMOKES = {
    COMPOSITION_PROFILE: (
        "suppressPackageStartupMessages({library(speckle);library(limma);"
        "library(edgeR)})"
    ),
}
PYTHON_PACKAGES = {
    PYTHON_PROFILE: (
        "anndata", "scanpy", "mudata", "numpy", "pandas", "scipy", "pyyaml",
        "scikit-learn", "igraph", "leidenalg", "pyarrow",
    ),
    PERTURBSEQ_PYTHON_PROFILE: (
        "anndata", "scanpy", "mudata", "numpy", "pandas", "scipy",
        "scikit-learn", "igraph", "leidenalg", "pyarrow", "scikit-misc",
        "pertpy", "scrublet",
    ),
    INTERPRETATION_PROFILE: (
        "numpy", "pandas", "scipy", "scikit-learn", "pyarrow", "gseapy", "decoupler",
    ),
    VIRTUAL_EVAL_PROFILE: (
        "anndata", "numpy", "pandas", "scipy", "scikit-learn", "pyarrow",
    ),
}
CONDA_PACKAGE_NAMES = {
    "igraph": "python-igraph",
    "decoupler": "decoupler-py",
}
PYTHON_IMPORT_SMOKES = {
    PYTHON_PROFILE: (
        "import yaml;"
        "assert yaml.safe_load('dependency: declared')['dependency'] == 'declared';"
        "print('python-science-import-smoke-ok')"
    ),
    PERTURBSEQ_PYTHON_PROFILE: (
        "import inspect,pertpy,scanpy,skmisc,scrublet;"
        "from pertpy.tools import Mixscape;"
        "from sklearn.mixture import GaussianMixture;"
        "assert 'xp' in inspect.signature(GaussianMixture._m_step).parameters,"
        "'scikit-learn GaussianMixture ABI lacks xp';"
        "Mixscape();"
        "print('perturbseq-import-smoke-ok')"
    ),
}


def cache_root() -> Path:
    override = os.environ.get("PERTURA_CACHE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return (Path(os.environ["LOCALAPPDATA"]) / "Pertura" / "cache").resolve()
    return (Path.home() / ".cache" / "pertura").resolve()


def micromamba_path() -> Path:
    name = "micromamba.exe" if os.name == "nt" else "micromamba"
    return cache_root() / "bin" / name


def environment_prefix(profile_name: str = PROFILE) -> Path:
    if profile_name not in SUPPORTED_PROFILES:
        raise ValueError(f"unknown environment profile: {profile_name}")
    overrides = {
        PROFILE: "PERTURA_EDGER_ENV",
        PYTHON_PROFILE: "PERTURA_PYTHON_SCIENCE_ENV",
        PERTURBSEQ_PYTHON_PROFILE: "PERTURA_PERTURBSEQ_PYTHON_ENV",
        SCEPTRE_PROFILE: "PERTURA_SCEPTRE_ENV",
        COMPOSITION_PROFILE: "PERTURA_COMPOSITION_ENV",
        INTERPRETATION_PROFILE: "PERTURA_INTERPRETATION_ENV",
        VIRTUAL_EVAL_PROFILE: "PERTURA_VIRTUAL_EVAL_ENV",
    }
    override = os.environ.get(overrides[profile_name])
    return Path(override).expanduser().resolve() if override else cache_root() / "envs" / profile_name

def setup_environment(profile_name: str = PROFILE) -> dict[str, Any]:
    """Explicit networked setup. Analysis runners never call this function."""

    if profile_name not in SUPPORTED_PROFILES:
        raise ValueError(f"unknown environment profile: {profile_name}")
    binary = _ensure_micromamba()
    prefix = environment_prefix(profile_name)
    spec_path = resources.files("pertura_workflow").joinpath("environments", f"{profile_name}.yml")
    prefix.parent.mkdir(parents=True, exist_ok=True)
    subprocess_env = _minimal_env()
    for key in ("MAMBA_ROOT_PREFIX", "CONDA_PKGS_DIRS", "TEMP", "TMP"):
        Path(subprocess_env[key]).mkdir(parents=True, exist_ok=True)
    if "TMPDIR" in subprocess_env:
        Path(subprocess_env["TMPDIR"]).mkdir(parents=True, exist_ok=True)
    command = [str(binary), "create", "--yes", "--no-rc", "--prefix", str(prefix), "--file", str(spec_path)]
    print(
        f"[pertura] creating {profile_name} at {prefix}",
        file=sys.stderr,
        flush=True,
    )
    completed = subprocess.run(
        command,
        stdout=sys.stderr,
        stderr=sys.stderr,
        timeout=3600,
        check=False,
        env=subprocess_env,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "micromamba environment creation failed with exit code "
            f"{completed.returncode}; see the streamed setup output above"
        )
    installer_name = R_INSTALLERS.get(profile_name)
    if installer_name:
        installer = resources.files("pertura_workflow").joinpath("environments", installer_name)
        print(
            f"[pertura] bootstrapping R packages for {profile_name}",
            file=sys.stderr,
            flush=True,
        )
        bootstrap = subprocess.run(
            [str(binary), "run", "--prefix", str(prefix), "Rscript", str(installer)],
            stdout=sys.stderr,
            stderr=sys.stderr,
            timeout=3600,
            check=False,
            env=subprocess_env,
        )
        if bootstrap.returncode != 0:
            raise RuntimeError(
                f"{profile_name} R bootstrap failed with exit code "
                f"{bootstrap.returncode}; see the streamed setup output above"
            )
    package_list = subprocess.run(
        [str(binary), "list", "--json", "--prefix", str(prefix)],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=120,
        check=True,
        env=subprocess_env,
    )
    packages = json.loads(package_list.stdout)
    manifest = {
        "schema_version": "pertura-environment-manifest-v2",
        "profile": profile_name,
        "platform": platform.platform(),
        "micromamba": {"path": str(binary), "sha256": file_sha256(binary), "version": MICROMAMBA_VERSION},
        "prefix": str(prefix),
        "spec_hash": canonical_hash(spec_path.read_text(encoding="utf-8")),
        "resource_hashes": _resource_hashes(profile_name),
        "expected_versions": _expected_versions(profile_name),
        "packages": packages,
    }
    manifest["lock_hash"] = canonical_hash(manifest)
    manifest_path = prefix / "pertura-environment-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    doctor = doctor_environment(profile_name)
    if not doctor["ok"]:
        raise RuntimeError("environment was created but failed doctor: " + "; ".join(doctor["problems"]))
    return manifest | {"doctor": doctor}

def doctor_environment(profile_name: str = PROFILE) -> dict[str, Any]:
    if profile_name not in SUPPORTED_PROFILES:
        raise ValueError(f"unknown environment profile: {profile_name}")
    binary = micromamba_path()
    prefix = environment_prefix(profile_name)
    problems: list[str] = []
    if not binary.is_file():
        problems.append(f"micromamba is missing; run pertura env setup {profile_name}")
    if not prefix.is_dir():
        problems.append(f"environment is missing; run pertura env setup {profile_name}")
    versions: dict[str, str] = {}
    if not problems and profile_name in R_EXPECTED_VERSIONS:
        expected = R_EXPECTED_VERSIONS[profile_name]
        if profile_name == PROFILE:
            package_names = ["edgeR", "limma", "jsonlite"]
            names = ["R", "BiocManager", "Bioconductor", *package_names]
            expressions = [
                "cat(paste(R.version$major, R.version$minor, sep='.'), '\\n')",
                "cat(as.character(packageVersion('BiocManager')), '\\n')",
                "cat(as.character(BiocManager::version()), '\\n')",
            ]
        else:
            package_names = [name for name in expected if name not in {"R", "Bioconductor"}]
            names = ["R"] + (["Bioconductor"] if "Bioconductor" in expected else []) + package_names
            expressions = ["cat(paste(R.version$major, R.version$minor, sep='.'), '\\n')"]
            if "Bioconductor" in expected:
                expressions.append("cat(as.character(BiocManager::version()), '\\n')")
        import_smoke = R_IMPORT_SMOKES.get(profile_name)
        if import_smoke:
            expressions.insert(0, import_smoke)
        expressions.extend(
            f"cat(as.character(packageVersion('{name}')), '\\n')"
            for name in package_names
        )
        completed = subprocess.run(
            [str(binary), "run", "--prefix", str(prefix), "Rscript", "-e", ";".join(expressions)],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=120,
            check=False,
            env=_minimal_env(),
        )
        if completed.returncode != 0:
            problems.append("R package check failed: " + completed.stderr.strip()[-1000:])
        else:
            lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
            if len(lines) >= len(names):
                versions = dict(zip(names, lines[-len(names):]))
                for name, required in expected.items():
                    if versions.get(name) != required:
                        problems.append(f"expected {name} {required}, found {versions.get(name)}")
            else:
                problems.append("R package check returned an incomplete version manifest")
    elif not problems:
        package_names = PYTHON_PACKAGES[profile_name]
        completed = subprocess.run(
            [str(binary), "list", "--json", "--prefix", str(prefix)],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=120,
            check=False,
            env=_minimal_env(),
        )
        if completed.returncode != 0:
            problems.append(
                "Python scientific package inventory failed: "
                + completed.stderr.strip()[-1000:]
            )
        else:
            try:
                package_inventory = json.loads(completed.stdout)
                installed = {
                    str(item["name"]).lower(): str(item["version"])
                    for item in package_inventory
                    if isinstance(item, dict)
                    and item.get("name")
                    and item.get("version")
                }
                versions = {
                    name: installed[CONDA_PACKAGE_NAMES.get(name, name).lower()]
                    for name in package_names
                    if CONDA_PACKAGE_NAMES.get(name, name).lower() in installed
                }
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                problems.append(
                    "Python scientific package inventory returned invalid JSON"
                )
            else:
                for name in package_names:
                    if name not in versions:
                        problems.append(f"Python scientific package is missing: {name}")
                for name, required in PYTHON_EXPECTED_VERSIONS.get(
                    profile_name, {}
                ).items():
                    if versions.get(name) != required:
                        problems.append(
                            f"expected {name} {required}, found {versions.get(name)}"
                        )
        smoke_expression = PYTHON_IMPORT_SMOKES.get(profile_name)
        if smoke_expression:
            smoke = subprocess.run(
                [
                    str(binary), "run", "--prefix", str(prefix),
                    "python", "-c", smoke_expression,
                ],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=120,
                check=False,
                env=_minimal_env(),
            )
            if smoke.returncode != 0:
                detail = (smoke.stderr or smoke.stdout).strip()[-2000:]
                problems.append(
                    "Perturb-seq Python API compatibility check failed: " + detail
                )
    manifest_path = prefix / "pertura-environment-manifest.json"
    lock_hash = None
    if not problems or prefix.is_dir():
        if not manifest_path.is_file():
            problems.append("environment manifest is missing; rerun explicit environment setup")
        else:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                observed_lock = str(manifest.pop("lock_hash", ""))
                calculated_lock = canonical_hash(manifest)
                if observed_lock != calculated_lock:
                    problems.append("environment manifest lock hash mismatch")
                if manifest.get("schema_version") != "pertura-environment-manifest-v2":
                    problems.append("environment manifest schema is not v2")
                if manifest.get("profile") != profile_name:
                    problems.append("environment manifest profile mismatch")
                micromamba = manifest.get("micromamba") or {}
                if micromamba.get("version") != MICROMAMBA_VERSION:
                    problems.append("environment micromamba version drift")
                if binary.is_file() and micromamba.get("sha256") != file_sha256(binary):
                    problems.append("environment micromamba binary hash drift")
                if manifest.get("resource_hashes") != _resource_hashes(profile_name):
                    problems.append("environment runner/installer resource hash drift")
                if manifest.get("expected_versions") != _expected_versions(profile_name):
                    problems.append("environment expected-version manifest drift")
                lock_hash = observed_lock
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                problems.append(f"environment manifest is invalid: {exc}")
    return {
        "schema_version": "pertura-environment-doctor-v1",
        "profile": profile_name,
        "ok": not problems,
        "problems": problems,
        "versions": versions,
        "micromamba_path": str(binary),
        "prefix": str(prefix),
        "manifest_path": str(manifest_path) if manifest_path.exists() else None,
        "lock_hash": lock_hash,
    }

def environment_lock(profile_name: str = PROFILE) -> dict[str, Any]:
    doctor = doctor_environment(profile_name)
    if not doctor["ok"]:
        raise RuntimeError("; ".join(doctor["problems"]))
    path = Path(str(doctor["manifest_path"]))
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return {
        "schema_version": manifest["schema_version"],
        "profile": profile_name,
        "platform": manifest["platform"],
        "lock_hash": manifest["lock_hash"],
        "resource_hashes": manifest["resource_hashes"],
        "expected_versions": manifest["expected_versions"],
        "versions": doctor["versions"],
    }


def _ensure_micromamba() -> Path:
    destination = micromamba_path()
    if destination.is_file():
        return destination
    asset = _asset_name()
    api_url = f"https://api.github.com/repos/mamba-org/micromamba-releases/releases/tags/{MICROMAMBA_VERSION}"
    request = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "pertura-environment-setup"})
    with urllib.request.urlopen(request, timeout=60) as response:
        release = json.load(response)
    entry = next((item for item in release.get("assets", []) if item.get("name") == asset), None)
    if not entry:
        raise RuntimeError(f"micromamba release does not contain {asset}")
    digest = str(entry.get("digest") or "")
    if not digest.startswith("sha256:"):
        raise RuntimeError("GitHub release asset did not provide a sha256 digest; refusing unchecked bootstrap")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".download")
    download = urllib.request.Request(entry["browser_download_url"], headers={"User-Agent": "pertura-environment-setup"})
    with urllib.request.urlopen(download, timeout=300) as response, temporary.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    actual = file_sha256(temporary)
    if actual != digest:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"micromamba checksum mismatch: expected {digest}, observed {actual}")
    temporary.replace(destination)
    if os.name != "nt":
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR)
    bootstrap = {
        "version": MICROMAMBA_VERSION,
        "asset": asset,
        "url": entry["browser_download_url"],
        "sha256": actual,
        "release_id": release.get("id"),
    }
    (destination.parent / "micromamba-bootstrap.json").write_text(json.dumps(bootstrap, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _asset_name() -> str:
    machine = platform.machine().lower()
    if os.name == "nt":
        return "micromamba-win-64"
    if platform.system() == "Darwin":
        return "micromamba-osx-arm64" if machine in {"arm64", "aarch64"} else "micromamba-osx-64"
    return "micromamba-linux-aarch64" if machine in {"arm64", "aarch64"} else "micromamba-linux-64"


def _resource_hashes(profile_name: str) -> dict[str, str]:
    spec = resources.files("pertura_workflow").joinpath("environments", f"{profile_name}.yml")
    hashes = {"environment_spec": file_sha256(Path(str(spec)))}
    installer_name = R_INSTALLERS.get(profile_name)
    if installer_name:
        installer = resources.files("pertura_workflow").joinpath("environments", installer_name)
        hashes["installer"] = file_sha256(Path(str(installer)))
    runner_names = {
        PROFILE: "edger_ql.R",
        SCEPTRE_PROFILE: "sceptre_association.R",
        COMPOSITION_PROFILE: "propeller_composition.R",
    }
    if profile_name in runner_names:
        runner = resources.files("pertura_workflow.capabilities").joinpath(
            "runners", runner_names[profile_name]
        )
        hashes["runner"] = file_sha256(Path(str(runner)))
    python_runner_sets = {
        PYTHON_PROFILE: (
            "executors.py", "state_candidates.py", "backed_selection.py",
            "runners/environment_worker.py",
        ),
        PERTURBSEQ_PYTHON_PROFILE: (
            "executors.py", "state_candidates.py", "target_candidates.py",
            "backed_selection.py",
            "runners/environment_worker.py",
        ),
        INTERPRETATION_PROFILE: (
            "executors.py", "p4_candidates.py", "runners/environment_worker.py",
            "runners/gsea_prerank_runner.py", "runners/ulm_runner.py",
        ),
        VIRTUAL_EVAL_PROFILE: (
            "executors.py", "p5_candidates.py", "runners/environment_worker.py",
        ),
    }
    capability_root = Path(str(resources.files("pertura_workflow.capabilities")))
    for relative in python_runner_sets.get(profile_name, ()):
        path = capability_root / relative
        hashes[f"python_runner:{relative}"] = file_sha256(path)
    return hashes


def _expected_versions(profile_name: str) -> dict[str, str]:
    return R_EXPECTED_VERSIONS.get(
        profile_name,
        PYTHON_EXPECTED_VERSIONS.get(profile_name, {}),
    )


def _minimal_env() -> dict[str, str]:
    allowed = ("SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE", "PATH")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    root = cache_root()
    environment["MAMBA_ROOT_PREFIX"] = str(root / "mamba-root")
    environment["CONDA_PKGS_DIRS"] = str(root / "pkgs")
    environment["TEMP"] = str(root / "tmp")
    environment["TMP"] = str(root / "tmp")
    if os.name != "nt":
        environment["TMPDIR"] = str(root / "tmp")
    return environment
