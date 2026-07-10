from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from pertura_bench.models import BenchmarkSplitManifest, TargetVerdict, TargetVerdictSet


CLASSES = ("screen_passed", "caution", "blocked", "unresolved")


def published_proxy_verdict_set(
    *,
    modality: str,
    split: BenchmarkSplitManifest,
    rows: Iterable[dict[str, Any]],
    importer_version: str,
    importer_hash: str,
) -> TargetVerdictSet:
    verdicts = []
    for row in rows:
        verdicts.append(TargetVerdict(
            modality=modality,
            dataset_id=str(row["dataset_id"]),
            target_id=str(row["target_id"]),
            expected_direction=str(row["expected_direction"]),
            verdict=str(row["verdict"]),
            reason_codes=tuple(str(item) for item in row.get("reason_codes") or ()),
            label_source="published_proxy",
            validated=False,
            doi=row.get("doi"),
            pmid=row.get("pmid"),
            supplement=row.get("supplement"),
            table=row.get("table"),
            sheet=row.get("sheet"),
            row=str(row.get("row") or "") or None,
            importer_version=importer_version,
            importer_hash=importer_hash,
        ))
    return TargetVerdictSet(
        modality=modality,
        label_source="published_proxy",
        split_manifest_hash=split.canonical_hash,
        verdicts=tuple(verdicts),
        validated=False,
    )


def evaluate_target_predictions(verdict_set: TargetVerdictSet, predictions: dict[str, str]) -> dict[str, Any]:
    confusion = {name: Counter() for name in CLASSES}
    for item in verdict_set.verdicts:
        predicted = predictions.get(item.target_id, "unresolved")
        if predicted not in CLASSES:
            raise ValueError(f"unknown predicted verdict: {predicted}")
        confusion[item.verdict][predicted] += 1
    recalls = {}
    f1s = []
    for label in CLASSES:
        true_positive = confusion[label][label]
        support = sum(confusion[label].values())
        predicted_total = sum(confusion[actual][label] for actual in CLASSES)
        recall = true_positive / support if support else None
        precision = true_positive / predicted_total if predicted_total else None
        recalls[label] = recall
        if support:
            f1s.append(2 * precision * recall / (precision + recall) if precision is not None and precision + recall else 0.0)
    non_block = [item for item in verdict_set.verdicts if item.verdict != "blocked"]
    erroneous_blocks = sum(predictions.get(item.target_id, "unresolved") == "blocked" for item in non_block)
    return {
        "schema_version": "pertura-target-profile-evaluation-v1",
        "verdict_set_hash": verdict_set.canonical_hash,
        "label_source": verdict_set.label_source,
        "proxy_only": verdict_set.label_source == "published_proxy",
        "production_eligible": verdict_set.label_source == "expert_adjudicated" and verdict_set.validated,
        "macro_f1": sum(f1s) / len(f1s) if f1s else 0.0,
        "per_class_recall": recalls,
        "erroneous_block_rate": erroneous_blocks / len(non_block) if non_block else 0.0,
        "n_verdicts": len(verdict_set.verdicts),
    }
