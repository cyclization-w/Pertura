"""Gene-set and TF databases kept offline (fetched once by analysis/resources/fetch_gene_set_databases.py, approved
by the user on 2026-10-09): MSigDB (release 2025.1), CollecTRI regulons and PROGENy pathway models, for human and
mouse. MSigDB began with Hallmark and GO biological process; the GO cellular components, the known complexes, came on
2026-11-04 (analysis/resources/fetch_go_cellular_component_sets.py), and every other collection on 2026-11-05
(analysis/resources/fetch_msigdb_all_collections.py). The folder is PERTURA_GENE_SETS, or the server's fetched set.

    from pertura import gene_sets as gs
    hallmark = gs.msigdb("hallmark", "mouse")      # {set name: [genes]}
    net = gs.collectri("mouse")                     # source, target, weight (decoupler's network format)
    prog = gs.progeny("human", top=500)             # source (pathway), target (gene), weight, p_value
"""
import json
import os

import numpy as np
import pandas as pd

DEFAULT = os.path.join(os.path.expanduser(os.environ.get("PERTURA_BASE", "~/pertura")), "resources/gene_sets/v2026-10-09")
SPECIES = {"human": "Hs", "mouse": "Mm"}
# MSigDB's collections by a name that holds for both organisms: (human code, mouse code), None where MSigDB has no
# mouse collection, whose human sets are then translated to mouse (human_to_mouse); any other code the folder holds is
# read by its code (collections(organism))
NAMED = {"hallmark": ("h.all", "mh.all"), "go_bp": ("c5.go.bp", "m5.go.bp"), "go_cc": ("c5.go.cc", "m5.go.cc"),
         "go_mf": ("c5.go.mf", "m5.go.mf"), "reactome": ("c2.cp.reactome", "m2.cp.reactome"),
         "wikipathways": ("c2.cp.wikipathways", "m2.cp.wikipathways"), "kegg": ("c2.cp.kegg_medicus", None),
         "canonical_pathways": ("c2.cp", "m2.cp"), "tf_targets": ("c3.tft", "m3.gtrd"),
         "immunologic": ("c7.all", "m7.all"), "immunesigdb": ("c7.immunesigdb", None),
         "cell_types": ("c8.all", "m8.all"), "oncogenic": ("c6.all", None)}
HCOP = ("hcop", "human_mouse_hcop_fifteen_column.txt.gz")
MIN_EVIDENCE, ONE_TO_MANY = 3, 5  # decoupler.op.translate's defaults


def folder():
    return os.environ.get("PERTURA_GENE_SETS", DEFAULT)


def manifest():
    with open(os.path.join(folder(), "MANIFEST.json")) as fh:
        return json.load(fh)


def read_gmt(path):
    """{set name: [genes]} from a GMT file (name, description, genes...)."""
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3:
                out[parts[0]] = [g for g in parts[2:] if g]
    return out


def collections(organism):
    """The MSigDB codes the folder holds for the organism (human or mouse), such as c2.cgp or m5.mpt."""
    tag = f".v2025.1.{SPECIES[organism]}.symbols.gmt"
    where = os.path.join(folder(), "msigdb")
    return sorted(f[:-len(tag)] for f in os.listdir(where) if f.endswith(tag)) if os.path.isdir(where) else []


def _gmt(code, organism):
    return os.path.join(folder(), "msigdb", f"{code}.v2025.1.{SPECIES[organism]}.symbols.gmt")


def human_to_mouse(sets, min_evidence=MIN_EVIDENCE, one_to_many=ONE_TO_MANY):
    """{set: [human genes]} translated to mouse through HCOP, as decoupler.op.translate does it: a gene's mouse
    orthologs that at least min_evidence of HCOP's resources support, a gene with more than one_to_many of them left
    out, each of the others kept."""
    t = pd.read_csv(os.path.join(folder(), *HCOP), sep="\t", compression="gzip", low_memory=False,
                    usecols=["human_symbol", "mouse_symbol", "support"])
    t = t[t["support"].astype(str).str.split(",").str.len() >= min_evidence]
    orth = t.groupby("human_symbol")["mouse_symbol"].apply(lambda s: sorted(set(s.astype(str))))
    orth = orth[orth.str.len() <= one_to_many].to_dict()
    return {name: sorted({m for g in genes for m in orth.get(g, [])}) for name, genes in sets.items()}


def translated(collection, organism):
    """Whether msigdb gives this collection for the organism by translating human sets (mouse, no mouse file)."""
    code = NAMED[collection][organism == "mouse"] if collection in NAMED else collection
    human = NAMED[collection][0] if collection in NAMED else collection
    return (organism == "mouse" and not (code and os.path.exists(_gmt(code, organism)))
            and os.path.exists(_gmt(human, "human")))


def msigdb(collection, organism):
    """{set name: [genes]} of an MSigDB collection for human or mouse: a name of NAMED (hallmark, go_bp, go_cc,
    go_mf, reactome, wikipathways, kegg, canonical_pathways, tf_targets, immunologic, immunesigdb, cell_types,
    oncogenic), or any code the folder holds (collections). For mouse, a collection MSigDB has only for human (kegg,
    immunesigdb, oncogenic, or a human code) is its human sets translated through HCOP (human_to_mouse)."""
    if organism not in SPECIES:
        raise ValueError(f"organism human or mouse (got {organism})")
    if translated(collection, organism):
        return human_to_mouse(read_gmt(_gmt(NAMED[collection][0] if collection in NAMED else collection, "human")))
    code = NAMED[collection][organism == "mouse"] if collection in NAMED else collection
    path = _gmt(code, organism) if code else None
    if path is None or not os.path.exists(path):
        raise ValueError(f"no MSigDB collection {collection} for {organism}: the names are {', '.join(NAMED)}; the "
                         f"codes held are {', '.join(collections(organism)) or 'none'}")
    return read_gmt(path)


def collectri(organism):
    """CollecTRI as decoupler's network: source (TF), target, weight (sign of regulation), one row per pair. The
    mouse table repeats pairs that two human genes map to (353 pairs on 2026-11-09), which decoupler refuses: a pair
    whose rows agree is kept once, as decoupler's own message advises; one whose rows disagree in sign is left out,
    its sign of regulation unknown in mouse."""
    if organism == "human":
        t = pd.read_csv(os.path.join(folder(), "collectri", "CollecTRI_regulons_human.csv"))
    elif organism == "mouse":
        t = pd.read_csv(os.path.join(folder(), "collectri", "CollecTRI_regulons_mouse.tsv"), sep="\t")
    else:
        raise ValueError("organism human or mouse")
    t.columns = [c.lower() for c in t.columns]
    return one_row_per_pair(t[["source", "target", "weight"]])


def one_row_per_pair(net):
    """A network with each (source, target) pair once: repeated rows that agree kept once, pairs whose rows disagree
    in sign left out."""
    signs = net.groupby(["source", "target"])["weight"].transform(lambda w: np.sign(w).nunique())
    return net[signs == 1].drop_duplicates(subset=["source", "target"]).reset_index(drop=True)


def progeny(organism, top=500):
    """PROGENy as decoupler's network: source (pathway), target (gene), weight, p_value; the top genes per pathway by
    p-value (500 is decoupler's default; None for the full model)."""
    if organism not in ("human", "mouse"):
        raise ValueError("organism human or mouse")
    name = f"progeny_{organism}_top500.tsv" if top == 500 else f"progeny_{organism}_full.tsv"
    t = pd.read_csv(os.path.join(folder(), "progeny", name), sep="\t")
    t = t.rename(columns={"pathway": "source", "gene": "target", "p.value": "p_value"})
    if top not in (None, 500):
        t = t.sort_values("p_value").groupby("source").head(int(top))
    return t[["source", "target", "weight", "p_value"]].reset_index(drop=True)
