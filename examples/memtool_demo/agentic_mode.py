# -*- coding: utf-8 -*-
"""Autonomous MemTool mode (Full Agency): search/remove tools inside the loop.

This merges Orchestrator and Worker into a single agent. The agent is equipped
with two management tools: `search_tools` and `remove_tools`. These are routed
to an internal ToolManager service that adds/removes tool functions in the
agent's Toolkit dynamically between reasoning steps.
"""

import os
import asyncio
from typing import List
from pydantic import BaseModel, Field

from agentscope import logger
from agentscope.agent import ReActAgent
from agentscope.formatter import (
    OpenAIChatFormatter,
    DashScopeChatFormatter,
    GeminiChatFormatter,
)
from agentscope.message import Msg
from agentscope.model import (
    OpenAIChatModel,
    DashScopeChatModel,
    GeminiChatModel,
)
from agentscope.tool import Toolkit, ToolResponse
from agentscope.tracing import trace

try:
    # Reuse the default Tool KB (functions + metadata)
    from examples.memtool_demo.memtool_components import build_default_tool_kb
except Exception:  # pragma: no cover - script mode fallback
    from memtool_components import build_default_tool_kb  # type: ignore


DEFAULT_TOOL_BUDGET = 6  # Max number of dynamically equipped tools


def _pick_model_and_formatter():
    if os.getenv("OPENAI_API_KEY"):
        model = OpenAIChatModel(
            model_name=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=os.getenv("OPENAI_API_KEY"),
            stream=True,
        )
        return model, OpenAIChatFormatter()

    # Prefer Google Gemini if key provided
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if gemini_key:
        model = GeminiChatModel(
            model_name=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            api_key=gemini_key,
            stream=True,
        )
        return model, GeminiChatFormatter()

    # Default to DashScope
    model = DashScopeChatModel(
        model_name=os.getenv("DASHSCOPE_MODEL", "qwen-max"),
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        stream=True,
    )
    return model, DashScopeChatFormatter()


class ToolManagerService:
    """Backend service that maintains and mutates the agent's dynamic tools."""

    def __init__(self, toolkit: Toolkit, budget: int = DEFAULT_TOOL_BUDGET) -> None:
        self.toolkit = toolkit
        self.kb = build_default_tool_kb()
        self.budget = budget
        # Group for dynamically equipped tools
        self.toolkit.create_tool_group(
            group_name="dynamic",
            description=(
                "Dynamically equipped tools discovered by search_tools."
            ),
            active=True,
            notes=(
                "Use only the necessary tools. Keep total count under the"
                f" budget ({budget}) to preserve context. Remove stale"
                " tools when switching subtasks."
            ),
        )

    def _equipped(self) -> list[str]:
        # Current dynamic tools in the toolkit
        return [
            n
            for n, reg in self.toolkit.tools.items()
            if getattr(reg, "group", "") == "dynamic"
        ]

    def list_equipped_tools(self) -> str:
        eq = self._equipped()
        return ", ".join(eq) if eq else "<empty>"

    # ---- Tool functions exposed to the agent ----

    @trace("MemTool.Search_Tools")
    async def search_tools(self, queries: List[str], top_k: int = 5) -> "ToolResponse":
        """Search and equip tools (RRF over lexical + optional embedding).

        Args:
            queries (list[str]):
                Short phrases describing the tools you need (e.g.,
                ["math", "write file", "grep text"]).
            top_k (int, optional):
                Max number of candidate tools to consider from the KB.

        Behavior:
            - Always ranks lexically; if embeddings are available, also ranks by
              vector similarity and uses Reciprocal Rank Fusion (RRF) to merge.
            - Equips at most (budget - current_count) new tools into group
              "dynamic". If no slots available, returns a note to remove tools
              first via `remove_tools`.
        """
        from agentscope.tool import ToolResponse  # local import to avoid cycle
        from agentscope.message import TextBlock

        query = " ".join(queries)
        candidates = await self.kb.search_rrf(query, top_k=max(top_k, 1))
        equipped = set(self._equipped())
        free_slots = max(0, self.budget - len(equipped))

        if free_slots == 0:
            logger.info(
                "[MemTool] No free slots (budget=%s). Equipped: %s",
                self.budget,
                self.list_equipped_tools(),
            )
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            "No free slots available. Please call Remove_Tools"
                            f" to free capacity (budget={self.budget}).\n"
                            f"Currently equipped: {self.list_equipped_tools()}"
                        ),
                    ),
                ],
            )

        added: list[str] = []
        for name in candidates:
            if name in equipped:
                continue
            meta = self.kb.get(name)
            if not meta:
                continue
            try:
                self.toolkit.register_tool_function(
                    meta.fn,
                    group_name="dynamic",
                )
                added.append(name)
                equipped.add(name)
                if len(added) >= free_slots:
                    break
            except Exception:
                # Ignore duplicates or registration errors
                continue

        if not added:
            logger.info(
                "[MemTool] No new tools equipped. Candidates: %s; Equipped: %s",
                candidates,
                self.list_equipped_tools(),
            )
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            "No new tools equipped. Either none matched or"
                            " they are already present."
                        ),
                    ),
                ],
            )

        logger.info("[MemTool] Equipped tools: %s; Now: %s", added, self.list_equipped_tools())
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        "Equipped tools: "
                        f"{', '.join(added)}.\nCurrent: "
                        f"{self.list_equipped_tools()}\n"
                        "Remember to remove stale tools when switching tasks."
                    ),
                ),
            ],
        )

    @trace("MemTool.Remove_Tools")
    def remove_tools(self, tool_names: List[str]) -> "ToolResponse":
        """Remove equipped tools by exact names.

        Args:
            tool_names (list[str]):
                Tool function names to remove (e.g., ["write_text_file"]).
        """
        from agentscope.tool import ToolResponse
        from agentscope.message import TextBlock

        removed: list[str] = []
        for n in tool_names:
            if n in self.toolkit.tools and self.toolkit.tools[n].group == "dynamic":  # type: ignore[index]
                self.toolkit.remove_tool_function(n)
                removed.append(n)

        if not removed:
            msg = (
                "No matching dynamic tools removed. Current: "
                f"{self.list_equipped_tools()}"
            )
        else:
            msg = (
                f"Removed: {', '.join(removed)}. Current: "
                f"{self.list_equipped_tools()}"
            )
        logger.info("[MemTool] Removed tools: %s; Now: %s", removed, self.list_equipped_tools())
        return ToolResponse(content=[TextBlock(type="text", text=msg)])

    def list_equipped(self) -> "ToolResponse":
        """List currently equipped dynamic tools."""
        from agentscope.tool import ToolResponse
        from agentscope.message import TextBlock

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Equipped: {self.list_equipped_tools()}"
                    f" (budget={self.budget})",
                ),
            ],
        )

    # Aliases to match prompt naming
    async def Search_Tools(self, queries: List[str], top_k: int = 5):  # noqa: N802
        """Alias of search_tools for prompt consistency."""
        return await self.search_tools(queries, top_k)

    def Remove_Tools(self, tool_names: List[str]):  # noqa: N802
        """Alias of remove_tools for prompt consistency."""
        return self.remove_tools(tool_names)

    def List_Equipped(self):  # noqa: N802
        """Alias of list_equipped for prompt consistency."""
        return self.list_equipped()


class FinishAttributionModel(BaseModel):
    """Structured output fields for finish function."""

    used_tools: List[str] = Field(
        description="Names of tools used to produce the final answer.",
    )
    sources: str | None = Field(
        default=None,
        description="Optional citations or references used in reasoning.",
    )


def _render_protocol_prompt(
    domain_expertise: str,
    max_tool_count: int,
    tool_count: int,
) -> str:
    return f"""
# CORE IDENTITY & OBJECTIVE
You are a highly capable, autonomous agent designed to solve complex problems. Your designated area of expertise is {domain_expertise}. Your primary objective is to provide accurate, efficient, and well-sourced answers to user queries by dynamically managing a set of specialized tools.

# OPERATIONAL DIRECTIVES: Tool Memory Protocol
You are operating within a multi-turn conversation and have a limited short-term memory (context window) for tools. To manage this constraint, you must strictly adhere to the following protocol:

1. Memory Constraint: Your memory can hold a maximum of {max_tool_count} active tools.
2. Proactive Pruning (Hygiene First): At the beginning of EVERY turn, before taking any other action, you MUST evaluate the tools currently in your memory.
   - Analyze: Is each tool still relevant to the ongoing conversation and the user's latest query?
   - Act: If any tools are no longer relevant, you MUST use the `Remove_Tools` function to prune them from your memory immediately. This is your highest priority action to ensure memory availability.
3. Targeted Retrieval (Search When Necessary): Only after you have completed the pruning step, analyze the user's query.
   - Analyze: Do you have the necessary tools in your active memory to fully address the query?
   - Act: If you are missing a required capability, you MUST use the `Search_Tools` function with a clear, descriptive query to find and add the appropriate tool(s).
4. Execution: Once you have the correct set of tools in memory, use them to fulfill the user's request.

# STATE AWARENESS
To assist your decision-making, your current memory status is:
- Current Number of Tools in Memory: {tool_count}

# OUTPUT REQUIREMENTS
- Attribution: You MUST cite the tool(s) used to generate your response. Provide them in the `used_tools` field when calling `generate_response` (schema is enforced).
- Clarity: Provide a clear, concise, and helpful answer to the user.

Available management tools:
- Search_Tools(queries: list[str], top_k: int = 5)
- Remove_Tools(tool_names: list[str])
- List_Equipped()
"""


def build_autonomous_agent(
    domain_expertise: str = "General Problem Solving",
    max_tool_count: int = DEFAULT_TOOL_BUDGET,
) -> ReActAgent:
    """Create the autonomous MemTool agent with Search/Remove tools."""
    toolkit = Toolkit()

    manager = ToolManagerService(toolkit, budget=max_tool_count)
    # Register management tools with prompt-consistent names
    toolkit.register_tool_function(
        manager.Search_Tools,
        group_name="basic",
        func_description=(
            "Search and equip relevant tools without exceeding the tool budget."
            " Provide short queries like ['math', 'write file', 'grep text']."
        ),
    )
    toolkit.register_tool_function(
        manager.Remove_Tools,
        group_name="basic",
        func_description=(
            "Remove equipped tools by exact names to free memory capacity."
        ),
    )
    toolkit.register_tool_function(
        manager.List_Equipped,
        group_name="basic",
        func_description="List currently equipped dynamic tools and the budget.",
    )

    model, formatter = _pick_model_and_formatter()

    # Dynamic sys prompt based on provided template
    tool_count = len(manager._equipped())  # 0 at start
    sys_prompt = _render_protocol_prompt(
        domain_expertise=domain_expertise,
        max_tool_count=max_tool_count,
        tool_count=tool_count,
    )

    agent = ReActAgent(
        name="AutonomousAgent",
        sys_prompt=sys_prompt,
        model=model,
        formatter=formatter,
        toolkit=toolkit,
        max_iters=12,
        enable_meta_tool=False,
    )

    # Enforce attribution fields in finish function
    agent.toolkit.set_extended_model("generate_response", FinishAttributionModel)
    agent._required_structured_model = FinishAttributionModel  # type: ignore[attr-defined]

    # Keep sys_prompt's tool_count fresh before each reasoning step
    def _pre_reasoning_update(self, kwargs):  # noqa: ANN001
        current = len(manager._equipped())
        self._sys_prompt = _render_protocol_prompt(
            domain_expertise=domain_expertise,
            max_tool_count=max_tool_count,
            tool_count=current,
        )
        return None

    agent.register_instance_hook(
        "pre_reasoning",
        "memtool_update_sys_prompt",
        _pre_reasoning_update,
    )

    return agent


async def run_agentic(user_query: str):
    domain = os.getenv("AGENT_DOMAIN_EXPERTISE", "General Problem Solving")
    try:
        budget = int(os.getenv("AGENT_MAX_TOOL_COUNT", str(DEFAULT_TOOL_BUDGET)))
    except ValueError:
        budget = DEFAULT_TOOL_BUDGET

    agent = build_autonomous_agent(domain_expertise=domain, max_tool_count=budget)
    msg = Msg("user", user_query, role="user")
    return await agent(msg)


if __name__ == "__main__":
    import sys

    q = sys.argv[1] if len(sys.argv) > 1 else "请计算 2+2 并写入 notes.txt"
    asyncio.run(run_agentic(q))
