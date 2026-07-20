"""a VBR server behind a TLS-terminating proxy is plain HTTP; don't hardcode it.

Same defect monitoring-aiops hit on a live Zabbix: ``base_url`` was built as
``https://{host}:{port}`` with no override, so an http-only instance was
simply unreachable — the only clue being a TLS record-layer error. The tell is
that this tool CONSTRUCTS its URL; the siblings that take a free-form
``base_url`` never had the problem.
"""

from __future__ import annotations

import pytest

from veeam_aiops.config import TargetConfig


@pytest.mark.unit
def test_scheme_defaults_to_https_so_existing_configs_are_unchanged():
    t = TargetConfig(name="v1", host="h", username="u", port=9419)
    assert t.scheme == "https"
    assert t.base_url.startswith("https://")


@pytest.mark.unit
def test_scheme_http_is_honoured():
    t = TargetConfig(name="v1", host="h", username="u", port=8080, scheme="http")
    assert t.base_url.startswith("http://")
    assert not t.base_url.startswith("https://")


@pytest.mark.unit
def test_invalid_scheme_is_rejected_at_construction():
    with pytest.raises(ValueError, match="scheme must be"):
        TargetConfig(name="v1", host="h", username="u", port=9419, scheme="ftp")
