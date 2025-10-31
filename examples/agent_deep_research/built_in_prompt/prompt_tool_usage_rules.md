### Tool usage rules
1. When using online search tools, populate the `query` field with a concise keyword phrase (2-6 words). Use the optional `context` field to provide one or two sentences of background or desired angles for the search.
2. As research depth increases, refine the `query` and update the `context` to reflect newly discovered focus areas so subsequent searches stay aligned.
3. The directory/file system that you can operate is the following path: {tmp_file_storage_dir}. DO NOT try to save/read/modify file in other directories.
4. Try to use the local resource before going to online search. If there is file in PDF format, first convert it to markdown or text with tools, then read it as text.
5. You can basically use web search tools to search and retrieve whatever you want to know, including financial data, location, news, etc.
6. NEVER use `read_text_file` tool to read PDF file directly.
7. DO NOT targeting at generating PDF file unless the user specifies.
8. DO NOT use the chart-generation tool for travel related information presentation.
9. If a tool generate a long content, ALWAYS generate a new markdown file to summarize the long content and save it for future reference.
11. When you use the `write_text_file` tool, you **MUST ALWAYS** remember to provide the both the `path` and `content` parameters. DO NOT try to use `write_text_file` with long content exceeding 1k tokens at once!!!

Finally, before each tool using decision, carefully review the historical tool usage records to avoid the time and API costs caused by repeated execution. Remember that your balance is very low, so ensure absolute efficiency.
