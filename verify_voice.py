# -*- coding: utf-8 -*-
"""Verify Voice Capability Headless"""
import os
import asyncio
import hashlib
import agentscope
from agentscope.tool._speech.speech import text_to_speech

async def verify_tts():
    print("Testing TTS Module...")
    
    test_text = "Hello, this is a test of the AgentScope Voice System."
    voice = "en-US-AriaNeural"
    
    # 1. Call TTS
    # We set play_audio=False for headless verification safety, or True to test pygame load
    # Let's try True to see if it crashes.
    print(f"Generating audio for: '{test_text}'")
    res = await text_to_speech(test_text, voice=voice, play_audio=True)
    
    print(f"TTS Result: {res.content[0]['text']}")
    
    # Verify file existence
    # Re-calculate hash to find file
    # This logic matches speech.py
    hash_str = hashlib.md5(f"{test_text}-{voice}".encode()).hexdigest()
    filename = f"tts_{hash_str}.mp3"
    cache_dir = os.path.join(os.path.dirname(agentscope.__file__), "tool", "_speech", ".audio_cache")
    filepath = os.path.join(cache_dir, filename)
    
    if os.path.exists(filepath):
        print(f"SUCCESS: Audio file found at {filepath}")
        return True
    else:
        print(f"FAILURE: Audio file NOT found at {filepath}")
        return False

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(verify_tts())
