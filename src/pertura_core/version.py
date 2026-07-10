from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def package_version() -> str:
    """Return installed package metadata without maintaining a second version constant."""

    try:
        return version("pertura")
    except PackageNotFoundError:
        return "0+unknown"

\n