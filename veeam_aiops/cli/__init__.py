"""CLI package for veeam-aiops.

Re-exports ``app`` so the pyproject entry point
``veeam-aiops = "veeam_aiops.cli:app"`` works unchanged.
"""

from veeam_aiops.cli._root import app

__all__ = ["app"]
