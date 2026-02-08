# -*- coding: utf-8 -*-
import re
import pytest

from agentscope.tool import search_wiki, ToolResponse  # type: ignore


def test_wiki_search_e2e_returns_results() -> None:
    query = "python programming"
    res: ToolResponse = search_wiki(query)
    blocks = [b for b in res.content if b.get("type") == "text"]
    assert blocks, "no text blocks"
    text = "\n".join(b.get("text", "") for b in blocks)
    lower = text.lower()
    if text.startswith("Error:") and any(
        s in lower
        for s in [
            "connectionpool",
            "max retries exceeded",
            "timed out",
            "temporary failure",
            "name or service not known",
            "connection refused",
        ]
    ):
        pytest.skip("Network unavailable for Wikipedia E2E")
    print("=== E2E Wiki Results (python programming) ===")
    print(text)
    print("=== END ===")
    assert re.search(r"https?://\S+", text) is not None, "no URL detected"
