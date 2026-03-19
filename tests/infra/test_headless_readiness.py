"""Tests for headless readiness check."""

from unittest.mock import patch

from grist.run import check_headless_ready


def test_no_zone_registry_blocks_readiness():
    """Missing zone registrations should block readiness."""
    with patch("grist.run._ZONE_REGISTRY", {}):
        with patch("grist.run._load_zone_registry"):
            ready, issues = check_headless_ready()
            assert ready is False
            assert any("No zone" in i for i in issues)


def test_check_returns_tuple():
    """check_headless_ready should return (bool, list)."""
    ready, issues = check_headless_ready()
    assert isinstance(ready, bool)
    assert isinstance(issues, list)
