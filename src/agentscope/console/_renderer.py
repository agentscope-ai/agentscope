# -*- coding: utf-8 -*-
"""Render agent event streams as human-readable terminal output."""
import json
from typing import Literal

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from ..event import (
    AgentEvent,
    DataBlockEndEvent,
    HintBlockEvent,
    ModelCallEndEvent,
    ModelCallStartEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    ThinkingBlockDeltaEvent,
    ThinkingBlockEndEvent,
    ThinkingBlockStartEvent,
    ToolCallEndEvent,
    ToolResultEndEvent,
)
from ..message import (
    AssistantMsg,
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from ..model import FinishedReason
from ..types import ReplyFinishedReason

Verbosity = Literal["quiet", "default", "debug"]

_VERBOSITY_LEVELS = {"quiet": 0, "default": 1, "debug": 2}

_RESULT_STATE_STYLES = {
    "success": ("✓", "green"),
    "error": ("✗", "red"),
    "denied": ("⊘", "yellow"),
    "interrupted": ("⚠", "yellow"),
    "running": ("…", "dim"),
}


def _human_size(n_bytes: int) -> str:
    """Format a byte count as a human-readable size."""
    size = float(n_bytes)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def _format_tool_input(raw: str) -> str:
    """Pretty-print the raw JSON input of a tool call."""
    raw = raw.strip()
    if not raw:
        return "{}"
    try:
        obj = json.loads(raw)
    except ValueError:
        return raw
    compact = json.dumps(obj, ensure_ascii=False)
    if len(compact) <= 72:
        return compact
    return json.dumps(obj, ensure_ascii=False, indent=2)


class ConsoleRenderer:
    """Render :class:`~agentscope.event.AgentEvent` streams as line-based
    terminal output, so that consuming :meth:`Agent.reply_stream` in the
    terminal becomes a one-liner:

    .. code-block:: python

        renderer = ConsoleRenderer()
        async for event in agent.reply_stream(msg):
            renderer.render(event)

    The renderer prints text/thinking deltas as they arrive, buffers the
    concurrently-streamed tool calls/results via
    :meth:`~agentscope.message.Msg.append_event` and prints them as whole
    blocks on their end events. The accumulated reply message is available
    at :attr:`last_msg` after (or during) the reply.
    """

    def __init__(
        self,
        verbosity: Verbosity = "default",
        max_tool_result_lines: int | None = 20,
        console: Console | None = None,
    ) -> None:
        """Initialize the console renderer.

        Args:
            verbosity (`Verbosity`, defaults to `"default"`):
                - `"quiet"`: only the streamed reply text and errors.
                - `"default"`: plus thinking, tool calls/results, hint
                  blocks, token usage and human-in-the-loop notices.
                - `"debug"`: plus lifecycle events and other events that
                  are invisible by default.
            max_tool_result_lines (`int | None`, defaults to `20`):
                Truncate the printed tool results to this number of lines.
                `None` means no truncation.
            console (`Console | None`, optional):
                The rich console to print to. A new one on stdout is
                created if not provided.
        """
        self.verbosity = verbosity
        self.max_tool_result_lines = max_tool_result_lines
        self.console = console or Console(highlight=False)

        self._msg: Msg | None = None
        self._mid_stream = False

    @property
    def last_msg(self) -> Msg | None:
        """The reply message accumulated from the rendered events."""
        return self._msg

    def render(self, event: AgentEvent) -> None:
        """Render the given agent event to the console.

        Unknown event types are silently skipped (or printed as a dim line
        under `"debug"` verbosity), so that the renderer keeps working when
        new event types are introduced.

        Args:
            event (`AgentEvent`):
                The event to render.
        """
        self._accumulate(event)

        if isinstance(event, ReplyStartEvent):
            self._render_reply_start(event)
        elif isinstance(event, ThinkingBlockStartEvent):
            self._render_thinking_start()
        elif isinstance(event, ThinkingBlockDeltaEvent):
            self._stream(event.delta, style="dim")
        elif isinstance(event, ThinkingBlockEndEvent):
            if self._show(1):
                self._break_line()
                self.console.print()
        elif isinstance(event, TextBlockDeltaEvent):
            self._stream(event.delta, quiet_ok=True)
        elif isinstance(event, TextBlockEndEvent):
            self._break_line(quiet_ok=True)
        elif isinstance(event, ToolCallEndEvent):
            self._render_tool_call(event)
        elif isinstance(event, ToolResultEndEvent):
            self._render_tool_result(event)
        elif isinstance(event, DataBlockEndEvent):
            self._render_data_block(event)
        elif isinstance(event, ModelCallStartEvent):
            self._debug_line(f"model call → {event.model_name}")
        elif isinstance(event, ModelCallEndEvent):
            self._render_model_call_end(event)
        elif isinstance(event, HintBlockEvent):
            self._render_hint(event)
        elif isinstance(event, RequireUserConfirmEvent):
            self._render_hitl(
                event.tool_calls,
                "Tool calls awaiting user confirmation:",
            )
        elif isinstance(event, RequireExternalExecutionEvent):
            self._render_hitl(
                event.tool_calls,
                "Tool calls awaiting external execution:",
            )
        elif isinstance(event, ReplyEndEvent):
            self._render_reply_end(event)
        else:
            evt_type = str(getattr(event, "type", type(event).__name__))
            # Delta events are noise even under debug — their content is
            # rendered as a whole block on the corresponding end event.
            if not evt_type.endswith("_DELTA"):
                self._debug_line(evt_type)

    # ==================================================================
    # Internal helpers
    # ==================================================================

    def _accumulate(self, event: AgentEvent) -> None:
        """Apply the event onto the accumulated reply message."""
        reply_id = getattr(event, "reply_id", None)
        if reply_id is None:
            return
        if isinstance(event, ReplyStartEvent):
            self._msg = AssistantMsg(name=event.name, content=[], id=reply_id)
        elif self._msg is None or self._msg.id != reply_id:
            # A continuation (e.g. after HITL) without a ReplyStartEvent
            self._msg = AssistantMsg(name="agent", content=[], id=reply_id)
        self._msg.append_event(event)

    def _show(self, level: int) -> bool:
        """Whether the current verbosity covers the given level."""
        return _VERBOSITY_LEVELS[self.verbosity] >= level

    def _break_line(self, quiet_ok: bool = False) -> None:
        """Terminate the current streamed line, if any."""
        if not (quiet_ok or self._show(1)):
            return
        if self._mid_stream:
            self.console.print()
            self._mid_stream = False

    def _stream(
        self,
        delta: str,
        style: str | None = None,
        quiet_ok: bool = False,
    ) -> None:
        """Print a streamed delta without a trailing newline."""
        if not (quiet_ok or self._show(1)):
            return
        self.console.print(
            Text(delta, style=style or ""),
            end="",
            soft_wrap=True,
        )
        self._mid_stream = True

    def _debug_line(self, text: str) -> None:
        """Print a dim single-line note under debug verbosity."""
        if self._show(2):
            self._break_line()
            self.console.print(Text(f"· {text}", style="dim"))

    def _find_block(self, block_type: str, block_id: str) -> object | None:
        """Find a block in the accumulated message by type and id."""
        if self._msg is None:
            return None
        for block in self._msg.content:
            if block.type == block_type and block.id == block_id:
                return block
        return None

    # ==================================================================
    # Per-event renderers
    # ==================================================================

    def _render_reply_start(self, event: ReplyStartEvent) -> None:
        if self._show(1):
            self.console.rule(Text(event.name, style="bold"), style="dim")

    def _render_thinking_start(self) -> None:
        if self._show(1):
            self._break_line()
            self.console.print(Text("✻ Thinking…", style="dim italic"))

    def _render_tool_call(self, event: ToolCallEndEvent) -> None:
        if not self._show(1):
            return
        block = self._find_block("tool_call", event.tool_call_id)
        if not isinstance(block, ToolCallBlock):
            return
        self._break_line()
        header = Text("→ ", style="cyan")
        header.append(block.name, style="bold cyan")
        args = _format_tool_input(block.input)
        if "\n" in args:
            self.console.print(header)
            self.console.print(
                Text("\n".join(f"  {_}" for _ in args.splitlines())),
            )
        else:
            header.append(f" {args}")
            self.console.print(header)

    def _render_tool_result(self, event: ToolResultEndEvent) -> None:
        if not self._show(1):
            return
        block = self._find_block("tool_result", event.tool_call_id)
        if not isinstance(block, ToolResultBlock):
            return
        icon, style = _RESULT_STATE_STYLES.get(block.state, ("•", ""))

        self._break_line()
        header = Text(f"{icon} {block.name}", style=style)
        header.append(f" · {block.state}", style="dim")
        self.console.print(header)

        text = self._format_result_output(block)
        lines = text.splitlines() or ([""] if text else [])
        max_lines = self.max_tool_result_lines
        truncated = 0
        if max_lines is not None and len(lines) > max_lines:
            truncated = len(lines) - max_lines
            lines = lines[:max_lines]
        for line in lines:
            self.console.print(Text(f"  {line}", style="dim"), soft_wrap=True)
        if truncated:
            self.console.print(
                Text(f"  … (+{truncated} more lines)", style="dim italic"),
            )
        if self._show(2) and event.metadata:
            self.console.print(
                Text(f"  metadata: {event.metadata}", style="dim"),
            )
        self.console.print()

    def _format_result_output(self, block: ToolResultBlock) -> str:
        """Flatten a tool result output into printable text."""
        if isinstance(block.output, str):
            return block.output
        parts = []
        for sub in block.output:
            if isinstance(sub, TextBlock):
                parts.append(sub.text)
            elif isinstance(sub, DataBlock):
                parts.append(self._data_placeholder(sub))
        return "\n".join(parts)

    @staticmethod
    def _data_placeholder(block: DataBlock) -> str:
        """A placeholder line for binary data instead of raw base64."""
        source = block.source
        if isinstance(source, Base64Source):
            size = _human_size(len(source.data) * 3 // 4)
            return f"[data: {source.media_type}, ~{size}]"
        return f"[data: {source.media_type}, {source.url}]"

    def _render_data_block(self, event: DataBlockEndEvent) -> None:
        if not self._show(1):
            return
        block = self._find_block("data", event.block_id)
        if isinstance(block, DataBlock):
            self._break_line()
            self.console.print(
                Text(self._data_placeholder(block), style="magenta"),
            )

    def _render_model_call_end(self, event: ModelCallEndEvent) -> None:
        if not self._show(1):
            return
        note = f"tokens: {event.input_tokens} in / {event.output_tokens} out"
        if event.finished_reason != FinishedReason.COMPLETED:
            note += f" · {event.finished_reason}"
        self._break_line()
        self.console.print(Text(f"· {note}", style="dim"))

    def _render_hint(self, event: HintBlockEvent) -> None:
        if not self._show(1):
            return
        if isinstance(event.hint, str):
            text = event.hint
        else:
            text = "\n".join(
                sub.text
                if isinstance(sub, TextBlock)
                else self._data_placeholder(sub)
                for sub in event.hint
            )
        self._break_line()
        source = f" from {event.source}" if event.source else ""
        self.console.print(
            Panel(
                Text(text, style="dim"),
                title=Text(f"◈ hint{source}", style="yellow"),
                title_align="left",
                border_style="dim yellow",
                expand=False,
                padding=(0, 1),
            ),
        )

    def _render_hitl(
        self,
        tool_calls: list[ToolCallBlock],
        title: str,
    ) -> None:
        if not self._show(1):
            return
        self._break_line()
        self.console.print(Text(f"⚠ {title}", style="bold yellow"))
        for tool_call in tool_calls:
            line = Text("  • ", style="yellow")
            line.append(tool_call.name, style="bold yellow")
            line.append(f" {_format_tool_input(tool_call.input)}")
            self.console.print(line, soft_wrap=True)
            for rule in tool_call.suggested_rules:
                pattern = f"({rule.rule_content})" if rule.rule_content else ""
                self.console.print(
                    Text(
                        f"    suggested rule: {rule.behavior.value} "
                        f"{rule.tool_name}{pattern}",
                        style="dim",
                    ),
                    soft_wrap=True,
                )

    def _render_reply_end(self, event: ReplyEndEvent) -> None:
        self._break_line(quiet_ok=True)
        if event.error is not None:
            self.console.print(
                Text(
                    f"✗ Error ({event.error.type}): {event.error.message}",
                    style="bold red",
                ),
            )
        elif (
            event.finished_reason == ReplyFinishedReason.INTERRUPTED
            and self._show(1)
        ):
            self.console.print(
                Text("⚠ Reply interrupted by the user.", style="yellow"),
            )
        elif (
            event.finished_reason == ReplyFinishedReason.EXCEED_MAX_ITERS
            and self._show(1)
        ):
            self.console.print(
                Text(
                    "⚠ Exceeded the maximum reasoning-acting iterations.",
                    style="yellow",
                ),
            )
        self._debug_line(f"reply end · {event.finished_reason}")
