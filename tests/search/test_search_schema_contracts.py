# -*- coding: utf-8 -*-
"""Schema equality (字段集合等价) tests for search tools.

Contract: each tool MUST expose exactly one parameter `query` (required).
"""
from __future__ import annotations

from agentscope.tool import (  # type: ignore
    Toolkit,
    search_bing,
    search_github,
    search_sogou,
    search_wiki,
)


def _assert_query_only_schema(schema: dict) -> None:
    fn = schema["function"]["name"]
    params = schema["function"]["parameters"]
    props = params.get("properties", {})
    required = params.get("required", [])
    assert set(props.keys()) == {
        "query",
    }, f"{fn}: unexpected properties {set(props.keys())}"
    assert required == [
        "query",
    ], f"{fn}: required must be ['query'] but got {required}"


def test_bing_schema_equals_query_only() -> None:
    tk = Toolkit()
    tk.register_tool_function(search_bing, preset_kwargs={"client": None})
    schemas = tk.get_json_schemas()
    sb = next(s for s in schemas if s["function"]["name"] == "search_bing")
    _assert_query_only_schema(sb)


def test_sogou_schema_equals_query_only() -> None:
    tk = Toolkit()
    tk.register_tool_function(search_sogou)
    schemas = tk.get_json_schemas()
    s = next(s for s in schemas if s["function"]["name"] == "search_sogou")
    _assert_query_only_schema(s)


def test_github_schema_equals_query_only() -> None:
    tk = Toolkit()
    tk.register_tool_function(search_github)
    schemas = tk.get_json_schemas()
    s = next(s for s in schemas if s["function"]["name"] == "search_github")
    _assert_query_only_schema(s)


def test_wiki_schema_equals_query_only() -> None:
    tk = Toolkit()
    tk.register_tool_function(search_wiki)
    schemas = tk.get_json_schemas()
    s = next(s for s in schemas if s["function"]["name"] == "search_wiki")
    _assert_query_only_schema(s)
