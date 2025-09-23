# -*- coding: utf-8 -*-
"""
Audio user agent.
"""
import base64
import threading
from typing import Optional, Any
import numpy as np
import sounddevice as sd
from agentscope.agent import AgentBase
from agentscope.message import Msg, AudioBlock, Base64Source, TextBlock
from agentscope._logging import logger


class MicrophoneRecorder:
    """Real-time microphone recorder using sounddevice"""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        dtype: np.dtype = np.int16,
    ) -> None:
        """Initialize microphone recorder

        Args:
            sample_rate: Sampling rate, default 16000Hz
            channels: Number of channels, default 1 (mono)
            dtype: Data type for audio samples, default np.int16
        """
        self.stream = None
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = dtype
        self.frames: list[np.ndarray] = []
        self._is_recording = False
        self._record_thread: Optional[threading.Thread] = None

    def _audio_callback(
        self,
        indata: np.ndarray,
        _frames: int,
        _time: Any,
        status: sd.CallbackFlags,
    ) -> None:
        """Callback function for audio stream

        Args:
            indata: Input audio data
            _frames: Number of frames (unused)
            _time: Time info (unused)
            status: Stream status
        """
        if status:
            logger.info("Stream callback status: %s", status)
        self.frames.append(indata.copy())

    def start(self) -> None:
        """Start recording"""
        if self._is_recording:
            logger.info("Recording is already in progress")
            return

        try:
            self.frames.clear()
            self._is_recording = True
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=self.dtype,
                callback=self._audio_callback,
            )
            self.stream.start()
            logger.info("Microphone recording started...")
        except Exception as e:
            self._is_recording = False
            logger.info("Failed to start microphone: %s", e)
            raise

    def stop(self) -> Optional[bytes]:
        """Stop recording and return audio data

        Returns:
            bytes | None: Recorded audio data, None if recording failed
        """
        if not self._is_recording:
            return None

        try:
            self._is_recording = False
            self.stream.stop()
            self.stream.close()

            if not self.frames:
                return None

            # Concatenate all frames and convert to bytes
            audio_data = np.concatenate(self.frames, axis=0)
            return audio_data.tobytes()

        except Exception as e:
            logger.info("Error stopping recording: %s", e)
            return None
        finally:
            logger.info("Microphone recording stopped")


class AudioUserAgent(AgentBase):
    """Agent for handling user audio input"""

    CHUNK_SIZE = 3200

    def __init__(self, name: str) -> None:
        """Initialize audio user agent

        Args:
            name: Agent name
        """
        super().__init__()
        self.name = name

    async def handle_interrupt(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Msg:
        """The post-processing logic when the reply is interrupted by the
        user or something else."""

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """Receive the given message(s) without generating a reply.

        Args:
            msg (`Msg | list[Msg] | None`):
                The message(s) to be observed.
        """

    def _create_audio_blocks(self, audio_data: bytes) -> list[AudioBlock]:
        """Convert audio data to AudioBlock list

        Args:
            audio_data: Raw audio data

        Returns:
            list[AudioBlock]: List of AudioBlocks containing chunked audio data
        """
        return [
            AudioBlock(
                type="audio",
                source=Base64Source(
                    type="base64",
                    data=base64.b64encode(chunk).decode("ascii"),
                    media_type="audio/wav",
                ),
            )
            for chunk in (
                audio_data[i : i + self.CHUNK_SIZE]
                for i in range(0, len(audio_data), self.CHUNK_SIZE)
            )
            if chunk
        ]

    async def reply(self) -> Msg:
        """Process user input and return message. User can either:
        1. Press Enter to start voice recording
        2. Type text directly to send a text message
        3. Type text during recording to cancel and send text instead

        Returns:
            Msg: User message containing either audio or text content
        """
        while True:
            recorder = MicrophoneRecorder(sample_rate=16000)

            user_input = input(
                "Ready to interact. Press:\n"
                "- Enter to start VOICE recording\n"
                "- Or type your message for TEXT input\n",
            )

            # If user pressed Enter, start voice recording
            if user_input == "":
                try:
                    recorder.start()
                    recording_input = input(
                        "Recording... Press Enter to stop,"
                        " or type message to send text.\n",
                    )

                    # Stop recording regardless of input
                    recorded_audio = recorder.stop()

                    # If user typed text during recording,
                    # send text message instead
                    if recording_input != "":
                        return Msg(
                            name=self.name,
                            content=[
                                TextBlock(
                                    type="text",
                                    text=recording_input,
                                ),
                            ],
                            role="user",
                        )

                    # Process recorded audio
                    if not recorded_audio:
                        logger.info(
                            "No valid audio recorded."
                            " Please restart the conversation.",
                        )
                        continue

                    logger.info(
                        "Successfully recorded audio: %d bytes",
                        len(recorded_audio),
                    )

                    return Msg(
                        name=self.name,
                        content=self._create_audio_blocks(recorded_audio),
                        role="user",
                    )

                except Exception as e:
                    logger.info("Error during recording: %s", e)
                    continue

            # If user typed text directly, send text message
            else:
                return Msg(
                    name=self.name,
                    content=[
                        TextBlock(
                            type="text",
                            text=user_input,
                        ),
                    ],
                    role="user",
                )
