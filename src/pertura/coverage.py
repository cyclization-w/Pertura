"""What a screen with too few control guides for hidden checks is told (PLAN.md Part 7's decision of 2026-10-07).

A screen measures its own false-hit rate when its control guides make a reference and at least one hidden group of as
many guides as its targets have (hidden_checks). Below that, Pertura still analyses it, with the same rule and the
same checks, and says what its hits can be trusted for, by kind of screen: the confirmed context (in vivo; in vitro,
primary cells and cell lines together) and modality.
- **recipe:** a kind where the coverage extension's recipe passed (in vitro CRISPRi), and in vitro knockout, which the
  held-out evaluation added (atlas protocol amendment 21, claim 1b, 2026-10-08). The screen is told the rate the same
  analysis set had on similar screens, labelled as measured on other screens, never its own rate and never a bound,
  together with its record on the held-out screens, where it was once far too low (HELD_OUT).
- **constrained:** every other kind. Supported hits and the skeptic's checks, no rate, said plainly. A kind with only
  one or two development screens shows their rates as information only.
No kind is refused. The numbers are those of the coverage extension (analysis/atlas/ATLAS_PROTOCOL.md amendment 19,
2026-10-07; report _research/pertura_coverage_extension_2026-10-07.md); they change only with a new run of it.
"""
CONTEXTS = {"in_vivo": "in vivo", "primary_in_vitro": "primary cells in vitro", "cell_line": "a cell line"}
MODALITY_NAMES = {"crispr_ko": "knockout", "crispri": "CRISPRi", "crispra": "CRISPRa", "mixed": "mixed modality",
                  "other": "another modality"}
REFERENCE_SHARE = 0.35  # pertura_research.retired.universe.build_universe's reference size, as a share of the control guides
SOURCE = "Pertura's coverage test on 16 published development screens (2026-10-07; atlas protocol amendment 19)"
KINDS = {
    ("in vitro", "crispri"): {
        "mode": "recipe", "judged": True, "screens": 6, "studies": 2,
        "rate": "1% or less on every core set (at most 3.3% on sets of 4 analyses), also with only 2 control guides in "
                "the reference",
        "caveat": "every development screen of this kind had a low rate, so there the statement was never tested as an "
                  "estimate of a higher one"},
    ("in vivo", "crispr_ko"): {
        "mode": "constrained", "judged": True, "screens": 6, "studies": 5,
        "rate": "from 1% to 57% on the core set (up to 100% on sets of 4 analyses), and no substitute estimated it "
                "within 2x reliably"},
    ("in vitro", "crispr_ko"): {  # measured on the 3 held-out screens; the 2 development ones had no supported hit
        "mode": "recipe", "judged": True, "screens": 3, "studies": 1,
        "rate": "1% or less on every core set (the 2 development screens of this kind had no supported hits to "
                "measure)",
        "caveat": "every one of those screens had a low rate, so the statement was never tested as an estimate of a "
                  "higher one"},
    ("in vitro", "crispra"): {"mode": "constrained", "judged": False, "screens": 2, "studies": 1,
                              "rate": "1% or less on both core sets"},
}
# The recipe statement's record on the held-out screens, tested once (atlas protocol amendment 21, 2026-10-08)
HELD_OUT = {
    ("in vitro", "crispri"): "On held-out screens it held on 2 of 3 in vitro CRISPRi screens; on the third, a microglia "
                             "cell line with 20 supported hits, the rate was 13.5%, so a screen can lie far above it",
    ("in vitro", "crispr_ko"): "Those 3 screens were held out (one study, a cell line at three time points); judged with "
                               "in vitro CRISPRi as one kind, the statement held on 5 of the 6 held-out in vitro "
                               "screens, and the sixth, a microglia CRISPRi screen, had 13.5%",
}
CANDIDATES = "Treat the supported hits as candidates, and confirm the top ones by an independent experiment."
# Single-guide libraries (atlas protocol amendment 20): hits supported across analyses and units instead of guides.
SINGLE_GUIDE_MIN_CONTROLS = 6  # tier 2: enough single control guides for hidden checks of one guide each
SINGLE_GUIDE_RULE = ("This library has one guide per target, so a hit cannot be replicated across guides: it must be "
                     "significant in at least half of the analyses run (units count through the analyses that test "
                     "per unit), and one guide's off-target effect cannot be ruled out.")
SINGLE_GUIDE_KINDS = {  # the single-guide rule's core-set rate on the development screens, emulated (amendment 20)
    ("in vivo", "crispr_ko"): "a median of 3.8% on the core set (up to 43%), over 6 screens from 5 studies",
    ("in vitro", "crispri"): "1% or less (median) on the core set, over 6 screens from 2 studies; on 3 held-out screens "
                             "(2026-10-08), a median of 3.7%",
    ("in vitro", "crispra"): "a median of 2.1% on the core set, over 2 screens from 1 study",
    ("in vitro", "crispr_ko"): "not measurable on its 2 development screens (1 study: too few supported hits); on 3 "
                               "held-out screens of one study (2026-10-08), 1% or less on the core set",
}


def hidden_checks_single_guide(n_control_guides):
    """(possible, reference guides, guides left for hidden groups of one guide each) for a single-guide library:
    possible with at least SINGLE_GUIDE_MIN_CONTROLS control guides."""
    n_ref = max(1, int(round(REFERENCE_SHARE * n_control_guides)))
    return n_control_guides >= SINGLE_GUIDE_MIN_CONTROLS, n_ref, n_control_guides - n_ref


def single_guide_statement(context, modality, n_control_guides):
    """What a single-guide library without hidden checks is told: constrained mode, with the single-guide rule's
    development rate for its kind as information (amendment 20)."""
    k = kind(context, modality)
    name = kind_name(context, modality)
    rate = SINGLE_GUIDE_KINDS.get(k)
    head = (f"This screen has {n_control_guides} control guide{'s' if n_control_guides != 1 else ''} with cells: too "
            f"few for hidden checks of single guides (it needs {SINGLE_GUIDE_MIN_CONTROLS}), so the false-hit rate of "
            "its hits cannot be measured on it.")
    info = (f" On development screens of this kind, emulated as single-guide libraries, the same rule's rate was "
            f"{rate} (information only)." if rate else
            f" Pertura has no development screen of this kind ({name}), so nothing from other screens applies.")
    return {"mode": "constrained", "kind": name, "statement": f"{head} {SINGLE_GUIDE_RULE}{info} {CANDIDATES}",
            "information": rate, "source": SOURCE.replace("amendment 19", "amendment 20")}


def hidden_checks(n_control_guides, guides_per_target):
    """(possible, reference guides, guides left for hidden groups): a reference of REFERENCE_SHARE of the control
    guides (at least one), and at least one hidden group of guides_per_target guides."""
    n_ref = max(1, int(round(REFERENCE_SHARE * n_control_guides)))
    pool = n_control_guides - n_ref
    return pool >= max(1, guides_per_target), n_ref, pool


def kind(context, modality):
    """(setting, modality): the setting is 'in vivo' or 'in vitro' (primary cells and cell lines together)."""
    if context not in CONTEXTS:
        raise ValueError(f"context {context!r} is not one of {sorted(CONTEXTS)}")
    return ("in vivo" if context == "in_vivo" else "in vitro"), modality


def kind_name(context, modality):
    setting, mod = kind(context, modality)
    return f"{setting} {MODALITY_NAMES.get(mod, mod)}"


def statement(context, modality, n_control_guides):
    """What a screen without hidden checks is told: {mode, kind, statement, information, source}."""
    k = kind(context, modality)
    name = kind_name(context, modality)
    head = (f"This screen has {n_control_guides} control guide{'s' if n_control_guides != 1 else ''} with cells: too "
            "few for hidden checks, so the false-hit rate of its hits cannot be measured on it.")
    info = KINDS.get(k)
    if info and info["mode"] == "recipe":
        held = HELD_OUT.get(k)
        text = (f"{head} On {info['screens']} {name} screens from {info['studies']} "
                f"stud{'y' if info['studies'] == 1 else 'ies'}, the same analysis set's supported hits had a false-hit "
                f"rate of {info['rate']}. That rate was measured on other screens, not this one, and {info['caveat']}."
                + (f" {held}." if held else ""))
        return {"mode": "recipe", "kind": name, "statement": text, "information": info["rate"], "held_out": held,
                "source": SOURCE}
    if info and info["judged"]:
        text = (f"{head} On {info['screens']} {name} screens from {info['studies']} studies, the same rule's rate "
                f"ranged {info['rate']}, so no rate from other screens applies. {CANDIDATES}")
        return {"mode": "constrained", "kind": name, "statement": text, "information": info["rate"],
                "source": SOURCE}
    if info:
        text = (f"{head} Pertura has {info['screens']} {name} screens from {info['studies']} "
                f"stud{'y' if info['studies'] == 1 else 'ies'}, too few to rely on; their rate was {info['rate']} "
                f"(information only). {CANDIDATES}")
        return {"mode": "constrained", "kind": name, "statement": text, "information": info["rate"],
                "source": SOURCE}
    text = f"{head} Pertura has no development screen of this kind ({name}), so nothing from other screens applies. " \
           f"{CANDIDATES}"
    return {"mode": "constrained", "kind": name, "statement": text, "information": None, "source": SOURCE}
