# -*- coding: utf-8 -*-
"""Speech recognition and synthesis tools."""
import os
import asyncio
import hashlib
from typing import Optional
import pygame

try:
    import edge_tts
except ImportError:
    edge_tts = None

try:
    import speech_recognition as sr
except ImportError:
    sr = None
    
from agentscope.tool import ToolResponse
from agentscope.message import TextBlock

def _get_audio_cache_dir():
    cache_dir = os.path.join(os.path.dirname(__file__), ".audio_cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir

async def text_to_speech(
    text: str,
    voice: str = "en-US-AriaNeural",
    play_audio: bool = True
) -> ToolResponse:
    """
    Convert text to speech using Edge TTS.
    
    Args:
        text (str): The text to convert.
        voice (str): The voice to use (e.g., "en-US-AriaNeural", "zh-CN-XiaoxiaoNeural").
        play_audio (bool): Whether to play the audio immediately.
        
    Returns:
        ToolResponse: The path to the generated audio file.
    """
    if not edge_tts:
        return ToolResponse(content=[TextBlock(type="text", text="Error: edge_tts not installed.")])

    try:
        # Generate filename from hash
        hash_str = hashlib.md5(f"{text}-{voice}".encode()).hexdigest()
        filename = f"tts_{hash_str}.mp3"
        filepath = os.path.join(_get_audio_cache_dir(), filename)
        
        # Synthesize if not exists
        if not os.path.exists(filepath):
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(filepath)
            
        msg = f"Audio generated at {filepath}"
        
        if play_audio:
            try:
                # Use pygame to play
                if not pygame.get_init():
                    pygame.init()
                if not pygame.mixer.get_init():
                    pygame.mixer.init()
                    
                pygame.mixer.music.load(filepath)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    await asyncio.sleep(0.1)
                msg += " and played successfully."
            except Exception as e:
                msg += f" but failed to play: {e}"
                
        return ToolResponse(content=[TextBlock(type="text", text=msg)])
        
    except Exception as e:
        return ToolResponse(content=[TextBlock(type="text", text=f"TTS Error: {e}")])

def speech_to_text(timeout: int = 5, phrase_time_limit: int = 10) -> ToolResponse:
    """
    Listen to the microphone and convert speech to text.
    
    Args:
        timeout (int): Seconds to wait for speech to start.
        phrase_time_limit (int): Max seconds for a single phrase.
        
    Returns:
        ToolResponse: The recognized text.
    """
    if not sr:
        return ToolResponse(content=[TextBlock(type="text", text="Error: speech_recognition not installed.")])
        
    r = sr.Recognizer()
    try:
        with sr.Microphone() as source:
            print("[System] Listening... (Speak now)")
            r.adjust_for_ambient_noise(source)
            audio = r.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
            
        print("[System] Processing audio...")
        # Default to Chinese headers for this user
        text = r.recognize_google(audio, language="zh-CN")
        return ToolResponse(content=[TextBlock(type="text", text=text)])
        
    except sr.WaitTimeoutError:
         return ToolResponse(content=[TextBlock(type="text", text="Error: Listening timed out (no speech detected).")])
    except sr.UnknownValueError:
         return ToolResponse(content=[TextBlock(type="text", text="Error: Could not understand audio.")])
    except Exception as e:
        return ToolResponse(content=[TextBlock(type="text", text=f"ASR Error: {e}")])
