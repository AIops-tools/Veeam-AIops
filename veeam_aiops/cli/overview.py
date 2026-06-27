"""``veeam-aiops overview`` — one-shot environment health summary."""

from __future__ import annotations

import json

from veeam_aiops.cli._common import TargetOption, cli_errors, console, get_connection
from veeam_aiops.ops import overview


@cli_errors
def overview_cmd(target: TargetOption = None) -> None:
    """Health summary: jobs by last result, repos near full, running sessions."""
    conn, _ = get_connection(target)
    data = overview.health_overview(conn)
    console.print_json(json.dumps(data))
