# -*- coding: utf-8 -*-
"""Verify unified model configuration is working correctly."""
import os
import sys
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

def verify_config():
    """Verify that all modules use unified configuration."""
    print("=== Verifying Unified Model Configuration ===")
    
    # Check environment variables
    print("\\n1. Environment Variables:")
    print(f"   DASHSCOPE_API_KEY: {'Set' if os.getenv('DASHSCOPE_API_KEY') else 'Not Set'}")
    print(f"   DASHSCOPE_MODEL_NAME: {os.getenv('DASHSCOPE_MODEL_NAME', 'Not Set (using default)')}")
    
    # Test model_config module
    print("\\n2. ModelConfig Module:")
    try:
        from model_config import ModelConfig
        print(f"   Model Name: {ModelConfig.get_model_name()}")
        print(f"   API Key Status: {'Set' if ModelConfig.get_api_key() else 'Not Set'}")
        print("   ✓ ModelConfig working correctly")
    except Exception as e:
        print(f"   ✗ ModelConfig error: {e}")
    
    # Test main.py configuration
    print("\\n3. main.py Configuration:")
    try:
        # Import and check if it uses ModelConfig
        with open('main.py', 'r', encoding='utf-8') as f:
            content = f.read()
            if 'ModelConfig.get_model_name()' in content:
                print("   ✓ Uses ModelConfig.get_model_name()")
            else:
                print("   ✗ Does not use ModelConfig.get_model_name()")
            if 'ModelConfig.get_api_key()' in content:
                print("   ✓ Uses ModelConfig.get_api_key()")
            else:
                print("   ✗ Does not use ModelConfig.get_api_key()")
    except Exception as e:
        print(f"   ✗ Error checking main.py: {e}")
    
    # Test agent.py configuration
    print("\\n4. agent.py Configuration:")
    try:
        with open('agent.py', 'r', encoding='utf-8') as f:
            content = f.read()
            if 'ModelConfig.get_model_name()' in content:
                print("   ✓ Uses ModelConfig.get_model_name()")
            else:
                print("   ✗ Does not use ModelConfig.get_model_name()")
            if 'ModelConfig.get_api_key()' in content:
                print("   ✓ Uses ModelConfig.get_api_key()")
            else:
                print("   ✗ Does not use ModelConfig.get_api_key()")
    except Exception as e:
        print(f"   ✗ Error checking agent.py: {e}")
    
    # Test agent_with_real_model.py configuration
    print("\\n5. agent_with_real_model.py Configuration:")
    try:
        with open('agent_with_real_model.py', 'r', encoding='utf-8') as f:
            content = f.read()
            if 'ModelConfig.get_model_name()' in content:
                print("   ✓ Uses ModelConfig.get_model_name()")
            else:
                print("   ✗ Does not use ModelConfig.get_model_name()")
    except Exception as e:
        print(f"   ✗ Error checking agent_with_real_model.py: {e}")
    
    # Check .env file
    print("\\n6. .env File Configuration:")
    try:
        with open('.env', 'r', encoding='utf-8') as f:
            content = f.read()
            if 'DASHSCOPE_MODEL_NAME' in content:
                print("   ✓ Contains DASHSCOPE_MODEL_NAME")
            else:
                print("   ✗ Missing DASHSCOPE_MODEL_NAME")
            if 'DASHSCOPE_API_KEY' in content:
                print("   ✓ Contains DASHSCOPE_API_KEY")
            else:
                print("   ✗ Missing DASHSCOPE_API_KEY")
    except Exception as e:
        print(f"   ✗ Error checking .env: {e}")
    
    print("\\n=== Verification Complete ===")
    print("\\nTo change the model, edit the DASHSCOPE_MODEL_NAME in .env file")
    print("Available models can be found at: https://help.aliyun.com/document_detail/2712534.html")

if __name__ == "__main__":
    verify_config()