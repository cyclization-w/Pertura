"""Helpers of the regression tests (tests/regressions): a guide row, a confirmed design node, a design for a list of
guides. The tests began as an outside audit's failing reproductions of the defects in docs/build/bug_register.md,
written against main 2f87b85 on synthetic screens; each is marked with the register's entry it shows."""


def node(v):
    return {"value": v, "status": "confirmed", "source": "synthetic regression fixture"}


def design_for(guides, matches=()):
    ts = [g for g in guides if g["kind"] == "targeting"]
    return {"perturbation": {
        "modality": node("mixed"), "delivery": node("singles"),
        "target_map": node({t: [g["guide"] for g in ts if g["target"] == t]
                            for t in dict.fromkeys(g["target"] for g in ts)}),
        "controls": node([{"name": "NT", "kind": "non_targeting",
                           "guides": [g["guide"] for g in guides if g["kind"] != "targeting"]}]),
        "guides": node({g["guide"]: {k: g[k] for k in ("mode", "gene", "element", "vector") if k in g}
                        for g in guides}),
        "reference_matches": node(list(matches))},
        "units": {"independent_unit": node("none")}}


def guide(g, target, mode="CRISPRi", **attrs):
    return dict(guide=g, target=target, kind="non_targeting" if target.startswith("NT") else "targeting",
                mode=mode, **attrs)
