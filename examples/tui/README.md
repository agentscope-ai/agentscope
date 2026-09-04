# Terminal UI

This example runs an AgentScope agent in the optional Textual terminal UI.
The UI restores historical `Msg` objects, incrementally consumes
`AgentEvent` streams, renders Markdown and tools, and handles tool-call
confirmation without launching the web service.

## Quickstart

```bash
pip install "agentscope[tui]"
export DASHSCOPE_API_KEY=sk-...
python main.py
```

Controls:

- `Enter` sends the current message and `Shift+Enter` inserts a newline.
- Tool and thinking rows expand with a click or with `Enter` when focused.
- A pending HITL request replaces the composer until it is resolved.
- `Ctrl+Q` exits the TUI.

## Embedding the UI

Widget sizes are controlled with Textual CSS rather than constructor
arguments. `MessagesUI` is the read-only building block:

```python
from textual.app import App, ComposeResult

from agentscope.tui import MessagesUI


class HistoryApp(App):
    CSS = """
    #history {
        width: 96%;
        max-width: 110;
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield MessagesUI(messages=history, id="history")


HistoryApp().run()
```

An authoritative history refresh reconciles widgets by message and block ID:

```python
history_ui = app.query_one("#history", MessagesUI)
await history_ui.set_messages(await load_messages())
```

`ChatUI` adds the composer and HITL controls while leaving execution and
concurrency policy to the containing application:

```python
from textual import on
from textual.app import App, ComposeResult

from agentscope.tui import ChatUI


class RuntimeApp(App):
    def compose(self) -> ComposeResult:
        yield ChatUI(messages=history, id="chat")

    async def consume_events(self) -> None:
        chat = self.query_one("#chat", ChatUI)
        async for item in runtime.events:
            chat.feed(item)

    @on(ChatUI.Submitted)
    async def submit(self, event: ChatUI.Submitted) -> None:
        await runtime.submit(event.msg)

    @on(ChatUI.Confirmed)
    async def confirm(self, event: ChatUI.Confirmed) -> None:
        await runtime.submit(event.value)

    @on(ChatUI.InterruptRequested)
    async def interrupt(self, event: ChatUI.InterruptRequested) -> None:
        await runtime.interrupt(event.reply_id)
```

The ordinary composer can be toggled independently. Pending HITL always
replaces it until confirmation or external execution completes:

```python
chat = app.query_one("#chat", ChatUI)
chat.input_enabled = False
chat.input_enabled = True
```

For an Agent or `PipelineProtocol`, the standalone launcher wires the same
events automatically:

```python
from agentscope.tui import launch_tui

await launch_tui(agent, messages=history, user_name="user")
```
