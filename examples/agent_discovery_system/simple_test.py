#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simple direct test for Gemini API.
"""

import configparser
from pathlib import Path

def test_api():
    print("🧪 Simple Gemini API Test")
    print("=" * 30)
    
    # Read API key from config
    config_path = Path(__file__).parent / "config.ini"
    config = configparser.ConfigParser()
    config.read(config_path)
    api_key = config.get("api", "gemini_api_key", fallback=None)
    
    if not api_key:
        print("❌ No API key found")
        return False
    
    print(f"✅ API key: {api_key[:8]}...")
    
    try:
        import google.generativeai as genai
        print("✅ Google Generative AI library imported")
        
        genai.configure(api_key=api_key)
        print("✅ API configured")
        
        model = genai.GenerativeModel('gemini-2.5-flash-lite')
        print("✅ Model created")
        
        response = model.generate_content("Say 'Hello, Gemini is working!'")
        print(f"✅ Response: {response.text}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    test_api()