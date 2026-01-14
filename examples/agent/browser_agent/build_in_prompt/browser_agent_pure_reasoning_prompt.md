Current subtask to be completed: {current_subtask}

Please carefully evaluate whether you need to use a tool to achieve your current goal, or if you can accomplish it through reasoning alone.

**If you only need reasoning:**
- Analyze the currently available information
- Provide your reasoning response based on the analysis
- Pay special attention to whether this subtask is completed after your response
- If you believe the subtask is complete, summarize the results and call `browser_subtask_manager` to proceed to the next subtask

**If you need to use a tool:**
- Analyze previous chat history - if previous tool calls were unsuccessful, try a different tool or approach
- Return the appropriate tool call along with your reasoning response
- For example, use tools to navigate, click, select, or type content on the webpage

Remember to be strategic in your approach and learn from any previous failed attempts.

If you believe the current subtask is complete, provide the results and call `browser_subtask_manager` to proceed to the next subtask.

If the final answer to the user query, i.e., {init_query}, has been found, directly call `browser_generate_final_response` to finish the process. DO NOT call `browser_subtask_manager` in this case.
