# CLAUDE.md — Logic & Call‑Path Guide

Module: `src/agentscope/tool/_coding/_python.py`
Responsibility: Python code execution in isolated temporary files with timeout control.
Key Types: ToolResponse, TextBlock

Key Functions/Methods
- `execute_python_code(code: str, timeout: float = 300, **kwargs: Any) -> ToolResponse`
  - Purpose: Execute Python code safely in temporary file and capture output/errors
  - Inputs: Python code string, optional timeout in seconds
  - Returns: ToolResponse with returncode, stdout, stderr content
  - Side‑effects: Creates and removes temporary files; spawns subprocess
  - Invariants/Pre/Post: Code must be valid Python; temp file always cleaned up
  - Errors: TimeoutError returns error indicator; asyncio subprocess failures
  - Notes: Uses asyncio.create_subprocess_exec with isolated environment
  - References: `src/agentscope/tool/_coding/_python.py:17`

Module: `src/agentscope/tool/_coding/_shell.py`
Responsibility: Shell command execution with timeout and output capture.

Call Graph
- Agent → Toolkit.call_tool_function → execute_python_code → ToolResponse