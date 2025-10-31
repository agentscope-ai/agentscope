Module: `src/agentscope/tool/_web/`
Responsibility: Web interaction and data extraction tools
Key Types: `WebScrapeTool`, `WebBrowseTool`, `UrlAnalysisTool`

Key Functions/Methods
- `web_scrape(url, selector="body", timeout=30) → dict`
  - Purpose: Web content scraping and extraction with CSS selectors

- `browse_web_url(url, actions=None, timeout=60) → dict`
  - Purpose: Navigate and interact with web pages programmatically
  - Inputs: Target URL, CSS selector, timeout
  - Returns: Extracted content, metadata, and success status
  - Side-effects: Network requests, potentially rendering JavaScript
  - References: `src/agentscope/tool/_web/_web_scrape.py:78`
  - Type Safety: URL validation, selector syntax checking

- `analyze_url_structure(url) → dict`
  - Purpose: URL and web page structure analysis
  - Errors: Connection timeouts, invalid URLs, parsing failures

- `fetch_web_content(url, method="GET", headers=None) → dict`
  - Purpose: Low-level web content fetching with custom headers

Call Graph
- `ReActAgent._acting` → web tool calls → content processing

## Testing Strategy
- Unit tests: Web tool functionality across different site types
- Integration: End-to-end web interaction workflows
- Edge cases: Dynamic content loading, authentication requirements, CAPTCHAs

## Related SOP: `docs/tool/SOP.md`