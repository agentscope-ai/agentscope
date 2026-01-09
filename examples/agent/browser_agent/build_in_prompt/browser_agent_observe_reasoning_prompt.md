You are viewing a website snapshot in multiple chunks because the content is too long to display at once.
Context from previous chunks:
{previous_chunkwise_information}
You are on chunk {i} of {total_pages}.
Below is the content of this chunk:
{chunk}

**Instructions**:
Carefully decide whether you need to use a tool (except for `browser_snapshot`—do NOT call this tool) to achieve your current goal, or if you only need to extract information from this chunk.
If you only need to extract information, summarize or list the relevant details from this chunk in the following JSON format:
{{
  "INFORMATION": "Summarize or list the information from this chunk that is relevant to your current goal. If nothing is found, write 'None'.",
  "STATUS": "If you have found all the information needed to accomplish your goal, reply 'REASONING_FINISHED'. Otherwise, reply 'CONTINUE'."
}}
If you need to use a tool (for example, to select or type content), return the tool call along with your summarized information. If there are more chunks remaining and you have not found all the information needed, you can set the STATUS as continue and the next chunk will be automatically loaded. (Do not call other tools in this case.) Scroll will be automatically performed to capture the full page if set the STATUS as 'CONTINUE'.

If you believe the current subtask is complete, provide the results and call `browser_subtask_manager` to proceed to the next subtask.

If the final answer to the user query, i.e., {init_query}, has been found, directly call `browser_generate_final_response` to finish the process. DO NOT call `browser_subtask_manager` in this case.
