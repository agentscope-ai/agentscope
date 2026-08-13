# -*- coding: utf-8 -*-
"""The read tool in agentscope."""
import fnmatch
from typing import Any, List

from .._base import ToolBase, ToolMiddlewareBase
from ...permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
    PermissionRule,
)
from .._response import ToolChunk
from ...message import TextBlock, ToolResultState
from ...state import AgentState
from ._backend import BackendBase, _normalize_newlines


class Read(ToolBase):
    """The read tool."""

    name: str = "Read"
    """The tool name presented to the agent."""

    # pylint: disable=line-too-long
    description: str = """从本地文件系统读取文件。你可以直接使用此工具访问任何文件。
假设本工具能够读取机器上的所有文件。如果用户提供了文件路径，请假定该路径有效。读取不存在的文件也没问题，此时会返回错误。

用法：
- file_path 参数必须是绝对路径，不能是相对路径
- 默认情况下，从文件开头读取最多 2000 行
- 你可以选择指定行偏移量（offset）和限制（limit）（对于长文件尤其有用），但建议不提供这些参数以读取整个文件
- 结果以 cat -n 格式返回，行号从 1 开始
- 此工具允许你读取图片（如 PNG、JPG 等）。读取图片文件时，内容会以可视方式呈现，因为你是一个多模态 LLM。
- 此工具可以读取 PDF 文件（.pdf）。对于较大的 PDF（超过 10 页），你必须提供 pages 参数来读取特定页面。"""  # noqa: E501
    """The description presented to the agent."""

    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要读取的文件的绝对路径。",
            },
            "offset": {
                "type": "integer",
                "description": "可选的开始读取的起始行号（从 1 开始）"
                "（默认值：1）",
                "default": 1,
                "minimum": 1,
            },
            "limit": {
                "type": "integer",
                "description": "可选的读取的最大行数"
                "（默认值：2000，最大值：2000）",
                "default": 2000,
                "maximum": 2000,
                "minimum": 1,
            },
        },
        "required": ["file_path"],
    }

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = True

    def __init__(
        self,
        max_line_characters: int = 2000,
        middlewares: List[ToolMiddlewareBase] | None = None,
        backend: BackendBase | None = None,
    ) -> None:
        """Initialize the read tool.

        Args:
            max_line_characters (`int`, defaults to 2000):
                The maximum number of characters to include for each line when
                reading files. Lines longer than this will be truncated with
                a "[truncated]" suffix. This prevents overwhelming the agent
                with excessively long lines while still providing useful
                content.
            middlewares (`List[ToolMiddlewareBase] | None`, optional):
                Tool middlewares wrapping the tool execution.
            backend (`BackendBase | None`, optional):
                The sandbox backend to use for file I/O. When ``None``,
                a :class:`LocalBackend` is created.
        """
        from ._backend import LocalBackend

        super().__init__(middlewares=middlewares)
        self._max_line_characters = max_line_characters
        self._backend = backend or LocalBackend()

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for file reading.

        Read is a read-only tool. In EXPLORE mode the engine already handles
        the ALLOW via _check_explore_mode, so here we just return PASSTHROUGH
        to let the engine continue with rule matching.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="File reading is read-only.",
        )

    async def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        """Check if a permission rule matches the file path.

        Matches rule_content as a glob pattern against the "file_path"
        parameter using fnmatch. If rule_content is None, matches all
        invocations (tool-name-level rule).

        Args:
            rule_content (`str | None`):
                Glob pattern to match against the file path (e.g., "src/**"),
                or None to match all invocations
            tool_input (`dict[str, Any]`):
                The tool input data containing "file_path" key

        Returns:
            `bool`:
                True if the glob pattern matches the file path, False otherwise
        """
        # None = tool-name-level rule, matches everything
        if rule_content is None:
            return True

        file_path = tool_input.get("file_path", "")
        if not file_path:
            return False
        return fnmatch.fnmatch(file_path, rule_content)

    async def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        """Generate suggested permission rules for the file path.

        Suggests a glob pattern covering the parent directory of the file,
        allowing the user to grant permission for the entire directory at once.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data containing "file_path" key

        Returns:
            `List[PermissionRule]`:
                A single suggested rule covering the parent directory
                (e.g., file "/src/main.py" -> rule "src/**")
        """
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return []

        parent = self._backend.dirname(file_path)
        # Glob patterns are POSIX-style strings (matched by fnmatch),
        # not real filesystem paths — do NOT use backend.join_path here.
        pattern = (parent.rstrip("/\\") + "/**") if parent else "**"

        return [
            PermissionRule(
                tool_name=self.name,
                rule_content=pattern,
                behavior=PermissionBehavior.ALLOW,
                source="suggested",
            ),
        ]

    async def call(  # type: ignore[override]
        self,
        file_path: str,
        offset: int = 1,
        limit: int = 2000,
        _agent_state: AgentState | None = None,
    ) -> ToolChunk:
        """Read the file and return the content with line numbers."""

        # Validate file_path is absolute
        if not self._backend.isabs(file_path):
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Error: file_path must be an absolute path, "
                        f"got: {file_path}",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        # Check file exists
        if not await self._backend.file_exists(file_path):
            return ToolChunk(
                content=[
                    TextBlock(text=f"Error: File does not exist: {file_path}"),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        # Check it's not a directory
        if await self._backend.is_dir(file_path):
            return ToolChunk(
                content=[
                    TextBlock(
                        text=f"Error: Path is a directory, not a file: "
                        f"{file_path}",
                    ),
                ],
                state=ToolResultState.ERROR,
                is_last=True,
            )

        try:
            # Read file content via backend
            lines = None
            if _agent_state is not None:
                cache = await _agent_state.tool_context.get_cache(file_path)
                if cache is not None:
                    lines = cache.lines

            if lines is None:
                raw = await self._backend.read_file(file_path)
                content_str = raw.decode("utf-8", errors="replace")
                # Normalize CRLF/CR so cached lines end in "\n" regardless
                # of the platform the file was written on (Windows text
                # files use "\r\n").
                content_str = _normalize_newlines(content_str)
                lines = content_str.splitlines(keepends=True)

                # Cache file if state is provided
                if _agent_state is not None:
                    await _agent_state.tool_context.cache_file(
                        file_path=file_path,
                        lines=lines,
                    )

            # Apply offset and limit (offset is 1-based)
            start_idx = offset - 1
            end_idx = start_idx + limit
            selected_lines = lines[start_idx:end_idx]

            # Format with line numbers (6-char padded + tab + content)
            formatted_lines = []
            for i, line in enumerate(selected_lines, start=offset):
                # Remove trailing newline if present
                line_content = line.rstrip("\n\r")

                # Truncate lines longer than 2000 chars
                if len(line_content) > self._max_line_characters:
                    line_content = (
                        line_content[: self._max_line_characters]
                        + "[truncated]"
                    )

                # Format: 6-char padded line number + tab + content
                formatted_line = f"{i:6d}\t{line_content}"
                formatted_lines.append(formatted_line)

            # Join all lines
            result = "\n".join(formatted_lines)

            return ToolChunk(
                content=[TextBlock(text=result)],
                state=ToolResultState.RUNNING,
                is_last=True,
            )

        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"Error reading file: {str(e)}")],
                state=ToolResultState.ERROR,
                is_last=True,
            )
