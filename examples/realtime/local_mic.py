# -*- coding: utf-8 -*-
"""Talk to a Qwen-Omni realtime model through the local microphone.

    export DASHSCOPE_API_KEY=sk-...
    python examples/realtime/local_mic.py

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


async def main() -> None:
    """Run one voice session until interrupted."""
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise SystemExit("Set DASHSCOPE_API_KEY first.")

    model = DashScopeRealtimeModel(
        os.environ.get("REALTIME_MODEL", "qwen3-omni-flash-realtime"),
        credential=DashScopeCredential(api_key=api_key),
    )
    agent = RealtimeAgent(
        name="Friday",
        sys_prompt="你是一个中文语音助手，回答尽量简短。",
        model=model,
        transport=LocalAudioTransport(
            input_sample_rate=model.input_sample_rate,
            output_sample_rate=model.output_sample_rate,
        ),
    )

    print(f"[{model.model_name}] listening... (Ctrl-C to quit)")
    async with agent:
        async for event in agent.events():
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


def _ms(seconds: float | None) -> str:
    """Format a latency for the console."""
    return "n/a" if seconds is None else f"{seconds * 1000:.0f}ms"


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nbye")
