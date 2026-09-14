# Realtime Voice Agent

Talk to a DashScope speech-to-speech model through your microphone.
`local_mic.py` wires up the three pieces of `agentscope.realtime`:

- `DashScopeAudioRealtimeModel` / `DashScopeRealtimeModel` — the provider
  session, resolved from the model card so any listed model works.
- `LocalAudioTransport` — the sound card: capture, playback, playout
  accounting and a short fade when the reply is cut off.
- `RealtimeAgent` — the turn-taking state machine in between: barge-in,
  tool calls with permission prompts, per-turn latency metrics.

The agent owns the model session, you own the transport, and one
`agent.reply_stream(transport)` borrows both until the transport ends.

The conversation is shown by `agentscope.tui`: `launch_realtime_ui()` renders
the events of that stream — what you said, what the agent replied, the tools
it called — and hands what you confirm or type back to `agent.send()`. The
audio itself never reaches the view; the transport plays it.

## Prerequisites

- Python 3.11 or newer
- AgentScope with the realtime and TUI extras:

  ```bash
  pip install "agentscope[realtime,tui]"
  ```

  `sounddevice` needs PortAudio: it is bundled on macOS and Windows; on
  Debian/Ubuntu run `apt install libportaudio2`.
- A DashScope API key with access to a realtime model.

## Run

```bash
export DASHSCOPE_API_KEY=sk-...
python examples/realtime/local_mic.py
```

Speak, hear the reply, and speak over it to interrupt. `Ctrl-Q` quits.

The default model is `qwen-audio-3.0-realtime-plus`. Any card listed for the
credential works; the class is looked up from the card, the same way the
service layer does it:

```bash
REALTIME_MODEL=qwen3.5-omni-flash-realtime python examples/realtime/local_mic.py
```

An unknown name prints the available ones.

### Tools

The example registers `Bash`, `Edit`, `Write` and `Read`. When the model calls
one that needs permission, the composer is replaced by an approval card
showing the call and its arguments; pick an answer with the arrow keys and
`Enter`. Audio keeps flowing while the card is up, and the agent gives up on
an unanswered one after five minutes.

## Tips

- **Use headphones.** With speakers, the microphone picks up the agent's own
  voice; the provider's VAD then hears "the user" start speaking and cuts the
  reply off — the agent interrupts itself. `LocalAudioTransport` does no echo
  cancellation (a browser would), so headphones are the fix.
- **Do not route both directions through one Bluetooth headset.** macOS
  switches AirPods and similar devices to the hands-free profile when they
  are used for input and output at once, and PortAudio's separate
  input/output streams on that device often produce no sound. Pick devices
  by index from `python -m sounddevice`, e.g. the headset's microphone with
  the built-in speakers:

  ```bash
  REALTIME_INPUT_DEVICE=3 REALTIME_OUTPUT_DEVICE=2 python examples/realtime/local_mic.py
  ```

- **Silence is fine.** DashScope closes a session after about three minutes
  without a response. The example logs that it happened and keeps the
  microphone open; the next thing you say reconnects the model, with the
  transcript so far appended to the system prompt so the model keeps the
  thread.
- **Model limits are per turn, not per token.** `qwen3-omni-flash-realtime`
  remembers eight turns, `qwen-audio-3.0-realtime-*` fifty; older turns are
  dropped silently on the provider side. The cards under
  `agentscope/realtime/_dashscope/` list each model's limits.
- **Typed input** only works on models that accept text
  (`qwen-audio-3.0-realtime-*`); the Omni models are audio-in only, and the
  composer is disabled for them.
