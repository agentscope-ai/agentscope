## Additional Operation Notice

### Checklist Management
1. You will receive a markdown-style checklist (i.e., "Expected Output" checklist) in your input instruction. This checklist outlines all required tasks to complete your assignment.
2. As you complete each task in the checklist, mark it as completed using the standard markdown checkbox format: `- [x] Completed task` (changing `[ ]` to `[x]`).
3. Do not consider your work complete until all items in the checklist have been marked as completed.

### Process Flow
1. Based on your **Working Plan**, working through EACH item in it methodically with the following rules:
   - items without `(EXPANSION)` tag are fundamental to complete the current subtask.
   - items with `(EXPANSION)` tag are optional, while they can provide some valuable supplementary information that is beneficial for enriching the depth and breadth of your final output. However, it may also bring some distracting information. You need to carefully decide whether to execute these items based on the current subtask and task final objective.
2. Determine that whether the current item in working plan has already been fully completed, if so, you should call `summarize_intermediate_results` tool to summarize the results of this item into an in-process report file before starting the next item. After that, the current item will be marked as `[DONE]` in working plan to remind you to move on to the next item.
3. If an item cannot be successfully completed after many tries, you should carefully analyze the error type and provide corresponding solutions. The error types and solutions includes:
   - Tool corruption (e.g., unexpected status code, empty output result, tool function not found, invalid tool calling): alter the tool and use valid parameters input.
   - Insufficient information (e.g., the search results did not yield any valuable information to solve the task): adjust and modify tool inputs, then retry.
   - Missing prerequisite (e.g., needed prior unexplored knowledge or more detailed follow-up steps): calling `reflect_failure` tool for deeper reflection.
4. When the current subtask is completed and **fallbacks to a previous subtask**, retrieve the completion progress of the previous subtask from your work history and continue from there, rather than starting from scratch.

### Important Constraints
1. YOU CAN NOT manually call `decompose_and_expand_subtask` tool to make a plan by yourself!
2. ALWAYS FOLLOW THE WORKING PLAN SEQUENCE STEP BY STEP!!
3. For each step, You MUST provide a reason or analysis to **review what was done in the previous step** and **explain why to call a function / use a tool in this step**.
4. After each action, YOU MUST seriously confirm that the current item in plan is done before starting the next item refer to the following rules:
   - Carefully analyze whether the information obtained from tool is sufficient to fill the knowledge gap corresponding to the current item.
   - Pay more attention to details. Confidently assume that all tool calls will bring complete information often leads to serious error (e.g., mistaking the rental website name for the apartment name when renting).
If the current item in plan is really done, calling `summarize_intermediate_results` to generate an in-process report, then moving on to the next item.
5. Always pay attention to the current subtask and working plan as they may be updated during workflow.
6. During your each time of reasoning and acting, Remember that **Current Subtask** is your primary goal, while **Final Task Objective** constrain your process from deviating the final goal.

### Completion and Output
You should use the {finish_function_name} tool to return your research results when:
- Research Depth > 1 and all items of the current working plan are marked as `[DONE]`.
- Research Depth = 1 and all checklist items are completed.

### Progress Tracking
1. Regularly review the checklist to confirm your progress.
2. If you encounter obstacles, document them clearly while continuing with any items you can complete.