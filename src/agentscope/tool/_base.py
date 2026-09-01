# -*- coding: utf-8 -*-
# pylint: disable=unused-argument
"""The tool protocol in agentscope."""
import inspect
import os
from abc import abstractmethod, ABC
from pathlib import Path
from typing import AsyncGenerator, Any, Callable, List

from pydantic import BaseModel

from ._constants import DEFAULT_DANGEROUS_FILES, DEFAULT_DANGEROUS_DIRECTORIES
from ..permission import (
    PermissionContext,
    PermissionDecision,
    PermissionRule,
    PermissionBehavior,
)
from ._response import ToolChunk
from ._utils import _remove_title_field


class ParamsBase(BaseModel):
    """A base class for tool parameters that remove the title field from the
    exported JSON schema.
    """

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict:
        """An override implementation to remove the title field from the
        exported schema.
        """
        return _remove_title_field(super().model_json_schema(*args, **kwargs))


class ToolMiddlewareBase(ABC):
    """Base class for tool middlewares.

    A tool middleware wraps the execution of a tool in an onion fashion: the
    first registered middleware is the outermost layer and runs its pre-logic
    before any inner layer, then its post-logic after all inner layers have
    completed. Subclass this and implement :meth:`on_tool_call` — the signature
    is already spelled out, so second-party developers only need to fill in the
    body without reasoning about the wrapping protocol.

    Streaming and non-streaming tools are unified: ``next_handler`` always
    returns an async generator, so a middleware never needs to know whether the
    underlying tool yields a stream of chunks or returns a single chunk.

    Example:
        ```python
        class LoggingMiddleware(ToolMiddlewareBase):
            async def on_tool_call(self, tool, input_kwargs, next_handler):
                print(f"Calling {tool.name} with {input_kwargs}")
                async for chunk in next_handler(**input_kwargs):
                    yield chunk
                print(f"Finished {tool.name}")

        tool = MyTool(middlewares=[LoggingMiddleware()])
        ```
    """

    @abstractmethod
    async def on_tool_call(
        self,
        tool: "ToolBase",
        input_kwargs: dict[str, Any],
        next_handler: Callable[..., AsyncGenerator[ToolChunk, None]],
    ) -> AsyncGenerator[ToolChunk, None]:
        """Intercept a single tool invocation.

        Add pre-/post-logic around ``next_handler``, rewrite the tool inputs by
        passing modified keyword arguments to ``next_handler``, or transform
        the yielded chunks.

        Args:
            tool (`ToolBase`):
                The tool instance being invoked.
            input_kwargs (`dict[str, Any]`):
                The tool's input arguments for this invocation. Pass them on
                via ``next_handler(**input_kwargs)``; mutate or replace them to
                change what the inner layers and the tool itself receive.
            next_handler (`Callable[..., AsyncGenerator[ToolChunk, None]]`):
                Call it as ``next_handler(**input_kwargs)`` to run the next
                layer. It always returns an async generator, regardless of
                whether the underlying tool is streaming or not.

        Yields:
            `ToolChunk`:
                The chunks produced by this tool invocation.
        """


class ToolBase(ABC):
    """The tool protocol."""

    name: str
    """The name presented to the agent."""
    description: str
    """The agent-oriented tool description."""
    input_schema: dict[str, Any]
    """The input schema of the tool, following JSON schema format."""
    is_concurrency_safe: bool
    """If this tool is concurrency safe."""
    is_read_only: bool
    """If this tool is read-only, which will be used in the permission
    checking."""
    is_external_tool: bool = False
    """If this tool is an external tool, which doesn't need to implement the
    __call__ method and the agent will yield the external tool call event."""
    is_state_injected: bool = False
    """If this tool requires agent state to be injected when called. If `True`,
    the state will be injected by an argument named `_agent_state`. Note your
    tool should be able to accept such argument.
    """
    is_mcp: bool = False
    """If this tool is an MCP tool, which will be used in the permission"""
    mcp_name: str | None = None
    """The name of the MCP server this tool belongs to, which is required if
    this tool is an MCP tool."""

    # Class attributes for dangerous path checking
    dangerous_files: list[str] = DEFAULT_DANGEROUS_FILES
    """List of dangerous files that should be protected from auto-editing."""
    dangerous_directories: list[str] = DEFAULT_DANGEROUS_DIRECTORIES
    """List of dangerous directories that should be protected from
    auto-editing."""

    def __init__(
        self,
        middlewares: List["ToolMiddlewareBase"] | None = None,
    ) -> None:
        """Initialize the tool with optional middlewares.

        Args:
            middlewares (`List[ToolMiddlewareBase] | None`, optional):
                A list of :class:`ToolMiddlewareBase` instances wrapping the
                tool execution in an onion fashion. Defaults to an empty list.
        """
        self._middlewares: List["ToolMiddlewareBase"] = (
            middlewares if middlewares is not None else []
        )

    async def call(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> ToolChunk | AsyncGenerator[ToolChunk, None]:
        """Execute the tool logic.

        This is the new override point for tool implementations.
        Subclasses should override this method instead of
        :meth:`__call__`.  The base implementation raises
        :exc:`NotImplementedError` for non-external tools and
        :exc:`RuntimeError` for external tools.

        Args:
            **kwargs: Tool input arguments.

        Returns:
            `ToolChunk | AsyncGenerator[ToolChunk, None]`:
                A single :class:`~agentscope.tool.ToolChunk` or an
                async generator that yields them.
        """
        if not self.is_external_tool:
            raise NotImplementedError(
                f"{self.__class__.__name__} does not implement call",
            )

        raise RuntimeError(
            f"{self.__class__.__name__} is an external tool and should not "
            f"be called directly",
        )

    async def __call__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> ToolChunk | AsyncGenerator[ToolChunk, None]:
        """Invoke the tool, layering any registered middlewares around
        :meth:`call`.

        Tools are always invoked with keyword arguments only. ``*args`` is
        accepted in the signature solely to stay Liskov-compatible with
        subclasses that override ``__call__`` with their own positional
        parameters; any positional argument actually passed here is rejected
        (raising :exc:`TypeError`) so it fails loudly instead of being silently
        dropped.

        Middlewares are applied in an onion fashion: the first registered
        middleware is the outermost layer and runs its pre-logic before
        any inner layers, then its post-logic after all inner layers
        have completed.
        """
        if args:
            raise TypeError(
                f"{type(self).__name__} must be called with keyword arguments "
                f"only, but got {len(args)} positional argument(s).",
            )
        # ``getattr`` with a default so the no-middleware path keeps working
        # even if a subclass overrides ``__init__`` without calling
        # ``super().__init__()``.
        middlewares = getattr(self, "_middlewares", [])
        if not middlewares:
            if inspect.isasyncgenfunction(self.call):
                return self.call(**kwargs)
            return await self.call(**kwargs)

        async def execute_chain(
            index: int = 0,
            **chain_kwargs: Any,
        ) -> AsyncGenerator[ToolChunk, None]:
            """Execute the tool middleware chain."""
            if index >= len(middlewares):
                # Innermost layer: run the tool's own ``call``. ``call`` is
                # always async but comes in two shapes — an async generator
                # function (e.g. ``Bash``) or a coroutine returning a single
                # ``ToolChunk`` / an async generator (e.g. ``FunctionTool``).
                # Normalize both into a single stream so middlewares never have
                # to distinguish them.
                if inspect.isasyncgenfunction(self.call):
                    async for chunk in self.call(**chain_kwargs):
                        yield chunk
                else:
                    result = await self.call(**chain_kwargs)
                    if isinstance(result, AsyncGenerator):
                        async for chunk in result:
                            yield chunk
                    else:
                        yield result
            else:
                mw = middlewares[index]
                input_kwargs = dict(chain_kwargs)

                async def next_handler(
                    **kw: Any,
                ) -> AsyncGenerator[ToolChunk, None]:
                    async for chunk in execute_chain(index + 1, **kw):
                        yield chunk

                async for chunk in mw.on_tool_call(
                    tool=self,
                    input_kwargs=input_kwargs,
                    next_handler=next_handler,
                ):
                    yield chunk

        return execute_chain(**kwargs)

    @abstractmethod
    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Check permissions for the tool usage."""

    async def check_read_only(
        self,
        tool_input: dict[str, Any],
    ) -> bool:
        """Decide whether this specific invocation is read-only.

        Returns the static :attr:`is_read_only` attribute by default.
        Subclasses with input-dependent semantics (e.g. ``Bash``) should
        override this to inspect ``tool_input`` — for example, ``Bash`` is
        statically marked as not read-only but ``ls -a`` is in fact read-only.

        Should be cheap — the permission engine may call this before the
        full :meth:`check_permissions` flow.

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data for this invocation.

        Returns:
            `bool`:
                ``True`` if this invocation is read-only, ``False`` otherwise.
        """
        return self.is_read_only

    async def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        """Check if a permission rule matches the tool input.

        .. note:: This is an optional method. A rule with no content (``None``)
        is a tool-name-level rule that matches every invocation; a rule
        with content requires the tool to override this method with its
        own matching logic, otherwise it returns ``False``.

        This means:
        - ``_FunctionTool`` and ``MCPTool`` (which do not override this)
          can still be controlled at the tool-name level via rules like
          ``{"tool_name": "my_tool", "rule_content": None}``.
        - Specific tools (Bash, Read, Write, Edit, Glob, Grep) override
          this method to support fine-grained pattern matching.

        Args:
            rule_content (`str | None`):
                The rule pattern to match. ``None`` means "match all
                invocations of this tool" (tool-name-level rule).
            tool_input (`dict[str, Any]`):
                The tool input data

        Returns:
            `bool`:
                True if the rule matches, False otherwise
        """
        # None rule_content = tool-name-level rule, matches everything
        return rule_content is None

    async def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> List[PermissionRule]:
        """Generate suggested permission rules for the tool input.

        .. note:: Suggest a single tool-name-level rule (``rule_content=None``)
        that allows all invocations of this tool. Tools can override this to
        provide finer-grained suggestions.

        For example:
        - File tools (Read/Write/Edit): suggest a glob pattern covering the
          parent directory (e.g., "src/main.py" -> "src/**")
        - Bash: suggest command prefix patterns (e.g., "git commit -m 'xxx'"
          -> "git commit:*")
        - Grep/Glob: suggest patterns based on search paths

        Args:
            tool_input (`dict[str, Any]`):
                The tool input data

        Returns:
            `List[PermissionRule]`:
                List of suggested permission rules (usually 1, max 5 for
                compound operations)
        """
        return [
            PermissionRule(
                tool_name=self.name,
                rule_content=None,
                behavior=PermissionBehavior.ALLOW,
                source="suggested",
            ),
        ]

    def _path_in_allowed_working_path(
        self,
        file_path: str,
        context: PermissionContext,
    ) -> bool:
        """Check if a file path is within any allowed working directory.

        A "working directory" is the process's current directory plus any
        entries in :attr:`PermissionContext.working_directories`. Paths
        are compared via :func:`os.path.realpath` so that aliases like
        macOS's ``/tmp`` → ``/private/tmp`` and symlinked working
        directories compare equal on both sides.

        Used by tools that conditionally auto-allow file operations in
        :attr:`PermissionMode.ACCEPT_EDITS` (e.g. Write, Edit, and the
        filesystem-command branch of Bash).

        Args:
            file_path (`str`):
                The file path to check.
            context (`PermissionContext`):
                The permission context containing the working directories.

        Returns:
            `bool`:
                True if ``file_path`` is within any allowed working
                directory.
        """
        current_dir = os.getcwd()
        additional_dirs = list(context.working_directories.keys())
        all_working_dirs = [current_dir] + additional_dirs

        abs_file_path = os.path.realpath(os.path.expanduser(file_path))

        for working_dir in all_working_dirs:
            abs_working_dir = os.path.realpath(
                os.path.expanduser(working_dir),
            )
            try:
                os.path.relpath(abs_file_path, abs_working_dir)
                if (
                    abs_file_path.startswith(abs_working_dir + os.sep)
                    or abs_file_path == abs_working_dir
                ):
                    return True
            except ValueError:
                # On Windows, relpath raises ValueError if paths are on
                # different drives.
                continue

        return False

    def _is_dangerous_path(self, file_path: str) -> bool:
        """Check if a file path is dangerous (sensitive file or directory).

        A path is considered dangerous if:
        1. The filename matches a dangerous file (e.g., .bashrc, .gitconfig)
        2. Any path segment matches a dangerous directory (e.g., .git, .ssh)

        Case-insensitive matching is used to prevent bypasses on
        case-insensitive filesystems (macOS, Windows).

        Args:
            file_path (`str`):
                The file path to check

        Returns:
            `bool`:
                True if the path is dangerous and should require explicit
                permission

        Example:
            >>> self._is_dangerous_path("/home/user/.bashrc")
            True
            >>> self._is_dangerous_path("/home/user/.git/config")
            True
            >>> self._is_dangerous_path("/home/user/project/main.py")
            False
        """

        # Normalize path
        abs_path = os.path.abspath(os.path.expanduser(file_path))

        # Split path into segments
        path_parts = Path(abs_path).parts
        path_parts_lower = [p.lower() for p in path_parts]

        # Check if filename matches dangerous files (case-insensitive)
        filename = os.path.basename(abs_path)
        filename_lower = filename.lower()
        for dangerous_file in self.dangerous_files:
            if filename_lower == dangerous_file.lower():
                return True

        # Check if any path segment matches dangerous directories
        # (case-insensitive)
        for dangerous_dir in self.dangerous_directories:
            dangerous_dir_lower = dangerous_dir.lower()
            if dangerous_dir_lower in path_parts_lower:
                return True

        return False
