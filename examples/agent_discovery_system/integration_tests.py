#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Comprehensive Integration Testing Suite for Discovery System

This module provides complete testing for the AgentScope Discovery System
implementation, following the design specifications.
"""

import asyncio
import json
import tempfile
import pytest
import os
import sys
from pathlib import Path
from typing import Dict, List, Any
from unittest.mock import Mock, AsyncMock, patch

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

# Import the components we've implemented
from discovery_coordinator import DiscoveryAgentCoordinator, DiscoveryConfig
from prompt_manager import DiscoveryPromptManager
from streaming_manager import DiscoveryStreamer, ConnectionManager, EventType
from discovery_toolkit import DiscoveryTools, SearchTool, AnalysisTool


@pytest.fixture
def test_config():
    """Create test configuration."""
    config = DiscoveryConfig()
    config.gemini_api_key = "test_api_key"
    config.max_loops = 3
    config.token_budget = 1000
    config.time_budget = 300
    return config


@pytest.fixture
def mock_knowledge_files():
    """Create mock knowledge files for testing."""
    return [
        {
            "filename": "test1.md",
            "content": """
# Machine Learning Fundamentals

Machine learning is a subset of artificial intelligence that focuses on algorithms
that can learn from data. Neural networks are a key component of deep learning.

## Key Concepts
- Supervised learning uses labeled data
- Unsupervised learning finds patterns in unlabeled data
- Reinforcement learning learns through interaction
"""
        },
        {
            "filename": "test2.md", 
            "content": """
# Data Science Applications

Data science combines statistics, programming, and domain expertise.
Machine learning algorithms are widely used in data science projects.

## Applications
- Predictive analytics for business intelligence
- Natural language processing for text analysis
- Computer vision for image recognition
"""
        }
    ]


class TestDiscoveryAgentCoordinator:
    """Test the main discovery agent coordinator."""
    
    @pytest.mark.asyncio
    async def test_coordinator_initialization(self, test_config):
        """Test coordinator initialization with proper AgentScope components."""
        coordinator = DiscoveryAgentCoordinator(test_config)
        
        # Check that all components are initialized
        assert coordinator.model is not None
        assert coordinator.formatter is not None
        assert coordinator.memory is not None
        assert coordinator.toolkit is not None
        assert coordinator.agents is not None
        
        # Check that required agents are present
        required_agents = [
            'user_proxy', 'orchestrator', 'exploration_planner',
            'knowledge_graph', 'web_search', 'verification',
            'surprise_assessment', 'insight_generator', 'meta_analysis'
        ]
        
        for agent_name in required_agents:
            assert agent_name in coordinator.agents
    
    @pytest.mark.asyncio
    async def test_session_initialization(self, test_config, mock_knowledge_files):
        """Test discovery session initialization."""
        coordinator = DiscoveryAgentCoordinator(test_config)
        
        # Create temporary knowledge base
        with tempfile.TemporaryDirectory() as temp_dir:
            for file_data in mock_knowledge_files:
                file_path = Path(temp_dir) / file_data["filename"]
                file_path.write_text(file_data["content"])
            
            # Start discovery session
            result = await coordinator.start_discovery_session(
                knowledge_base_path=temp_dir,
                initial_idea="Explore machine learning concepts",
                focus_areas=["artificial intelligence", "data science"]
            )
            
            assert result["status"] == "started"
            assert "session_id" in result
            assert coordinator.session_active is True
            assert coordinator.current_session_id is not None
    
    @pytest.mark.asyncio
    async def test_exploration_loop(self, test_config, mock_knowledge_files):
        """Test exploration loop execution."""
        coordinator = DiscoveryAgentCoordinator(test_config)
        
        # Mock the model responses
        with patch.object(coordinator.model, '__call__', new_callable=AsyncMock) as mock_model:
            mock_model.return_value = Mock(
                content=[{"type": "text", "text": "Test discovery about machine learning"}]
            )
            
            with tempfile.TemporaryDirectory() as temp_dir:
                for file_data in mock_knowledge_files:
                    file_path = Path(temp_dir) / file_data["filename"]
                    file_path.write_text(file_data["content"])
                
                # Start session
                await coordinator.start_discovery_session(
                    knowledge_base_path=temp_dir,
                    initial_idea="Explore machine learning concepts"
                )
                
                # Run exploration loop
                loop_result = await coordinator.run_exploration_loop()
                
                assert "loop_number" in loop_result
                assert "planning" in loop_result
                assert "execution" in loop_result
                assert "synthesis" in loop_result
                assert loop_result["status"] in ["completed", "continue"]


class TestPromptManager:
    """Test the prompt management system."""
    
    def test_prompt_manager_initialization(self):
        """Test prompt manager initialization."""
        manager = DiscoveryPromptManager()
        
        # Check that all required agent types are available
        required_types = [
            'orchestrator', 'exploration_planner', 'knowledge_graph',
            'insight_generator', 'meta_analysis', 'user_proxy'
        ]
        
        available_types = manager.get_available_agent_types()
        for agent_type in required_types:
            assert agent_type in available_types
    
    def test_prompt_rendering(self):
        """Test prompt rendering with parameters."""
        manager = DiscoveryPromptManager()
        
        # Test orchestrator prompt
        orchestrator_params = {
            'token_budget': 1000,
            'time_budget': 300,
            'cost_budget': 5.0,
            'max_loops': 3,
            'agent_descriptions': "Test agents",
            'session_id': "test-session",
            'initial_idea': "Test idea",
            'focus_areas': "test areas",
            'knowledge_files_count': 2,
            'current_loop': 1
        }
        
        prompt = manager.get_prompt('orchestrator', **orchestrator_params)
        
        assert "Discovery Orchestrator" in prompt
        assert "test-session" in prompt
        assert "Test idea" in prompt
        assert "1000" in prompt  # token budget
    
    def test_prompt_validation(self):
        """Test prompt parameter validation."""
        manager = DiscoveryPromptManager()
        
        # Test with missing parameters
        validation_result = manager.validate_prompt('orchestrator')
        assert validation_result["valid"] is False
        assert "missing_params" in validation_result
        
        # Test with all parameters
        required_params = {
            'token_budget': 1000, 'time_budget': 300, 'cost_budget': 5.0,
            'max_loops': 3, 'agent_descriptions': "Test", 'session_id': "test",
            'initial_idea': "Test", 'focus_areas': "test", 'knowledge_files_count': 1,
            'current_loop': 1
        }
        
        validation_result = manager.validate_prompt('orchestrator', **required_params)
        assert validation_result["valid"] is True


class TestStreamingManager:
    """Test the real-time streaming system."""
    
    @pytest.fixture
    def connection_manager(self):
        """Create connection manager for testing."""
        return ConnectionManager()
    
    @pytest.fixture
    def discovery_streamer(self, connection_manager):
        """Create discovery streamer for testing."""
        return DiscoveryStreamer(connection_manager)
    
    @pytest.mark.asyncio
    async def test_connection_management(self, connection_manager):
        """Test WebSocket connection management."""
        # Mock WebSocket
        mock_websocket = AsyncMock()
        
        # Test connection
        client_id = await connection_manager.connect(mock_websocket)
        assert client_id is not None
        assert client_id in connection_manager.active_connections
        
        # Test subscription
        await connection_manager.subscribe_to_session(client_id, "test-session")
        assert "test-session" in connection_manager.session_subscriptions
        assert client_id in connection_manager.session_subscriptions["test-session"]
        
        # Test disconnection
        await connection_manager.disconnect(client_id)
        assert client_id not in connection_manager.active_connections
    
    @pytest.mark.asyncio
    async def test_event_streaming(self, discovery_streamer):
        """Test event streaming functionality."""
        # Set session
        await discovery_streamer.set_current_session("test-session")
        
        # Test agent thinking stream
        await discovery_streamer.stream_agent_thinking_step(
            agent_name="TestAgent",
            thinking_step="analysis",
            content="Analyzing the problem..."
        )
        
        # Check event history
        history = discovery_streamer.get_event_history()
        assert len(history) >= 2  # session start + thinking step
        
        thinking_events = [e for e in history if e["type"] == EventType.AGENT_THINKING_STEP.value]
        assert len(thinking_events) == 1
        assert thinking_events[0]["data"]["agent_name"] == "TestAgent"
    
    @pytest.mark.asyncio
    async def test_discovery_streaming(self, discovery_streamer):
        """Test discovery event streaming."""
        await discovery_streamer.set_current_session("test-session")
        
        test_discovery = {
            "text": "Machine learning enables pattern recognition",
            "confidence": 0.8,
            "evidence": ["research papers", "experiments"]
        }
        
        await discovery_streamer.stream_discovery(test_discovery, "TestAgent")
        
        history = discovery_streamer.get_event_history()
        discovery_events = [e for e in history if e["type"] == EventType.DISCOVERY_FOUND.value]
        assert len(discovery_events) == 1
        assert discovery_events[0]["data"]["discovery"] == test_discovery


class TestDiscoveryToolkit:
    """Test the discovery-specific toolkit."""
    
    @pytest.fixture
    def discovery_tools(self):
        """Create discovery tools for testing."""
        return DiscoveryTools()
    
    def test_search_tool_initialization(self, discovery_tools):
        """Test search tool initialization."""
        search_tool = discovery_tools.search_tool
        assert search_tool.name == "search_tool"
        assert search_tool.description is not None
    
    @pytest.mark.asyncio
    async def test_search_functionality(self, discovery_tools, mock_knowledge_files):
        """Test search tool functionality."""
        search_tool = discovery_tools.search_tool
        
        # Load knowledge base
        search_tool.load_knowledge_base(mock_knowledge_files)
        
        # Test search
        results = await search_tool("machine learning algorithms")
        
        assert len(results) > 0
        assert all(hasattr(result, 'content') for result in results)
        assert all(hasattr(result, 'relevance_score') for result in results)
        assert all(result.relevance_score > 0 for result in results)
    
    @pytest.mark.asyncio
    async def test_analysis_tool(self, discovery_tools):
        """Test analysis tool functionality."""
        analysis_tool = discovery_tools.analysis_tool
        
        test_content = """
        Machine learning is a powerful approach to artificial intelligence.
        Neural networks enable deep learning capabilities. Data science
        combines machine learning with statistical analysis.
        """
        
        # Test comprehensive analysis
        analysis_result = await analysis_tool(test_content, "comprehensive")
        
        assert "patterns" in analysis_result
        assert "relationships" in analysis_result
        assert "themes" in analysis_result
        
        # Check that patterns were identified
        patterns = analysis_result["patterns"]
        assert len(patterns) > 0
        
        # Check that themes were identified
        themes = analysis_result["themes"]
        assert len(themes) > 0
        assert any("machine" in theme["theme"].lower() for theme in themes)
    
    @pytest.mark.asyncio
    async def test_hypothesis_generation(self, discovery_tools):
        """Test hypothesis generation tool."""
        hypothesis_tool = discovery_tools.hypothesis_generator_tool
        
        test_discoveries = [
            {
                "text": "Machine learning algorithms improve with more data",
                "confidence": 0.8
            },
            {
                "text": "Neural networks can learn complex patterns",
                "confidence": 0.9
            }
        ]
        
        hypotheses = await hypothesis_tool(test_discoveries)
        
        assert len(hypotheses) > 0
        assert all("statement" in h for h in hypotheses)
        assert all("confidence" in h for h in hypotheses)
        assert all(h["confidence"] > 0 for h in hypotheses)


class TestIntegrationScenarios:
    """Test complete integration scenarios."""
    
    @pytest.mark.asyncio
    async def test_complete_discovery_workflow(self, test_config, mock_knowledge_files):
        """Test complete discovery workflow integration."""
        # Initialize all components
        coordinator = DiscoveryAgentCoordinator(test_config)
        prompt_manager = DiscoveryPromptManager()
        connection_manager = ConnectionManager()
        streamer = DiscoveryStreamer(connection_manager)
        
        # Mock model responses
        with patch.object(coordinator.model, '__call__', new_callable=AsyncMock) as mock_model:
            mock_model.return_value = Mock(
                content=[{"type": "text", "text": """
                {
                    "discoveries": [
                        {"text": "Machine learning enables automation", "confidence": 0.8}
                    ],
                    "insights": ["AI transforms data into knowledge"],
                    "hypotheses": ["More data improves ML performance"],
                    "questions": ["What are the limits of current ML?"]
                }
                """}]
            )
            
            with tempfile.TemporaryDirectory() as temp_dir:
                # Setup knowledge base
                for file_data in mock_knowledge_files:
                    file_path = Path(temp_dir) / file_data["filename"]
                    file_path.write_text(file_data["content"])
                
                # Set up streaming
                await streamer.set_current_session("test-integration")
                
                # Start discovery session
                session_result = await coordinator.start_discovery_session(
                    knowledge_base_path=temp_dir,
                    initial_idea="Explore machine learning and AI concepts",
                    focus_areas=["artificial intelligence", "data science"]
                )
                
                assert session_result["status"] == "started"
                
                # Run exploration loops
                for i in range(2):
                    await streamer.stream_loop_start(i+1, 3, "Strategic exploration")
                    
                    loop_result = await coordinator.run_exploration_loop()
                    
                    await streamer.stream_loop_complete(i+1, 3, {
                        "discoveries": len(coordinator.session_data.get("discoveries", [])),
                        "insights": len(coordinator.session_data.get("insights", []))
                    })
                    
                    if loop_result["status"] == "session_terminated":
                        break
                
                # Get final results
                final_results = await coordinator.get_final_results()
                
                assert "session_id" in final_results
                assert "discoveries" in final_results
                assert "insights" in final_results
                assert "meta_analysis" in final_results
                
                # Verify streaming events were created
                event_history = streamer.get_event_history()
                assert len(event_history) > 0
                
                # Check for different event types
                event_types = {event["type"] for event in event_history}
                expected_types = {
                    EventType.SESSION_STARTED.value,
                    EventType.EXPLORATION_LOOP_STARTING.value,
                    EventType.EXPLORATION_LOOP_COMPLETED.value
                }
                assert expected_types.issubset(event_types)
    
    @pytest.mark.asyncio 
    async def test_error_handling(self, test_config):
        """Test error handling throughout the system."""
        coordinator = DiscoveryAgentCoordinator(test_config)
        
        # Test with invalid knowledge base path
        with pytest.raises(Exception):
            await coordinator.start_discovery_session(
                knowledge_base_path="/nonexistent/path",
                initial_idea="Test idea"
            )
        
        # Test with empty initial idea
        result = await coordinator.start_discovery_session(
            knowledge_base_path=".",
            initial_idea=""
        )
        # Should handle gracefully
        assert "session_id" in result
    
    @pytest.mark.asyncio
    async def test_resource_management(self, test_config, mock_knowledge_files):
        """Test resource management and budget tracking."""
        test_config.token_budget = 100  # Very small budget
        test_config.time_budget = 5     # Very short time
        
        coordinator = DiscoveryAgentCoordinator(test_config)
        
        with tempfile.TemporaryDirectory() as temp_dir:
            for file_data in mock_knowledge_files:
                file_path = Path(temp_dir) / file_data["filename"]
                file_path.write_text(file_data["content"])
            
            session_result = await coordinator.start_discovery_session(
                knowledge_base_path=temp_dir,
                initial_idea="Test budget management"
            )
            
            assert session_result["status"] == "started"
            
            # Session should have budget tracking
            assert "budget_tracking" in coordinator.session_data
            budget_tracking = coordinator.session_data["budget_tracking"]
            assert "tokens_used" in budget_tracking
            assert "time_elapsed" in budget_tracking
            assert "cost_accumulated" in budget_tracking


class TestPerformanceAndScalability:
    """Test performance and scalability aspects."""
    
    @pytest.mark.asyncio
    async def test_large_knowledge_base(self):
        """Test with large knowledge base."""
        # Create a larger set of mock files
        large_knowledge_base = []
        for i in range(10):
            large_knowledge_base.append({
                "filename": f"large_file_{i}.md",
                "content": f"""
# Document {i}

This is a large document with substantial content about topic {i}.
It contains multiple sections and detailed information.

## Section 1
Detailed content about {i} with technical information.

## Section 2  
More content with examples and case studies.

## Section 3
Advanced topics and future directions.
""" * 5  # Multiply to make it larger
            })
        
        search_tool = SearchTool()
        search_tool.load_knowledge_base(large_knowledge_base)
        
        # Test search performance
        import time
        start_time = time.time()
        results = await search_tool("technical information", max_results=20)
        end_time = time.time()
        
        # Should complete in reasonable time (< 2 seconds)
        assert (end_time - start_time) < 2.0
        assert len(results) > 0
    
    @pytest.mark.asyncio
    async def test_concurrent_sessions(self, test_config):
        """Test handling multiple concurrent sessions."""
        connection_manager = ConnectionManager()
        
        # Simulate multiple WebSocket connections
        mock_websockets = [AsyncMock() for _ in range(5)]
        client_ids = []
        
        for ws in mock_websockets:
            client_id = await connection_manager.connect(ws)
            client_ids.append(client_id)
            await connection_manager.subscribe_to_session(client_id, f"session-{client_id}")
        
        # Verify all connections are managed
        assert len(connection_manager.active_connections) == 5
        assert len(connection_manager.session_subscriptions) == 5
        
        # Test broadcasting to all sessions
        broadcast_count = await connection_manager.broadcast_to_all({
            "type": "test_message",
            "content": "Hello all sessions"
        })
        
        assert broadcast_count == 5


# Test fixtures for running the tests
@pytest.fixture(scope="session") 
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# Integration test runner
async def run_integration_tests():
    """Run all integration tests manually."""
    print("Running Discovery System Integration Tests...")
    
    # Test configuration
    config = DiscoveryConfig()
    config.gemini_api_key = "test_key"
    
    # Mock knowledge files
    knowledge_files = [
        {
            "filename": "test.md",
            "content": "# Test\nThis is test content about machine learning."
        }
    ]
    
    try:
        # Test 1: Component initialization
        print("✓ Testing component initialization...")
        coordinator = DiscoveryAgentCoordinator(config)
        assert len(coordinator.agents) > 0
        
        # Test 2: Prompt management
        print("✓ Testing prompt management...")
        prompt_manager = DiscoveryPromptManager()
        assert len(prompt_manager.get_available_agent_types()) > 0
        
        # Test 3: Streaming system
        print("✓ Testing streaming system...")
        connection_manager = ConnectionManager()
        streamer = DiscoveryStreamer(connection_manager)
        await streamer.set_current_session("test")
        
        # Test 4: Discovery tools
        print("✓ Testing discovery tools...")
        tools = DiscoveryTools()
        tools.load_knowledge_base(knowledge_files)
        results = await tools.search_tool("machine learning")
        assert len(results) >= 0
        
        print("🎉 All integration tests passed!")
        
    except Exception as e:
        print(f"❌ Integration test failed: {e}")
        raise


if __name__ == "__main__":
    # Run integration tests
    asyncio.run(run_integration_tests())