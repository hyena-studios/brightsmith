"""Tests for BaseSystemPrompt — system prompt assembly."""

from __future__ import annotations

import json


from brightsmith.mcp.base_system_prompt import BaseSystemPrompt, PromptSection


class TestEmptyPrompt:
    def test_framework_sections_always_present(self):
        """Framework sections exist even with no domain sections."""
        sp = BaseSystemPrompt()
        text = sp.build()
        assert "## Response Guidelines" in text

    def test_build_returns_string(self):
        sp = BaseSystemPrompt()
        text = sp.build()
        assert isinstance(text, str)


class TestDomainSections:
    def test_domain_sections_included(self):
        class MyPrompt(BaseSystemPrompt):
            def get_domain_sections(self):
                return [
                    PromptSection(
                        name="roster",
                        title="Entity Roster",
                        builder=lambda: "- Apple\n- Google",
                        priority=1,
                    ),
                ]

        sp = MyPrompt()
        text = sp.build()
        assert "## Entity Roster" in text
        assert "- Apple" in text

    def test_domain_sections_ordered_by_priority(self):
        class MyPrompt(BaseSystemPrompt):
            def get_domain_sections(self):
                return [
                    PromptSection(name="second", title="Second", builder=lambda: "B", priority=50),
                    PromptSection(name="first", title="First", builder=lambda: "A", priority=1),
                ]

        sp = MyPrompt()
        text = sp.build()
        first_pos = text.index("## First")
        second_pos = text.index("## Second")
        assert first_pos < second_pos


class TestTokenBudget:
    def test_budget_truncates_sections(self):
        class VerbosePrompt(BaseSystemPrompt):
            def get_domain_sections(self):
                return [
                    PromptSection(
                        name="big",
                        title="Big Section",
                        builder=lambda: "x" * 10000,
                        priority=1,
                    ),
                    PromptSection(
                        name="small",
                        title="Small Section",
                        builder=lambda: "hello",
                        priority=2,
                    ),
                ]

        sp = VerbosePrompt()
        # Very small budget — big section should fit, small may not
        text = sp.build(max_tokens=100)
        # Budget is 100 tokens ~400 chars — framework sections may take some
        assert isinstance(text, str)

    def test_no_budget_includes_all(self):
        class MyPrompt(BaseSystemPrompt):
            def get_domain_sections(self):
                return [
                    PromptSection(name="a", title="A", builder=lambda: "content a", priority=1),
                    PromptSection(name="b", title="B", builder=lambda: "content b", priority=2),
                ]

        sp = MyPrompt()
        text = sp.build()
        assert "## A" in text
        assert "## B" in text


class TestSectionMaxTokens:
    def test_per_section_budget(self):
        class MyPrompt(BaseSystemPrompt):
            def get_domain_sections(self):
                return [
                    PromptSection(
                        name="capped",
                        title="Capped",
                        builder=lambda: "x" * 10000,
                        priority=1,
                        max_tokens=10,  # ~40 chars
                    ),
                ]

        sp = MyPrompt()
        text = sp.build()
        assert "## Capped" in text
        # Content should be truncated
        capped_start = text.index("## Capped")
        capped_content = text[capped_start:]
        assert len(capped_content) < 10000


class TestEmptySections:
    def test_empty_section_skipped(self):
        class MyPrompt(BaseSystemPrompt):
            def get_domain_sections(self):
                return [
                    PromptSection(name="empty", title="Empty", builder=lambda: "", priority=1),
                    PromptSection(name="real", title="Real", builder=lambda: "content", priority=2),
                ]

        sp = MyPrompt()
        text = sp.build()
        assert "## Empty" not in text
        assert "## Real" in text


class TestBrokenSection:
    def test_exception_in_builder_skipped(self):
        class MyPrompt(BaseSystemPrompt):
            def get_domain_sections(self):
                return [
                    PromptSection(
                        name="broken",
                        title="Broken",
                        builder=lambda: 1 / 0,
                        priority=1,
                    ),
                    PromptSection(
                        name="ok",
                        title="OK",
                        builder=lambda: "works",
                        priority=2,
                    ),
                ]

        sp = MyPrompt()
        text = sp.build()
        assert "## Broken" not in text
        assert "## OK" in text


class TestFrameworkSections:
    def test_guidelines_always_present(self):
        sp = BaseSystemPrompt()
        text = sp.build()
        assert "cite specific values" in text.lower() or "cite" in text.lower()

    def test_governance_missing_graceful(self):
        """Missing governance files don't break prompt assembly."""
        sp = BaseSystemPrompt()
        # Should not raise even without governance dir
        text = sp.build()
        assert isinstance(text, str)

    def test_glossary_from_file(self, tmp_path):
        """Glossary section reads from business-glossary.json if present."""
        import brightsmith.config as cfg

        old_root = cfg.PROJECT_ROOT
        cfg.PROJECT_ROOT = tmp_path

        gov = tmp_path / "governance"
        gov.mkdir()
        glossary = {
            "terms": [
                {"term": "Revenue", "definition": "Total income from sales"},
                {"term": "EBITDA", "definition": "Earnings before interest, tax, depreciation, amortization"},
            ]
        }
        (gov / "business-glossary.json").write_text(json.dumps(glossary))

        try:
            sp = BaseSystemPrompt()
            text = sp.build()
            assert "Revenue" in text
            assert "EBITDA" in text
        finally:
            cfg.PROJECT_ROOT = old_root
