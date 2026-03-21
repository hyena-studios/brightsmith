"""Tests for lineage CLI commands.

These tests verify the CLI functions handle edge cases gracefully.
Most lineage CLI commands need an Iceberg table, so we test the
no-data and error paths here.
"""

from brightsmith.infra.lineage import (
    _job_name_to_slug,
    cmd_status,
    cmd_history,
    cmd_graph,
    cmd_verify,
)


class TestJobNameSlug:
    def test_dot_to_dash(self):
        assert _job_name_to_slug("base.financial_facts") == "base-financial_facts"

    def test_colon_to_dash(self):
        assert _job_name_to_slug("ingest:my_source") == "ingest-my_source"

    def test_combined(self):
        assert _job_name_to_slug("promote:base.facts") == "promote-base-facts"

    def test_no_special_chars(self):
        assert _job_name_to_slug("simple") == "simple"


class TestCmdStatusNoData:
    def test_status_no_crash(self, capsys):
        """status command should not crash even without Iceberg table."""
        # This will fail to find the table and print a message
        try:
            cmd_status()
        except SystemExit:
            pass
        captured = capsys.readouterr()
        # Either "No lineage events" or an error about missing table — both are fine
        assert captured.out != ""


class TestCmdHistoryNoData:
    def test_history_no_crash(self, capsys):
        try:
            cmd_history("nonexistent-job")
        except SystemExit:
            pass
        captured = capsys.readouterr()
        assert captured.out != ""


class TestCmdGraphNoData:
    def test_graph_no_crash(self, capsys):
        try:
            cmd_graph()
        except SystemExit:
            pass
        captured = capsys.readouterr()
        assert captured.out != ""


class TestCmdVerify:
    def test_verify_no_events_fails(self, capsys):
        """Verify should fail when no events exist."""
        try:
            result = cmd_verify("nonexistent-spec")
        except SystemExit:
            result = 1
        # Should return failure (1) or print failure
        captured = capsys.readouterr()
        assert "FAIL" in captured.out or result == 1
