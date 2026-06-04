"""Structured error primitives for operator-facing diagnostics."""

from __future__ import annotations

from typing import Any


DOCS_BASE_URL = "https://pertura.readthedocs.io/en/latest"


class PerturaError(Exception):
    """Base error with stable codes and documentation links."""

    default_code = "pertura.error"
    default_doc_path = "errors"

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        doc_url: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.doc_url = doc_url or _doc_url(self.default_doc_path)
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "code": self.code,
            "doc_url": self.doc_url,
            "details": self.details,
        }


def _doc_url(path: str) -> str:
    return f"{DOCS_BASE_URL}/{path.strip('/')}"
