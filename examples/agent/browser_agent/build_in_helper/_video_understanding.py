# -*- coding: utf-8 -*-
"""Standalone video understanding skill for the browser agent."""
# flake8: noqa: E501
# pylint: disable=W0212
# pylint: disable=too-many-lines
# pylint: disable=C0301
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from base64 import b64encode
from pathlib import Path
from typing import Any, List, Optional

from agentscope.message import (
    Base64Source,
    ImageBlock,
    Msg,
    TextBlock,
)
from agentscope.tool import ToolResponse


async def video_understanding(
    browser_agent: Any,
    video_path: str,
    task: str,
) -> ToolResponse:
    """
    Perform video understanding on the provided video file.

    Args:
        video_path (str): The path to the video file to analyze.
        task (str): The specific task or question to solve about
        the video (e.g., summary, object detection, activity recognition,
        or answering a question about the video's content).

    Returns:
        ToolResponse: A structured response containing the answer
        to the specified task based on the video content.
    """

    workdir = _prepare_workdir(browser_agent)
    try:
        frames_dir = os.path.join(workdir, "frames")
        frames = extract_frames(video_path, frames_dir)
    except Exception as exc:
        return _error_response(f"Failed to extract frames: {exc}")

    audio_path = os.path.join(
        workdir,
        f"audio_{getattr(browser_agent, 'iter_n', 0)}.wav",
    )
    try:
        extract_audio(video_path, audio_path)
    except Exception as exc:
        return _error_response(f"Failed to extract audio: {exc}")

    try:
        transcript = audio2text(audio_path)
    except Exception as exc:
        return _error_response(f"Failed to transcribe audio: {exc}")

    sys_prompt = (
        "You are a web video analysis expert. "
        "Given the following video frames and audio transcript, "
        "analyze the content and provide a solution to the task. "
        'Return ONLY a JSON object: {"answer": <your answer>}'
    )

    content_blocks = _build_multimodal_blocks(frames, transcript, task)

    prompt = await browser_agent.formatter.format(
        msgs=[
            Msg("system", sys_prompt, role="system"),
            Msg("user", content_blocks, role="user"),
        ],
    )

    res = await browser_agent.model(prompt)
    if browser_agent.model.stream:
        async for chunk in res:
            model_text = chunk.content[0]["text"]
    else:
        model_text = res.content[0]["text"]

    try:
        if "```json" in model_text:
            model_text = model_text.replace("```json", "").replace(
                "```",
                "",
            )
        answer_info = json.loads(model_text)
        answer = answer_info.get("answer", "")
    except Exception:  # pylint: disable=broad-except
        return _error_response("Failed to parse answer from model output.")

    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=(
                    "Video analysis completed.\n" f"Task solution: {answer}"
                ),
            ),
        ],
    )


def audio2text(audio_path: str) -> str:
    """Convert audio to text using DashScope ASR."""

    try:  # Local import to avoid hard dependency when unused.
        from dashscope.audio.asr import Recognition, RecognitionCallback
    except ImportError as exc:
        raise RuntimeError(
            "dashscope.audio is required for audio transcription.",
        ) from exc

    callback = RecognitionCallback()
    recognizer = Recognition(
        model="paraformer-realtime-v1",
        format="wav",
        sample_rate=16000,
        callback=callback,
    )

    result = recognizer.call(audio_path)
    sentences = result.get("output", {}).get("sentence", [])
    return " ".join(sentence.get("text", "") for sentence in sentences)


def extract_frames(
    video_path: str,
    output_dir: str,
    max_frames: int = 16,
) -> List[str]:
    """Extract representative frames using ffmpeg (no OpenCV dependency)."""

    if max_frames <= 0:
        raise ValueError("max_frames must be greater than zero.")

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video path not found: {video_path}")

    os.makedirs(output_dir, exist_ok=True)

    # Clean up previous generated frames
    for existing in Path(output_dir).glob("frame_*.jpg"):
        try:
            existing.unlink()
        except OSError:
            # Ignore errors during cleanup;
            # leftover files will be overwritten or do not affect frame extraction
            pass

    duration = _probe_video_duration(video_path)
    if duration and duration > 0:
        fps = max_frames / duration
    else:
        fps = 1.0

    fps = max(min(fps, 30.0), 0.1)

    command = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vf",
        f"fps={fps:.5f}",
        "-frames:v",
        str(max_frames),
        os.path.join(output_dir, "frame_%04d.jpg"),
    ]

    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ffmpeg is required to extract frames from video.",
        ) from exc

    frame_files = sorted(
        str(path) for path in Path(output_dir).glob("frame_*.jpg")
    )

    if not frame_files:
        raise RuntimeError("No frames could be extracted from the video.")

    return frame_files


def extract_audio(video_path: str, audio_path: str) -> str:
    """Extract audio track with ffmpeg and save as wav."""

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video path not found: {video_path}")

    os.makedirs(os.path.dirname(audio_path), exist_ok=True)

    command = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        audio_path,
    ]

    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "ffmpeg is required to extract audio from video.",
        ) from exc

    return audio_path


def _probe_video_duration(video_path: str) -> Optional[float]:
    """Return the video duration in seconds using ffprobe, if available."""

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]

    try:
        result = subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        duration_str = result.stdout.strip()
        if duration_str:
            return float(duration_str)
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError):
        return None

    return None


def _build_multimodal_blocks(
    frames: List[str],
    transcript: str,
    task: str,
) -> list:
    """Construct multimodal content blocks for the model input."""

    blocks: list = []
    for frame_path in frames:
        with open(frame_path, "rb") as file:
            data = b64encode(file.read()).decode("ascii")
        image_block = ImageBlock(
            type="image",
            source=Base64Source(
                type="base64",
                media_type="image/jpeg",
                data=data,
            ),
        )
        blocks.append(image_block)

    blocks.append(
        TextBlock(
            type="text",
            text=f"Audio transcript:\n{transcript}",
        ),
    )
    blocks.append(
        TextBlock(
            type="text",
            text=f"The task to be solved is: {task}",
        ),
    )
    return blocks


def _prepare_workdir(browser_agent: Any) -> str:
    """Prepare a working directory for intermediate artifacts."""

    base_dir = getattr(browser_agent, "state_saving_dir", None)
    if not base_dir:
        base_dir = tempfile.gettempdir()

    workdir = os.path.join(base_dir, "video_understanding", uuid.uuid4().hex)
    os.makedirs(workdir, exist_ok=True)
    return workdir


def _error_response(message: str) -> ToolResponse:
    """Create a standardized error response."""

    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=message,
            ),
        ],
        metadata={"success": False},
    )
