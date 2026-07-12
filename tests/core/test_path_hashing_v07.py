from __future__ import annotations

from pathlib import Path

from pertura_core.hashing import file_sha256, path_sha256


def test_file_path_hash_remains_file_hash_compatible(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text('{"value": 1}\n', encoding="utf-8")

    assert path_sha256(path) == file_sha256(path)


def test_directory_hash_uses_relative_names_and_contents_not_root(tmp_path: Path) -> None:
    first = tmp_path / "first.zarr"
    second = tmp_path / "moved.zarr"
    for root in (first, second):
        (root / "c").mkdir(parents=True)
        (root / "zarr.json").write_text("metadata", encoding="utf-8")
        (root / "c" / "0").write_bytes(b"chunk")

    assert path_sha256(first) == path_sha256(second)

    (second / "c" / "0").write_bytes(b"changed")
    assert path_sha256(first) != path_sha256(second)
