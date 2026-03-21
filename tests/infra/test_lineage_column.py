"""Tests for column-level lineage: ColumnMapping and build_column_lineage."""

from brightsmith.infra.lineage import ColumnMapping, build_column_lineage


class TestColumnMapping:
    def test_direct_mapping(self):
        m = ColumnMapping(
            target_field="revenue",
            input_fields=[{"namespace": "proj", "name": "raw.facts", "field": "val"}],
            transformation_type="DIRECT",
        )
        assert m.target_field == "revenue"
        assert m.transformation_type == "DIRECT"
        assert len(m.input_fields) == 1

    def test_derived_mapping(self):
        m = ColumnMapping(
            target_field="record_id",
            input_fields=[
                {"namespace": "proj", "name": "raw.facts", "field": "entity_id"},
                {"namespace": "proj", "name": "raw.facts", "field": "metric"},
            ],
            transformation_type="DERIVED",
            transformation_description="SHA-256 hash of (entity_id, metric)",
        )
        assert m.transformation_type == "DERIVED"
        assert m.transformation_description is not None
        assert len(m.input_fields) == 2

    def test_defaults(self):
        m = ColumnMapping(target_field="name")
        assert m.input_fields == []
        assert m.transformation_type == "DIRECT"
        assert m.transformation_description is None


class TestBuildColumnLineage:
    def test_empty_mappings(self):
        result = build_column_lineage([])
        assert result == {"fields": {}}

    def test_single_mapping(self):
        mappings = [
            ColumnMapping(
                target_field="revenue",
                input_fields=[{"namespace": "p", "name": "t", "field": "val"}],
                transformation_type="DIRECT",
            ),
        ]
        result = build_column_lineage(mappings)
        assert "revenue" in result["fields"]
        assert result["fields"]["revenue"]["transformationType"] == "DIRECT"
        assert len(result["fields"]["revenue"]["inputFields"]) == 1

    def test_multiple_mappings(self):
        mappings = [
            ColumnMapping(target_field="a", transformation_type="DIRECT"),
            ColumnMapping(target_field="b", transformation_type="AGGREGATION",
                         transformation_description="SUM(x)"),
            ColumnMapping(target_field="c", transformation_type="DERIVED"),
        ]
        result = build_column_lineage(mappings)
        assert len(result["fields"]) == 3
        assert result["fields"]["b"]["transformationDescription"] == "SUM(x)"

    def test_no_description_omitted(self):
        mappings = [
            ColumnMapping(target_field="x", transformation_type="DIRECT"),
        ]
        result = build_column_lineage(mappings)
        assert "transformationDescription" not in result["fields"]["x"]

    def test_description_included(self):
        mappings = [
            ColumnMapping(
                target_field="x",
                transformation_type="DERIVED",
                transformation_description="Computed field",
            ),
        ]
        result = build_column_lineage(mappings)
        assert result["fields"]["x"]["transformationDescription"] == "Computed field"
