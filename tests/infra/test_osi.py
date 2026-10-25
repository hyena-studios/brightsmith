"""Tests for the OSI semantic-model exporter (docs/specs/osi-semantic-model-export.md).

Covers: contract→dataset mapping, glossary enrichment (descriptions, synonyms,
CDE/PII custom extensions), Mermaid-ER→relationship resolution (D5), metric
hints, determinism (D4), drift detection, and the loud-failure paths (D7).
"""

from __future__ import annotations

import json

import pytest
import yaml

from brightsmith import config
from brightsmith.infra import osi
from brightsmith.infra.osi import (
    OSIExportError,
    build_semantic_model,
    check_drift,
    export_semantic_model,
    render_semantic_model,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _contract(table: str, columns: list[dict], grain: list[str], version: str = "1.0.0") -> dict:
    namespace, tbl = table.split(".", 1)
    return {
        "apiVersion": "brightsmith/v1",
        "kind": "DataContract",
        "metadata": {
            "name": tbl.replace("_", "-"),
            "version": version,
            "status": "active",
            "owner": "@data-steward",
        },
        "schema": {
            "table": table,
            "namespace": namespace,
            "grain": {"columns": grain, "description": f"one row per {', '.join(grain)}"},
            "columns": columns,
        },
        "quality": {},
        "lineage": {"inputs": ["silver.base_things"]},
    }


def _write_contract(contracts_dir, contract: dict) -> None:
    """Write a contract YAML the way load_contract/list_contracts read it."""
    name = contract["metadata"]["name"]
    (contracts_dir / f"{name}.yaml").write_text(
        yaml.dump(contract, default_flow_style=False, sort_keys=False)
    )


def _col(name: str, **overrides) -> dict:
    col = {
        "name": name,
        "type": "string",
        "required": False,
        "business_term_id": None,
        "is_cde": False,
        "cde_rationale": "",
        "is_pii": False,
        "pii_rationale": "",
        "description": "",
    }
    col.update(overrides)
    return col


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A tmp project root with a gold contract, glossary, ER model, metrics
    hint (via manifest), and domain context — the full artifact set."""
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    gov = tmp_path / "governance"
    contracts_dir = gov / "data-contracts"
    contracts_dir.mkdir(parents=True)

    # Two gold contracts + one silver (must be excluded by default zones).
    # Written as YAML directly — the exporter reads the YAML files; going
    # through save_contract() would also exercise its Iceberg dual-write,
    # which validates business_term_id against the enterprise governance DB
    # (out of scope for exporter tests).
    _write_contract(
        contracts_dir,
        _contract(
            "gold.company_metrics",
            [
                _col("company_id", required=True, business_term_id="BT-001"),
                _col("fiscal_year", type="int", required=True),
                _col("revenue", type="double", description="Total revenue in USD"),
                _col("ssn", is_pii=True, pii_rationale="national identifier"),
            ],
            grain=["company_id", "fiscal_year"],
        ),
    )
    _write_contract(
        contracts_dir,
        _contract(
            "gold.companies",
            [
                _col("company_id", required=True, is_cde=True, cde_rationale="join key"),
                _col("company_name"),
            ],
            grain=["company_id"],
        ),
    )
    _write_contract(
        contracts_dir,
        _contract("silver.base_things", [_col("thing_id")], grain=["thing_id"]),
    )

    # Business glossary: BT-001 matched by id; company_name matched by name
    (gov / "business-glossary.json").write_text(json.dumps({
        "terms": [
            {"term_id": "BT-001", "name": "Company ID", "definition": "Canonical company identifier",
             "synonyms": ["CIK", "entity id"]},
            {"term_id": "BT-002", "name": "company_name", "definition": "Registered legal name",
             "synonyms": []},
        ]
    }))

    # ER model: companies ||--o{ company_metrics, PK company_id
    models = gov / "models"
    models.mkdir()
    (models / "physical-model.md").write_text(
        "# Physical model\n\n```mermaid\nerDiagram\n"
        "    companies {\n        string company_id PK\n        string company_name\n    }\n"
        "    company_metrics {\n        string company_id FK\n        int fiscal_year\n    }\n"
        '    companies ||--o{ company_metrics : "has metrics"\n'
        "```\n"
    )

    # Domain context
    (gov / "domain-context.md").write_text("# Domain\nCompanies file annual metrics.\n")

    # Metrics hint wired through the domain manifest
    metrics_file = tmp_path / "domain" / "metrics.yaml"
    metrics_file.parent.mkdir(parents=True)
    metrics_file.write_text(yaml.safe_dump({
        "metrics": [
            {"name": "total_revenue", "expression": "SUM(revenue)", "description": "Sum of revenue"},
        ]
    }))
    (tmp_path / "domain" / "manifest.yaml").write_text(yaml.safe_dump({
        "name": "testproj",
        "version": "1.0",
        "description": "test",
        "sources": [],
        "hints": {"metrics": "domain/metrics.yaml"},
    }))

    return {"root": tmp_path, "contracts_dir": contracts_dir}


# ---------------------------------------------------------------------------
# Composer
# ---------------------------------------------------------------------------


class TestBuildSemanticModel:
    def test_document_shape_and_zone_filter(self, project):
        doc = build_semantic_model(contracts_dir=project["contracts_dir"])
        assert doc["version"] == osi.OSI_SPEC_VERSION
        model = doc["semantic_model"]
        names = [d["name"] for d in model["datasets"]]
        assert names == ["companies", "company_metrics"]  # sorted; silver excluded
        assert "base_things" not in names

    def test_dataset_source_and_primary_key(self, project):
        doc = build_semantic_model(contracts_dir=project["contracts_dir"])
        metrics_ds = next(d for d in doc["semantic_model"]["datasets"] if d["name"] == "company_metrics")
        assert metrics_ds["source"] == f"{config.PROJECT_NAME}.gold.company_metrics"
        assert metrics_ds["primary_key"] == ["company_id", "fiscal_year"]

    def test_fields_carry_ansi_sql_expressions(self, project):
        doc = build_semantic_model(contracts_dir=project["contracts_dir"])
        ds = next(d for d in doc["semantic_model"]["datasets"] if d["name"] == "company_metrics")
        field = next(f for f in ds["fields"] if f["name"] == "revenue")
        assert field["expression"]["dialects"] == [{"dialect": "ANSI_SQL", "expression": "revenue"}]
        assert field["description"] == "Total revenue in USD"

    def test_glossary_enrichment_by_term_id_and_name(self, project):
        doc = build_semantic_model(contracts_dir=project["contracts_dir"])
        ds = next(d for d in doc["semantic_model"]["datasets"] if d["name"] == "company_metrics")
        company_id = next(f for f in ds["fields"] if f["name"] == "company_id")
        assert company_id["description"] == "Canonical company identifier"
        assert company_id["ai_context"]["synonyms"] == ["CIK", "entity id"]

        companies = next(d for d in doc["semantic_model"]["datasets"] if d["name"] == "companies")
        company_name = next(f for f in companies["fields"] if f["name"] == "company_name")
        assert company_name["description"] == "Registered legal name"

    def test_cde_pii_flags_in_custom_extensions(self, project):
        doc = build_semantic_model(contracts_dir=project["contracts_dir"])
        ds = next(d for d in doc["semantic_model"]["datasets"] if d["name"] == "company_metrics")
        ssn = next(f for f in ds["fields"] if f["name"] == "ssn")
        ext = json.loads(ssn["custom_extensions"][0]["data"])
        assert ssn["custom_extensions"][0]["vendor_name"] == "brightsmith"
        assert ext["is_pii"] is True
        assert ext["pii_rationale"] == "national identifier"

        companies = next(d for d in doc["semantic_model"]["datasets"] if d["name"] == "companies")
        cde = next(f for f in companies["fields"] if f["name"] == "company_id")
        assert json.loads(cde["custom_extensions"][0]["data"])["is_cde"] is True

    def test_dataset_extension_carries_contract_identity_and_lineage(self, project):
        doc = build_semantic_model(contracts_dir=project["contracts_dir"])
        ds = next(d for d in doc["semantic_model"]["datasets"] if d["name"] == "company_metrics")
        ext = json.loads(ds["custom_extensions"][0]["data"])
        assert ext["contract_name"] == "company-metrics"
        assert ext["contract_version"] == "1.0.0"
        assert ext["lineage_inputs"] == ["silver.base_things"]

    def test_relationship_resolved_from_mermaid(self, project):
        doc = build_semantic_model(contracts_dir=project["contracts_dir"])
        rels = doc["semantic_model"]["relationships"]
        assert len(rels) == 1
        rel = rels[0]
        assert rel["from"] == "company_metrics" and rel["to"] == "companies"
        assert rel["from_columns"] == ["company_id"] and rel["to_columns"] == ["company_id"]

    def test_relationship_with_unresolvable_columns_is_skipped(self, project):
        # An entity pair with no resolvable key columns must be skipped (D5)
        models = project["root"] / "governance" / "models"
        (models / "conceptual.md").write_text(
            "```mermaid\nerDiagram\n"
            '    companies ||--o{ mystery_table : "unknown"\n'
            "```\n"
        )
        doc = build_semantic_model(contracts_dir=project["contracts_dir"])
        names = {r["name"] for r in doc["semantic_model"].get("relationships", [])}
        assert names == {"company_metrics__companies"}

    def test_metrics_from_hints(self, project):
        doc = build_semantic_model(contracts_dir=project["contracts_dir"])
        metrics = doc["semantic_model"]["metrics"]
        assert metrics == [{
            "name": "total_revenue",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(revenue)"}]},
            "description": "Sum of revenue",
        }]

    def test_model_ai_context_from_domain_context(self, project):
        doc = build_semantic_model(contracts_dir=project["contracts_dir"])
        instructions = doc["semantic_model"]["ai_context"]["instructions"]
        assert "Companies file annual metrics." in instructions

    def test_optional_artifacts_absent_yields_lean_model(self, project):
        gov = project["root"] / "governance"
        (gov / "business-glossary.json").unlink()
        (gov / "domain-context.md").unlink()
        (gov / "models" / "physical-model.md").unlink()
        (project["root"] / "domain" / "manifest.yaml").unlink()

        doc = build_semantic_model(contracts_dir=project["contracts_dir"])
        model = doc["semantic_model"]
        assert len(model["datasets"]) == 2
        assert "ai_context" not in model
        assert "relationships" not in model
        assert "metrics" not in model


class TestLoudFailures:
    def test_zero_contracts_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
        empty = tmp_path / "governance" / "data-contracts"
        empty.mkdir(parents=True)
        with pytest.raises(OSIExportError, match="No data contracts found"):
            build_semantic_model(contracts_dir=empty)

    def test_unreadable_contract_raises(self, project):
        (project["contracts_dir"] / "broken.yaml").write_text("{not: valid: yaml: [")
        with pytest.raises(OSIExportError, match="unreadable"):
            build_semantic_model(contracts_dir=project["contracts_dir"])

    def test_malformed_glossary_raises(self, project):
        (project["root"] / "governance" / "business-glossary.json").write_text("{broken json")
        with pytest.raises(OSIExportError, match="glossary"):
            build_semantic_model(contracts_dir=project["contracts_dir"])

    def test_glossary_non_dict_top_level_raises(self, project):
        # Valid JSON but wrong shape must take the loud OSIExportError path
        # (staff review finding 1), not leak a raw AttributeError.
        (project["root"] / "governance" / "business-glossary.json").write_text('["not", "an", "object"]')
        with pytest.raises(OSIExportError, match="top level must be an object"):
            build_semantic_model(contracts_dir=project["contracts_dir"])

    def test_duplicate_metric_names_raise(self, project):
        (project["root"] / "domain" / "metrics.yaml").write_text(
            yaml.safe_dump({"metrics": [
                {"name": "total_revenue", "expression": "SUM(revenue)"},
                {"name": "total_revenue", "expression": "SUM(rev)"},
            ]})
        )
        with pytest.raises(OSIExportError, match="duplicate metric name"):
            build_semantic_model(contracts_dir=project["contracts_dir"])

    def test_malformed_metrics_file_raises(self, project):
        (project["root"] / "domain" / "metrics.yaml").write_text(
            yaml.safe_dump({"metrics": [{"description": "no name or expression"}]})
        )
        with pytest.raises(OSIExportError, match="needs a 'name'"):
            build_semantic_model(contracts_dir=project["contracts_dir"])

    def test_metric_without_expression_raises(self, project):
        (project["root"] / "domain" / "metrics.yaml").write_text(
            yaml.safe_dump({"metrics": [{"name": "orphan"}]})
        )
        with pytest.raises(OSIExportError, match="needs an 'expression'"):
            build_semantic_model(contracts_dir=project["contracts_dir"])


# ---------------------------------------------------------------------------
# Determinism, export, drift (D4)
# ---------------------------------------------------------------------------


class TestExportAndDrift:
    def test_export_is_deterministic(self, project):
        a = render_semantic_model(build_semantic_model(contracts_dir=project["contracts_dir"]))
        b = render_semantic_model(build_semantic_model(contracts_dir=project["contracts_dir"]))
        assert a == b

    def test_export_writes_valid_yaml_at_default_path(self, project):
        path = export_semantic_model(contracts_dir=project["contracts_dir"])
        assert path == project["root"] / "governance" / "semantic-model.osi.yaml"
        parsed = yaml.safe_load(path.read_text())
        assert parsed["version"] == osi.OSI_SPEC_VERSION
        assert {d["name"] for d in parsed["semantic_model"]["datasets"]} == {"companies", "company_metrics"}

    def test_check_in_sync_after_export(self, project):
        export_semantic_model(contracts_dir=project["contracts_dir"])
        assert check_drift(contracts_dir=project["contracts_dir"]) == []

    def test_check_detects_hand_edit(self, project):
        path = export_semantic_model(contracts_dir=project["contracts_dir"])
        path.write_text(path.read_text() + "# hand edit\n")
        problems = check_drift(contracts_dir=project["contracts_dir"])
        assert problems and "stale or hand-edited" in problems[0]

    def test_check_detects_contract_change(self, project):
        export_semantic_model(contracts_dir=project["contracts_dir"])
        _write_contract(
            project["contracts_dir"],
            _contract("gold.companies", [_col("company_id", required=True)], grain=["company_id"],
                      version="1.1.0"),
        )
        problems = check_drift(contracts_dir=project["contracts_dir"])
        assert problems and "stale or hand-edited" in problems[0]

    def test_check_reports_missing_file(self, project):
        problems = check_drift(contracts_dir=project["contracts_dir"])
        assert problems and "not found" in problems[0]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def test_generate_and_check_roundtrip(self, project, monkeypatch, capsys):
        out = project["root"] / "governance" / "semantic-model.osi.yaml"

        monkeypatch.setattr("sys.argv", ["osi", "generate", "--output", str(out)])
        # CLI reads contracts from the live config dir — point it at the fixture's
        monkeypatch.setattr(config, "PROJECT_ROOT", project["root"])
        osi.main()
        assert out.exists()
        assert "written" in capsys.readouterr().out

        monkeypatch.setattr("sys.argv", ["osi", "check", "--output", str(out)])
        osi.main()
        assert "in sync" in capsys.readouterr().out

    def test_check_exits_1_on_drift(self, project, monkeypatch, capsys):
        out = project["root"] / "governance" / "semantic-model.osi.yaml"
        monkeypatch.setattr(config, "PROJECT_ROOT", project["root"])
        export_semantic_model(output_path=out)
        out.write_text(out.read_text() + "# tampered\n")

        monkeypatch.setattr("sys.argv", ["osi", "check", "--output", str(out)])
        with pytest.raises(SystemExit) as exc:
            osi.main()
        assert exc.value.code == 1
        assert "stale or hand-edited" in capsys.readouterr().err

    def test_generate_exits_1_without_contracts(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
        (tmp_path / "governance").mkdir()
        monkeypatch.setattr("sys.argv", ["osi", "generate"])
        with pytest.raises(SystemExit) as exc:
            osi.main()
        assert exc.value.code == 1
        assert "No data contracts found" in capsys.readouterr().err
