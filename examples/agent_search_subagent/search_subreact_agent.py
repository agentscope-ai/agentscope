# -*- coding: utf-8 -*-
"""SearchSubReactAgent — a SubAgent with full ReAct capability for search.

Design:
- Input contract: zero-deviation (`query: str`).
- Tools: hard-import all stable search tools (bing/sogou/wiki/github).
- Execution: compose an inner ReActAgent that uses the SubAgent's own Toolkit;
  `delegate()` (from SubAgentBase) folds the Msg into a ToolResponse for Host.
"""

from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, Field

from src.agentscope.agent._subagent_base import SubAgentBase
from src.agentscope.agent import ReActAgent
from src.agentscope.message import Msg
from src.agentscope.tool import Toolkit


class SearchInput(BaseModel):
    """Zero-deviation input for the search subagent."""

    query: str = Field(..., min_length=1, description="Search query string")


# Hard-import stable search tools (no defensive programming by design)
from src.agentscope.tool._search.bing import search_bing  # noqa: E402
from src.agentscope.tool._search.sogou import search_sogou  # noqa: E402
from src.agentscope.tool._search.wiki import search_wiki  # noqa: E402
from src.agentscope.tool._search.github import search_github  # noqa: E402


def get_all_search_tools() -> list[Callable[..., Any]]:
    """Return all built-in stable search tools.

    Toolkit.register_tool_function will auto-use each function's __doc__ as the
    tool description and auto-generate the JSON Schema from its signature.
    """

    return [search_bing, search_sogou, search_wiki, search_github]


class SearchSubReactAgent(SubAgentBase):
    """ReAct-capable search SubAgent (agent-as-tool)."""

    InputModel = SearchInput

    def __init__(
        self,
        *,
        permissions,
        spec_name: str,
        toolkit: Toolkit | None = None,
        memory=None,
        tools: list[Callable[..., Any]] | None = None,
        model_override=None,
        ephemeral_memory: bool = True,
    ) -> None:
        super().__init__(
            permissions=permissions,
            spec_name=spec_name,
            toolkit=toolkit,
            memory=memory,
            tools=tools,  # host registers via SubAgentSpec(tools=get_all_search_tools())
            model_override=model_override,
            ephemeral_memory=ephemeral_memory,
        )
        self._inner: ReActAgent | None = None

    def _ensure_inner(self) -> ReActAgent:
        if self._inner is not None:
            return self._inner

        # Model must be inherited from Host via export_agent(model_override)
        # No local defaults, no fallback.
        from src.agentscope.formatter import OpenAIChatFormatter

        model = self.model_override
        if model is None:
            raise RuntimeError(
                "SearchSubReactAgent requires host.model via model_override (see SOP: 模型继承).",
            )
        formatter = OpenAIChatFormatter()

        self._inner = ReActAgent(
            name=f"{self.spec_name}-react",
            sys_prompt=(
                "You are a search assistant. Use search_* tools to gather information. "
                "Prefer Wiki for factual answers, GitHub for code, Bing/Sogou for general queries. "
                "Return concise, source-backed findings."
            ),
            model=model,
            formatter=formatter,
            toolkit=self.toolkit,
            parallel_tool_calls=False,
        )
        return self._inner

    async def reply(self, input_obj: SearchInput, **_: Any) -> Msg:
        # Forward the user query to the inner ReAct agent for autonomous tool use
        msg = Msg(name="user", content=input_obj.query, role="user")
        agent = self._ensure_inner()
        return await agent(msg)


__all__ = [
    "SearchInput",
    "SearchSubReactAgent"
]
