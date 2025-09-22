#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quick demo script for the Agent Discovery System integration.

This script demonstrates the key features of the integrated system.
"""

import os
import asyncio
import tempfile
from pathlib import Path

# Import AgentScope components
from agentscope.model import GeminiChatModel
from agentscope.formatter import GeminiChatFormatter

# Import Discovery System components
from agentscope.discovery import DiscoveryWorkflow


async def demo_discovery_system():
    """Demonstrate the integrated Agent Discovery System."""
    
    print("🎯 Agent Discovery System - Quick Demo")
    print("=" * 50)
    
    # Check API key
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY not set. Please set your API key first.")
        print("   You can get one from: https://makersuite.google.com/app/apikey")
        return
    
    print(f"✅ API key found: {api_key[:8]}...")
    
    try:
        # Initialize model and formatter
        print("\n🤖 Initializing Gemini model...")
        model = GeminiChatModel(
            model_name="gemini-2.5-flash-lite",  # Use faster model for demo
            api_key=api_key,
            stream=False,  # Disable streaming for demo
            generate_kwargs={
                "temperature": 0.7,
            }
        )
        
        formatter = GeminiChatFormatter()
        print("✅ Model initialized successfully")
        
        # Create sample knowledge base
        print("\n📚 Creating sample knowledge base...")
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create sample MD file
            sample_md = Path(temp_dir) / "ai_basics.md"
            sample_content = """
# Artificial Intelligence Basics

AI is transforming how we work and live. Key areas include:

## Machine Learning
- Supervised learning from labeled data
- Unsupervised pattern discovery
- Reinforcement learning through trial and error

## Applications
- Natural language processing
- Computer vision
- Autonomous systems

## Future Trends
- Explainable AI
- AI ethics and safety
- Human-AI collaboration
            """
            
            sample_md.write_text(sample_content.strip())
            print(f"✅ Created sample knowledge base at: {temp_dir}")
            
            # Initialize discovery workflow
            print("\n🔄 Initializing discovery workflow...")
            workflow = DiscoveryWorkflow(
                model=model,
                formatter=formatter,
                storage_path=temp_dir + "/discovery_storage",
                max_loops=1,  # Just one loop for demo
                token_budget=5000,  # Small budget for demo
                time_budget=300.0,  # 5 minutes
                cost_budget=1.0,
            )
            
            # Run discovery
            print("\n🚀 Starting discovery process...")
            initial_idea = "What are the key trends in AI development?"
            focus_areas = ["machine learning", "AI applications", "future trends"]
            
            print(f"💡 Exploring: {initial_idea}")
            print(f"🎯 Focus areas: {', '.join(focus_areas)}")
            
            # This would normally run the full discovery, but for demo we'll show the setup
            results = await workflow.start_discovery(
                knowledge_base_path=temp_dir,
                initial_idea=initial_idea,
                focus_areas=focus_areas,
                exploration_depth="shallow",
            )
            
            print("✅ Discovery session started successfully!")
            print(f"   Session ID: {results['session_id']}")
            print(f"   Status: {results['status']}")
            
            # Get session status
            status = await workflow.get_session_status()
            print(f"   Current status: {status}")
            
            print("\n🎉 Demo completed successfully!")
            print("\n💡 To run the full system:")
            print("   1. Run: python run_discovery_system.py")
            print("   2. Open: http://localhost:8000")
            print("   3. Upload your MD files and start exploring!")
            
    except Exception as e:
        print(f"❌ Demo failed: {e}")
        print("\n🔧 Troubleshooting:")
        print("   1. Check your Gemini API key")
        print("   2. Ensure all dependencies are installed")
        print("   3. Run: python test_gemini.py")


if __name__ == "__main__":
    asyncio.run(demo_discovery_system())