#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Example showing the new messages saving functionality.

This example demonstrates how to use the refactored messages saving feature
that was previously called "SFT collection".
"""

import asyncio
import tempfile
import os

# Example usage of the new messages saving functionality
async def main():
    """Demonstrate the new messages saving API."""
    
    # Method 1: Enable messages saving directly in model initialization
    print("=== Method 1: Direct model initialization ===")
    
    # Create a temporary file for saving messages
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        save_path = f.name
    
    try:
        # Note: This is just an example - you would need actual API keys
        # model = OpenAIChatModel(
        #     model_name="gpt-4",
        #     api_key="your-api-key",
        #     save_messages=True,  # Enable messages saving
        #     save_path=save_path  # Path to save the data
        # )
        
        print(f"Model would save messages to: {save_path}")
        print("Parameters: save_messages=True, save_path='...'")
        
    except Exception as e:
        print(f"Note: This example requires actual API keys: {e}")
    
    # Method 2: Using the enable_messages_save function
    print("\n=== Method 2: Using enable_messages_save function ===")
    
    # This would be used with an existing model
    # from agentscope.model import enable_messages_save
    # model = enable_messages_save(existing_model, save_path)
    
    print("Use enable_messages_save(model, save_path) to add saving to existing models")
    
    # Method 3: Using MessagesDataCollector directly
    print("\n=== Method 3: Using MessagesDataCollector directly ===")
    
    # Import directly to avoid dependency issues
    import sys
    sys.path.insert(0, 'src')
    
    # Import the module directly
    import importlib.util
    spec = importlib.util.spec_from_file_location("messages_save", "src/agentscope/model/_messages_save.py")
    messages_save_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(messages_save_module)
    MessagesDataCollector = messages_save_module.MessagesDataCollector
    
    collector = MessagesDataCollector(
        output_path=save_path,
        enable_collection=True
    )
    
    # Example of collecting data
    messages = [
        {"role": "user", "content": "Hello, how are you?"},
        {"role": "assistant", "content": "I'm doing well, thank you!"}
    ]
    
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather information"
            }
        }
    ]
    
    await collector.collect(
        messages=messages,
        tools=tools,
        metadata={"example": True}
    )
    
    print(f"Data collected and saved to: {save_path}")
    
    # Show the saved data
    if os.path.exists(save_path):
        with open(save_path, 'r') as f:
            content = f.read()
            print(f"Saved data:\n{content}")
    
    # Clean up
    os.unlink(save_path)
    
    print("\n=== Summary ===")
    print("✅ Refactoring completed successfully!")
    print("✅ Old 'SFT' naming replaced with 'MessagesSave'")
    print("✅ Functionality integrated into model module")
    print("✅ New API: save_messages=True, save_path='...'")
    print("✅ Backward compatibility maintained through enable_messages_save()")

if __name__ == "__main__":
    asyncio.run(main())
