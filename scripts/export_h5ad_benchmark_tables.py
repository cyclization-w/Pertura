from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _gene_alias(values: list[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for value in values:
        source, separator, target = value.partition("=")
        if not separator or not source.strip() or not target.strip():
            raise ValueError("--gene-alias must use SOURCE=TARGET")
        aliases[source.strip()] = target.strip()
    return aliases


def _requested_genes(path: Path, column: str) -> list[str]:
    import pandas as pd

    table = pd.read_csv(path, sep="\t" if path.suffix.lower() in {".tsv", ".txt"} else ",")
    if column not in table.columns:
        raise ValueError(f"gene file is missing column: {column}")
    return list(dict.fromkeys(str(item).strip() for item in table[column] if str(item).strip()))


def export_tables(args: argparse.Namespace) -> dict[str, object]:
    import anndata as ad
    import numpy as np
    import pandas as pd
    from scipy import sparse

    source = args.input.expanduser().resolve()
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    data = ad.read_h5ad(source, backed="r")
    try:
        missing_obs = sorted(set(args.obs_column) - set(data.obs.columns))
        if missing_obs:
            raise ValueError("H5AD obs columns are missing: " + ", ".join(missing_obs))
        metadata = data.obs.loc[:, args.obs_column].copy()
        metadata.insert(0, args.cell_column, data.obs_names.astype(str))
        metadata_path = output / "cell_metadata.tsv"
        metadata.to_csv(metadata_path, sep="\t", index=False, lineterminator="\n")

        files: dict[str, str] = {metadata_path.name: _sha256(metadata_path)}
        expression_columns: list[str] = []
        if args.expression_genes_file is not None:
            aliases = _gene_alias(args.gene_alias)
            excluded = set(args.exclude_gene)
            requested = [
                aliases.get(gene, gene)
                for gene in _requested_genes(
                    args.expression_genes_file.expanduser().resolve(),
                    args.expression_gene_column,
                )
                if gene not in excluded
            ]
            requested = list(dict.fromkeys(requested))
            positions = {str(gene): index for index, gene in enumerate(data.var_names)}
            missing = [gene for gene in requested if gene not in positions]
            if missing:
                raise ValueError("H5AD expression genes are missing: " + ", ".join(missing))
            estimated = int(data.n_obs) * len(requested) * 8
            maximum = float(args.max_memory_gb) * 1024**3
            if estimated > maximum:
                raise MemoryError(
                    f"selected expression estimate {estimated / 1024**3:.3f} GB "
                    f"exceeds max_memory_gb={args.max_memory_gb}"
                )
            matrix_source = data.X if args.layer == "X" else data.layers[args.layer]
            selected = matrix_source[:, [positions[gene] for gene in requested]]
            if hasattr(selected, "to_memory"):
                selected = selected.to_memory()
            values = selected.toarray() if sparse.issparse(selected) else np.asarray(selected)
            expression = pd.DataFrame(values, columns=requested)
            expression.insert(0, args.cell_column, data.obs_names.astype(str))
            expression_path = output / "target_expression.tsv"
            expression.to_csv(expression_path, sep="\t", index=False, lineterminator="\n")
            files[expression_path.name] = _sha256(expression_path)
            expression_columns = requested
    finally:
        if getattr(data, "file", None):
            data.file.close()

    manifest: dict[str, object] = {
        "schema_version": "pertura-h5ad-benchmark-tables-v1",
        "source_sha256": _sha256(source),
        "cell_count": int(metadata.shape[0]),
        "cell_column": args.cell_column,
        "metadata_columns": list(args.obs_column),
        "expression_columns": expression_columns,
        "layer": args.layer if expression_columns else None,
        "files": files,
    }
    manifest_path = output / "benchmark_tables_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return manifest | {"manifest_sha256": _sha256(manifest_path)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export hashable metadata and selected expression columns from H5AD."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--obs-column", action="append", default=[])
    parser.add_argument("--cell-column", default="cell_id")
    parser.add_argument("--expression-genes-file", type=Path)
    parser.add_argument("--expression-gene-column", default="target")
    parser.add_argument("--gene-alias", action="append", default=[])
    parser.add_argument("--exclude-gene", action="append", default=[])
    parser.add_argument("--layer", default="X")
    parser.add_argument("--max-memory-gb", type=float, default=4.0)
    args = parser.parse_args()
    if not args.obs_column:
        parser.error("at least one --obs-column is required")
    if args.max_memory_gb <= 0:
        parser.error("--max-memory-gb must be positive")
    print(json.dumps(export_tables(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
