"""pertura: the MCP server and the analysis code of Pertura, a Claude Code plugin for analysing Perturb-seq screens.

The server is started with `python -m pertura.mcp_server serve-analysis <workspace config>`; the README describes the
parts.
"""
import os as _os

__version__ = "0.3.0"

# JAX (pertpy's scCODA and guide mixture model) computes on the CPU, within the job's own cores: a GPU is shared with
# other work, and JAX would otherwise reserve most of its memory when it starts
_os.environ.setdefault("JAX_PLATFORMS", "cpu")
