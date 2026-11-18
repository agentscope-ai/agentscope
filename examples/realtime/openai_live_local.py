# -*- coding: utf-8 -*-
"""Talk to GPT-Live through the local microphone.

Set ``OPENAI_API_KEY`` and ``OPENAI_LIVE_BACKEND_MODEL`` before running.
Use headphones because the local transport does not cancel acoustic echo.
"""
import asyncio
import os

from agentscope.agent import Agent, RealtimeAgent
from agentscope.credential import OpenAICredential
from agentscope.model import OpenAIChatModel
from agentscope.realtime import LocalAudioTransport, OpenAILiveModel
from agentscope.state import AgentState
from agentscope.tui import launch_realtime_ui


async def main() -> None:
    """Run one local GPT-Live voice session."""
    api_key = os.environ.get("OPENAI_API_KEY")
    backend_model = os.environ.get("OPENAI_LIVE_BACKEND_MODEL")
    if not api_key:
        raise SystemExit("Set OPENAI_API_KEY first.")
    if not backend_model:
        raise SystemExit("Set OPENAI_LIVE_BACKEND_MODEL first.")

    credential = OpenAICredential(api_key=api_key)
    live_model = OpenAILiveModel(
        "gpt-live-1",
        credential,
    )
    backend = OpenAIChatModel(credential=credential, model=backend_model)

    def _create_backend_agent(state: AgentState) -> Agent:
        """Build the task agent used for one client delegation."""
        return Agent(
            name="Friday",
            system_prompt=(
                "Answer accurately and keep task results concise for speech."
            ),
            model=backend,
            state=state,
        )

    agent = RealtimeAgent(
        name="Friday",
        system_prompt=(
            "Speak briefly. Listen while the user is talking. "
            "Delegate questions that need deeper reasoning."
        ),
        model=live_model,
        delegation_agent_factory=_create_backend_agent,
    )
    transport = LocalAudioTransport(
        input_sample_rate=live_model.input_sample_rate,
        output_sample_rate=live_model.output_sample_rate,
        input_device=_device("REALTIME_INPUT_DEVICE"),
        output_device=_device("REALTIME_OUTPUT_DEVICE"),
    )

    async with agent, transport:
        await launch_realtime_ui(agent, transport)


def _device(env: str) -> int | str | None:
    """Return a sounddevice index or name from an environment variable."""
    value = os.environ.get(env)
    if value is None:
        return None
    return int(value) if value.isdigit() else value


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nbye")
