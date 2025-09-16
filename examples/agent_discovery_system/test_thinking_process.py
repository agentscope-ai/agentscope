#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script for the thinking process functionality.
"""

import asyncio
import json
import tempfile
import os
from pathlib import Path

# Add the src path
src_path = Path(__file__).parent.parent.parent / "src"
import sys
sys.path.insert(0, str(src_path))

from simple_discovery_server import SimpleDiscoveryWorkflow, setup_model_and_formatter

async def test_thinking_process():
    """Test the thinking process functionality."""
    print("🧪 Testing Agent Thinking Process")
    print("=" * 40)
    
    try:
        # Setup model and formatter
        model, formatter = setup_model_and_formatter()
        print("✅ Model and formatter initialized")
        
        # Create workflow
        workflow = SimpleDiscoveryWorkflow(model, formatter)
        print("✅ Workflow created")
        
        # Create test knowledge base
        test_kb_dir = tempfile.mkdtemp(prefix="test_kb_")
        test_md_file = os.path.join(test_kb_dir, "test.md")
        
        with open(test_md_file, 'w', encoding='utf-8') as f:
            f.write("""
# Test Knowledge

This is a test markdown file for demonstrating the thinking process.

## Artificial Intelligence
AI is transforming how we solve problems and understand the world.

## Machine Learning  
ML algorithms learn patterns from data to make predictions.

## Connections
AI and ML are deeply connected fields that build upon each other.
""")
        
        print("✅ Test knowledge base created")
        
        # Start discovery
        start_result = await workflow.start_discovery(
            knowledge_base_path=test_kb_dir,
            initial_idea="How do AI and ML connect to solve real-world problems?",
            focus_areas=["artificial intelligence", "machine learning"],
            exploration_depth="normal"
        )
        
        print(f"✅ Discovery started: {start_result['session_id']}")
        
        # Run one exploration loop to test thinking process
        print("\n🔄 Running exploration loop...")
        loop_result = await workflow.run_exploration_loop()
        
        print("\n📋 Loop Result:")
        print(f"  Loop Number: {loop_result.get('loop_number')}")
        print(f"  Status: {loop_result.get('status')}")
        
        # Display thinking process
        thinking = loop_result.get('thinking_process', {})
        if thinking:
            print("\n🧠 Thinking Process:")
            for step, content in thinking.items():
                print(f"  {step}: {content[:100]}..." if len(content) > 100 else f"  {step}: {content}")
        
        # Display discoveries
        discoveries = loop_result.get('discoveries', [])
        if discoveries:
            print(f"\n🔍 Discoveries ({len(discoveries)}):")
            for i, discovery in enumerate(discoveries[:3]):
                if isinstance(discovery, dict):
                    print(f"  {i+1}. {discovery.get('text', 'No text')[:80]}...")
                    if discovery.get('reasoning'):
                        print(f"     Reasoning: {discovery['reasoning'][:60]}...")
                else:
                    print(f"  {i+1}. {str(discovery)[:80]}...")
        
        # Cleanup
        import shutil
        shutil.rmtree(test_kb_dir)
        print("\n✅ Test completed successfully!")
        
        return True
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    asyncio.run(test_thinking_process())