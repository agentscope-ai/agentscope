# -*- coding: utf-8 -*-
"""Realtime audio assistant agent"""
# https://help.aliyun.com/zh/model-studio/realtime

from typing import Any
from dashscope.audio.qwen_omni import (
    OmniRealtimeConversation,
    MultiModality,
    AudioFormat,
)

from agentscope.agent import AgentBase
from agentscope.message import Msg
from agentscope._logging import logger
from examples.voice_conversation._customized_omni_callback import (
    CustomizedOmniCallback,
)


class AudioAssistantAgent(AgentBase):
    """Agent for handling audio conversations"""

    def __init__(
        self,
        name: str,
        sys_prompt: str = "You are a helpful assistant.",
        api_key: str | None = None,
        voice: str = "Cherry",
        model: str = "qwen3-omni-flash-realtime",
    ) -> None:
        """Initialize audio assistant agent

        Args:
            name: Agent name
            api_key: API key for authentication
            voice: Voice model to use
            model: Language model to use
        """
        super().__init__()
        self.name = name
        self.callback = CustomizedOmniCallback()

        # Initialize DashScope
        import dashscope

        dashscope.api_key = api_key

        # Initialize conversation
        self.conversation = OmniRealtimeConversation(
            model=model,
            callback=self.callback,
        )
        self.callback.conversation = self.conversation

        # Connect and set session parameters
        self.conversation.connect()
        self.conversation.update_session(
            output_modalities=[MultiModality.AUDIO, MultiModality.TEXT],
            voice=voice,
            input_audio_format=AudioFormat.PCM_16000HZ_MONO_16BIT,
            output_audio_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
            enable_input_audio_transcription=True,
            input_audio_transcription_model="gummy-realtime-v1",
            enable_turn_detection=False,
            instructions=sys_prompt,
        )

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """Process observed messages"""

    async def handle_interrupt(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Msg:
        """The post-processing logic when the reply is interrupted by the
        user or something else."""

    async def reply(self, msg: Msg | list[Msg] | None) -> Msg:
        """Generate reply for input message

        Args:
            msg: Input message

        Returns:
            Msg: Response message with text and audio
        """
        # Reset response content
        self.callback.response_text = ""
        self.callback.response_audio = ""
        self.callback.chunks = []

        response_msg = Msg(self.name, [], "assistant")

        async def handle_response_chunk(
            content_blocks: list[dict],
            is_final: bool,
        ) -> None:
            response_msg.content = content_blocks
            await self.print(response_msg, is_final)

        self.callback.on_response_chunk = handle_response_chunk

        if msg.get_text_content():
            self.conversation.commit()
            self.conversation.create_response(
                instructions=msg.get_text_content(),
                output_modalities=[MultiModality.AUDIO, MultiModality.TEXT],
            )

        else:
            # Process audio input
            for audio_block in msg.get_content_blocks(block_type="audio"):
                self.conversation.append_audio(audio_block["source"]["data"])

            logger.info("Recording sent, waiting for response...")
            self.conversation.commit()
            self.conversation.create_response()

        # Wait for response completion
        self.callback.wait_for_complete()

        self.callback.on_response_chunk = None

        # Return response message
        return response_msg
