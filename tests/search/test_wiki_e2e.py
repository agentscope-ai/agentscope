# -*- coding: utf-8 -*-
import re

from agentscope.tool import search_wiki, ToolResponse  # type: ignore


def test_wiki_search_e2e_returns_results() -> None:
    query = "python programming"
    res: ToolResponse = search_wiki(query)
    blocks = [b for b in res.content if b.get("type") == "text"]
    assert blocks, "no text blocks"
    text = "\n".join(b.get("text", "") for b in blocks)
    print("=== E2E Wiki Results (python programming) ===")
    print(text)
    print("=== END ===")
    assert re.search(r"https?://\S+", text) is not None, "no URL detected"
