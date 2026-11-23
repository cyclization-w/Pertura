"""The most common of a set of values, with one rule for a tie wherever Pertura picks one value for many (a guide's
label, a target's guide, a unit's kind, a column's value per unit): the tie is reported, and broken by the values'
order (numbers by size, the rest as text), never by the order of the rows."""
from collections import Counter


def _missing(v):
    return v is None or (isinstance(v, float) and v != v)


def _order(v):
    number = isinstance(v, (int, float)) and not isinstance(v, bool)
    return (0, v, "") if number else (1, 0, str(v))


def most_common(values):
    """(the most common value, the values tied for most common in their order, [] when none ties); (None, []) when
    there is no value. Missing values (None, NaN) are not counted."""
    counts = Counter(v for v in values if not _missing(v))
    if not counts:
        return None, []
    top = max(counts.values())
    tied = sorted((v for v, n in counts.items() if n == top), key=_order)
    return tied[0], (tied if len(tied) > 1 else [])


def most_common_value(values):
    """The most common value alone, a tie broken by the values' order; None when there is none."""
    return most_common(values)[0]
