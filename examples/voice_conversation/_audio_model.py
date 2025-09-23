# -*- coding: utf-8 -*-
"""Audio model"""
# https://help.aliyun.com/zh/model-studio/realtime
import asyncio
import sys
import threading

from dashscope.audio.qwen_omni import OmniRealtimeCallback

from agentscope.message import TextBlock, AudioBlock, Base64Source
from agentscope._logging import logger


class OmniCallback(OmniRealtimeCallback):
    """Callback handler for Omni realtime conversation"""

    def __init__(self) -> None:
        super().__init__()
        self.complete_event: threading.Event = threading.Event()
        self.conversation = None
        self.response_text = ""
        self.response_audio = ""

        self.chunks = []

        async def _noop(_content_blocks: list, _is_final: bool) -> None:
            pass

        self.on_response_chunk = _noop

    def wait_for_complete(self) -> None:
        """Wait for response completion"""
        self.complete_event.wait()
        self.complete_event = threading.Event()

    def on_open(self) -> None:
        """Handle connection open"""
        logger.info("Connection opened, initializing audio...")

    def on_close(self, close_status_code: int, close_msg: str) -> None:
        """Handle connection close"""
        logger.info("Connection closed: %s, %s", close_status_code, close_msg)
        sys.exit(0)

    def on_event(self, response: dict) -> None:
        """Handle various event types"""
        try:
            event_type = response["type"]
            # print(response)
            if event_type == "session.created":
                logger.info("Session started: %s", response["session"]["id"])
            elif (
                event_type
                == "conversation.item.input_audio_transcription.completed"
            ):
                logger.info("Question: %s", response["transcript"])
            elif event_type == "response.audio_transcript.delta":
                text = response["delta"]
                # print(f"Got LLM response delta: {text}")
                self.response_text += text
                content_blocks = [
                    TextBlock(type="text", text=self.response_text),
                ]
                self.chunks.append(
                    (content_blocks, False),
                )
                callback = self.on_response_chunk
                if callback and callable(callback):
                    asyncio.run(callback(content_blocks, False))
            elif event_type == "response.audio.delta":
                audio_data = response["delta"]
                self.response_audio += audio_data
                content_blocks = [
                    TextBlock(type="text", text=self.response_text),
                    AudioBlock(
                        type="audio",
                        source=Base64Source(
                            type="base64",
                            media_type="audio/wav",
                            data=self.response_audio,
                        ),
                    ),
                ]
                self.chunks.append((content_blocks, False))
                callback = self.on_response_chunk
                if callback and callable(callback):
                    asyncio.run(callback(content_blocks, False))
            elif event_type == "response.done":
                logger.info("======RESPONSE DONE======")
                logger.info(
                    "[Metric] response: %s, first text delay: %s, "
                    "first audio delay: %s",
                    self.conversation.get_last_response_id(),
                    self.conversation.get_last_first_text_delay(),
                    self.conversation.get_last_first_audio_delay(),
                )

                # print(self.response_audio)
                content_blocks = [
                    TextBlock(
                        type="text",
                        text=self.response_text,
                    ),
                    AudioBlock(
                        type="audio",
                        source=Base64Source(
                            type="base64",
                            media_type="audio/wav",
                            data=self.response_audio,
                        ),
                    ),
                ]
                self.chunks.append((content_blocks, True))
                callback = self.on_response_chunk
                if callback and callable(callback):
                    asyncio.run(callback(content_blocks, True))

                self.complete_event.set()
        except Exception as e:
            logger.info("[Error] %s", e)
