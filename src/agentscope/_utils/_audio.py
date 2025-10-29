# -*- coding: utf-8 -*-
"""Audio recording and processing utilities."""
import base64
from typing import Any, List
import numpy as np
import sounddevice as sd

from .._logging import logger
from ..message import AudioBlock, Base64Source


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
        self.frames: List[np.ndarray] = []
        self._is_recording = False

    def _audio_callback(
        self,
        indata: np.ndarray,
        _frames: int,
        _time: Any,
        status: sd.CallbackFlags,
    ) -> None:
        """Callback function for audio stream"""
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

    def stop(self) -> bytes | None:
        """Stop recording and return audio data"""
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


class AudioProcessor:
    """Audio data processing utilities"""

    @staticmethod
    def create_audio_blocks(
        audio_data: bytes,
        chunk_size: int = 3200,
        media_type: str = "audio/wav",
    ) -> List[AudioBlock]:
        """Convert audio data to AudioBlock list

        Args:
            audio_data: Raw audio data
            chunk_size: Size of each chunk in bytes

        Returns:
            List[AudioBlock]: List of AudioBlocks containing chunked audio data
        """
        return [
            AudioBlock(
                type="audio",
                source=Base64Source(
                    type="base64",
                    data=base64.b64encode(chunk).decode("ascii"),
                    media_type=media_type,
                ),
            )
            for chunk in (
                audio_data[i : i + chunk_size]
                for i in range(0, len(audio_data), chunk_size)
            )
            if chunk
        ]

    @staticmethod
    def validate_audio_params(
        sample_rate: int,
        channels: int,
        chunk_size: int = 3200,
    ) -> None:
        """Validate audio parameters

        Args:
            sample_rate: Audio sampling rate
            channels: Number of audio channels
            chunk_size: Size of audio chunks

        Raises:
            ValueError: If parameters are invalid
        """
        if sample_rate <= 0:
            raise ValueError("Sample rate must be positive")
        if channels not in [1, 2]:
            raise ValueError("Channels must be 1 (mono) or 2 (stereo)")
        if chunk_size <= 0:
            raise ValueError("Chunk size must be positive")
