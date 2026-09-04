# -*- coding: utf-8 -*-
"""Message-oriented widgets used by the AgentScope terminal UI."""

# Textual lifecycle callbacks inherit their intent from their widget classes.
# pylint: disable=missing-function-docstring,attribute-defined-outside-init
# pylint: disable=protected-access

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import json
import os
from typing import Iterable, Sequence, TypeAlias

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Collapsible, Markdown, Static

from ..event import AgentEvent, EventType, ReplyStartEvent
from ..message import (
    AssistantMsg,
    Base64Source,
    ContentBlock,
    DataBlock,
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)


def _elapsed(created_at: str, finished_at: str | None) -> str:
    """Return a compact elapsed time for an ISO timestamp pair."""
    try:
        started = datetime.fromisoformat(created_at).timestamp()
        ended = (
            datetime.fromisoformat(finished_at).timestamp()
            if finished_at
            else datetime.now().timestamp()
        )
    except ValueError:
        return ""
    seconds = max(0.0, ended - started)
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes}m {remainder:02d}s"


def _human_size(n_bytes: int) -> str:
    size = float(n_bytes)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def _pretty_json(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return "{}"
    try:
        return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except ValueError:
        return raw


def _result_text(result: ToolResultBlock | None) -> str:
    if result is None:
        return ""
    if isinstance(result.output, str):
        return result.output
    parts: list[str] = []
    for block in result.output:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        else:
            parts.append(_attachment_label(block))
    return "\n".join(parts)


def _attachment_label(block: DataBlock) -> str:
    source = block.source
    if isinstance(source, Base64Source):
        try:
            size = len(base64.b64decode(source.data, validate=False))
        except ValueError:
            size = len(source.data) * 3 // 4
        location = _human_size(size)
    else:
        location = str(source.url)
    name = block.name or "attachment"
    return f"{name} · {source.media_type} · {location}"


class TextBlockUI(Markdown):
    """A Markdown block which can consume streaming text deltas."""

    def __init__(self, block: TextBlock) -> None:
        super().__init__(block.text, classes="as-text-block", open_links=True)
        self.block_id = block.id
        self._finished = block.finished_at is not None
        self._stream = None
        self._stop_requested = False

    def on_mount(self) -> None:
        if not self._finished:
            self._stream = Markdown.get_stream(self)

    def append_delta(self, delta: str) -> None:
        if not delta:
            return
        if self._stream is None:
            self.update(self.source + delta)
        else:
            self.run_worker(
                self._stream.write(delta),
                group=f"markdown-{self.block_id}",
                exclusive=False,
            )

    def finish(self) -> None:
        self._finished = True
        if self._stream is not None and not self._stop_requested:
            self._stop_requested = True
            self.run_worker(
                self._stop_stream(),
                group=f"markdown-stop-{self.block_id}",
            )

    async def _stop_stream(self) -> None:
        stream = self._stream
        if stream is not None:
            await stream.stop()
            if self._stream is stream:
                self._stream = None

    async def on_unmount(self) -> None:
        if self._stream is not None:
            await self._stream.stop()
            self._stream = None

    def replace(self, block: TextBlock) -> None:
        self._finished = block.finished_at is not None
        self.update(block.text)


class ThinkingUI(Collapsible):
    """Collapsed-by-default display for a thinking content block."""

    def __init__(self, block: ThinkingBlock) -> None:
        self.block = block
        text = block.thinking or "Protected reasoning content"
        self.markdown = Markdown(
            text,
            classes="as-thinking-body",
            open_links=True,
        )
        super().__init__(
            self.markdown,
            title=self._title_text(),
            collapsed=True,
            classes="as-thinking",
        )
        self._timer: Timer | None = None

    def on_mount(self) -> None:
        if self.block.finished_at is None:
            self._timer = self.set_interval(1.0, self._update_title)

    def _title_text(self) -> str:
        elapsed = _elapsed(self.block.created_at, self.block.finished_at)
        prefix = (
            "◌ Thinking" if self.block.finished_at is None else "◆ Thought"
        )
        return f"{prefix} · {elapsed}" if elapsed else prefix

    def _update_title(self) -> None:
        self.title = self._title_text()

    def append_delta(self, delta: str) -> None:
        if delta:
            self.markdown.update(self.markdown.source + delta)

    def replace(self, block: ThinkingBlock) -> None:
        self.block = block
        self.markdown.update(block.thinking or "Protected reasoning content")
        self.title = self._title_text()
        if block.finished_at is not None and self._timer is not None:
            self._timer.pause()


class AttachmentUI(Static):
    """Portable terminal representation of a multimodal data block."""

    def __init__(self, block: DataBlock) -> None:
        self.block = block
        super().__init__(self._render_block(), classes="as-attachment")

    def _render_block(self) -> RenderableType:
        media_type = self.block.source.media_type
        category = media_type.split("/", maxsplit=1)[0]
        icon = {
            "image": "▧",
            "audio": "♪",
            "video": "▶",
        }.get(category, "▤")
        label = Text(f"{icon} {self.block.name or 'attachment'}", style="bold")
        label.append(f"  {media_type}", style="dim")
        source = self.block.source
        if isinstance(source, Base64Source):
            try:
                size = len(base64.b64decode(source.data, validate=False))
            except ValueError:
                size = len(source.data) * 3 // 4
            label.append(f"  {_human_size(size)}", style="dim")
        else:
            url = str(source.url)
            label.append("  open", style=f"blue underline link {url}")
        if self.block.finished_at is None:
            label.append("  receiving…", style="yellow")
        return Panel(label, border_style="dim magenta", padding=(0, 1))

    def replace(self, block: DataBlock) -> None:
        self.block = block
        self.update(self._render_block())


@dataclass
class _ToolPair:
    call: ToolCallBlock
    result: ToolResultBlock | None = None


@dataclass
class _ToolGroup:
    calls: list[_ToolPair]


_DisplayBlock: TypeAlias = ContentBlock | _ToolGroup


def _group_tool_calls(content: Iterable[ContentBlock]) -> list[_DisplayBlock]:
    """Pair results and group consecutive tool calls like the Web UI."""
    call_map: dict[str, _ToolPair] = {}
    ordering: list[ContentBlock | tuple[str, str]] = []
    orphan_results: list[ToolResultBlock] = []
    for block in content:
        if isinstance(block, ToolCallBlock):
            call_map[block.id] = _ToolPair(block)
            ordering.append(("tool", block.id))
        elif isinstance(block, ToolResultBlock):
            pair = call_map.get(block.id)
            if pair is None:
                orphan_results.append(block)
            else:
                pair.result = block
        else:
            ordering.append(block)

    grouped: list[_DisplayBlock] = []
    pending: list[_ToolPair] = []

    def flush() -> None:
        if pending:
            grouped.append(_ToolGroup(list(pending)))
            pending.clear()

    for item in ordering:
        if isinstance(item, tuple):
            pair = call_map.get(item[1])
            if pair is not None:
                pending.append(pair)
        else:
            flush()
            grouped.append(item)
    flush()

    for result in orphan_results:
        grouped.append(
            _ToolGroup(
                [
                    _ToolPair(
                        ToolCallBlock(
                            id=result.id,
                            name=result.name,
                            input="",
                            state="finished",
                        ),
                        result,
                    ),
                ],
            ),
        )
    return grouped


def _diff_stats(diff: str) -> tuple[int, int]:
    insertions = 0
    deletions = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            insertions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return insertions, deletions


def _file_path(call: ToolCallBlock) -> str | None:
    try:
        value = json.loads(call.input).get("file_path")
    except (AttributeError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _tool_body(pair: _ToolPair) -> RenderableType:
    """Return the built-in detail rendering for one tool invocation."""
    items: list[RenderableType] = []
    if pair.call.input.strip():
        items.append(
            Panel(
                Syntax(
                    _pretty_json(pair.call.input),
                    "json",
                    word_wrap=True,
                    background_color="default",
                ),
                title="input",
                title_align="left",
                border_style="dim cyan",
            ),
        )
    result = pair.result
    if result is None:
        items.append(Text("Waiting for result…", style="dim italic"))
        return Group(*items)

    output = _result_text(result)
    name = pair.call.name
    diff = result.metadata.get("diff")
    if name in ("Edit", "Write") and isinstance(diff, str) and diff:
        rendered: RenderableType = Syntax(
            diff,
            "diff",
            line_numbers=True,
            word_wrap=False,
            background_color="default",
        )
    elif name == "Read":
        path = _file_path(pair.call) or ""
        lexer = os.path.splitext(path)[1].lstrip(".") or "text"
        rendered = Syntax(
            output,
            lexer,
            line_numbers=True,
            word_wrap=False,
            background_color="default",
        )
    elif name == "Bash":
        rendered = Syntax(
            output,
            "console",
            word_wrap=True,
            background_color="default",
        )
    else:
        rendered = Text(output or "(no output)", style="dim")
    state_style = {
        "success": "green",
        "error": "red",
        "denied": "yellow",
        "interrupted": "yellow",
        "running": "cyan",
    }.get(str(result.state), "dim")
    items.append(
        Panel(
            rendered,
            title=f"result · {result.state}",
            title_align="left",
            border_style=state_style,
        ),
    )
    return Group(*items)


def _tool_title(pair: _ToolPair) -> str:
    state = pair.result.state if pair.result is not None else "running"
    icon = {
        "success": "✓",
        "error": "✗",
        "denied": "⊘",
        "interrupted": "⚠",
        "running": "…",
    }.get(str(state), "·")
    path = _file_path(pair.call)
    primary = os.path.basename(path) if path else ""
    details = f" {primary}" if primary else ""
    if pair.call.name in ("Edit", "Write") and pair.result is not None:
        diff = pair.result.metadata.get("diff")
        if isinstance(diff, str) and diff:
            added, removed = _diff_stats(diff)
            details += f"  +{added} -{removed}"
    return f"{icon} {pair.call.name}{details}"


class ToolCallUI(Collapsible):
    """One built-in, expandable tool call/result rendering."""

    def __init__(self, pair: _ToolPair) -> None:
        super().__init__(
            Static(_tool_body(pair), classes="as-tool-body"),
            title=_tool_title(pair),
            collapsed=True,
            classes="as-tool-call",
        )


def _tool_group_title(group: _ToolGroup) -> str:
    counts: dict[str, int] = {}
    added = 0
    removed = 0
    running = False
    for pair in group.calls:
        counts[pair.call.name] = counts.get(pair.call.name, 0) + 1
        running = (
            running or pair.result is None or pair.result.state == "running"
        )
        if pair.result is not None:
            diff = pair.result.metadata.get("diff")
            if isinstance(diff, str):
                pair_added, pair_removed = _diff_stats(diff)
                added += pair_added
                removed += pair_removed
    pieces = [
        f"{name} {count}" if count > 1 else name
        for name, count in counts.items()
    ]
    summary = ", ".join(pieces) or "Tools"
    if added or removed:
        summary += f"  +{added} -{removed}"
    return f"{'◌' if running else '◆'} {summary}"


class ToolGroupUI(Collapsible):
    """A collapsed group of consecutive tool invocations."""

    def __init__(self, group: _ToolGroup) -> None:
        self.call_ids = {pair.call.id for pair in group.calls}
        super().__init__(
            Vertical(*(ToolCallUI(pair) for pair in group.calls)),
            title=_tool_group_title(group),
            collapsed=True,
            classes="as-tool-group",
        )


class MessageUI(Vertical):
    """Rendering of one Msg with block-local streaming updates."""

    def __init__(
        self,
        message: Msg,
        *,
        show_thinking: bool,
        show_usage: bool,
    ) -> None:
        super().__init__(classes=f"as-message as-message-{message.role}")
        self.message = message
        self.show_thinking = show_thinking
        self.show_usage = show_usage
        self._block_uis: dict[str, object] = {}
        self._footer: Static | None = None
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        self._block_uis = {}
        yield Static(self.message.name, classes="as-message-header")
        for block in _group_tool_calls(self.message.content):
            widget = self._make_block_ui(block)
            if widget is not None:
                yield widget
        if self.message.role == "assistant":
            self._footer = Static(
                self._footer_text(),
                classes="as-message-footer",
            )
            yield self._footer
        else:
            self._footer = None

    def on_mount(self) -> None:
        if (
            self.message.role == "assistant"
            and self.message.finished_at is None
        ):
            self._timer = self.set_interval(1.0, self._update_footer)

    def _make_block_ui(self, block: _DisplayBlock) -> Widget | None:
        if isinstance(block, TextBlock):
            widget = TextBlockUI(block)
            self._block_uis[block.id] = widget
            return widget
        if isinstance(block, ThinkingBlock):
            if not self.show_thinking:
                return None
            widget = ThinkingUI(block)
            self._block_uis[block.id] = widget
            return widget
        if isinstance(block, DataBlock):
            widget = AttachmentUI(block)
            self._block_uis[block.id] = widget
            return widget
        if isinstance(block, _ToolGroup):
            widget = ToolGroupUI(block)
            for call_id in widget.call_ids:
                self._block_uis[call_id] = widget
            return widget
        if block.type == "hint":
            text = (
                block.hint
                if isinstance(block.hint, str)
                else "\n".join(
                    (
                        item.text
                        if isinstance(item, TextBlock)
                        else _attachment_label(item)
                    )
                    for item in block.hint
                )
            )
            source = f" from {block.source}" if block.source else ""
            widget = Collapsible(
                Markdown(text, classes="as-hint-body"),
                title=f"◇ Hint{source}",
                collapsed=True,
                classes="as-hint",
            )
            self._block_uis[block.id] = widget
            return widget
        return None

    def _footer_text(self) -> Text:
        running = self.message.finished_at is None
        icon = "◌" if running else "✓"
        elapsed = _elapsed(self.message.created_at, self.message.finished_at)
        text = Text(f"{icon} {elapsed or 'running'}", style="dim")
        if self.show_usage and self.message.usage is not None:
            text.append(
                f"  ↑{self.message.usage.input_tokens}"
                f" ↓{self.message.usage.output_tokens}",
            )
        if self.message.finished_reason not in (None, "completed"):
            text.append(f"  {self.message.finished_reason}", style="yellow")
        if self.message.error is not None:
            text.append(
                f"  {self.message.error.type}: {self.message.error.message}",
                style="red",
            )
        return text

    def _update_footer(self) -> None:
        if self._footer is not None:
            self._footer.update(self._footer_text())

    def apply(self, message: Msg, event: AgentEvent | None = None) -> None:
        self.message = message
        self._update_footer()
        if message.finished_at is not None and self._timer is not None:
            self._timer.pause()

        if event is None:
            self.call_later(self.recompose)
            return
        event_type = event.type
        if event_type in (EventType.REPLY_END, EventType.MODEL_CALL_END):
            return
        if event_type == EventType.TEXT_BLOCK_DELTA:
            widget = self._block_uis.get(event.block_id)
            if isinstance(widget, TextBlockUI):
                widget.append_delta(event.delta)
                return
        elif event_type == EventType.TEXT_BLOCK_END:
            widget = self._block_uis.get(event.block_id)
            if isinstance(widget, TextBlockUI):
                widget.finish()
                return
        elif event_type == EventType.THINKING_BLOCK_DELTA:
            widget = self._block_uis.get(event.block_id)
            if isinstance(widget, ThinkingUI):
                widget.append_delta(event.delta)
                return
        elif event_type == EventType.THINKING_BLOCK_END:
            widget = self._block_uis.get(event.block_id)
            block = message._find_block("thinking", event.block_id)
            if isinstance(widget, ThinkingUI) and isinstance(
                block,
                ThinkingBlock,
            ):
                widget.replace(block)
                return
        elif event_type in (
            EventType.DATA_BLOCK_DELTA,
            EventType.DATA_BLOCK_END,
        ):
            widget = self._block_uis.get(event.block_id)
            block = message._find_block("data", event.block_id)
            if isinstance(widget, AttachmentUI) and isinstance(
                block,
                DataBlock,
            ):
                widget.replace(block)
                return
        elif event_type in (
            EventType.TOOL_CALL_DELTA,
            EventType.TOOL_CALL_END,
            EventType.TOOL_RESULT_START,
            EventType.TOOL_RESULT_TEXT_DELTA,
            EventType.TOOL_RESULT_DATA_DELTA,
            EventType.TOOL_RESULT_END,
            EventType.REQUIRE_USER_CONFIRM,
            EventType.USER_CONFIRM_RESULT,
            EventType.REQUIRE_EXTERNAL_EXECUTION,
            EventType.EXTERNAL_EXECUTION_RESULT,
        ):
            # Recompose this message only. Expansion state is deliberately
            # preserved at the conversation/message level, while tool content
            # remains authoritative from Msg.append_event().
            self.call_later(self.recompose)
            return
        self.call_later(self.recompose)


class MessagesUI(VerticalScroll):
    """Render historical Msg objects and incrementally apply AgentEvents."""

    DEFAULT_CSS = """
    MessagesUI {
        width: 100%;
        height: 1fr;
        scrollbar-size: 1 1;
        padding: 0 1;
    }

    MessageUI {
        width: 100%;
        height: auto;
        margin: 0 0 1 0;
        padding: 0 1;
    }

    .as-message-user {
        margin-left: 8;
        background: $boost;
        border-left: tall $primary;
    }

    .as-message-assistant {
        margin-right: 4;
        border-left: tall $accent;
    }

    .as-message-header {
        height: 1;
        color: $text-muted;
        text-style: bold;
    }

    .as-message-footer {
        height: auto;
        min-height: 1;
        color: $text-muted;
        margin-top: 1;
    }

    .as-text-block, .as-thinking, .as-tool-group, .as-hint {
        width: 100%;
        height: auto;
    }

    .as-thinking-body, .as-tool-body, .as-hint-body {
        padding: 0 1;
        background: $surface;
    }

    .as-tool-call {
        width: 100%;
        height: auto;
        margin-left: 1;
    }

    .as-attachment {
        width: 100%;
        height: auto;
        margin: 1 0 0 0;
    }
    """

    def __init__(
        self,
        messages: Sequence[Msg] = (),
        *,
        show_thinking: bool = True,
        show_usage: bool = True,
        id: str | None = None,  # pylint: disable=redefined-builtin
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(id=id, classes=classes, disabled=disabled)
        self.show_thinking = show_thinking
        self.show_usage = show_usage
        self._messages = [msg.model_copy(deep=True) for msg in messages]
        self._by_id = {msg.id: msg for msg in self._messages}
        self._message_uis: dict[str, MessageUI] = {}

    @property
    def messages(self) -> tuple[Msg, ...]:
        """A safe snapshot of the messages currently being rendered."""
        return tuple(msg.model_copy(deep=True) for msg in self._messages)

    def _current_messages(self) -> tuple[Msg, ...]:
        """Return internal messages for sibling TUI components."""
        return tuple(self._messages)

    def compose(self) -> ComposeResult:
        self._message_uis = {}
        for message in self._messages:
            widget = self._new_message_ui(message)
            self._message_uis[message.id] = widget
            yield widget

    def _new_message_ui(self, message: Msg) -> MessageUI:
        return MessageUI(
            message,
            show_thinking=self.show_thinking,
            show_usage=self.show_usage,
        )

    async def set_messages(self, messages: Sequence[Msg]) -> None:
        """Reconcile an authoritative full conversation snapshot."""
        copied = [msg.model_copy(deep=True) for msg in messages]
        incoming_ids = [msg.id for msg in copied]
        current_ids = [msg.id for msg in self._messages]
        previous_by_id = self._by_id
        self._messages = copied
        self._by_id = {msg.id: msg for msg in copied}

        if incoming_ids != current_ids:
            await self.remove_children()
            self._message_uis = {}
            widgets = []
            for message in copied:
                widget = self._new_message_ui(message)
                self._message_uis[message.id] = widget
                widgets.append(widget)
            if widgets:
                await self.mount(*widgets)
        else:
            for message in copied:
                widget = self._message_uis.get(message.id)
                previous = previous_by_id.get(message.id)
                if (
                    widget is not None
                    and previous is not None
                    and previous.model_dump() != message.model_dump()
                ):
                    widget.apply(message)
        self.scroll_end(animate=False)

    def feed(self, item: AgentEvent | Msg) -> None:
        """Apply one complete message or streaming event to the display."""
        event: AgentEvent | None = None
        if isinstance(item, Msg):
            message = item.model_copy(deep=True)
            existing = self._by_id.get(message.id)
            if existing is None:
                self._messages.append(message)
            else:
                index = self._messages.index(existing)
                self._messages[index] = message
            self._by_id[message.id] = message
        else:
            event = item
            reply_id = getattr(item, "reply_id", None)
            if reply_id is None:
                return
            message = self._by_id.get(reply_id)
            if message is None:
                name = (
                    item.name if isinstance(item, ReplyStartEvent) else "agent"
                )
                message = AssistantMsg(name=name, content=[], id=reply_id)
                self._messages.append(message)
                self._by_id[reply_id] = message
            elif isinstance(item, ReplyStartEvent):
                message.name = item.name
            if not isinstance(item, ReplyStartEvent):
                message.append_event(item)

        widget = self._message_uis.get(message.id)
        if widget is None:
            widget = self._new_message_ui(message)
            self._message_uis[message.id] = widget
            if self.is_mounted:
                self.call_later(self.mount, widget)
        else:
            widget.apply(message, event)
        if self.is_mounted:
            self.call_later(self.scroll_end, animate=False)


__all__ = ["MessagesUI"]
