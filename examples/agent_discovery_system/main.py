#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Example usage script for the Agent Discovery System.

This script demonstrates how to use the Agent Discovery System to explore
a knowledge base and generate novel insights through eureka moments and
serendipitous discoveries.
"""

import asyncio
import os
from pathlib import Path

# Import AgentScope components
from agentscope.model import GeminiChatModel
from agentscope.formatter import GeminiChatFormatter

# Import Discovery System components
from agentscope.discovery import DiscoveryWorkflow


async def main():
    """Main example demonstrating the Agent Discovery System."""
    
    print("🔍 Agent Discovery System - Example Usage")
    print("=" * 50)
    
    # Configure model and formatter
    # Note: You'll need to set up your Gemini API key
    model = GeminiChatModel(
        model_name="gemini-2.5-pro",
        api_key=os.getenv("GEMINI_API_KEY", "your-gemini-api-key"),
        stream=True,
        generate_kwargs={
            "temperature": 0.7,
        }
    )
    
    formatter = GeminiChatFormatter()
    
    # Create discovery workflow
    workflow = DiscoveryWorkflow(
        model=model,
        formatter=formatter,
        storage_path="./example_discovery_storage",
        max_loops=3,  # Limit for example
        token_budget=10000,  # Reduced for example
        time_budget=1800.0,  # 30 minutes
        cost_budget=5.0,
    )
    
    # Example knowledge base setup
    print("\n📚 Setting up example knowledge base...")
    knowledge_base_path = "./example_knowledge_base"
    setup_example_knowledge_base(knowledge_base_path)
    
    # Define exploration parameters
    initial_idea = "How do machine learning algorithms relate to cognitive psychology?"
    focus_areas = [
        "artificial intelligence",
        "human cognition",
        "learning theories",
        "neural networks"
    ]
    
    print(f"\n🎯 Initial idea: {initial_idea}")
    print(f"🔎 Focus areas: {', '.join(focus_areas)}")
    
    try:
        # Run complete discovery process
        print("\n🚀 Starting discovery process...")
        results = await workflow.run_full_discovery(
            knowledge_base_path=knowledge_base_path,
            initial_idea=initial_idea,
            focus_areas=focus_areas,
            exploration_depth="normal",
        )
        
        # Display results
        print("\n📊 Discovery Results")
        print("=" * 30)
        
        print_discovery_results(results)
        
    except Exception as e:
        print(f"\n❌ Error during discovery: {e}")
        
        # Try to get partial results
        try:
            status = await workflow.get_session_status()
            print(f"Session status: {status}")
        except:
            pass
    
    print("\n✅ Example completed!")


def setup_example_knowledge_base(base_path: str):
    """Set up an example knowledge base with sample documents."""
    
    os.makedirs(base_path, exist_ok=True)
    
    # Sample documents about AI and cognitive science (in Markdown format)
    documents = {
        "ai_fundamentals.md": """
# Artificial Intelligence Fundamentals

Artificial Intelligence (AI) is a branch of computer science that aims to create 
intelligent machines capable of performing tasks that typically require human 
intelligence. These tasks include learning, reasoning, problem-solving, perception, 
and language understanding.

## Machine Learning

Machine learning is a subset of AI that focuses on algorithms that can learn and 
improve from experience without being explicitly programmed. Deep learning, a 
subset of machine learning, uses neural networks with multiple layers to model 
and understand complex patterns in data.

## Key AI Techniques

- **Supervised learning**: Learning from labeled data
- **Unsupervised learning**: Finding patterns in unlabeled data
- **Reinforcement learning**: Learning through trial and error
- **Natural language processing**: Understanding and generating human language
- **Computer vision**: Interpreting and analyzing visual information
        """,
        
        "cognitive_psychology.md": """
# Cognitive Psychology

Cognitive psychology is the scientific study of mental processes such as attention, 
language use, memory, perception, problem solving, creativity, and reasoning. 
It emerged in the 1950s as a response to behaviorism and focuses on how people 
acquire, process, and store information.

## Key Concepts

### Information Processing
Cognitive psychologists view the mind as an information processing system, similar 
to how computers process data.

### Memory Systems
- **Working memory**: Temporary storage and manipulation of information
- **Long-term memory**: Permanent storage of knowledge and experiences
- **Attention mechanisms**: Selective focus on relevant information
- **Cognitive load theory**: Limits on mental processing capacity

## Applications
These concepts have influenced educational psychology and human-computer interaction.
Cognitive scientists study how the mind works, often drawing parallels between 
human cognition and computer processing.
        """,
        
        "learning_theories.md": """
# Learning Theories

Learning theories attempt to describe how students and animals learn, thereby 
helping us understand the inherently complex process of learning.

## Major Learning Theories

### Behaviorism
- Focuses on observable behaviors
- Emphasizes the role of reinforcement
- Associated with Pavlov, Skinner, and Watson

### Cognitivism
- Emphasizes mental processes and information processing
- Focuses on how knowledge is acquired and organized
- Associated with Piaget and Vygotsky

### Constructivism
- Suggests that learners actively construct their own understanding
- Knowledge is built through experience and reflection
- Associated with Dewey and Bruner

## Modern Theories

### Social Learning Theory
Emphasizes learning through observation and imitation (Bandura).

### Connectivism
Addresses learning in the digital age through networks and connections (Siemens).
        """,
        
        "neural_networks.md": """
# Neural Networks

Neural networks are computing systems inspired by biological neural networks 
that constitute animal brains. They are used in machine learning and artificial 
intelligence to recognize patterns and solve complex problems.

## Architecture

### Basic Structure
A neural network consists of nodes (neurons) organized in layers:
- **Input layer**: Receives data
- **Hidden layers**: Process information
- **Output layer**: Produces results

### Learning Process
Each connection has a weight that adjusts as learning proceeds, strengthening 
or weakening the signal between neurons.

## Deep Learning

Deep neural networks with many hidden layers can learn hierarchical 
representations of data, making them powerful for:
- Image recognition
- Natural language processing
- Game playing
- Speech recognition
        """,
        
        "interdisciplinary_connections.md": """
# Interdisciplinary Connections: AI and Cognitive Psychology

The intersection of artificial intelligence and cognitive psychology has led to 
significant advances in both fields.

## Mutual Influence

### AI ← Psychology
Cognitive psychology provides insights into human mental processes that can inspire AI algorithms:
- **Attention mechanisms** in neural networks inspired by human attention
- **Memory models** in AI systems mirror theories of human memory
- **Reinforcement learning** shares similarities with human learning from rewards

### Psychology ← AI
AI techniques help model and test theories about human cognition:
- Computational models of cognitive processes
- Neural network simulations of brain function
- Machine learning approaches to understanding learning

## Applications

This interdisciplinary approach has applications in:
- **Education technology**: Adaptive learning systems
- **Cognitive modeling**: Simulating human thought processes
- **Brain-computer interfaces**: Direct neural control of devices
- **Explainable AI**: Making AI decisions more interpretable
        """
    }
    
    # Write documents to files
    for filename, content in documents.items():
        file_path = os.path.join(base_path, filename)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content.strip())
    
    print(f"📁 Created example knowledge base with {len(documents)} MD documents at: {base_path}")
    print("💡 You can also upload your own MD files using the web interface!")


def print_discovery_results(results: dict):
    """Print formatted discovery results."""
    
    final_results = results.get("final_results", {})
    
    # Executive Summary
    exec_summary = final_results.get("executive_summary", {})
    print(f"Session ID: {exec_summary.get('session_id', 'N/A')}")
    print(f"Total time: {exec_summary.get('total_exploration_time', 0):.1f} seconds")
    print(f"Loops completed: {exec_summary.get('loops_completed', 0)}")
    print(f"Surprise level: {exec_summary.get('surprise_level', 0):.2f}")
    print(f"Coverage: {exec_summary.get('exploration_coverage', 'unknown')}")
    
    # Key Insights
    print("\n💡 Key Insights:")
    key_insights = exec_summary.get("key_insights", [])
    if key_insights:
        for i, insight in enumerate(key_insights, 1):
            print(f"  {i}. {insight}")
    else:
        print("  No key insights generated")
    
    # Discoveries
    discoveries = final_results.get("discoveries", [])
    print(f"\n🔍 Discoveries ({len(discoveries)}):")
    for i, discovery in enumerate(discoveries[:5], 1):  # Show first 5
        if isinstance(discovery, dict):
            insight_text = discovery.get("insight", "No insight text")
            confidence = discovery.get("confidence", 0)
            print(f"  {i}. {insight_text} (confidence: {confidence:.2f})")
        else:
            print(f"  {i}. {discovery}")
    
    # Hypotheses
    hypotheses = final_results.get("hypotheses", [])
    print(f"\n🧪 Hypotheses Generated ({len(hypotheses)}):")
    for i, hypothesis in enumerate(hypotheses[:3], 1):  # Show first 3
        if isinstance(hypothesis, dict):
            hyp_text = hypothesis.get("hypothesis", "No hypothesis text")
            print(f"  {i}. {hyp_text}")
        else:
            print(f"  {i}. {hypothesis}")
    
    # Research Questions
    questions = final_results.get("questions_generated", [])
    print(f"\n❓ Research Questions ({len(questions)}):")
    for i, question in enumerate(questions[:3], 1):  # Show first 3
        if isinstance(question, dict):
            q_text = question.get("question", "No question text")
            priority = question.get("priority", "unknown")
            print(f"  {i}. {q_text} (priority: {priority})")
        else:
            print(f"  {i}. {question}")
    
    # Budget Utilization
    budget = final_results.get("budget_utilization", {})
    print(f"\n💰 Budget Utilization:")
    print(f"  Tokens used: {budget.get('tokens_used', 0)}/{budget.get('token_budget', 0)}")
    print(f"  Time used: {budget.get('time_used', 0):.1f}/{budget.get('time_budget', 0):.1f} seconds")
    print(f"  Cost used: ${budget.get('cost_used', 0):.2f}/${budget.get('cost_budget', 0):.2f}")
    print(f"  Termination reason: {budget.get('termination_reason', 'unknown')}")
    
    # Meta-analysis
    meta_analysis = final_results.get("meta_analysis", {})
    if meta_analysis:
        print(f"\n📈 Meta-analysis:")
        conf_assessment = meta_analysis.get("confidence_assessment", {})
        if conf_assessment:
            overall_score = conf_assessment.get("overall_score", 0)
            print(f"  Overall confidence: {overall_score:.2f}")
        
        gaps = meta_analysis.get("knowledge_gaps", [])
        if gaps:
            print(f"  Knowledge gaps identified: {len(gaps)}")
            for gap in gaps[:3]:
                print(f"    - {gap}")


if __name__ == "__main__":
    # Check for required environment variables
    if not os.getenv("GEMINI_API_KEY"):
        print("⚠️  Warning: GEMINI_API_KEY not set. Using mock model for demonstration.")
        print("   Set your Gemini API key to run with real language models.")
        print("   You can get your API key from: https://makersuite.google.com/app/apikey")
        print("")
    
    # Run the example
    asyncio.run(main())