#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simple test script to verify Gemini API configuration.
"""

import os
import asyncio
import configparser
from pathlib import Path
from agentscope.model import GeminiChatModel
from agentscope.formatter import GeminiChatFormatter


def test_gemini_setup():
    """Test if Gemini is properly configured."""
    
    print("🧪 Testing Gemini API Configuration")
    print("=" * 40)
    
    # Check API key
    api_key = os.getenv("GEMINI_API_KEY")
    
    # If not found in environment, try config file
    if not api_key:
        config_path = Path(__file__).parent / "config.ini"
        if config_path.exists():
            config = configparser.ConfigParser()
            config.read(config_path)
            api_key = config.get("api", "gemini_api_key", fallback=None)
            if api_key:
                print("✅ API key found in config.ini")
    
    if not api_key:
        print("❌ GEMINI_API_KEY not found in environment variables or config.ini")
        print("   Please either:")
        print("   1. Set environment variable: set GEMINI_API_KEY='your-api-key'")
        print("   2. Update config.ini with your API key")
        print("   You can get your API key from: https://makersuite.google.com/app/apikey")
        return False
    
    print(f"✅ API key found: {api_key[:8]}...")
    
    try:
        # Initialize model
        model = GeminiChatModel(
            model_name="gemini-2.5-flash-lite",  # Use faster model for testing
            api_key=api_key,
            stream=False,  # Disable streaming for simple test
            generate_kwargs={
                "temperature": 0.7,
            }
        )
        
        formatter = GeminiChatFormatter()
        
        print("✅ Model and formatter initialized successfully")
        
        # Test simple generation using direct Google Generative AI
        print("\n🔄 Testing direct Gemini API...")
        
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        
        # Use the direct Gemini API for testing
        model_direct = genai.GenerativeModel('gemini-2.5-flash-lite')
        test_prompt = "Hello! Please respond with 'Gemini is working!' to confirm the setup."
        
        response = model_direct.generate_content(test_prompt)
        
        if response and response.text:
            print(f"✅ Model response: {response.text}")
            return True
        else:
            print(f"❌ Unexpected response format: {response}")
            return False
            
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("   Please install the Google Generative AI library:")
        print("   pip install google-generativeai")
        return False
        
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        print("   Please check your API key and network connection")
        return False


def main():
    """Main test function."""
    
    success = test_gemini_setup()
    
    if success:
        print("\n🎉 Gemini setup test completed successfully!")
        print("   You can now run the Agent Discovery System examples.")
    else:
        print("\n❌ Gemini setup test failed.")
        print("   Please resolve the issues above before running the examples.")


if __name__ == "__main__":
    main()