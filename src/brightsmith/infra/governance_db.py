"""Compatibility shim for Iceberg-backed governance APIs.

New code should import from :mod:`brightsmith.infra.governance`. This module
remains during migration so existing callers continue to work.

Private names (_TABLE_CONFIGS, _get_governance_table, _query_table,
_write_records) are no longer re-exported here. Import them from their
canonical modules:

    from brightsmith.infra.governance.schemas import _TABLE_CONFIGS
    from brightsmith.infra.governance.queries import (
        _get_governance_table, _query_table, _write_records,
    )
"""

from __future__ import annotations

from brightsmith.infra.governance.enterprise import *  # noqa: F403
from brightsmith.infra.governance.exporters import *  # noqa: F403
from brightsmith.infra.governance.migration import *  # noqa: F403
from brightsmith.infra.governance.product import *  # noqa: F403

if __name__ == "__main__":
    # `python -m brightsmith.infra.governance_db <command>` is the documented
    # CLI. Without this guard it imported the façade and exited 0 having done
    # nothing — sync, export and status were all silent no-ops.
    from brightsmith.infra.governance.cli import main

    main()
