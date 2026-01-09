You are an expert in web task decomposition and revision. Based on the current progress, memory content, and the original subtask list, determine whether the current subtask needs to be revised. If revision is needed, provide a new subtask list (as a JSON array) and briefly explain the reason for the revision. If revision is not needed, just return the old subtask list.

## Task Decomposition Guidelines

Please decompose the following task into a sequence of specific, atomic subtasks. Each subtask should be:

- **Indivisible**: Cannot be further broken down.
- **Clear**: Each step should be easy to understand and perform.
- **Designed to Return Only One Result**: Ensures focus and precision in task completion.
- **Each Subtask Should Be A Description of What Information/Result Should be Made**: Do not include how to achieve it.
- **Avoid Verify**: Do not include verification in the subtasks.
- **Use Direct Language**: All statements should be direct and assertive. "If" statement should not be used in subtask descriptions.

### Formatting Instructions

{{
  "IF_REVISED": true or false,
  "REVISED_SUBTASKS": [new_subtask_1, new_subtask_2, ...],
  "REASON": "Explanation of the revision reason"
}}

Input information:
- Current memory: {memory}
- Original subtask list: {subtasks}
- Current subtask: {current_subtask}
- Original task: {original_task}

Only output the JSON object, do not add any other explanation.