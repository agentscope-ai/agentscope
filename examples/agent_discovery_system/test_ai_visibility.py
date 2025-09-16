#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test AI Model Visibility

This script demonstrates the enhanced AI model call visibility in the Discovery System.
"""

import asyncio
import sys
from pathlib import Path

# Add project paths
project_root = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(project_root))

from agentscope.model import GeminiChatModel
from agentscope.formatter import GeminiChatFormatter  
from agentscope.message import Msg

# Import the interceptor
from ai_model_interceptor import enable_ai_model_visibility


async def test_ai_visibility():
    """Test the AI model call visibility."""
    print("🔍 Testing AI Model Call Visibility")
    print("=" * 50)
    
    # Create a Gemini model
    model = GeminiChatModel(
        model_name="gemini-2.5-flash-lite",
        api_key="AIzaSyAIjsJCDEtJ14pdxfRIS4uj9sjriB_ff6I",
        stream=True,
        generate_kwargs={
            "temperature": 0.7,
            "max_output_tokens": 100,
        }
    )
    
    # Enable AI model visibility
    print("✅ Enabling AI model visibility...")
    model = enable_ai_model_visibility(model, "TestAgent")
    
    # Create a test message
    messages = [
        {"role": "user", "content": "Hello! Please tell me a short fun fact about space."}
    ]
    
    print(f"📤 Sending test message: '{messages[0]['content']}'")
    print("⏳ Waiting for AI response with full visibility...")
    
    try:
        # Make the model call (this will be intercepted and streamed)
        response = await model(messages)
        
        if hasattr(response, '__aiter__'):
            print("📡 Streaming response detected - processing chunks...")
            async for chunk in response:
                if hasattr(chunk, 'content') and chunk.content:
                    for content_block in chunk.content:
                        if hasattr(content_block, 'text') and content_block.text:
                            print(f"   📝 Chunk: {content_block.text[:50]}...")
        else:
            print("📄 Complete response received")
            if hasattr(response, 'content'):
                for content_block in response.content:
                    if hasattr(content_block, 'text'):
                        print(f"   📝 Content: {content_block.text}")
        
        print("✅ Test completed successfully!")
        print("\n💡 All AI calls are now visible in the web UI:")
        print("   🌐 Open http://localhost:8000 to see real-time AI call tracking")
        print("   📊 View call details, token usage, and timing information")
        print("   🔄 Watch streaming responses in real-time")
        
    except Exception as e:
        print(f"❌ Error during test: {e}")
        print("   Check that the server is running and API key is valid")


if __name__ == "__main__":
    asyncio.run(test_ai_visibility())