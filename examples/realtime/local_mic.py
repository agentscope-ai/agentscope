# -*- coding: utf-8 -*-
"""Talk to a DashScope realtime model through the local microphone, with
tools the model may call and a terminal prompt for the ones that need
your permission.

    export DASHSCOPE_API_KEY=sk-...
    python examples/realtime/local_mic.py

Any of the DashScope realtime models works; the class is looked up from
the model card, the same way the service layer does it:

    REALTIME_MODEL=qwen3.5-omni-flash-realtime python ...

Pick devices by index from ``python -m sounddevice`` when the defaults
are wrong, e.g. a Bluetooth headset used for both directions:

    REALTIME_INPUT_DEVICE=3 REALTIME_OUTPUT_DEVICE=2 python ...

Speak, hear the reply, and speak over it to interrupt. Ctrl-C to quit.
"""
import asyncio
import os

from agentscope.agent import RealtimeAgent
from agentscope.credential import DashScopeCredential
from agentscope.event import (
    ConfirmResult,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    UserConfirmResultEvent,
    UserInputAudioStartEvent,
    UserInputTranscriptionEvent,
)
from agentscope.realtime import LocalAudioTransport
from agentscope.tool import Bash, Edit, Read, Toolkit, Write


async def main() -> None:
    """Run one voice session until interrupted."""
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise SystemExit("Set DASHSCOPE_API_KEY first.")
    credential = DashScopeCredential(api_key=api_key)

    # Resolve the model class from its card rather than hard-coding it:
    # a credential can serve several realtime APIs, and each card carries
    # the tag of the class that produced it.
    name = os.environ.get("REALTIME_MODEL", "qwen-audio-3.0-realtime-plus")
    cards = {c.name: c for c in credential.list_realtime_models()}
    classes = {c.type: c for c in credential.get_realtime_model_classes()}
    if name not in cards:
        raise SystemExit(f"Unknown model {name!r}; try: {sorted(cards)}")
    card = cards[name]
    model = classes[card.model_type](name, credential, model_card=card)

    agent = RealtimeAgent(
        name="Friday",
        sys_prompt="你是一个中文语音助手，回答尽量简短。",
        model=model,
        toolkit=Toolkit(tools=[Bash(), Edit(), Write(), Read()]),
    )
    transport = LocalAudioTransport(
        input_sample_rate=model.input_sample_rate,
        output_sample_rate=model.output_sample_rate,
        input_device=_device("REALTIME_INPUT_DEVICE"),
        output_device=_device("REALTIME_OUTPUT_DEVICE"),
    )

    print(f"[{name}] listening... (Ctrl-C to quit)")
    # The agent owns the model session, we own the transport, and one
    # run() borrows both until the transport ends.
    async with agent, transport:
        async for event in agent.run(transport):
            match event:
                case UserInputAudioStartEvent():
                    print("\n[you] ...", end="", flush=True)
                case UserInputTranscriptionEvent():
                    print(f"\r[you] {event.transcript}")
                case ReplyStartEvent():
                    print(f"[{agent.name}] ", end="", flush=True)
                case TextBlockDeltaEvent():
                    print(event.delta, end="", flush=True)
                case RequireUserConfirmEvent():
                    await confirm(agent, event)
                case ToolResultStartEvent():
                    print(f"\n  [tool] {event.tool_call_name} running...")
                case ToolResultEndEvent():
                    print(f"  [tool] {event.state}")
                case ReplyEndEvent():
                    m = agent.last_turn_metrics
                    print(
                        f"\n  ({event.finished_reason}"
                        f" | ttfb={_ms(m.backend_ttfb)}"
                        f" | e2e={_ms(m.e2e_latency)})",
                    )
                    if agent.state.context:
                        tail = agent.state.context[-1]
                        print(
                            f"  context[-1] = {tail.role}: "
                            f"{tail.get_text_content()!r}",
                        )


async def confirm(
    agent: RealtimeAgent,
    event: RequireUserConfirmEvent,
) -> None:
    """Ask on the terminal whether each pending tool call may run.

    The prompt runs in a thread so the audio pumps keep going while we
    wait; the agent itself gives up after five minutes.
    """
    loop = asyncio.get_running_loop()
    results = []
    for call in event.tool_calls:
        print(f"\n  [permission] {call.name}({call.input})")
        answer = await loop.run_in_executor(None, input, "  allow? [y/N] ")
        results.append(
            ConfirmResult(
                tool_call=call,
                confirmed=answer.strip().lower() in ("y", "yes"),
            ),
        )
    await agent.send(
        UserConfirmResultEvent(
            reply_id=event.reply_id,
            confirm_results=results,
        ),
    )


def _device(env: str) -> int | str | None:
    """A sounddevice index or name from the environment, if given."""
    value = os.environ.get(env)
    if value is None:
        return None
    return int(value) if value.isdigit() else value


def _ms(seconds: float | None) -> str:
    """Format a latency for the console."""
    return "n/a" if seconds is None else f"{seconds * 1000:.0f}ms"


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nbye")
