"""Generic Pertura capability references.

This module intentionally exposes only domain-neutral harness capabilities.
Domain-specific actions live under their domain pack, for example:

    from pertura.domain import perturbseq as ps
    ps.caps.run_de

The runtime still serializes capability ids as strings so domain JSON remains
portable and easy to edit.
"""

from pertura.core_caps import *  # noqa: F403
from pertura.core_caps import __all__  # re-export core capability names only
