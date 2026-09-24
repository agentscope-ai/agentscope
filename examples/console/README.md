# Terminal Console

This example demonstrates the `agentscope.console` module: trying and
debugging an agent directly in the terminal, without launching the web
service or writing any UI code.

## What the demo shows

`main.py` assembles a full-featured agent and hands it to
`launch_console`:

- **Model**: `DashScopeChatModel` (default `qwen3.7-max`), streaming.
- **Workspace**: a `LocalWorkspace` rooted at `./workspace`. The
  builtin filesystem tools (Bash/Edit/Glob/Grep/Read/Write) and the
  agent skills both come from the workspace, bound to its backend and
  skill partition.
- **Long-term memory**: `AgenticMemoryMiddleware` persists durable
  facts as Markdown files under the workspace directory, surviving
  across runs.
- **Interaction** (all handled by `launch_console`):
  - streamed rendering of text, thinking, tool calls/results, hint
    blocks and token usage;
  - tool-call confirmation — `y` allows once, `a` also accepts the
    suggested permission rules so matching calls won't ask again;
  - Ctrl+C interrupts the current reply; `exit`/`quit`/Ctrl+D leaves.

## Quickstart

```bash
export DASHSCOPE_API_KEY=sk-...
python main.py                        # interactive chat
python main.py --verbosity debug      # plus lifecycle events
python main.py --verbosity quiet      # only the reply text
```

Things worth trying:

- `List the python files in this directory` — read-only tools run
  without confirmation.
- `Create a note.md summarizing our conversation` — `Write` asks for
  confirmation; answer `a` and watch follow-up writes skip the prompt.
- `Please remember that I prefer concise Chinese answers` — the memory
  middleware persists it under `workspace/`; restart the demo and ask
  `What do you remember about me?`.

## Embedding the renderer in your own code

For agent pipelines or scripts where you own the loop, use the passive
`ConsoleRenderer` instead of `launch_console`:

```python
from agentscope.console import ConsoleRenderer

renderer = ConsoleRenderer()
async for event in agent.reply_stream(msg):
    renderer.render(event)
final_msg = renderer.last_msg
```

Inputs, tool-call confirmation and interruption are then the caller's
responsibility — the renderer only prints.
