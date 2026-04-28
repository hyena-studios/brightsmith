"""Governance Iceberg schemas and table configuration."""

from __future__ import annotations

from brightsmith.infra.governance.enterprise import ENTERPRISE_TABLE_CONFIGS
from brightsmith.infra.governance.product import _TABLE_CONFIGS as PRODUCT_TABLE_CONFIGS

__all__ = ["ENTERPRISE_TABLE_CONFIGS", "PRODUCT_TABLE_CONFIGS"]

