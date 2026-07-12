from __future__ import annotations

import json
import shutil
import urllib.request
from importlib import resources
from pathlib import Path
from typing import Any

from pertura_core.hashing import canonical_hash, file_sha256
from pertura_workflow.environment import cache_root


RESOURCE_PROFILES = ("pathway-knowledge-v1", "regulator-knowledge-v1")


def resource_profile(profile: str) -> dict[str, Any]:
    if profile not in RESOURCE_PROFILES:
        raise ValueError(f"unknown knowledge-resource profile: {profile}")
    entry = resources.files("pertura_workflow").joinpath(
        "knowledge_resource_profiles", f"{profile}.json"
    )
    payload = json.loads(entry.read_text(encoding="utf-8"))
    expected = canonical_hash(
        {key: value for key, value in payload.items() if key != "manifest_hash"}
    )
    if payload.get("manifest_hash") not in {None, "", expected}:
        raise ValueError(f"knowledge-resource manifest hash mismatch: {profile}")
    payload["manifest_hash"] = expected
    return payload


def resource_cache_dir(profile: str) -> Path:
    resource_profile(profile)
    return cache_root() / "resources" / profile


def setup_resource(profile: str) -> dict[str, Any]:
    """Explicit networked setup; analysis runners never call this function."""

    manifest = resource_profile(profile)
    artifacts = manifest.get("artifacts") or []
    if not artifacts:
        raise RuntimeError(
            f"{profile} requires a maintainer-generated local snapshot; "
            "use pertura resources freeze after generating the declared resource"
        )
    destination = resource_cache_dir(profile)
    destination.mkdir(parents=True, exist_ok=True)
    locked: list[dict[str, Any]] = []
    for artifact in artifacts:
        url = str(artifact.get("url") or "")
        name = str(artifact.get("name") or "")
        expected = str(artifact.get("sha256") or "")
        if not url.startswith("https://") or not name:
            raise RuntimeError("resource artifact requires an https URL and stable name")
        if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected.lower()):
            raise RuntimeError(
                f"resource artifact {name} has no maintainer-pinned SHA-256; "
                "refusing first-use download"
            )
        final = destination / name
        temporary = final.with_suffix(final.suffix + ".download")
        request = urllib.request.Request(
            url, headers={"User-Agent": "pertura-resource-setup/0.2"}
        )
        with urllib.request.urlopen(request, timeout=300) as response, temporary.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        observed = file_sha256(temporary)
        if expected != observed:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(
                f"resource checksum mismatch for {name}: expected {expected}, observed {observed}"
            )
        temporary.replace(final)
        locked.append(
            {
                "artifact_id": str(artifact["artifact_id"]),
                "relative_path": name,
                "sha256": observed,
                "bytes": final.stat().st_size,
                "source_url": url,
            }
        )
    return _write_lock(profile, manifest, locked)


def freeze_local_resource(
    profile: str, artifacts: dict[str, str | Path]
) -> dict[str, Any]:
    """Freeze explicitly generated resources without accepting analysis paths."""

    manifest = resource_profile(profile)
    expected_ids = {
        str(item["artifact_id"]) for item in manifest.get("generated_artifacts") or ()
    }
    if set(artifacts) != expected_ids:
        raise ValueError(
            f"{profile} requires generated artifacts {sorted(expected_ids)}"
        )
    destination = resource_cache_dir(profile)
    destination.mkdir(parents=True, exist_ok=True)
    locked: list[dict[str, Any]] = []
    for artifact_id, source_value in sorted(artifacts.items()):
        source = Path(source_value).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        suffix = "".join(source.suffixes) or ".dat"
        name = f"{artifact_id}{suffix}"
        final = destination / name
        shutil.copyfile(source, final)
        locked.append(
            {
                "artifact_id": artifact_id,
                "relative_path": name,
                "sha256": file_sha256(final),
                "bytes": final.stat().st_size,
                "source_url": None,
            }
        )
    return _write_lock(profile, manifest, locked)


def doctor_resource(profile: str) -> dict[str, Any]:
    manifest = resource_profile(profile)
    directory = resource_cache_dir(profile)
    lock_path = directory / "pertura-resource-lock.json"
    problems: list[str] = []
    lock: dict[str, Any] | None = None
    if not lock_path.is_file():
        problems.append(f"resource is missing; run pertura resources setup {profile}")
    else:
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            observed_hash = str(lock.pop("lock_hash", ""))
            expected_hash = canonical_hash(lock)
            lock["lock_hash"] = observed_hash
            if observed_hash != expected_hash:
                problems.append("resource lock hash mismatch")
            if lock.get("profile") != profile:
                problems.append("resource profile mismatch")
            if lock.get("source_manifest_hash") != manifest["manifest_hash"]:
                problems.append("resource source-manifest drift")
            locked_artifacts = {
                str(item.get("artifact_id") or ""): item
                for item in lock.get("artifacts") or ()
            }
            expected_artifacts = {
                str(item.get("artifact_id") or ""): item
                for item in manifest.get("artifacts") or ()
            }
            generated_ids = {
                str(item.get("artifact_id") or "")
                for item in manifest.get("generated_artifacts") or ()
            }
            if set(locked_artifacts) != set(expected_artifacts) | generated_ids:
                problems.append("resource artifact identity set does not match profile")
            for artifact_id, artifact in locked_artifacts.items():
                path = directory / str(artifact.get("relative_path") or "")
                expected = str((expected_artifacts.get(artifact_id) or {}).get("sha256") or "")
                if artifact_id in expected_artifacts and artifact.get("sha256") != expected:
                    problems.append(f"resource artifact differs from pinned checksum: {artifact_id}")
                if not path.is_file():
                    problems.append(f"resource artifact is missing: {path.name}")
                elif file_sha256(path) != artifact.get("sha256"):
                    problems.append(f"resource artifact hash drift: {path.name}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            problems.append(f"resource lock is invalid: {exc}")
    return {
        "schema_version": "pertura-resource-doctor-v1",
        "profile": profile,
        "ok": not problems,
        "problems": problems,
        "lock_hash": lock.get("lock_hash") if lock else None,
        "license": manifest.get("license"),
        "resource_dir": str(directory),
    }


def knowledge_resource_lock(profile: str) -> dict[str, Any]:
    doctor = doctor_resource(profile)
    if not doctor["ok"]:
        raise RuntimeError("; ".join(doctor["problems"]))
    lock_path = resource_cache_dir(profile) / "pertura-resource-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    return {
        "schema_version": lock["schema_version"],
        "profile": profile,
        "resource_version": lock["resource_version"],
        "source_manifest_hash": lock["source_manifest_hash"],
        "lock_hash": lock["lock_hash"],
        "license": lock["license"],
        "artifacts": lock["artifacts"],
        "resource_dir": str(resource_cache_dir(profile)),
    }


def _write_lock(
    profile: str, manifest: dict[str, Any], artifacts: list[dict[str, Any]]
) -> dict[str, Any]:
    payload = {
        "schema_version": "pertura-knowledge-resource-lock-v1",
        "profile": profile,
        "resource_version": str(manifest["resource_version"]),
        "source_manifest_hash": str(manifest["manifest_hash"]),
        "license": manifest.get("license") or {},
        "artifacts": sorted(artifacts, key=lambda item: item["artifact_id"]),
    }
    payload["lock_hash"] = canonical_hash(payload)
    path = resource_cache_dir(profile) / "pertura-resource-lock.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload
