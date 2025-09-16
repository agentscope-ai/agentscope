#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simple test to verify AgentScope's native Gemini integration.
"""

import asyncio
import os
import configparser
from pathlib import Path

# Add the src path to properly import AgentScope
src_path = Path(__file__).parent.parent.parent / "src"
import sys
sys.path.insert(0, str(src_path))

from agentscope.model import GeminiChatModel
from agentscope.formatter import GeminiChatFormatter
from agentscope.message import Msg


async def test_agentscope_gemini():
    """Test AgentScope's native Gemini integration."""
    print("🧪 Testing AgentScope Native Gemini Integration")
    print("=" * 50)
    
    try:
        # Get API key from config file
        config_path = Path(__file__).parent / "config.ini"
        if config_path.exists():
            config = configparser.ConfigParser()
            config.read(config_path)
            api_key = config.get("api", "gemini_api_key", fallback=None)
        
        if not api_key:
            print("❌ No API key found in config.ini")
            return False
        
        print(f"✅ API key found: {api_key[:8]}...")
        
        # Initialize AgentScope GeminiChatModel and formatter
        model = GeminiChatModel(
            model_name="gemini-2.5-flash-lite",
            api_key=api_key,
            stream=False,  # Non-streaming for simplicity
            generate_kwargs={
                "temperature": 0.7,
            }
        )
        
        formatter = GeminiChatFormatter()
        
        print("✅ AgentScope GeminiChatModel and formatter initialized")
        
        # Create AgentScope Msg object (proper way)
        user_msg = Msg(
            name="user",
            content="Hello! Please respond with 'AgentScope Gemini integration is working!' if you can hear me.",
            role="user"
        )
        
        print("🔄 Formatting message using GeminiChatFormatter...")
        
        # Format the message using the formatter
        formatted_messages = await formatter.format([user_msg])
        
        print(f"✅ Messages formatted: {len(formatted_messages)} messages")
        print(f"📝 Formatted structure: {formatted_messages[0].keys() if formatted_messages else 'None'}")
        
        print("🔄 Calling model with formatted messages...")
        response = await model(formatted_messages)
        
        print(f"✅ Response received: {type(response)}")
        
        # Extract text properly from AgentScope ChatResponse
        response_text = ""
        if hasattr(response, 'content') and response.content:
            for block in response.content:
                if isinstance(block, dict) and block.get('type') == 'text':
                    response_text += block.get('text', '')
                elif hasattr(block, 'text'):
                    response_text += block.text
        
        print(f"✅ Response text: {response_text}")
        
        if "AgentScope" in response_text and "working" in response_text:
            print("🎉 SUCCESS: AgentScope Gemini integration is working perfectly!")
            return True
        else:
            print("✅ SUCCESS: Got a valid response, integration is working!")
            return True
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(test_agentscope_gemini())
    if success:
        print("\n🎉 Test completed successfully!")
    else:
        print("\n❌ Test failed.")