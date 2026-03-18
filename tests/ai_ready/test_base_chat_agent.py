"""Tests for BaseChatAgent — framework base class for AI-Ready zone."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from grist.ai_ready.base_chat_agent import BaseChatAgent


class ConcreteAgent(BaseChatAgent):
    """Test implementation of BaseChatAgent."""

    def get_system_prompt(self) -> str:
        return "You are a test agent."

    def register_tools(self) -> list[dict]:
        return [
            {
                "name": "test_tool",
                "description": "A test tool",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
                "handler": self._handle_test,
            },
        ]

    def _handle_test(self, input_dict: dict) -> str:
        return f"result for {input_dict.get('query', 'unknown')}"


@pytest.fixture
def agent(tmp_path):
    return ConcreteAgent(
        warehouse_path=tmp_path / "warehouse",
        catalog_path=tmp_path / "catalog.db",
    )


class TestBaseChatAgent:
    def test_get_system_prompt(self, agent):
        assert agent.get_system_prompt() == "You are a test agent."

    def test_register_tools(self, agent):
        tools = agent.register_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "test_tool"

    def test_build_tools_separates_handlers(self, agent):
        tools = agent._build_tools()
        assert len(tools) == 1
        assert "handler" not in tools[0]
        assert "test_tool" in agent._tool_handlers

    def test_handle_tool_call(self, agent):
        agent._build_tools()
        result = agent._handle_tool_call("test_tool", {"query": "revenue"})
        assert result == "result for revenue"

    def test_handle_unknown_tool(self, agent):
        agent._build_tools()
        result = agent._handle_tool_call("nonexistent", {})
        assert "error" in result
        assert "Unknown tool" in result

    def test_build_system_prompt_without_grounding(self, agent):
        prompt = agent._build_system_prompt()
        assert prompt == "You are a test agent."

    def test_build_system_prompt_with_grounding(self, tmp_path):
        docs_dir = tmp_path / "grounding"
        docs_dir.mkdir()
        (docs_dir / "context.md").write_text("Domain context here.")
        (docs_dir / "terms.md").write_text("Business terms here.")

        agent = ConcreteAgent(
            warehouse_path=tmp_path / "warehouse",
            catalog_path=tmp_path / "catalog.db",
            grounding_docs_path=docs_dir,
        )
        prompt = agent._build_system_prompt()
        assert "You are a test agent." in prompt
        assert "Domain context here." in prompt
        assert "Business terms here." in prompt

    def test_load_grounding_docs_empty(self, agent):
        assert agent.load_grounding_docs() == ""

    def test_load_grounding_docs_nonexistent(self, tmp_path):
        agent = ConcreteAgent(
            warehouse_path=tmp_path / "w",
            catalog_path=tmp_path / "c.db",
            grounding_docs_path=tmp_path / "nonexistent",
        )
        assert agent.load_grounding_docs() == ""

    def test_abstract_methods(self, tmp_path):
        agent = BaseChatAgent(
            warehouse_path=tmp_path / "w",
            catalog_path=tmp_path / "c.db",
        )
        with pytest.raises(NotImplementedError):
            agent.get_system_prompt()
        with pytest.raises(NotImplementedError):
            agent.register_tools()

    def test_chat_requires_anthropic(self, agent):
        """chat() should raise ImportError if anthropic not installed."""
        with patch.dict("sys.modules", {"anthropic": None}):
            # Since anthropic might be installed, mock the import
            import importlib
            with pytest.raises((ImportError, ModuleNotFoundError)):
                agent.chat("test")
