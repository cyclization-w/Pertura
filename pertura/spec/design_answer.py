"""Compile human interrupt answers into structured design updates."""

from __future__ import annotations

import json
import re
from typing import Any


def compile_design_answer(
    response: str,
    *,
    expected_fields: list[str] | None = None,
    provider: str = "deterministic",
) -> dict[str, Any]:
    """Return a conservative design patch from a PI/user answer.

    The deterministic path is CI-safe. LLM compilation can be layered later
    through the same function without changing Workbench.answer().
    """
    expected = set(expected_fields or [])
    patch = _parse_json_or_key_values(response)
    patch.update(_parse_known_phrases(response))
    patch = _filter_patch(patch, expected)
    return patch


def expected_fields_from_interrupt(snap, interrupt) -> list[str]:
    """Infer expected design fields from the latest human gate evaluation."""
    fields: list[str] = []
    for gate in reversed(getattr(snap, "gate_evaluations", [])):
        if gate.decision != "human_interrupt":
            continue
        for result in gate.condition_results:
            details = result.get("details", {}) if isinstance(result, dict) else {}
            field = details.get("field")
            if field:
                fields.append(field)
            inputs = result.get("details", {}).get("fields") if isinstance(result, dict) else None
            if isinstance(inputs, list):
                fields.extend(str(item) for item in inputs)
        if fields:
            break
    return sorted(set(fields))


def _parse_json_or_key_values(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return {str(k): v for k, v in parsed.items() if v not in ("", None, [])}
    except Exception:
        pass
    out = {}
    for field in _KNOWN_FIELDS:
        match = re.search(rf"\b{re.escape(field)}\s*(?:=|:|is|are)\s*([^\n;]+)", text, flags=re.IGNORECASE)
        if match:
            out[field] = _parse_value(match.group(1))
    return out


def _parse_known_phrases(text: str) -> dict[str, Any]:
    lower = (text or "").lower()
    out: dict[str, Any] = {}
    modality = re.search(r"\b(crispra|crispri|ko|knockout|knock-out)\b", lower)
    if modality:
        value = modality.group(1)
        out["perturbation_modality"] = "KO" if value in {"ko", "knockout", "knock-out"} else value.upper()

    patterns = {
        "guide_column": r"(?:guide|grna|sgrna)\s*(?:column|col)?\s*(?:is|=|:)\s*([A-Za-z0-9_.-]+)",
        "target_column": r"(?:target|perturbation)\s*(?:column|col)?\s*(?:is|=|:)\s*([A-Za-z0-9_.-]+)",
        "batch_column": r"(?:batch|sample|replicate)\s*(?:column|col)?\s*(?:is|=|:)\s*([A-Za-z0-9_.-]+)",
        "moi": r"\bmoi\s*(?:is|=|:)?\s*([A-Za-z0-9_.-]+)",
        "loading_strategy": r"\b(?:loading|droplet)\s*(?:strategy|mode)?\s*(?:is|=|:)\s*([A-Za-z0-9_.-]+)",
    }
    for field, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            out[field] = match.group(1)

    control = re.search(
        r"(?:control(?:s)?|control_labels|negative control|ntc)\s*(?:are|is|=|:)\s*([A-Za-z0-9_,; ./-]+)",
        text,
        flags=re.IGNORECASE,
    )
    if control:
        out["control_labels"] = _parse_labels(control.group(1))
    return out


def _parse_value(value: str) -> Any:
    value = value.strip()
    if "," in value or ";" in value:
        return _parse_labels(value)
    labels = _expand_range(value)
    return labels if len(labels) > 1 else value


def _parse_labels(value: str) -> list[str]:
    labels: list[str] = []
    for part in re.split(r"[,;/]|\band\b", value, flags=re.IGNORECASE):
        part = part.strip()
        if not part:
            continue
        expanded = _expand_range(part)
        labels.extend(expanded or [part])
    return _dedupe(labels)


def _expand_range(value: str) -> list[str]:
    match = re.fullmatch(r"([A-Za-z_.-]*?)(\d+)\s*-\s*(\d+)", value.strip())
    if not match:
        return [value.strip()] if value.strip() else []
    prefix, start_s, end_s = match.groups()
    start, end = int(start_s), int(end_s)
    if end < start or end - start > 100:
        return [value.strip()]
    width = max(len(start_s), len(end_s))
    return [f"{prefix}{i:0{width}d}" if width > 1 else f"{prefix}{i}" for i in range(start, end + 1)]


def _filter_patch(patch: dict[str, Any], expected: set[str]) -> dict[str, Any]:
    cleaned = {k: v for k, v in patch.items() if k in _KNOWN_FIELDS and v not in ("", None, [])}
    if not expected:
        return cleaned
    expected_cleaned = {k: v for k, v in cleaned.items() if k in expected}
    return expected_cleaned or cleaned


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


_KNOWN_FIELDS = {
    "control_labels",
    "guide_column",
    "target_column",
    "batch_column",
    "perturbation_modality",
    "moi",
    "loading_strategy",
    "guide_capture",
}
