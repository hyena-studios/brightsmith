"""Tests for idempotent promote pattern."""

import tempfile
from pathlib import Path

from pyiceberg.schema import Schema
from pyiceberg.types import IntegerType, NestedField, StringType

from brightsmith.infra.grain import compute_grain_id
from brightsmith.infra.iceberg_setup import (
    append_data,
    filter_existing_records,
    get_catalog,
    get_or_create_table,
    read_with_duckdb,
)
from brightsmith.infra.promote import promote

SCHEMA = Schema(
    NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
    NestedField(field_id=2, name="entity", field_type=StringType(), required=True),
    NestedField(field_id=3, name="value", field_type=IntegerType(), required=False),
)

GRAIN_FIELDS = ["entity"]


def _setup_table(tmp_dir: Path):
    """Create a fresh Iceberg table for testing."""
    warehouse = tmp_dir / "warehouse"
    catalog_path = tmp_dir / "catalog.db"
    catalog = get_catalog(warehouse, catalog_path)
    table = get_or_create_table(catalog, "test", "promote_test", SCHEMA)
    return table


def _make_records(entities: list[str], values: list[int]) -> list[dict]:
    """Create records with grain-based IDs."""
    records = []
    for entity, value in zip(entities, values, strict=False):
        row = {"entity": entity, "value": value}
        row["record_id"] = compute_grain_id(row, GRAIN_FIELDS, prefix="PT")
        records.append(row)
    return records


def test_first_promote_appends_all():
    """Promoting to an empty table should append all records."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        records = _make_records(["AAPL", "MSFT", "GOOG"], [100, 200, 300])

        result = promote(table, records, id_field="record_id")
        assert result["promoted"] == 3
        assert result["skipped"] == 0
        assert result["snapshot_id"] is not None

        rows = read_with_duckdb(table)
        assert len(rows) == 3


def test_second_promote_skips_duplicates():
    """Re-promoting the same data should produce 0 new rows."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        records = _make_records(["AAPL", "MSFT"], [100, 200])

        result1 = promote(table, records, id_field="record_id")
        assert result1["promoted"] == 2

        result2 = promote(table, records, id_field="record_id")
        assert result2["promoted"] == 0
        assert result2["skipped"] == 2
        assert result2["snapshot_id"] is None

        rows = read_with_duckdb(table)
        assert len(rows) == 2


def test_promote_appends_only_new():
    """Mix of existing and new records should append only new."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        batch1 = _make_records(["AAPL", "MSFT"], [100, 200])
        promote(table, batch1, id_field="record_id")

        batch2 = _make_records(["AAPL", "MSFT", "GOOG"], [100, 200, 300])
        result = promote(table, batch2, id_field="record_id")
        assert result["promoted"] == 1  # only GOOG
        assert result["skipped"] == 2   # AAPL, MSFT

        rows = read_with_duckdb(table)
        assert len(rows) == 3


def test_promote_returns_snapshot_id():
    """Promote should return the Iceberg snapshot ID when rows are added."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        records = _make_records(["AAPL"], [100])

        result = promote(table, records, id_field="record_id")
        assert isinstance(result["snapshot_id"], int)


def test_promote_returns_skip_count():
    """Skip count should be accurate."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        records = _make_records(["AAPL", "MSFT", "GOOG"], [100, 200, 300])
        promote(table, records, id_field="record_id")

        result = promote(table, records, id_field="record_id")
        assert result["skipped"] == 3
        assert result["promoted"] == 0


def test_promote_empty_records():
    """Promoting empty list should return zeros."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        result = promote(table, [], id_field="record_id")
        assert result["promoted"] == 0
        assert result["skipped"] == 0
        assert result["snapshot_id"] is None


def test_filter_existing_records_column_selective():
    """filter_existing_records should work with the ID column only."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        records = _make_records(["AAPL"], [100])
        append_data(table, records)

        new_records, skipped = filter_existing_records(table, records, "record_id")
        assert skipped == 1
        assert len(new_records) == 0


def test_grain_ids_are_deterministic():
    """Same entity should produce the same record_id across runs."""
    r1 = _make_records(["AAPL"], [100])
    r2 = _make_records(["AAPL"], [100])
    assert r1[0]["record_id"] == r2[0]["record_id"]


def test_in_batch_duplicate_promotes_once():
    """The same record_id twice in ONE promote call must append exactly once.

    (WP-2.4 / Q4: the anti-join only compares against the table, so in-batch
    duplicates were both surviving. filter_existing_records now dedups the
    incoming batch first.)
    """
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        # Two records with the same grain (entity) → same record_id.
        dup = _make_records(["AAPL", "AAPL"], [100, 100])
        assert dup[0]["record_id"] == dup[1]["record_id"]

        result = promote(table, dup, id_field="record_id")
        assert result["promoted"] == 1
        assert result["skipped"] == 1

        rows = read_with_duckdb(table)
        assert len(rows) == 1


def test_filter_existing_records_dedups_within_batch():
    """filter_existing_records collapses in-batch duplicate ids before append."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        records = _make_records(["AAPL", "AAPL", "MSFT"], [100, 100, 200])
        new_records, skipped = filter_existing_records(table, records, "record_id")
        ids = {r["record_id"] for r in new_records}
        assert len(new_records) == 2  # one AAPL collapsed, plus MSFT
        assert len(ids) == 2
        assert skipped == 1


def test_append_data_strict_raises_on_unknown_column():
    """strict append_data rejects a record key that matches no schema field.

    (WP-2.4 / Q5: a misspelled column would otherwise be silently dropped and
    the intended schema field silently filled with None.)
    """
    import pytest

    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        bad = [{"record_id": "X", "entity": "AAPL", "valeu": 100}]  # typo: valeu
        with pytest.raises(ValueError, match="valeu"):
            append_data(table, bad)

        # strict=False keeps the legacy lax behaviour (unknown key dropped).
        snap = append_data(table, bad, strict=False)
        assert isinstance(snap, int)
        rows = read_with_duckdb(table)
        assert len(rows) == 1
        assert rows[0]["value"] is None  # the real column was never populated


# --- 2026-09-08 audit Phase 0: corrections are never a silent skip ---

import pytest  # noqa: E402

from brightsmith.infra.promote import PromoteConflictError  # noqa: E402


def _values(table) -> dict[str, int]:
    return {r["entity"]: r["value"] for r in read_with_duckdb(table)}


def test_correction_raises_by_default_and_writes_nothing():
    """The audit's P0: a corrected value for an existing grain used to return
    {'promoted': 0, 'skipped': N} — identical to a healthy no-op."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        promote(table, _make_records(["AAPL", "MSFT"], [100, 200]))
        snapshots_before = len(list(table.snapshots()))

        corrected = _make_records(["AAPL", "MSFT", "GOOG"], [100, 250, 300])
        with pytest.raises(PromoteConflictError) as exc:
            promote(table, corrected)

        assert exc.value.conflicts == [corrected[1]["record_id"]]
        table.refresh()
        assert len(list(table.snapshots())) == snapshots_before  # GOOG not written either
        assert _values(table) == {"AAPL": 100, "MSFT": 200}


def test_correction_skip_counts_conflicts_separately():
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        promote(table, _make_records(["AAPL", "MSFT"], [100, 200]))

        result = promote(table, _make_records(["AAPL", "MSFT", "GOOG"], [100, 250, 300]), on_conflict="skip")

        assert (result["promoted"], result["skipped"], result["conflicts"]) == (1, 1, 1)
        assert _values(table) == {"AAPL": 100, "MSFT": 200, "GOOG": 300}


def test_correction_replace_overwrites_in_one_snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        promote(table, _make_records(["AAPL", "MSFT"], [100, 200]))
        table.refresh()
        commits_before = len(table.metadata.metadata_log)

        result = promote(table, _make_records(["AAPL", "MSFT", "GOOG"], [100, 250, 300]), on_conflict="replace")

        assert (result["promoted"], result["replaced"], result["skipped"], result["conflicts"]) == (1, 1, 1, 1)
        assert _values(table) == {"AAPL": 100, "MSFT": 250, "GOOG": 300}
        assert len(read_with_duckdb(table)) == 3
        # Delete and append land in ONE catalog commit, so the table's current
        # snapshot never points at the state where MSFT is deleted but not re-added.
        table.refresh()
        assert len(table.metadata.metadata_log) == commits_before + 1
        assert result["snapshot_id"] == table.current_snapshot().snapshot_id


def test_replace_rerun_is_a_noop():
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        promote(table, _make_records(["AAPL"], [100]))
        promote(table, _make_records(["AAPL"], [150]), on_conflict="replace")

        result = promote(table, _make_records(["AAPL"], [150]), on_conflict="replace")

        assert (result["promoted"], result["replaced"], result["skipped"]) == (0, 0, 1)
        assert result["snapshot_id"] is None


def test_in_batch_conflict_is_a_conflict():
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        clash = _make_records(["AAPL", "AAPL"], [100, 999])
        with pytest.raises(PromoteConflictError):
            promote(table, clash)
        result = promote(table, clash, on_conflict="replace")
        assert _values(table) == {"AAPL": 999}  # last one wins
        assert result["promoted"] == 1


def test_framework_timestamp_columns_do_not_count_as_changes():
    import datetime as dt

    from pyiceberg.types import TimestamptzType

    schema = Schema(
        NestedField(field_id=1, name="record_id", field_type=StringType(), required=True),
        NestedField(field_id=2, name="entity", field_type=StringType(), required=True),
        NestedField(field_id=3, name="promoted_at", field_type=TimestamptzType(), required=False),
    )
    with tempfile.TemporaryDirectory() as tmp:
        catalog = get_catalog(Path(tmp) / "warehouse", Path(tmp) / "catalog.db")
        table = get_or_create_table(catalog, "test", "stamped", schema)
        row = {"record_id": "R1", "entity": "AAPL"}
        promote(table, [{**row, "promoted_at": dt.datetime(2026, 1, 1, tzinfo=dt.UTC)}])

        result = promote(table, [{**row, "promoted_at": dt.datetime(2026, 9, 1, tzinfo=dt.UTC)}])

        assert (result["promoted"], result["skipped"]) == (0, 1)


def test_type_mismatch_is_not_a_false_conflict():
    """A value read back from Iceberg compares equal to the record that wrote it
    (the record is coerced to the table's types before hashing)."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        promote(table, _make_records(["AAPL"], [100]))
        result = promote(table, _make_records(["AAPL"], [100]))
        assert result["skipped"] == 1 and result["conflicts"] == 0


def test_null_record_id_is_rejected():
    """NULL = NULL never matches in the anti-join, so a null id was appended
    again on every run (audit §2 P0)."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        rows = [{"entity": "AAPL", "value": 1, "record_id": None}]
        with pytest.raises(ValueError, match="record_id"):
            promote(table, rows)
        with pytest.raises(ValueError, match="record_id"):
            filter_existing_records(table, [{"entity": "AAPL", "value": 1}], "record_id")
        assert read_with_duckdb(table) == []


def test_invalid_on_conflict_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        with pytest.raises(ValueError, match="on_conflict"):
            promote(table, _make_records(["AAPL"], [1]), on_conflict="overwrite")  # type: ignore[arg-type]


# --- promote lineage: actually written, into the table's own catalog ---


def _lineage_rows(catalog) -> list[dict]:
    from brightsmith.infra.lineage import _get_lineage_table

    return read_with_duckdb(_get_lineage_table(catalog))


def test_promote_lineage_round_trips_into_table_catalog():
    """promote() read `table.identifier`, which pyiceberg does not have, and
    swallowed the AttributeError — no promote event was ever written."""
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        result = promote(table, _make_records(["AAPL", "MSFT"], [1, 2]), spec_name="gold-test", agent_name="@t")

        events = _lineage_rows(table.catalog)
        assert sorted(e["event_type"] for e in events) == ["COMPLETE", "START"]
        complete = next(e for e in events if e["event_type"] == "COMPLETE")
        assert complete["output_table"] == "test.promote_test"
        assert complete["job_name"] == "promote:gold-test"
        assert complete["output_snapshot_id"] == result["snapshot_id"]
        assert complete["row_count"] == 2


def test_promote_lineage_never_touches_project_catalog(monkeypatch, tmp_path):
    import brightsmith.config as cfg

    project_catalog = tmp_path / "project" / "catalog.db"
    monkeypatch.setattr(cfg, "CATALOG_PATH", project_catalog)
    table = _setup_table(tmp_path / "scratch")
    promote(table, _make_records(["AAPL"], [1]), spec_name="s")
    assert not project_catalog.exists()


def test_promote_lineage_failure_raises_when_spec_named(monkeypatch):
    from brightsmith.infra import lineage

    def boom(*_a, **_k):
        raise RuntimeError("lineage store down")

    monkeypatch.setattr(lineage, "_write_event", boom)
    with tempfile.TemporaryDirectory() as tmp:
        table = _setup_table(Path(tmp))
        with pytest.raises(RuntimeError, match="lineage store down"):
            promote(table, _make_records(["AAPL"], [1]), spec_name="s")
        # Without a spec name no lineage is requested, so nothing to fail.
        assert promote(table, _make_records(["MSFT"], [2]))["promoted"] == 1
