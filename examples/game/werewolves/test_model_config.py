# -*- coding: utf-8 -*-
"""Test script to verify unified model configuration."""
import os
import sys

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from model_config import ModelConfig
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    load_dotenv(env_path)

def test_model_config():
    """Test the model configuration system."""
    print("=== Testing Unified Model Configuration ===")
    
    # Test configuration loading
    ModelConfig.print_config()
    
    # Test individual methods
    print(f"\\nAPI Key: {'Set' if ModelConfig.get_api_key() else 'Not Set'}")
    print(f"Model Name: {ModelConfig.get_model_name()}")
    print(f"Config Valid: {ModelConfig.validate_config()}")
    
    # Test configuration dictionary
    config = ModelConfig.get_model_config()
    print(f"\\nFull Config: {config}")
    
    print("\\n=== Configuration Test Complete ===")

def test_imports():
    """Test that all modules can import the configuration."""
    print("\\n=== Testing Module Imports ===")
    
    try:
        from main import get_official_agents
        print("✓ main.py imports successful")
    except Exception as e:
        print(f"✗ main.py imports failed: {e}")
    
    try:
        from agent import PlayerAgent
        print("✓ agent.py imports successful")
    except Exception as e:
        print(f"✗ agent.py imports failed: {e}")
    
    try:
        from agent_with_real_model import PlayerAgent as RealAgent
        print("✓ agent_with_real_model.py imports successful")
    except Exception as e:
        print(f"✗ agent_with_real_model.py imports failed: {e}")

if __name__ == "__main__":
    test_model_config()
    test_imports()