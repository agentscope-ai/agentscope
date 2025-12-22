# -*- coding: utf-8 -*-
"""Real-time voice user input based on MsgStream.

Contains:
- AudioCapture: Microphone audio capture
- RealtimeVoiceInput: Implementation of UserInputBase interface
"""

import asyncio
import base64
import threading
from typing import Any, Type, Optional, Callable, List, Dict

from pydantic import BaseModel

from agentscope.agent._user_input import UserInputBase, UserInputData
from agentscope.message import AudioBlock, Base64Source
from agentscope._logging import logger

from ._msg_stream import (
    MsgStream,
    MsgEvent,
    create_audio_msg,
    create_event_msg,
    get_audio_from_msg,
    get_event_from_msg,
)

# Lazy import for audio libraries
try:
    import pyaudio

    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    pyaudio = None


class AudioCapture:
    """Real-time audio capture based on MsgStream.

    Continuously captures audio data from microphone and pushes it to
    MsgStream in Msg format.
    """

    DEFAULT_SAMPLE_RATE = 16000
    DEFAULT_CHANNELS = 1
    DEFAULT_CHUNK_SIZE = 3200  # 200ms
    DEFAULT_FORMAT = pyaudio.paInt16 if PYAUDIO_AVAILABLE else None

    def __init__(
        self,
        msg_stream: Optional[MsgStream] = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        device_index: Optional[int] = None,
        source_name: str = "user",
    ) -> None:
        """Initialize the audio capture.

        Args:
            msg_stream (`Optional[MsgStream]`, defaults to `None`):
                The message stream to push audio data to. Can be set later
                via set_msg_stream().
            sample_rate (`int`, defaults to `16000`):
                Audio sample rate in Hz.
            channels (`int`, defaults to `1`):
                Number of audio channels (1 for mono, 2 for stereo).
            chunk_size (`int`, defaults to `3200`):
                Number of frames per buffer (3200 = 200ms at 16kHz).
            device_index (`Optional[int]`, defaults to `None`):
                Index of the audio input device. None uses default device.
            source_name (`str`, defaults to `"user"`):
                Name identifier for the audio source in messages.

        Raises:
            `ImportError`:
                If pyaudio is not installed.
        """
        if not PYAUDIO_AVAILABLE:
            raise ImportError("pyaudio is not installed")

        self._msg_stream = msg_stream  # Can be set later via set_msg_stream
        self._sample_rate = sample_rate
        self._channels = channels
        self._chunk_size = chunk_size
        self._device_index = device_index
        self._source_name = source_name

        self._pyaudio: Optional[pyaudio.PyAudio] = None
        self._input_stream: Optional[Any] = None
        self._capture_thread: Optional[threading.Thread] = None
        self._running = False
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._on_audio_callback: Optional[Callable[[bytes], None]] = None

    def set_on_audio_callback(self, callback: Callable[[bytes], None]) -> None:
        """Set callback for audio data.

        Args:
            callback (`Callable[[bytes], None]`):
                Function to call with each chunk of audio data.
        """
        self._on_audio_callback = callback

    def set_msg_stream(self, msg_stream: MsgStream) -> None:
        """Set the message stream (called by VoiceMsgHub).

        Args:
            msg_stream (`MsgStream`):
                The message stream instance.

        Raises:
            `RuntimeError`:
                If called while capture is running.
        """
        if self._running:
            raise RuntimeError(
                "Cannot set msg_stream while AudioCapture is running",
            )
        self._msg_stream = msg_stream

    async def start(self) -> None:
        """Start audio capture.

        Initializes PyAudio, opens input stream, and starts capture thread.

        Raises:
            `RuntimeError`:
                If msg_stream is not set.
        """
        if self._running:
            return

        if self._msg_stream is None:
            raise RuntimeError(
                "AudioCapture requires msg_stream. "
                "Either pass it to constructor or call set_msg_stream().",
            )

        self._event_loop = asyncio.get_running_loop()
        self._running = True

        self._pyaudio = pyaudio.PyAudio()
        self._input_stream = self._pyaudio.open(
            format=self.DEFAULT_FORMAT,
            channels=self._channels,
            rate=self._sample_rate,
            input=True,
            input_device_index=self._device_index,
            frames_per_buffer=self._chunk_size,
        )

        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
        )
        self._capture_thread.start()

        logger.info(
            "AudioCapture started: %dHz, %dch",
            self._sample_rate,
            self._channels,
        )

    def _capture_loop(self) -> None:
        """Main capture loop running in separate thread.

        Continuously reads audio from input stream and pushes to msg_stream.
        """
        while self._running and self._input_stream:
            try:
                audio_data = self._input_stream.read(
                    self._chunk_size,
                    exception_on_overflow=False,
                )

                if self._on_audio_callback:
                    try:
                        self._on_audio_callback(audio_data)
                    except Exception as e:
                        logger.error("Audio callback error: %s", e)

                # Create audio Msg and push to stream
                msg = create_audio_msg(
                    name=self._source_name,
                    audio_data=audio_data,
                    sample_rate=self._sample_rate,
                    role="user",
                    is_partial=True,
                )

                if self._event_loop and not self._event_loop.is_closed():
                    asyncio.run_coroutine_threadsafe(
                        self._msg_stream.push(msg),
                        self._event_loop,
                    )

            except Exception as e:
                if self._running:
                    logger.error("Capture loop error: %s", e)
                break

    async def stop(self) -> None:
        """Stop audio capture.

        Stops capture thread, closes audio stream, and pushes SPEECH_END event.
        """
        if not self._running:
            return

        self._running = False

        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=1.0)

        if self._input_stream:
            try:
                self._input_stream.stop_stream()
                self._input_stream.close()
            except Exception:
                pass
            self._input_stream = None

        if self._pyaudio:
            try:
                self._pyaudio.terminate()
            except Exception:
                pass
            self._pyaudio = None

        # Push SPEECH_END event
        await self._msg_stream.push(
            create_event_msg(
                name=self._source_name,
                event=MsgEvent.SPEECH_END,
                role="user",
            ),
        )

        logger.info("AudioCapture stopped")

    @property
    def is_running(self) -> bool:
        """Check if capture is currently running.

        Returns:
            `bool`:
                True if capturing audio, False otherwise.
        """
        return self._running

    def list_devices(self) -> List[Dict[str, int | str]]:
        """List available audio input devices.

        Returns:
            `List[Dict[str, int | str]]`:
                List of device info dictionaries with keys:
                - 'index': Device index
                - 'name': Device name
                - 'channels': Max input channels
                - 'sample_rate': Default sample rate
        """
        if not PYAUDIO_AVAILABLE:
            return []

        pa = pyaudio.PyAudio()
        devices = []
        try:
            for i in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(i)
                if info["maxInputChannels"] > 0:
                    devices.append(
                        {
                            "index": i,
                            "name": info["name"],
                            "channels": info["maxInputChannels"],
                            "sample_rate": int(info["defaultSampleRate"]),
                        },
                    )
        finally:
            pa.terminate()
        return devices


class RealtimeVoiceInput(UserInputBase):
    """Real-time voice input based on MsgStream.

    Implements UserInputBase interface for capturing realtime voice from
    microphone.
    """

    def __init__(
        self,
        msg_stream: Optional[MsgStream] = None,
        device_index: Optional[int] = None,
        timeout: float = 30.0,
        source_name: str = "user",
    ) -> None:
        """Initialize the real-time voice input.

        Args:
            msg_stream (`Optional[MsgStream]`, defaults to `None`):
                The message stream for communication. Can be set later via
                set_msg_stream() or VoiceMsgHub.
            device_index (`Optional[int]`, defaults to `None`):
                Index of the audio input device. None uses default device.
            timeout (`float`, defaults to `30.0`):
                Maximum time to wait for voice input in seconds.
            source_name (`str`, defaults to `"user"`):
                Name identifier for the voice source in messages.
        """
        self._msg_stream = msg_stream  # Can be set later via set_msg_stream
        self._source_name = source_name
        self._device_index = device_index
        self._capture: Optional[AudioCapture] = None
        self._timeout = timeout
        self._running = False

    def set_msg_stream(self, msg_stream: MsgStream) -> None:
        """Set the message stream (called by VoiceMsgHub).

        Args:
            msg_stream (`MsgStream`):
                The message stream instance.

        Raises:
            `RuntimeError`:
                If called while voice input is running.
        """
        if self._running:
            raise RuntimeError(
                "Cannot set msg_stream while RealtimeVoiceInput is running",
            )
        self._msg_stream = msg_stream
        if self._capture is not None:
            self._capture.set_msg_stream(msg_stream)

    async def start(self) -> None:
        """Start voice capture.

        Creates AudioCapture instance if needed and starts capturing.

        Raises:
            `RuntimeError`:
                If msg_stream is not set.
        """
        if not self._running:
            if self._msg_stream is None:
                raise RuntimeError(
                    "RealtimeVoiceInput requires msg_stream. "
                    "Either pass it to constructor or use VoiceMsgHub.",
                )
            # Lazy create AudioCapture
            if self._capture is None:
                self._capture = AudioCapture(
                    msg_stream=self._msg_stream,
                    device_index=self._device_index,
                    source_name=self._source_name,
                )
            await self._capture.start()
            self._running = True
            logger.info("RealtimeVoiceInput started")

    async def stop(self) -> None:
        """Stop voice capture.

        Stops the underlying AudioCapture instance.
        """
        if self._running and self._capture is not None:
            await self._capture.stop()
            self._running = False
            logger.info("RealtimeVoiceInput stopped")

    async def __call__(
        self,
        agent_id: str,
        agent_name: str,
        *args: Any,
        structured_model: Optional[Type[BaseModel]] = None,
        **kwargs: Any,
    ) -> UserInputData:
        """Get user voice input (for UserAgent, waits for SPEECH_END).

        Implements UserInputBase interface. Collects audio data until
        SPEECH_END event is received.

        Args:
            agent_id (`str`):
                The ID of the agent requesting input.
            agent_name (`str`):
                The name of the agent requesting input.
            *args (`Any`):
                Additional positional arguments (not used).
            structured_model (`Optional[Type[BaseModel]]`, defaults to `None`):
                Pydantic model for structured input (not used for voice).
            **kwargs (`Any`):
                Additional keyword arguments (not used).

        Returns:
            `UserInputData`:
                The user input data containing audio block.
        """
        if not self._running:
            await self.start()

        audio_buffer = bytearray()
        got_audio = False

        logger.info("Waiting for voice input (timeout=%ss)...", self._timeout)

        try:
            async for msg in self._msg_stream.subscribe(
                subscriber_id=f"voice_input_{agent_id}",
                filter_names=[self._source_name],
            ):
                if msg is None:
                    break

                event = get_event_from_msg(msg)
                audio_data = get_audio_from_msg(msg)

                if audio_data:
                    audio_buffer.extend(audio_data)
                    got_audio = True

                elif event == MsgEvent.SPEECH_END:
                    if got_audio:
                        logger.info(
                            "Got voice input: %s bytes",
                            len(audio_buffer),
                        )
                        break

        except asyncio.TimeoutError:
            logger.warning("Voice input timeout")

        finally:
            await self._msg_stream.unsubscribe(f"voice_input_{agent_id}")

        blocks_input = []
        if audio_buffer:
            blocks_input.append(
                AudioBlock(
                    type="audio",
                    source=Base64Source(
                        type="base64",
                        media_type="audio/pcm;rate=16000",
                        data=base64.b64encode(bytes(audio_buffer)).decode(
                            "ascii",
                        ),
                    ),
                ),
            )

        return UserInputData(blocks_input=blocks_input)

    def list_devices(self) -> List[Dict[str, int | str]]:
        """List available audio input devices.

        Returns:
            `List[Dict[str, int | str]]`:
                List of device info dictionaries. See
                AudioCapture.list_devices()
                for details.
        """
        if self._capture is None:
            # Create temporary instance for device listing
            temp_capture = AudioCapture()
            return temp_capture.list_devices()
        return self._capture.list_devices()

    @property
    def is_running(self) -> bool:
        """Check if voice input is currently running.

        Returns:
            `bool`:
                True if actively capturing voice, False otherwise.
        """
        return self._running
