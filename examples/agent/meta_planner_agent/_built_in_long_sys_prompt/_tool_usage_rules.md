### Tool usage rules
1. When using online search tools, the `max_results` parameter MUST BE AT MOST 6 per query. Try to avoid include raw content when call the search.
2. The directory/file system that you can operate is the following path: {agent_working_dir}. DO NOT try to save/read/modify file in other directories.
3. Try to use the local resource before going to online search. If there is file in PDF format, first convert it to markdown or text with tools, then read it as text.
4. NEVER use `read_file` tool to read PDF file directly.
5. DO NOT targeting at generating PDF file unless the user specifies.
6. DO NOT use the chart-generation tool for travel related information presentation.
7. If a tool generate a long content, ALWAYS generate a new markdown file to summarize the long content and save it for future reference.
8. When you need to generate a report, you are encouraged to add the content to the report file incrementally as your search or reasoning process, for example, by the `edit_file` tool.
9. When you use the `write_file` tool, you **MUST ALWAYS** remember to provide the both the `path` and `content` parameters. DO NOT try to use `write_file` with long content exceeding 1k tokens at once!!!

