"""Vector (construct) assignment tools.

- provided: the calls shipped with the data.
- min_umi:t: re-called from guide UMIs. The construct with the most UMIs (summed over its sgRNAs) wins if it has at
  least t UMIs and at least 80% of the cell's guide UMIs; otherwise the cell is dropped.

Constructs map back to groups through the provided labels (groups.build_groups). Planned: crispat mixture models,
and calls from CellBender-cleaned guide counts.
"""
import numpy as np
import pandas as pd
import scipy.sparse as sp

DOMINANCE = 0.8
MIN_UMI = (3, 5)


def recall_constructs(guide_counts, sgrna_names, suffix, min_umi, dominance=DOMINANCE):
    """Re-called construct label per cell ('' if none passes). guide_counts: cells x sgRNAs, same cell order as the
    dataset; sgRNA columns are named <construct><suffix><k>."""
    owner = [str(n).rsplit(suffix, 1)[0] for n in sgrna_names]
    labels = np.array(sorted(set(owner)))
    col = pd.Series(np.arange(len(labels)), index=labels)[owner].to_numpy()
    M = sp.csr_matrix((np.ones(len(owner)), (np.arange(len(owner)), col)), shape=(len(owner), len(labels)))
    dense = (sp.csr_matrix(guide_counts) @ M).toarray().astype(np.float32)
    best = dense.argmax(1)
    best_umi = dense[np.arange(len(best)), best]
    ok = (best_umi >= min_umi) & (best_umi >= dominance * dense.sum(1))
    return np.where(ok, labels[best], "")
