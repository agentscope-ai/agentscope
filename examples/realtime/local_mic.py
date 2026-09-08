# -*- coding: utf-8 -*-
"""Talk to a Qwen-Omni realtime model through the local microphone.

    export DASHSCOPE_API_KEY=sk-...
    python examples/realtime/local_mic.py

Pick devices by index from ``python -m sounddevice`` when the defaults
are wrong, e.g. a Bluetooth headset used for both directions:

    REALTIME_INPUT_DEVICE=3 REALTIME_OUTPUT_DEVICE=2 python ...

Speak, hear the reply, and speak over it to interrupt. Ctrl-C to quit.
"""
import asyncio
import os

from agentscope.credential import DashScopeCredential
from agentscope.event import (
    ReplyEndEvent,
    ReplyStartEvent,
    TextBlockDeltaEvent,
    UserInputAudioStartEvent,
    UserInputTranscriptionEvent,
)
from agentscope.realtime import (
    DashScopeRealtimeModel,
    LocalAudioTransport,
    RealtimeAgent,
)
from agentscope.tool import Toolkit, Bash, Edit, Write, Read


async def main() -> None:
    """Run one voice session until interrupted."""
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise SystemExit("Set DASHSCOPE_API_KEY first.")

    model = DashScopeRealtimeModel(
        os.environ.get("REALTIME_MODEL", "qwen3-omni-flash-realtime"),
        credential=DashScopeCredential(api_key=api_key),
    )

    toolkit = Toolkit(
        tools=[
            Bash(),
            Edit(), 
            Write(), 
            Read(),
        ]
    )

    agent = RealtimeAgent(
        name="Friday",
        sys_prompt="你是一个中文语音助手，回答尽量简短。",
        model=model,
        toolkit=toolkit,
    )
    transport = LocalAudioTransport(
        input_sample_rate=model.input_sample_rate,
        output_sample_rate=model.output_sample_rate,
        input_device=_device("REALTIME_INPUT_DEVICE"),
        output_device=_device("REALTIME_OUTPUT_DEVICE"),
    )

    print(f"[{model.model_name}] listening... (Ctrl-C to quit)")
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
                case ReplyEndEvent():
                    m = agent.last_turn_metrics
                    print(
                        f"\n  ({event.finished_reason}"
                        f" | ttfb={_ms(m.backend_ttfb)}"
                        f" | e2e={_ms(m.e2e_latency)})",
                    )
                    tail = agent.state.context[-1]
                    print(
                        f"  context[-1] = {tail.role}: "
                        f"{tail.get_text_content()!r}",
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
