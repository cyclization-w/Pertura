"""The numbers of a text against the outputs they should come from (docs/build_goal_session6_fixes.md, item 9, and
ADR 0034, D5): each number of a result's text, or of a finding's statement, is looked for among the numbers of the
runs it cites, as written or rounded to the text's precision, as a percentage of a fraction, or without its sign (a
change written as "down 0.39"). What is not found is listed for the agent; nothing is refused.

Left out of the text's numbers: those inside a word or an ID (GATA1, run0003), the record's markers ([run:ID]), and
the numbers of a figure, panel or result named in the text (Figure 2, panel b)."""
import math
import re
from decimal import ROUND_HALF_UP, Decimal

MARKER = re.compile(r"\[(?:run|finding|knowledge|question|dossier|doubt|result|thread):[^\]\s]+\]")
NAMED = re.compile(r"\b(?:Figure|Fig\.|Extended Data Fig\.|Extended Data Figure|Result|panel|Panel|Table|step|Step)"
                   r"\s+\d+[a-z]?", re.I)
NUMBER = re.compile(r"(?<![\w.\-])(-|−)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?:[eE]([-+]?\d+))?(%)?(?![\w])")


RANGE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*[-–]\s*(?=\d)")  # 2-3 fold: both ends; not NKX2-1
POWER = re.compile(r"\b10\s*\^\s*([-−]?\d+)")  # 10^-5 as 1e-5


def numbers(text):
    """[(the number as written, its value, its decimals, whether a percentage)] of a text."""
    text = NAMED.sub(" ", MARKER.sub(" ", str(text or "")))
    text = POWER.sub(lambda m: "1e" + m.group(1).replace("−", "-"), RANGE.sub(r"\1 to ", text))
    out = []
    for m in NUMBER.finditer(text):
        sign, whole, frac, exp, pct = m.groups()
        value = float(whole.replace(",", "") + (frac or "")) * (10 ** int(exp) if exp else 1)
        places = (len(frac) - 1 if frac else 0) - (int(exp) if exp else 0)  # 1e-5 is read to its fifth decimal
        out.append((m.group(0), -value if sign else value, places, bool(pct)))
    return out


def _walk(x, out):
    if isinstance(x, bool):
        return
    if isinstance(x, (int, float)):
        out.append(float(x))
    elif isinstance(x, str):
        if re.fullmatch(r"[\w:.\-+]+", x) and not re.fullmatch(r"[-+]?\d+(\.\d+)?([eE][-+]?\d+)?", x):
            return  # one token of letters, digits and marks: an ID or a time, not a measured number
        out += [v for _, v, _, _ in numbers(x)]
    elif isinstance(x, dict):
        for v in x.values():
            _walk(v, out)
    elif isinstance(x, (list, tuple)):
        for v in x:
            _walk(v, out)


def source_numbers(*sources):
    """Every number in the sources: the values of JSON-able objects (a result's summary and diagnostics, a table's
    rows), and the numbers of texts read as the text's own are (never the digits inside an ID or a time)."""
    out = []
    for s in sources:
        _walk(s, out)
    return out


def _round(x, decimals):
    try:
        return float(Decimal(repr(x)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP))
    except ArithmeticError:  # beyond Decimal's precision (a huge value, or many decimals): float rounding is as good
        return round(x, decimals)


def _matches(value, decimals, percent, y):
    if not math.isfinite(y):
        return False
    for cand in (y, -y, 100 * y, -100 * y) if percent else (y, -y):
        if _round(cand, decimals) == _round(value, decimals):
            return True
    return False


def unsupported(text, sources):
    """The numbers of the text found in none of the sources' numbers, as written in the text."""
    found = source_numbers(*sources)
    return [w for w, v, d, p in numbers(text) if not any(_matches(v, d, p, y) for y in found)]
