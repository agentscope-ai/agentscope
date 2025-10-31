Module: `src/agentscope/tool/_search/`
Responsibility: Search tools implementation for web, local, and knowledge base queries
Key Types: `SearchTool`, `WebSearchTool`, `KnowledgeSearchTool`

Key Functions/Methods
- `search_wiki(querty, limit=10) → list[dict]`
  - Purpose: Wikipedia search with content extraction and relevance scoring
  - Inputs: Search query, result limit
  - Returns: Search results with content, metadata, and relevance scores
  - Side-effects: Network calls to search engines, API rate limiting
  - References: `src/agentscope/tool/_search/_wiki_search.py:62`
  - Type Safety: Structured typing for search parameters and result schema

- `bing_search(querty, limit=10, mkt='en-US') → list[dict]`
  - Purpose: Microsoft Bing search integration
  - Inputs: Search query, result limit, market identifier

- `search_git_repo_issue(q, owner=None, repo=None, limit=10) → list[dict]`
  - Purpose: GitHub repository and issue search
  - Side-effects: GitHub API calls with authentication

Call Graph
- `ReActAgent._acting` → `Toolkit.call_tool_function` → search tool execution

## Testing Strategy
- Unit tests: Search tool response validation
- Integration: Agent workflow with multiple search types
- Edge cases: Network failures, API limits, authentication errors

## Related SOP: `docs/tool/SOP.md`