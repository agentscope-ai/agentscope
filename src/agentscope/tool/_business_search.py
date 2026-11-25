# -*- coding: utf-8 -*-
"""Discover scoped business tools without sending their schemas to the
model."""

import re
import unicodedata
from collections.abc import Callable, Iterator
from typing import Any, Literal

from ..permission import (
    PermissionBehavior,
    PermissionDecision,
    PermissionEngine,
)
from ..state import AgentState
from ._adapters import FunctionTool

_ACTIONS = {
    "query": (
        "查询",
        "查找",
        "列表",
        "查看",
        "list",
        "find",
        "search",
        "query",
    ),
    "save": (
        "新增",
        "新开",
        "新建",
        "创建",
        "建单",
        "开单",
        "开一张",
        "建一张",
        "开具",
        "录入",
        "保存",
        "create",
        "save",
    ),
    "readback": (
        "回查",
        "恢复",
        "操作id",
        "单据id",
        "operation id",
        "receipt id",
        "readback",
        "recover",
    ),
}
_ACTION_LABELS = {"query": "查询", "save": "新增草稿", "readback": "回查"}
_SAVE_VERBS = "|".join(re.escape(word) for word in _ACTIONS["save"])
_EXCLUDED_ACTION = re.compile(
    rf"(?:已(?:经)?|不要|不用|无需|不必|别|请勿)\s*(?:再\s*)?(?:{_SAVE_VERBS}|回查|恢复)"
    rf"|\b(?:do not|don['’]t|never|no need to)\s+(?:{_SAVE_VERBS}|recover|"
    r"readback)\b",
)
_QUERY_ONLY = re.compile(
    r"(?:只|仅)(?:需要|需|要|想)?\s*(?:查询|查看|查找|查|看|列出|检索|搜索)"
    r"|\b(?:only|just)\s+(?:list|find|search|query|show|view)\b",
)
_STOP = {
    "erp",
    "get",
    "id",
    "the",
    "for",
    "of",
    "please",
    "params",
    "query",
    "list",
    "create",
    "save",
    "read",
    "find",
    "search",
}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return (
        re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
        .casefold()
        .replace("_", " ")
    )


def _terms(text: str) -> set[str]:
    terms = set(re.findall(r"[a-z0-9]+", text)) - _STOP
    for word in re.findall(r"[\u3400-\u9fff]+", text):
        terms.update(word[i : i + 2] for i in range(len(word) - 1))
    return terms


# ID lookups also identify actual parameter names (such as operation_id).
_ACTION_TERMS = {
    term
    for words in _ACTIONS.values()
    for word in words
    if "id" not in word
    for term in _terms(word)
}


def _requested_action(query: str) -> tuple[str, str]:
    """Return the intent and the text used for matching business fields."""
    request, excluded = _EXCLUDED_ACTION.subn(" ", query)
    # A user checking a previous save can mention both actions.
    compact = re.sub(r"\s+", "", request)
    for key in _ACTIONS["readback"]:
        if key.replace(" ", "") in compact:
            return "readback", request
    subject, only = _QUERY_ONLY.subn(" ", request)
    if only:
        return "query_only", subject
    words = set(re.findall(r"[a-z]+", request))
    if any(
        key in words or (not key.isascii() and key in request)
        for key in _ACTIONS["save"]
    ):
        return "save", request
    return ("query_only" if excluded else "query"), request


def _parameter_names(schema: dict) -> Iterator[str]:
    """Index names in object, array and composed schemas, never sample
    values."""
    for name, child in schema.get("properties", {}).items():
        yield name
        if isinstance(child, dict):
            yield from _parameter_names(child)
    for child in [
        schema.get("items"),
        *schema.get("$defs", {}).values(),
        *schema.get("anyOf", []),
        *schema.get("oneOf", []),
        *schema.get("allOf", []),
    ]:
        if isinstance(child, dict):
            yield from _parameter_names(child)


async def _permitted(tool: FunctionTool, state: AgentState) -> bool:
    # These FunctionTools declare static permissions. Checking them does not
    # run the function, bind a confirmation or create a save operation.
    engine = PermissionEngine(state.permission_context)
    decision = await engine.check_permission(tool, {})
    return decision.behavior != PermissionBehavior.DENY


class PublishedBusinessTool(FunctionTool):
    """A published operation whose original callable and permissions are
    retained."""

    def __init__(
        self,
        func: Callable,
        *,
        business: str | None,
        action: Literal["query", "save", "readback"],
        **kwargs: Any,
    ) -> None:
        super().__init__(func, **kwargs)
        self.business = business
        self.action = action
        self._title = _normalize(business or "").strip()
        self._title_terms = _terms(self._title)
        self._normalized_name = _normalize(self.name)
        self._name_terms = _terms(self._normalized_name)
        self._parameters = _terms(
            _normalize(" ".join(_parameter_names(self.input_schema))),
        )
        self._description_terms = _terms(_normalize(self.description))

    async def show_schema(self, state: AgentState | None) -> bool:
        """Decide whether to expose this tool in the current session."""
        return state is None or (
            bool(self.business)
            and self.name in state.tool_context.selected_business_tools
            and await _permitted(self, state)
        )

    def score(self, query: str, terms: set[str], action: str) -> int:
        """Prefer the exact operation, then its business title and intent."""
        if action == "query_only":
            if self.action != "query":
                return 0
            if not terms:
                return 1
            action = "query"
        if query == self._normalized_name:
            return 1000
        if self.action == "save" and action != "save":
            return 0
        title_hits = len(terms & self._title_terms)
        score = 0
        if self._title and self._title in query:
            score = 60
        elif title_hits:
            score = 30 * title_hits // len(self._title_terms)
        score += 8 * len(terms & self._name_terms) + 6 * len(
            terms & self._parameters,
        )
        description_hits = len(terms & self._description_terms)
        if score or description_hits >= 2:
            score += min(6, description_hits)
            return score + (15 if self.action == action else 0)
        return 0


class BusinessToolSearch(FunctionTool):
    """Search the toolkit's current group and publication snapshot."""

    def __init__(
        self,
        name: str,
        catalog: list[PublishedBusinessTool],
    ) -> None:
        self.catalog = catalog
        super().__init__(
            self._search,
            name=name,
            description="根据员工的原话查找可用业务工具。查询、新增或回查业务单据前先检索，下一轮会提供匹配工具的参数。",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "业务名称、想做的操作或参数名。",
                        "minLength": 1,
                        "maxLength": 256,
                    },
                },
                "required": ["query"],
            },
            is_read_only=True,
            is_concurrency_safe=False,
            is_state_injected=True,
            permission=PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message="只读检索当前业务工具目录。",
            ),
        )

    async def show_schema(self, state: AgentState | None) -> bool:
        """Decide whether to expose this tool in the current session."""
        if state is not None and await _permitted(self, state):
            for tool in self.catalog:
                if await _permitted(tool, state):
                    return True
        return False

    async def _search(self, query: str, _agent_state: AgentState) -> dict:
        if not isinstance(query, str) or not query.strip() or len(query) > 256:
            raise ValueError("请输入 1 至 256 个字符的业务问法。")
        query = _normalize(query.strip())
        action, subject = _requested_action(query)
        terms = _terms(subject) - _ACTION_TERMS
        catalog = self.catalog
        if action == "query_only" and not terms:
            # A correction such as “不要新建，只查” can omit the previous business
            # name.
            selected = _agent_state.tool_context.selected_business_tools
            previous = {
                tool.business for tool in catalog if tool.name in selected
            }
            if previous:
                catalog = [
                    tool for tool in catalog if tool.business in previous
                ]
        ranked = sorted(
            [
                (tool.score(query, terms, action), tool)
                for tool in catalog
                if await _permitted(tool, _agent_state)
            ],
            key=lambda item: (-item[0], item[1].name),
        )
        # Leave out weak alternatives, particularly when a tool is named
        # exactly.
        best_score = ranked[0][0] if ranked else 0
        matches = [
            tool
            for score, tool in ranked[:3]
            if score and score >= best_score * 0.8
        ]
        _agent_state.tool_context.selected_business_tools = [
            tool.name for tool in matches
        ]
        return {
            "matches": [
                {
                    "name": t.name,
                    "business": t.business,
                    "action": _ACTION_LABELS[t.action],
                }
                for t in matches
            ],
            "message": "下一轮可调用匹配的工具。" if matches else "未找到匹配工具，请补充业务名称或参数。",
        }
