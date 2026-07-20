from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from pertura_core.hashing import canonical_hash
from pertura_runtime.project.models import AssetLocation, DataAssetRef
from pertura_runtime.project.store import ProjectStore

SMALL_OBJECT_LIMIT = 64 * 1024 * 1024


def _hash_path(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    paths = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    for item in paths:
        if path.is_dir():
            digest.update(item.relative_to(path).as_posix().encode("utf-8") + b"\0")
        with item.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
    return "sha256:" + digest.hexdigest(), size


class DataAssetRegistry:
    def __init__(self, *, project_id: str, store: ProjectStore, object_root: Path) -> None:
        self.project_id = project_id
        self.store = store
        self.object_root = Path(object_root)
        self.object_root.mkdir(parents=True, exist_ok=True)

    def register(
        self,
        path: Path,
        *,
        role: str,
        kind: str,
        source_class: str | None = None,
        created_by_turn: str | None = None,
        dependencies: tuple[str, ...] = (),
        origin_task_id: str | None = None,
        submission_id: str | None = None,
        schema_validation_status: str | None = None,
    ) -> DataAssetRef:
        source = Path(path).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(source)
        content_hash, size = _hash_path(source)
        format_name = source.suffix.lower().lstrip(".") if source.is_file() else "directory"
        identity = {
            "project_id": self.project_id,
            "kind": kind,
            "role": role,
            "format": format_name,
            "content_sha256": content_hash,
            "size_bytes": size,
            "source_class": source_class or self._default_source_class(kind, role),
            "dependencies": dependencies,
            "origin_task_id": origin_task_id,
            "submission_id": submission_id,
            "schema_validation_status": schema_validation_status,
        }
        asset_id = "asset_" + canonical_hash(identity).split(":", 1)[1][:32]
        asset = DataAssetRef(
            asset_id=asset_id,
            project_id=self.project_id,
            kind=kind,
            role=role,
            format=format_name,
            logical_name=source.name,
            content_sha256=content_hash,
            size_bytes=size,
            source_class=identity["source_class"],
            created_by_turn=created_by_turn,
            dependencies=dependencies,
            origin_task_id=origin_task_id,
            submission_id=submission_id,
            schema_validation_status=schema_validation_status,
        )
        location = self._materialize_location(asset, source)
        self.store.put_asset(asset, location)
        return asset

    def _materialize_location(self, asset: DataAssetRef, source: Path) -> AssetLocation:
        if source.is_file() and asset.kind in {"derived", "exploratory"}:
            digest = asset.content_sha256.split(":", 1)[1]
            destination = self.object_root / digest[:2] / _object_name(digest, source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            mode = "hardlink"
            if not destination.exists():
                try:
                    os.link(source, destination)
                except OSError:
                    shutil.copy2(source, destination)
                    mode = "copy"
            return AssetLocation(asset_id=asset.asset_id, absolute_path=str(destination), storage_mode=mode, observed_sha256=asset.content_sha256, observed_size_bytes=asset.size_bytes)
        if source.is_file() and asset.size_bytes <= SMALL_OBJECT_LIMIT and asset.kind == "external_resource":
            digest = asset.content_sha256.split(":", 1)[1]
            destination = self.object_root / digest[:2] / _object_name(digest, source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(source, destination)
            return AssetLocation(asset_id=asset.asset_id, absolute_path=str(destination), storage_mode="object", observed_sha256=asset.content_sha256, observed_size_bytes=asset.size_bytes)
        return AssetLocation(asset_id=asset.asset_id, absolute_path=str(source), storage_mode="reference", observed_sha256=asset.content_sha256, observed_size_bytes=asset.size_bytes)

    def resolve(self, asset_id: str, *, expected_role: str | None = None) -> Path:
        asset = self.store.get_asset(asset_id)
        if not asset:
            raise KeyError(f"unknown asset: {asset_id}")
        if expected_role and asset.role != expected_role:
            raise ValueError(f"asset {asset_id} has role {asset.role!r}, expected {expected_role!r}")
        checked = self.doctor(asset_id)
        if checked.status != "current":
            raise RuntimeError(f"asset {asset_id} is {checked.status}")
        locations = self.store.asset_locations(asset_id)
        return Path(locations[0].absolute_path)

    def doctor(self, asset_id: str) -> DataAssetRef:
        asset = self.store.get_asset(asset_id)
        if not asset:
            raise KeyError(f"unknown asset: {asset_id}")
        locations = self.store.asset_locations(asset_id)
        status = "missing"
        for location in locations:
            path = Path(location.absolute_path)
            if not path.exists():
                continue
            observed, _ = _hash_path(path)
            status = "current" if observed == asset.content_sha256 else "drifted"
            if status == "current":
                break
        updated = asset.model_copy(update={"status": status})
        self.store.update_asset(updated)
        return updated

    def doctor_all(self) -> tuple[DataAssetRef, ...]:
        return tuple(self.doctor(item.asset_id) for item in self.store.list_assets(self.project_id))

    @staticmethod
    def _default_source_class(kind: str, role: str) -> str:
        if role in {"prediction", "prediction_bundle"}:
            return "prediction"
        if kind == "external_resource":
            return "curated_prior"
        if kind == "exploratory":
            return "hypothesis"
        if kind == "derived":
            return "derived_artifact"
        return "observed_metadata"


def _object_name(digest: str, source: Path) -> str:
    """Keep the logical format visible on content-addressed object paths.

    Capability readers dispatch on extensions such as ``.h5ad`` and ``.tsv``.
    Removing those extensions when a small or derived asset is materialized can
    make binary HDF5 inputs look like text and can silently select the wrong
    delimiter for tables.  The digest remains the object identity; suffixes
    preserve only the format needed by readers.
    """

    suffixes = "".join(source.suffixes)
    return digest[2:] + suffixes
