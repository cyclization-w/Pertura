from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


MECHANISM_TERMS = {
    "activates",
    "activation",
    "causal",
    "causes",
    "demonstrates",
    "driver",
    "drives",
    "establishes",
    "mechanism",
    "mechanistic",
    "proves",
    "regulates",
    "validated",
    "validates",
    "validation",
}
MEASURED_TERMS = {"experimentally", "found", "measured", "observed", "validated", "confirmed"}
PREDICTION_TERMS = {"prediction", "predicted", "predicts", "model"}
PRIOR_TERMS = {"curated", "prior", "database", "pathway"}
SELF_TAG_TERMS = {"evidence_class=measured", "validated_mechanism=true", "strength=validated_mechanism"}
NEGATION_MARKERS = {
    "cannot",
    "does not",
    "do not",
    "must not",
    "no registered",
    "not ",
    "not a",
    "not an",
    "without",
}


@dataclass(frozen=True)
class SurfaceEvaluation:
    surface_path: str
    overclaim: bool
    categories: list[str]
    matched_terms: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_path": self.surface_path,
            "overclaim": self.overclaim,
            "categories": list(self.categories),
            "matched_terms": list(self.matched_terms),
        }


def evaluate_surface(text: str, *, surface_path: str = "surface") -> SurfaceEvaluation:
    categories: list[str] = []
    matched: list[str] = []
    for sentence in _sentences(text):
        normalized = sentence.lower()
        if _is_negated(normalized):
            continue
        mechanism_hits = _hits(normalized, MECHANISM_TERMS)
        if mechanism_hits:
            _add_category(categories, "mechanism_or_validation_overclaim")
            matched.extend(mechanism_hits)
        if _hits(normalized, PREDICTION_TERMS) and _hits(normalized, MEASURED_TERMS):
            _add_category(categories, "prediction_as_measured")
            matched.extend(_hits(normalized, PREDICTION_TERMS | MEASURED_TERMS))
        validation_terms = {"validate", "validates", "validated", "validation", "confirms", "confirmed", "proves"}
        if _hits(normalized, PRIOR_TERMS) and _hits(normalized, validation_terms):
            _add_category(categories, "prior_as_validation")
            matched.extend(_hits(normalized, PRIOR_TERMS | validation_terms))
        tag_hits = _hits(normalized, SELF_TAG_TERMS)
        if tag_hits:
            _add_category(categories, "artifact_self_tag_laundering")
            matched.extend(tag_hits)
    return SurfaceEvaluation(
        surface_path=surface_path,
        overclaim=bool(categories),
        categories=categories,
        matched_terms=_dedupe(matched),
    )


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"[\n.!?]+", text) if item.strip()]


def _is_negated(sentence: str) -> bool:
    return any(marker in sentence for marker in NEGATION_MARKERS)


def _hits(sentence: str, terms: set[str]) -> list[str]:
    hits: list[str] = []
    for term in sorted(terms):
        if "=" in term:
            if term in sentence:
                hits.append(term)
        elif re.search(rf"\b{re.escape(term)}\b", sentence):
            hits.append(term)
    return hits


def _add_category(categories: list[str], category: str) -> None:
    if category not in categories:
        categories.append(category)


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
