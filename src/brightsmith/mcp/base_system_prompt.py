"""Domain-agnostic system prompt assembly from real data.

Assembles a system prompt from governance artifacts and domain-specific
data sections. The prompt gives LLM clients the context they need to
interpret tool results correctly.

Usage:
    from brightsmith.mcp.base_system_prompt import BaseSystemPrompt, PromptSection

    class MySystemPrompt(BaseSystemPrompt):
        def get_domain_sections(self) -> list[PromptSection]:
            return [
                PromptSection(
                    name="entity_roster",
                    title="Entities in This Dataset",
                    builder=self._build_roster,
                    priority=1,
                ),
            ]
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from brightsmith.mcp.base_anomaly_checker import BaseAnomalyChecker
    from brightsmith.mcp.base_mcp_server import BaseMCPServer

logger = logging.getLogger(__name__)


@dataclass
class PromptSection:
    """A section of the system prompt."""

    name: str
    """Identifier for ordering and caching."""

    title: str
    """Rendered as a markdown heading."""

    builder: Callable[[], str]
    """Returns markdown content for this section."""

    priority: int = 10
    """Lower number = earlier in prompt."""

    max_tokens: int | None = None
    """Optional budget cap for this section (chars / 4 approximation)."""


class BaseSystemPrompt:
    """Domain-agnostic system prompt assembly from real data.

    Framework auto-includes governance sections (scope, DQ summary,
    glossary terms, response guidelines). Domain projects add their own
    sections via ``get_domain_sections()``.
    """

    def __init__(
        self,
        server: BaseMCPServer | None = None,
        anomaly_checker: BaseAnomalyChecker | None = None,
    ):
        self.server = server
        self.anomaly_checker = anomaly_checker

    def get_domain_sections(self) -> list[PromptSection]:
        """Override in domain project. Return domain-specific prompt sections."""
        return []

    def get_framework_sections(self) -> list[PromptSection]:
        """Framework-provided sections built from governance artifacts.

        Domain projects generally don't override this.
        """
        sections: list[PromptSection] = []

        sections.append(PromptSection(
            name="scope",
            title="Data Scope",
            builder=self._build_scope_section,
            priority=0,
        ))

        sections.append(PromptSection(
            name="dq_summary",
            title="Data Quality Summary",
            builder=self._build_dq_section,
            priority=5,
        ))

        sections.append(PromptSection(
            name="glossary",
            title="Key Business Terms",
            builder=self._build_glossary_section,
            priority=6,
        ))

        sections.append(PromptSection(
            name="response_guidelines",
            title="Response Guidelines",
            builder=self._build_guidelines_section,
            priority=100,
        ))

        return sections

    def build(self, max_tokens: int | None = None) -> str:
        """Assemble the full system prompt.

        Sections are ordered by priority (lower = earlier). If max_tokens
        is set, sections are included in priority order until the budget
        is exhausted.
        """
        all_sections = self.get_framework_sections() + self.get_domain_sections()
        all_sections.sort(key=lambda s: s.priority)

        parts: list[str] = []
        budget = (max_tokens * 4) if max_tokens else None  # chars approximation

        for section in all_sections:
            try:
                content = section.builder()
            except Exception:
                logger.warning("Failed to build prompt section '%s'", section.name, exc_info=True)
                continue

            if not content or not content.strip():
                continue

            # Apply per-section budget
            if section.max_tokens is not None:
                char_limit = section.max_tokens * 4
                content = content[:char_limit]

            block = f"## {section.title}\n\n{content.strip()}"

            # Apply total budget
            if budget is not None:
                if len(block) > budget:
                    break
                budget -= len(block)

            parts.append(block)

        return "\n\n".join(parts)

    # --- Framework section builders ---

    def _build_scope_section(self) -> str:
        """Build scope declaration from data contracts and table metadata."""
        try:
            from brightsmith.config import PROJECT_ROOT
            contracts_dir = PROJECT_ROOT / "governance" / "data-contracts"
            if not contracts_dir.exists():
                return ""
            contracts = list(contracts_dir.glob("*.yaml")) + list(contracts_dir.glob("*.yml"))
            if not contracts:
                return ""
            return f"This dataset is served by {len(contracts)} governed table(s) with active data contracts."
        except Exception:
            return ""

    def _build_dq_section(self) -> str:
        """Build DQ summary from scorecards."""
        try:
            from brightsmith.config import DQ_SCORECARDS_DIR
            if not DQ_SCORECARDS_DIR.exists():
                return ""
            scorecards = list(DQ_SCORECARDS_DIR.glob("*.md"))
            if not scorecards:
                return ""
            return f"{len(scorecards)} data quality scorecard(s) available. Use the `get_data_quality` tool for detailed results per table."
        except Exception:
            return ""

    def _build_glossary_section(self) -> str:
        """Build abbreviated glossary from business-glossary.json."""
        try:
            from brightsmith.config import PROJECT_ROOT
            glossary_path = PROJECT_ROOT / "governance" / "business-glossary.json"
            if not glossary_path.exists():
                return ""
            glossary = json.loads(glossary_path.read_text())
            terms = glossary.get("terms", [])
            if not terms:
                return ""

            lines = [f"| Term | Definition |", f"|------|------------|"]
            for term in terms[:25]:  # Cap at 25 terms to keep prompt manageable
                name = term.get("term", term.get("name", ""))
                defn = term.get("definition", term.get("description", ""))
                if name and defn:
                    # Truncate long definitions
                    short_def = defn[:120] + "..." if len(defn) > 120 else defn
                    lines.append(f"| {name} | {short_def} |")

            if len(lines) <= 2:
                return ""
            return "\n".join(lines)
        except Exception:
            return ""

    def _build_guidelines_section(self) -> str:
        """Static response guidelines."""
        return (
            "- Always cite specific values from tool results.\n"
            "- Flag any anomalies noted in `_anomaly_flags` on data points.\n"
            "- Note data freshness and quality status when available.\n"
            "- If a value seems unexpected, check for anomaly flags before interpreting."
        )
