# -*- coding: utf-8 -*-
"""Talk to a DashScope realtime model through the local microphone, with
tools the model may call and a terminal UI that shows the conversation
and asks for permission where it is needed.

    export DASHSCOPE_API_KEY=sk-...
    python examples/realtime/local_mic.py

Any of the DashScope realtime models works; the class is looked up from
the model card, the same way the service layer does it:

    REALTIME_MODEL=qwen3.5-omni-flash-realtime python ...

Pick devices by index from ``python -m sounddevice`` when the defaults
are wrong, e.g. a Bluetooth headset used for both directions:

    REALTIME_INPUT_DEVICE=3 REALTIME_OUTPUT_DEVICE=2 python ...

Speak, hear the reply, and speak over it to interrupt. Ctrl-Q to quit.
"""
import asyncio
import os

from agentscope.agent import RealtimeAgent
from agentscope.credential import DashScopeCredential
from agentscope.realtime import LocalAudioTransport
from agentscope.tool import Bash, Edit, Read, Toolkit, Write
from agentscope.tui import launch_realtime_ui


async def main() -> None:
    """Run one voice session until the UI is closed."""
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
        system_prompt="你是一个中文语音助手，回答尽量简短。",
        model=model,
        toolkit=Toolkit(tools=[Bash(), Edit(), Write(), Read()]),
    )
    transport = LocalAudioTransport(
        input_sample_rate=model.input_sample_rate,
        output_sample_rate=model.output_sample_rate,
        input_device=_device("REALTIME_INPUT_DEVICE"),
        output_device=_device("REALTIME_OUTPUT_DEVICE"),
    )

    # The agent owns the model session, we own the transport, and the UI
    # borrows both: it renders the events of one reply_stream() call and
    # hands what you type or confirm back through agent.send().
    async with agent, transport:
        await launch_realtime_ui(agent, transport)


def _device(env: str) -> int | str | None:
    """A sounddevice index or name from the environment, if given."""
    value = os.environ.get(env)
    if value is None:
        return None
    return int(value) if value.isdigit() else value


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nbye")
