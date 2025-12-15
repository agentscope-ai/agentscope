import os
import asyncio
from agentscope.tool._speech.speech import text_to_speech
import speech_recognition as sr

async def test_tts():
    print("[-] Testing TTS (Text-to-Speech)...")
    try:
        # Generate audio
        text = "Voice module operational. Systems check complete."
        res = await text_to_speech(text, play_audio=False) # Don't play, just generate
        
        # Check result
        if res.content and "Audio generated at" in res.content[0].text:
            path = res.content[0].text.split("generated at ")[1].split(" ")[0]
            if os.path.exists(path):
                print(f"[+] TTS Success! Audio saved to: {path}")
                return True
            else:
                print("[-] TTS Failed: File not found after reported generation.")
                return False
        else:
            print(f"[-] TTS Output unexpected: {res.content}")
            return False
    except Exception as e:
        print(f"[!] TTS Exception: {e}")
        return False

def test_asr_device():
    print("[-] Testing ASR (Microphone Detection)...")
    try:
        mic_names = sr.Microphone.list_microphone_names()
        if len(mic_names) > 0:
            print(f"[+] Microphone Detected! Found {len(mic_names)} devices.")
            print(f"    Default: {mic_names[0]}")
            return True
        else:
            print("[-] No microphone found.")
            return False
    except NotImplementedError:
        print("[!] PyAudio not installed or no audio driver found.")
        return False
    except Exception as e:
        print(f"[!] ASR Device Check Exception: {e}")
        return False

async def main():
    print("=== AgentScope Voice Diagnostic ===")
    tts_ok = await test_tts()
    asr_ok = test_asr_device()
    
    if tts_ok and asr_ok:
        print("\n✅ Verification PASSED: Voice module is ready.")
    else:
        print("\n❌ Verification FAILED: Please check drivers or dependencies.")

if __name__ == "__main__":
    asyncio.run(main())
