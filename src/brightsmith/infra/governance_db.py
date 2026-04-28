"""Compatibility shim for Iceberg-backed governance APIs.

New code should import from :mod:`brightsmith.infra.governance`. This module
remains during migration so existing callers continue to work.
"""

from __future__ import annotations

from brightsmith.infra.governance.enterprise import *  # noqa: F403
from brightsmith.infra.governance.exporters import *  # noqa: F403
from brightsmith.infra.governance.migration import *  # noqa: F403
from brightsmith.infra.governance.product import *  # noqa: F403
from brightsmith.infra.governance.product import (  # noqa: F401
    _TABLE_CONFIGS,
    _get_governance_table,
    _query_table,
    _write_records,
)
