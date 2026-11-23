"""Machine-readable design of a screen (schema pertura.design/v2).

It records which cell-metadata columns hold the perturbation, construct, unit and batch, what the controls are,
whether units are independent, and where the per-cell guide counts are. The free-text design.yaml given to agents
stays as it is; the grammar and the tools read this file.

Example (Zhou 2023, blinded): perturbation_col perturbation_id, construct_col vector_id, control_label control,
unit_col lane (technical, independent false), batch_col batch, constructs_per_target 2.
"""
import hashlib
import json
from dataclasses import asdict, dataclass, fields

SCHEMA = "pertura.design/v2"
MODALITIES = ("crispr_ko", "crispri", "crispra", "mixed", "other")
CONTEXTS = ("", "in_vivo", "primary_in_vitro", "cell_line")  # as design_gate.CONTEXT_VALUES, '' for unknown


@dataclass(frozen=True)
class Design:
    perturbation_col: str
    construct_col: str
    control_label: str
    modality: str = "crispr_ko"
    unit_col: str = ""              # pseudobulk unit: mouse, donor or lane ('' if none)
    unit_independent: bool = False  # True for mice or donors, False for technical lanes
    batch_col: str = ""
    control_kind_col: str = ""      # column naming the control kind (e.g. cutting, non-cutting), '' if one kind
    control_kinds: tuple = ()       # kinds present among controls
    constructs_per_target: int = 2
    guide_counts: str = ""          # path to per-cell guide UMI counts (AnnData), '' if not provided
    sgrna_suffix: str = "_s"        # guide-count columns are <construct><suffix><k>, e.g. P071_v1_s2
    schema: str = SCHEMA
    # the confirmed experiment.context (in_vivo, primary_in_vitro, cell_line), '' if unknown (2026-10-11): which
    # verified test may grade, and whether cells as replicates pass gate 3, depend on it (ADR 0013 §6, ADR 0016 §5.6)
    context: str = ""

    @classmethod
    def from_dict(cls, d):
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(d) - known)
        if unknown:
            raise ValueError(f"unknown design fields {unknown}")
        d = dict(d)
        d["control_kinds"] = tuple(d.get("control_kinds", ()))
        return cls(**d)

    @classmethod
    def load(cls, path):
        with open(path) as fh:
            return cls.from_dict(json.load(fh))

    def to_dict(self):
        d = asdict(self)
        d["control_kinds"] = list(self.control_kinds)
        if not d["context"]:  # a design without a confirmed context keeps its file, and its fingerprint, as before
            del d["context"]
        return d

    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()

    def problems(self, obs):
        """What is wrong with this design for a cell table (pandas DataFrame of cell metadata); [] if nothing."""
        out = []
        if self.schema != SCHEMA:
            out.append(f"schema {self.schema!r}, expected {SCHEMA!r}")
        if self.modality not in MODALITIES:
            out.append(f"modality {self.modality!r} not in {MODALITIES}")
        if self.context not in CONTEXTS:
            out.append(f"context {self.context!r} not in {CONTEXTS}")
        for name in ("perturbation_col", "construct_col", "unit_col", "batch_col", "control_kind_col"):
            col = getattr(self, name)
            if col and col not in obs.columns:
                out.append(f"{name} {col!r} is not a cell-metadata column")
        if self.perturbation_col in obs.columns and not (obs[self.perturbation_col] == self.control_label).any():
            out.append(f"no cell has the control label {self.control_label!r}")
        if self.control_kind_col and self.control_kind_col in obs.columns:
            ctrl = obs[self.perturbation_col] == self.control_label
            present = set(obs.loc[ctrl, self.control_kind_col].astype(str))
            missing = [k for k in self.control_kinds if k not in present]
            if missing:
                out.append(f"control kinds {missing} not found among control cells")
        if self.constructs_per_target < 1:
            out.append("constructs_per_target must be at least 1")
        return out
