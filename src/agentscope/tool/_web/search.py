# -*- coding: utf-8 -*-
"""The web search tool."""
import os
import json
import logging
from typing import Literal

try:
    import requests
except ImportError:
    requests = None

try:
    from duckduckgo_search import DDGS
except ImportError:
    DDGS = None

from .._response import ToolResponse
from ...message import TextBlock

# Defaults
DEFAULT_NUM_RESULTS = 5

async def web_search(
    query: str,
    engine: Literal["duckduckgo", "google", "bing"] = "duckduckgo",
    num_results: int = DEFAULT_NUM_RESULTS,
) -> ToolResponse:
    """
    Perform a web search using the specified engine.

    Args:
        query (`str`):
            The search query.
        engine (`str`, optional):
            The search engine to use. Defaults to "duckduckgo".
            Options: "duckduckgo", "google", "bing".
        num_results (`int`, optional):
            The number of results to return. Defaults to 5.

    Returns:
        `ToolResponse`:
            The tool response containing search results or error message.
    """
    if engine == "duckduckgo":
        return _search_duckduckgo(query, num_results)
    elif engine == "google":
        return _search_google(query, num_results)
    elif engine == "bing":
        return _search_bing(query, num_results)
    else:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: Unsupported search engine '{engine}'. Allowed: duckduckgo, google, bing.",
                )
            ]
        )

def _search_duckduckgo(query: str, num_results: int) -> ToolResponse:
    if DDGS is None:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="Error: 'duckduckgo-search' is not installed. Please install it via `pip install duckduckgo-search`.",
                )
            ]
        )
    
    try:
        results = DDGS().text(keywords=query, max_results=num_results)
        # results is a list of dicts: {'title':..., 'href':..., 'body':...}
        formatted = ""
        for i, res in enumerate(results):
            formatted += f"Result {i+1}:\nTitle: {res.get('title')}\nURL: {res.get('href')}\nSnippet: {res.get('body')}\n\n"
        
        if not formatted:
            formatted = "No results found."

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=formatted,
                )
            ]
        )
    except Exception as e:
        return ToolResponse(content=[TextBlock(type="text", text=f"DuckDuckGo Error: {str(e)}")])

def _search_google(query: str, num_results: int) -> ToolResponse:
    api_key = os.environ.get("GOOGLE_API_KEY")
    cse_id = os.environ.get("GOOGLE_CSE_ID")
    
    if not api_key or not cse_id:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="Error: Missing GOOGLE_API_KEY or GOOGLE_CSE_ID environment variables.",
                )
            ]
        )
        
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": api_key,
            "cx": cse_id,
            "q": query,
            "num": num_results
        }
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        
        items = data.get("items", [])
        formatted = ""
        for i, item in enumerate(items):
            formatted += f"Result {i+1}:\nTitle: {item.get('title')}\nURL: {item.get('link')}\nSnippet: {item.get('snippet')}\n\n"
            
        return ToolResponse(content=[TextBlock(type="text", text=formatted)])

    except Exception as e:
        return ToolResponse(content=[TextBlock(type="text", text=f"Google Search Error: {str(e)}")])

def _search_bing(query: str, num_results: int) -> ToolResponse:
    api_key = os.environ.get("BING_API_KEY")
    if not api_key:
        return ToolResponse(content=[TextBlock(type="text", text="Error: Missing BING_API_KEY environment variable.")])
        
    try:
        url = "https://api.bing.microsoft.com/v7.0/search"
        headers = {"Ocp-Apim-Subscription-Key": api_key}
        params = {"q": query, "count": num_results}
        
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        
        pages = data.get("webPages", {}).get("value", [])
        formatted = ""
        for i, page in enumerate(pages):
             formatted += f"Result {i+1}:\nTitle: {page.get('name')}\nURL: {page.get('url')}\nSnippet: {page.get('snippet')}\n\n"
             
        return ToolResponse(content=[TextBlock(type="text", text=formatted)])
        
    except Exception as e:
        return ToolResponse(content=[TextBlock(type="text", text=f"Bing Search Error: {str(e)}")])
