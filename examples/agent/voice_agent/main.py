# -*- coding: utf-8 -*-
"""Voice Interaction Demo (Jarvis)"""
import os
import asyncio
import agentscope
from agentscope.agent import ReActAgent
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit
from agentscope.formatter import DeepSeekChatFormatter
from agentscope.tool._speech.speech import text_to_speech, speech_to_text

# Configuration
# Configuration
# PLEASE REPLACE WITH YOUR OWN VALID API KEY
# You can set it in environment variable 'DEEPSEEK_API_KEY' or paste it here.
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "YOUR_API_KEY_HERE")
MODEL_NAME = "deepseek-chat"

if API_KEY == "YOUR_API_KEY_HERE":
    print("⚠️  WARNING: No API Key found.")
    print("Please set DEEPSEEK_API_KEY in environment or edit main.py.")
    # We will try to run, but it will likely fail if not caught later.
    # Actually let's prompt or exit?
    # For a demo, let's just warn.

async def run_voice_loop(agent):
    print("🎤 Voice Assistant Started! (Press Ctrl+C to exit)")
    print("Speak into your microphone...")
    
    while True:
        try:
            # 1. Listen (ASR)
            # Use speech_to_text tool directly as a function helper first
            # But the agent can also use it. Let's make the loop driver use it for input.
            res = speech_to_text(timeout=5)
            user_text = ""
            
            # Extract text from ToolResponse
            if res.content and res.content[0].get("text"):
                user_text = res.content[0]["text"]
            
            if not user_text or "Error" in user_text:
                print(f"👂 ... (Silence/Error: {user_text})")
                continue
                
            print(f"User: {user_text}")
            if user_text.lower() in ["exit", "quit", "goodbye"]:
                break

            # 2. Think (Agent)
            msg = Msg(name="User", content=user_text, role="user")
            response_msg = await agent(msg)
            
            # The agent might return text. We want it to SPEAK.
            # We can force the agent to use the TTS tool, 
            # OR we can just TTS the agent's text response.
            # Consistently, a "Voice Assistant" should just speak whatever the LLM says.
            
            agent_text = response_msg.content
            # Handle if content is list of blocks
            if isinstance(agent_text, list):
                # Join text blocks
                agent_text = " ".join([b["text"] for b in agent_text if b.get("type") == "text"])
                
            print(f"Jarvis: {agent_text}")
            
            # 3. Speak (TTS)
            # We call the tool function directly here for output
            await text_to_speech(agent_text, voice="en-US-ChristopherNeural") 
            # Or "zh-CN-YunxiNeural" for Chinese
            
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            err_str = str(e)
            if "402" in err_str or "Insufficient Balance" in err_str:
                print("\n🔴 API Error: Insufficient Balance (Error 402).")
                print("Your API Key has run out of funds. Please recharge or switch keys.")
                break # Exit loop on fatal auth error
            else:
                print(f"Error: {e}")
                await asyncio.sleep(1)

def main():
    agentscope.init(project="Voice_Agent", name="Jarvis")
    
    # Setup Agent
    model = OpenAIChatModel(
        config_name=MODEL_NAME,
        model_name=MODEL_NAME,
        api_key=API_KEY,
        client_kwargs={"base_url": "https://api.deepseek.com"}
    )
    
    # We don't necessarily need tools for the agent itself if the OUTER LOOP handles input/output.
    # But giving it capabilities is fun.
    toolkit = Toolkit()
    # No tools needed for basic chat, but let's give it search later.
    
    agent = ReActAgent(
        name="Jarvis",
        sys_prompt="You are Jarvis, a helpful voice assistant. Keep your responses CONCISE and conversational, suitable for speech synthesis.",
        model=model,
        formatter=DeepSeekChatFormatter(),
        toolkit=toolkit,
    )
    
    asyncio.run(run_voice_loop(agent))

if __name__ == "__main__":
    main()
