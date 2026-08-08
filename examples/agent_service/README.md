# Agent Service

Agent service is a FastAPI-based, multi-tenant and multi-session service built with AgentScope 2.0.

This example demonstrates

- how to set up the agent service with Redis storage, and
- how to launch the service and its companion Web UI

Details about the agent service please refer to the [tutorial](https://docs.agentscope.io/latest/en/deploy/agent-service).

## Prerequisites

- Python ≥ 3.11
- Node.js ≥ 20 with `npx`
- [optional] Gaode/AMap API key in `AMAP_API_KEY` (for the `amap` MCP)

## Quickstart

Install AgentScope from PyPI or source:

```bash
uv pip install agentscope[full]
# or
# uv pip install -e [full]
```

Install Redis and start it as backend storage:

```bash
# macOS (Homebrew)
brew install redis
brew services start redis

# Linux (systemd)
sudo apt install redis-server
sudo systemctl start redis-server

# Docker (cross-platform)
docker run --rm -p 6379:6379 redis:7
```

Start the agent service:

```bash
cd examples/agent_service

python main.py
```

Launch the Web UI in a separate terminal to experience a chat-style interface:

```bash
cd examples/web_ui/

pnpm install
# or npm install

# Run in dev mode
pnpm dev
```

After that, you can set the API endpoint `http://localhost:8000` in the Web UI and start experiencing the agent service.

<img src="https://gw.alicdn.com/imgextra/i2/O1CN01Phmg1G1brIVC8WXyU_!!6000000003518-2-tps-2938-1736.png" alt="Web UI Screenshot" width="100%">

## Telegram Channel

The example registers `TelegramChannel` alongside Feishu and Discord. The
`channel` extra installs its optional runtime dependency:

```bash
uv pip install 'agentscope[channel]'
```

1. Open [BotFather](https://t.me/BotFather), run `/newbot`, and keep the bot
   token secret.
2. Call the Bot API [`getMe`](https://core.telegram.org/bots/api#getme) once
   to read the numeric bot ID.
3. In the Agent Service Web UI, create a Telegram channel and enter the
   `bot_id` and `bot_token`. Enabling the channel starts long polling; no
   public webhook endpoint is required.

The adapter validates that `getMe` returns the configured bot ID. If the bot
already has a webhook, startup fails with an instruction to remove it; the
service never deletes an existing webhook on your behalf.

Private messages are accepted directly. In groups, the default
`only_at_reply=true` accepts a message only when it mentions the bot or
replies to one of the bot's messages. To set `only_at_reply=false`, disable
Privacy Mode for the bot with BotFather so Telegram delivers ordinary group
messages to it.

Telegram Bot API downloads are limited to 20 MiB. Text, captions, photos,
documents, audio, voice, video, animations, video notes, stickers, locations,
venues, and contacts are accepted; albums are combined before delivery to the
agent. The channel does not transcribe audio or parse video. Outbound photos
are limited to 10 MiB and documents to 50 MiB.

Tool approvals use inline Allow/Deny buttons. In a group, any member who can
see a button can click it. The authoritative pending tool call remains in the
AgentScope session; cached Telegram button data only carries lookup keys.
Buttons from a previous service process expire safely after a restart.

Only one long-polling AgentScope instance may consume updates for a bot token.
Running another poller for the same bot causes a Telegram `Conflict` and marks
the channel as failed. Webhooks, message-edit streaming, rich text, bot command
handlers, chat/member listing, forum-topic routing, pairing/allowlists, and
multi-node leader election are outside this initial implementation. Telegram
commands such as `/help` are delivered to the agent as ordinary text.

The channel manually follows python-telegram-bot's asynchronous application
lifecycle instead of calling `run_polling()`. This lets Agent Service retain
ownership of its asyncio event loop, signal handling, cancellation, and
shutdown while still using a 30-second Bot API long poll.

## What Next

- You can customize the service in `main.py` by adding your own MCPs, middlewares, or workspace manager implementations.

- Experience the agent service, including
    - human-in-the-loop interactions & permission system
<img src="https://gw.alicdn.com/imgextra/i1/O1CN01vGGiBw20agWwpzmjy_!!6000000006866-2-tps-2934-1732.png" alt="Permission System" width="100%">

    - schedule tasks
<img src="https://gw.alicdn.com/imgextra/i1/O1CN01Xi3Qw71E2haKKu4z0_!!6000000000294-2-tps-2932-1738.png" alt="Schedule Tasks" width="100%">

    - and more! (stay tuned for future updates)
