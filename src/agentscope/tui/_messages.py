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
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.markup import escape
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Collapsible, Markdown, Static

from ..message import (
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
        self._text = block.text

    def on_mount(self) -> None:
        if not self._finished:
            self._stream = Markdown.get_stream(self)

    async def replace(self, block: TextBlock) -> None:
        text = block.text
        if self._stream is not None and text.startswith(self._text):
            await self._stream.write(text[len(self._text) :])
            if block.finished_at is not None:
                await self._stream.stop()
                self._stream = None
        else:
            if self._stream is not None:
                await self._stream.stop()
                self._stream = None
            await self.update(text)
        self._text = text
        self._finished = block.finished_at is not None

    async def on_unmount(self) -> None:
        if self._stream is not None:
            await self._stream.stop()
            self._stream = None


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
            collapsed_symbol="→",
            expanded_symbol="↓",
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
            label.append("  open", style=f"underline link {url}")
        if self.block.finished_at is None:
            label.append("  receiving…", style="dim italic")
        return label

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

_TOOL_STATE_STYLES = {
    "success": ("✓", "green"),
    "error": ("✗", "red"),
    "denied": ("⊘", "yellow"),
    "interrupted": ("⚠", "yellow"),
    "running": ("→", "cyan"),
}


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
    compact_result_only = pair.call.name == "Read"
    if pair.call.input.strip() and not compact_result_only:
        items.append(Text("input", style="dim"))
        items.append(
            Syntax(
                _pretty_json(pair.call.input),
                "json",
                word_wrap=True,
                background_color="default",
            ),
        )
    result = pair.result
    if result is None:
        items.append(Text("Waiting for result…", style="dim italic"))
        return Group(*items)

    output = _result_text(result)
    name = pair.call.name
    diff = result.metadata.get("diff")
    rendered: RenderableType
    if compact_result_only:
        rendered = Text(output or "(no output)", style="dim")
    elif name in ("Edit", "Write") and isinstance(diff, str) and diff:
        rendered = Syntax(
            diff,
            "diff",
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
        "success": "dim",
        "error": "bold",
        "denied": "dim italic",
        "interrupted": "dim italic",
        "running": "dim italic",
    }.get(str(result.state), "dim")
    if not compact_result_only:
        items.append(Text(f"output · {result.state}", style=state_style))
    items.append(rendered)
    return Group(*items)


def _tool_title(
    pair: _ToolPair,
    *,
    show_running_icon: bool = True,
) -> str:
    state = pair.result.state if pair.result is not None else "running"
    icon, style = _TOOL_STATE_STYLES.get(str(state), ("·", ""))
    path = _file_path(pair.call)
    primary = os.path.basename(path) if path else ""
    details = f" {escape(primary)}" if primary else ""
    if pair.call.name in ("Edit", "Write") and pair.result is not None:
        diff = pair.result.metadata.get("diff")
        if isinstance(diff, str) and diff:
            added, removed = _diff_stats(diff)
            details += f"  +{added} -{removed}"
    icon_prefix = f"{icon} " if show_running_icon or state != "running" else ""
    name = escape(pair.call.name)
    styled_tool = (
        f"[{style}]{icon_prefix}[bold]{name}[/bold][/]"
        if style
        else f"{icon_prefix}[bold]{name}[/bold]"
    )
    return f"{styled_tool}{details}"


class ToolCallUI(Vertical):
    """One flat tool call/result row inside an expanded tool group."""

    def __init__(self, pair: _ToolPair, *, show_title: bool) -> None:
        super().__init__(classes="as-tool-call")
        self.pair = pair
        self.show_title = show_title

    def compose(self) -> ComposeResult:
        if self.show_title:
            yield Static(
                _tool_title(self.pair),
                classes="as-tool-call-title",
            )
        yield Static(_tool_body(self.pair), classes="as-tool-body")


def _tool_group_state(group: _ToolGroup) -> str:
    states = {
        (str(pair.result.state) if pair.result is not None else "running")
        for pair in group.calls
    }
    for state in ("error", "denied", "interrupted", "running"):
        if state in states:
            return state
    return "success"


def _tool_group_title(group: _ToolGroup, *, expanded: bool = False) -> str:
    state = _tool_group_state(group)
    _, style = _TOOL_STATE_STYLES[state]
    disclosure = "↓" if expanded else "→"
    if len(group.calls) == 1:
        return (
            f"[{style}]{disclosure}[/] "
            f"{_tool_title(group.calls[0], show_running_icon=False)}"
        )
    counts: dict[str, int] = {}
    added = 0
    removed = 0
    for pair in group.calls:
        counts[pair.call.name] = counts.get(pair.call.name, 0) + 1
        if pair.result is not None:
            diff = pair.result.metadata.get("diff")
            if isinstance(diff, str):
                pair_added, pair_removed = _diff_stats(diff)
                added += pair_added
                removed += pair_removed
    pieces = [
        f"{name} ×{count}" if count > 1 else name
        for name, count in counts.items()
    ]
    summary = ", ".join(pieces) or "Tools"
    if added or removed:
        summary += f"  +{added} -{removed}"
    icon, _ = _TOOL_STATE_STYLES[state]
    result_prefix = "" if state == "running" else f"{icon} "
    return (
        f"[{style}]{disclosure} {result_prefix}"
        f"[bold]{escape(summary)}[/bold][/]"
    )


class ToolGroupUI(Collapsible):
    """A collapsed group of consecutive tool invocations."""

    def __init__(self, group: _ToolGroup) -> None:
        self.group = group
        self.call_ids = {pair.call.id for pair in group.calls}
        multiple = len(group.calls) > 1
        super().__init__(
            Vertical(
                *(
                    ToolCallUI(pair, show_title=multiple)
                    for pair in group.calls
                ),
                classes="as-tool-list",
            ),
            title=_tool_group_title(group),
            collapsed=True,
            collapsed_symbol="",
            expanded_symbol="",
            classes="as-tool-group",
        )

    def _watch_collapsed(self, collapsed: bool) -> None:
        super()._watch_collapsed(collapsed)
        self.title = _tool_group_title(self.group, expanded=not collapsed)


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
        yield Static(self._header_text(), classes="as-message-header")
        for block in _group_tool_calls(self.message.content):
            widget = self._make_block_ui(block)
            if widget is not None:
                yield widget
        if self.message.role == "assistant":
            footer_content = self._footer_text()
            self._footer = Static(
                footer_content,
                classes="as-message-footer",
            )
            self._footer.display = bool(footer_content.plain)
            yield self._footer
        else:
            self._footer = None

    def _header_text(self) -> Rule:
        return Rule(
            Text(self.message.name, style="bold #d8b66f"),
            characters="─",
            style="#766b5b",
            align="left",
        )

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
                title=f"Hint{source}",
                collapsed=True,
                collapsed_symbol="→",
                expanded_symbol="↓",
                classes="as-hint",
            )
            self._block_uis[block.id] = widget
            return widget
        return None

    def _footer_text(self) -> Text:
        running = self.message.finished_at is None
        text = Text()
        if running:
            text.append("◌ running", style="dim italic")
        elif self.show_usage and self.message.usage is not None:
            text.append(
                f"↑{self.message.usage.input_tokens}"
                f" ↓{self.message.usage.output_tokens}",
                style="dim",
            )
        if self.message.finished_reason not in (None, "completed"):
            text.append(
                f"  {self.message.finished_reason}",
                style="italic",
            )
        if self.message.error is not None:
            text.append(
                f"  {self.message.error.type}: {self.message.error.message}",
                style="bold",
            )
        return text

    def _update_footer(self) -> None:
        if self._footer is not None:
            content = self._footer_text()
            self._footer.update(content)
            self._footer.display = bool(content.plain)

    async def apply(self, message: Msg) -> None:
        previous = self.message
        self.message = message
        self.query_one(".as-message-header", Static).update(
            self._header_text(),
        )
        self._update_footer()
        if message.finished_at is not None and self._timer is not None:
            self._timer.pause()

        old_blocks = _group_tool_calls(previous.content)
        new_blocks = _group_tool_calls(message.content)
        old_keys = [
            tuple(pair.call.id for pair in block.calls)
            if isinstance(block, _ToolGroup)
            else block.id
            for block in old_blocks
        ]
        new_keys = [
            tuple(pair.call.id for pair in block.calls)
            if isinstance(block, _ToolGroup)
            else block.id
            for block in new_blocks
        ]
        old_by_key = dict(zip(old_keys, old_blocks))
        old_widgets = set(self._block_uis.values())
        next_widgets: dict[str, object] = {}
        retained: set[Widget] = set()
        ordered: list[Widget] = []
        for key, block in zip(new_keys, new_blocks):
            block_id = key[0] if isinstance(key, tuple) else key
            widget = self._block_uis.get(block_id)
            old_block = old_by_key.get(key)
            if isinstance(widget, Widget) and old_block == block:
                pass
            elif isinstance(widget, TextBlockUI) and isinstance(
                block,
                TextBlock,
            ):
                await widget.replace(block)
            elif isinstance(widget, ThinkingUI) and isinstance(
                block,
                ThinkingBlock,
            ):
                widget.replace(block)
            elif isinstance(widget, AttachmentUI) and isinstance(
                block,
                DataBlock,
            ):
                widget.replace(block)
            else:
                collapsed = (
                    widget.collapsed
                    if isinstance(widget, Collapsible)
                    else True
                )
                widget = self._make_block_ui(block)
                if widget is None:
                    continue
                if isinstance(widget, Collapsible):
                    widget.collapsed = collapsed
                await self.mount(widget)
            retained.add(widget)
            ordered.append(widget)
            if isinstance(block, _ToolGroup):
                for pair in block.calls:
                    next_widgets[pair.call.id] = widget
            else:
                next_widgets[block.id] = widget

        for widget in old_widgets - retained:
            if isinstance(widget, Widget) and widget.is_mounted:
                await widget.remove()
        self._block_uis = next_widgets
        previous_widget = self.query_one(".as-message-header", Static)
        for widget in ordered:
            self.move_child(widget, after=previous_widget)
            previous_widget = widget


class MessagesUI(VerticalScroll):
    """Render authoritative message snapshots without consuming events."""

    DEFAULT_CSS = """
    MessagesUI {
        width: 100%;
        height: 1fr;
        scrollbar-visibility: hidden;
        padding: 0;
        background: transparent;
    }

    MessageUI {
        width: 100%;
        height: auto;
        margin: 0;
        padding: 0;
    }

    .as-message-user {
        margin-left: 0;
        background: transparent;
    }

    .as-message-assistant {
        margin-right: 0;
        background: transparent;
    }

    .as-message-header {
        height: 1;
        margin-bottom: 1;
        color: $text-muted;
        text-style: bold;
    }

    .as-message-user > .as-message-header {
        color: $foreground;
    }

    .as-message-assistant > .as-message-header {
        color: $foreground;
    }

    .as-message-footer {
        height: auto;
        color: $text-muted;
        margin-top: 1;
    }

    .as-text-block, .as-thinking, .as-tool-group, .as-hint {
        width: 100%;
        height: auto;
    }

    .as-text-block {
        padding: 0;
        background: transparent;
    }

    .as-thinking, .as-tool-group, .as-hint {
        background: transparent;
        border-top: none;
        padding: 0;
        margin: 0 0 1 0;
    }

    .as-thinking:ansi, .as-tool-group:ansi, .as-hint:ansi {
        background: transparent;
        border-top: none;
    }

    .as-thinking > CollapsibleTitle,
    .as-tool-group > CollapsibleTitle,
    .as-hint > CollapsibleTitle {
        width: 100%;
        padding: 0 1;
        background: transparent;
        text-style: none;
    }

    .as-thinking > CollapsibleTitle,
    .as-hint > CollapsibleTitle {
        color: $text-muted;
    }

    .as-tool-group > CollapsibleTitle {
        width: auto;
        padding-left: 0;
    }

    .as-thinking > CollapsibleTitle:hover,
    .as-thinking > CollapsibleTitle:focus,
    .as-tool-group > CollapsibleTitle:hover,
    .as-tool-group > CollapsibleTitle:focus,
    .as-hint > CollapsibleTitle:hover,
    .as-hint > CollapsibleTitle:focus {
        background: transparent;
        color: #d8b66f;
        text-style: bold;
    }

    .as-thinking > Contents,
    .as-tool-group > Contents,
    .as-hint > Contents {
        height: auto;
        padding: 0 0 0 2;
    }

    .as-tool-list {
        width: 100%;
        height: auto;
    }

    .as-thinking-body, .as-hint-body {
        padding: 0;
        background: transparent;
    }

    .as-tool-call {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    .as-tool-call-title {
        width: 100%;
        height: 1;
        color: $foreground;
    }

    .as-tool-body {
        width: 100%;
        height: auto;
        padding-left: 1;
        color: $text-muted;
        background: transparent;
    }

    .as-attachment {
        width: 100%;
        height: auto;
        margin-top: 1;
        padding-left: 2;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        messages: Sequence[Msg] = (),
        *,
        show_thinking: bool = True,
        show_usage: bool = False,
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

    def current_messages(self) -> tuple[Msg, ...]:
        """Return live messages for coordinated sibling UI components.

        Unlike :attr:`messages`, this avoids deep copies. Callers must treat
        the returned models as read-only.
        """
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
        copied = [
            self._by_id[msg.id]
            if self._by_id.get(msg.id) == msg
            else msg.model_copy(deep=True)
            for msg in messages
        ]
        incoming_ids = [msg.id for msg in copied]
        current_ids = [msg.id for msg in self._messages]
        previous_by_id = self._by_id
        self._messages = copied
        self._by_id = {msg.id: msg for msg in copied}

        for msg_id in set(current_ids) - set(incoming_ids):
            await self._message_uis.pop(msg_id).remove()
        previous_widget = None
        for message in copied:
            widget = self._message_uis.get(message.id)
            if widget is None:
                widget = self._new_message_ui(message)
                self._message_uis[message.id] = widget
                await self.mount(widget)
            elif previous_by_id[message.id] != message:
                await widget.apply(message)
            if incoming_ids != current_ids:
                if previous_widget is None:
                    self.move_child(widget, before=0)
                else:
                    self.move_child(widget, after=previous_widget)
            previous_widget = widget
        self.scroll_end(animate=False)
