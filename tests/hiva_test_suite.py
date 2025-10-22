#!/usr/bin/env python3
# -*- coding: utf-8 -*-
\"\"\"
Comprehensive Test Suite for HiVA MBTI Dynamic Agent Generation System

This test suite validates all components of the Agent Discovery System:
- Task Complexity Analyzer
- MBTI+Domain Template System
- HiVA Evolution Engine
- Dynamic Agent Generator
- User Proxy Agent
- Knowledge Infrastructure
\"\"\"

import asyncio
import pytest
import time
from unittest.mock import Mock, AsyncMock
from typing import Dict, List, Any

# Import system components
try:
    from agentscope.discovery import (
        TaskComplexityAnalyzer,
        ComplexityLevel,
        MBTIDomainTemplateRegistry,
        HiVAEvolutionEngine,
        DynamicAgentGenerator,
        HiVAUserProxyAgent,
        UniversalKnowledgeGraph,
        ContinuousLearningSystem
    )
except ImportError as e:
    print(f\"Import error: {e}\")
    print(\"This test requires the AgentScope framework with the discovery module\")
    exit(1)


class TestTaskComplexityAnalyzer:
    \"\"\"Test suite for Task Complexity Analyzer.\"\"\"
    
    @pytest.fixture
    def mock_model(self):
        \"\"\"Create a mock model for testing.\"\"\"
        model = Mock()
        model.generate = AsyncMock(return_value=\"Test response\")
        return model
    
    @pytest.fixture
    def mock_formatter(self):
        \"\"\"Create a mock formatter for testing.\"\"\"
        formatter = Mock()
        formatter.format = Mock(return_value=\"Formatted prompt\")
        return formatter
    
    @pytest.fixture
    def analyzer(self, mock_model, mock_formatter):
        \"\"\"Create a TaskComplexityAnalyzer for testing.\"\"\"
        return TaskComplexityAnalyzer(
            model=mock_model,
            formatter=mock_formatter
        )
    
    def test_initialization(self, analyzer):
        \"\"\"Test analyzer initialization.\"\"\"
        assert analyzer is not None
        assert hasattr(analyzer, 'complexity_history')
        assert hasattr(analyzer, 'user_patterns')
        assert hasattr(analyzer, 'domain_keywords')
        assert hasattr(analyzer, 'mbti_strengths')
    
    def test_domain_keywords_initialization(self, analyzer):
        \"\"\"Test that domain keywords are properly initialized.\"\"\"
        domain_keywords = analyzer.domain_keywords
        
        assert 'science' in domain_keywords
        assert 'technology' in domain_keywords
        assert 'humanities' in domain_keywords
        assert 'business' in domain_keywords
        
        # Test specific keywords
        assert 'physics' in domain_keywords['science']
        assert 'programming' in domain_keywords['technology']
    
    def test_mbti_strengths_initialization(self, analyzer):
        \"\"\"Test that MBTI strengths are properly initialized.\"\"\"
        mbti_strengths = analyzer.mbti_strengths
        
        # Test all 16 MBTI types are present
        expected_types = [
            'INTJ', 'INTP', 'ENTJ', 'ENTP',
            'INFJ', 'INFP', 'ENFJ', 'ENFP',
            'ISTJ', 'ISFJ', 'ESTJ', 'ESFJ',
            'ISTP', 'ISFP', 'ESTP', 'ESFP'
        ]
        
        for mbti_type in expected_types:
            assert mbti_type in mbti_strengths
            assert 'functions' in mbti_strengths[mbti_type]
            assert 'strengths' in mbti_strengths[mbti_type]
            assert 'domains' in mbti_strengths[mbti_type]
    
    @pytest.mark.asyncio
    async def test_fallback_analysis(self, analyzer):
        \"\"\"Test fallback analysis functionality.\"\"\"
        # Test simple task
        simple_task = \"What is photosynthesis?\"
        result = analyzer._fallback_analysis(simple_task)
        
        assert result.complexity_level in [ComplexityLevel.SIMPLE, ComplexityLevel.SIMPLE_MEDIUM]
        assert result.agent_count >= 1
        assert len(result.mbti_recommendations) > 0
        
        # Test complex task
        complex_task = \"Design a comprehensive strategy for addressing climate change while maintaining economic growth, considering social equity, technological innovation, and international cooperation across multiple domains including environmental science, economics, policy, and social psychology.\"
        result = analyzer._fallback_analysis(complex_task)
        
        assert result.complexity_level.value >= 3
        assert result.agent_count > 1
    
    def test_generate_default_mbti_recommendations(self, analyzer):
        \"\"\"Test default MBTI recommendation generation.\"\"\"
        from agentscope.discovery._task_complexity_analyzer import DomainRequirement
        
        # Test with science domain
        domain_reqs = [DomainRequirement(domain='science', importance=0.8, specializations=[])]
        recommendations = analyzer._generate_default_mbti_recommendations(
            ComplexityLevel.MEDIUM, domain_reqs
        )
        
        assert len(recommendations) > 0
        assert all(isinstance(rec, str) for rec in recommendations)
        assert all(len(rec) == 4 for rec in recommendations)  # Valid MBTI format


class TestMBTIDomainTemplateRegistry:
    \"\"\"Test suite for MBTI+Domain Template Registry.\"\"\"
    
    @pytest.fixture
    def registry(self):
        \"\"\"Create a template registry for testing.\"\"\"
        return MBTIDomainTemplateRegistry()
    
    def test_initialization(self, registry):
        \"\"\"Test registry initialization.\"\"\"
        assert registry is not None
        assert len(registry.templates) > 0
        assert len(registry.mbti_cognitive_functions) == 16
        assert len(registry.domain_expertise_profiles) > 0
    
    def test_cognitive_functions(self, registry):
        \"\"\"Test cognitive function initialization.\"\"\"
        functions = registry.mbti_cognitive_functions
        
        # Test INTJ functions
        intj_functions = functions['INTJ']
        assert intj_functions.dominant.name == 'Ni'
        assert intj_functions.auxiliary.name == 'Te'
        
        # Test ENTP functions
        entp_functions = functions['ENTP']
        assert entp_functions.dominant.name == 'Ne'
        assert entp_functions.auxiliary.name == 'Ti'
    
    def test_domain_profiles(self, registry):
        \"\"\"Test domain expertise profiles.\"\"\"
        profiles = registry.domain_expertise_profiles
        
        assert 'Physics' in profiles
        assert 'Computer_Science' in profiles
        assert 'Psychology' in profiles
        
        # Test physics profile
        physics = profiles['Physics']
        assert physics.domain_name == 'Physics'
        assert len(physics.specialization_areas) > 0
        assert len(physics.key_competencies) > 0
    
    def test_template_creation(self, registry):
        \"\"\"Test template creation and retrieval.\"\"\"
        # Get a specific template
        template = registry.get_template('INTJ', 'Physics')
        assert template is not None
        assert template.mbti_type == 'INTJ'
        assert template.domain_expertise.domain_name == 'Physics'
        
        # Test template configuration
        config = template.get_agent_config()
        assert 'name' in config
        assert 'sys_prompt' in config
        assert 'personality_type' in config
    
    def test_optimal_template_selection(self, registry):
        \"\"\"Test optimal template selection.\"\"\"
        optimal_templates = registry.get_optimal_templates(
            complexity_level=3,
            domain_requirements=['Physics', 'Computer_Science'],
            mbti_preferences=['INTJ', 'INTP', 'ENTJ']
        )
        
        assert len(optimal_templates) > 0
        assert len(optimal_templates) <= 3  # Complexity level 3
        
        # Verify templates match requirements
        for template in optimal_templates:
            assert template.mbti_type in ['INTJ', 'INTP', 'ENTJ']
            assert template.domain_expertise.domain_name in ['Physics', 'Computer_Science']
    
    def test_available_combinations(self, registry):
        \"\"\"Test available combination listing.\"\"\"
        combinations = registry.list_available_combinations()
        
        assert isinstance(combinations, dict)
        assert len(combinations) > 0
        
        # Test that all MBTI types have some domains
        for mbti_type, domains in combinations.items():
            assert len(mbti_type) == 4  # Valid MBTI format
            assert len(domains) > 0


class TestHiVAEvolutionEngine:
    \"\"\"Test suite for HiVA Evolution Engine.\"\"\"
    
    @pytest.fixture
    def evolution_engine(self):
        \"\"\"Create an evolution engine for testing.\"\"\"
        return HiVAEvolutionEngine(evolution_frequency=1.0)  # Fast for testing
    
    def test_initialization(self, evolution_engine):
        \"\"\"Test evolution engine initialization.\"\"\"
        assert evolution_engine is not None
        assert evolution_engine.learning_rate == 0.1
        assert not evolution_engine.learning_active
        assert len(evolution_engine.evolution_history) == 0
    
    @pytest.mark.asyncio
    async def test_start_stop_engine(self, evolution_engine):
        \"\"\"Test starting and stopping the evolution engine.\"\"\"
        # Start engine
        await evolution_engine.start_evolution_engine()
        assert evolution_engine.learning_active
        assert evolution_engine.evolution_task is not None
        
        # Wait a bit for evolution cycle
        await asyncio.sleep(2)
        
        # Stop engine
        await evolution_engine.stop_evolution_engine()
        assert not evolution_engine.learning_active
    
    @pytest.mark.asyncio
    async def test_record_task_execution(self, evolution_engine):
        \"\"\"Test recording task execution.\"\"\"
        initial_count = len(evolution_engine.evolution_history)
        
        await evolution_engine.record_task_execution(
            task=\"Test task\",
            agents_used=[\"INTJ_Physics_001\", \"ENTP_Computer_Science_002\"],
            execution_result={'success_score': 0.8}
        )
        
        assert len(evolution_engine.evolution_history) == initial_count + 1
        
        # Check network topology updates
        assert len(evolution_engine.agent_network) == 2
        assert \"INTJ_Physics_001\" in evolution_engine.agent_network
        assert \"ENTP_Computer_Science_002\" in evolution_engine.agent_network
    
    @pytest.mark.asyncio
    async def test_record_user_feedback(self, evolution_engine):
        \"\"\"Test recording user feedback.\"\"\"
        await evolution_engine.record_user_feedback(
            user_id=\"test_user\",
            feedback={'satisfaction': 0.9, 'clarity': 0.8}
        )
        
        assert \"test_user\" in evolution_engine.user_preferences
        assert evolution_engine.user_preferences[\"test_user\"]['satisfaction'] == 0.9
    
    def test_network_efficiency_calculation(self, evolution_engine):
        \"\"\"Test network efficiency calculation.\"\"\"
        # Empty network
        efficiency = evolution_engine._calculate_network_efficiency()
        assert efficiency == 0.0
        
        # Add some agents
        evolution_engine.agent_network[\"agent1\"].add(\"agent2\")
        evolution_engine.agent_network[\"agent2\"].add(\"agent1\")
        evolution_engine.collaboration_strengths[(\"agent1\", \"agent2\")] = 0.8
        
        efficiency = evolution_engine._calculate_network_efficiency()
        assert efficiency > 0.0
    
    def test_evolution_summary(self, evolution_engine):
        \"\"\"Test evolution summary generation.\"\"\"
        summary = evolution_engine.get_evolution_summary()
        
        assert isinstance(summary, dict)
        assert 'total_events' in summary
        assert 'network_size' in summary
        assert 'learning_active' in summary


class TestDynamicAgentGenerator:
    \"\"\"Test suite for Dynamic Agent Generator.\"\"\"
    
    @pytest.fixture
    def mock_template_registry(self):
        \"\"\"Create a mock template registry.\"\"\"
        registry = Mock()
        registry.get_optimal_templates = Mock(return_value=[])
        registry.get_template = Mock(return_value=None)
        registry.list_available_combinations = Mock(return_value={})
        return registry
    
    @pytest.fixture
    def mock_evolution_engine(self):
        \"\"\"Create a mock evolution engine.\"\"\"
        engine = Mock()
        engine.record_task_execution = AsyncMock()
        return engine
    
    @pytest.fixture
    def generator(self, mock_template_registry, mock_evolution_engine):
        \"\"\"Create a dynamic agent generator for testing.\"\"\"
        return DynamicAgentGenerator(
            template_registry=mock_template_registry,
            evolution_engine=mock_evolution_engine,
            max_concurrent_agents=5
        )
    
    def test_initialization(self, generator):
        \"\"\"Test generator initialization.\"\"\"
        assert generator is not None
        assert generator.max_concurrent_agents == 5
        assert len(generator.active_agents) == 0
        assert not generator.cleanup_active
    
    @pytest.mark.asyncio
    async def test_lifecycle_management(self, generator):
        \"\"\"Test agent lifecycle management.\"\"\"
        # Start lifecycle management
        await generator.start_agent_lifecycle_management()
        assert generator.cleanup_active
        
        # Stop lifecycle management
        await generator.stop_agent_lifecycle_management()
        assert not generator.cleanup_active
    
    def test_generation_statistics(self, generator):
        \"\"\"Test generation statistics.\"\"\"
        stats = generator.get_generation_statistics()
        
        assert isinstance(stats, dict)
        assert 'active_agents' in stats
        assert 'total_generated' in stats
        assert 'average_performance' in stats
    
    @pytest.mark.asyncio
    async def test_update_agent_performance(self, generator):
        \"\"\"Test updating agent performance metrics.\"\"\"
        # This test requires a real agent in active_agents
        # For now, test that the method exists and handles missing agents gracefully
        await generator.update_agent_performance(
            \"nonexistent_agent\",
            {'performance': 0.8}
        )
        # Should not raise an exception
    
    def test_get_active_agents(self, generator):
        \"\"\"Test getting active agents.\"\"\"
        active_agents = generator.get_active_agents()
        assert isinstance(active_agents, list)
        assert len(active_agents) == 0  # Initially empty


class TestUniversalKnowledgeGraph:
    \"\"\"Test suite for Universal Knowledge Graph.\"\"\"
    
    @pytest.fixture
    def knowledge_graph(self):
        \"\"\"Create a knowledge graph for testing.\"\"\"
        return UniversalKnowledgeGraph(max_nodes=100)
    
    def test_initialization(self, knowledge_graph):
        \"\"\"Test knowledge graph initialization.\"\"\"
        assert knowledge_graph is not None
        assert knowledge_graph.max_nodes == 100
        assert len(knowledge_graph.nodes) == 0
        assert len(knowledge_graph.patterns) == 0
    
    def test_add_knowledge_node(self, knowledge_graph):
        \"\"\"Test adding knowledge nodes.\"\"\"
        node_id = knowledge_graph.add_knowledge_node(
            node_type=\"test\",
            content={\"description\": \"Test node\", \"value\": 42},
            metadata={\"source\": \"test\"}
        )
        
        assert node_id in knowledge_graph.nodes
        node = knowledge_graph.nodes[node_id]
        assert node.node_type == \"test\"
        assert node.content[\"description\"] == \"Test node\"
        assert node.metadata[\"source\"] == \"test\"
    
    def test_add_learning_pattern(self, knowledge_graph):
        \"\"\"Test adding learning patterns.\"\"\"
        pattern_id = knowledge_graph.add_learning_pattern(
            pattern_type=\"test_pattern\",
            pattern_data={\"approach\": \"analytical\"},
            confidence_score=0.8
        )
        
        assert pattern_id in knowledge_graph.patterns
        pattern = knowledge_graph.patterns[pattern_id]
        assert pattern.pattern_type == \"test_pattern\"
        assert pattern.confidence_score == 0.8
    
    def test_find_nodes_by_type(self, knowledge_graph):
        \"\"\"Test finding nodes by type.\"\"\"
        # Add test nodes
        knowledge_graph.add_knowledge_node(\"task\", {\"description\": \"Task 1\"})
        knowledge_graph.add_knowledge_node(\"task\", {\"description\": \"Task 2\"})
        knowledge_graph.add_knowledge_node(\"pattern\", {\"description\": \"Pattern 1\"})
        
        task_nodes = knowledge_graph.find_nodes_by_type(\"task\")
        assert len(task_nodes) == 2
        assert all(node.node_type == \"task\" for node in task_nodes)
    
    def test_find_nodes_by_content(self, knowledge_graph):
        \"\"\"Test finding nodes by content keywords.\"\"\"
        # Add test nodes
        knowledge_graph.add_knowledge_node(
            \"task\", 
            {\"description\": \"Analyze machine learning algorithms\"}
        )
        knowledge_graph.add_knowledge_node(
            \"task\", 
            {\"description\": \"Study quantum physics concepts\"}
        )
        
        # Search for machine learning
        ml_nodes = knowledge_graph.find_nodes_by_content([\"machine\", \"learning\"])
        assert len(ml_nodes) >= 1
        
        # Search for physics
        physics_nodes = knowledge_graph.find_nodes_by_content([\"physics\"])
        assert len(physics_nodes) >= 1
    
    def test_get_insights_for_task(self, knowledge_graph):
        \"\"\"Test getting insights for a task.\"\"\"
        # Add some patterns and nodes
        knowledge_graph.add_learning_pattern(
            \"success\",
            {\"approach\": \"collaborative\"},
            confidence_score=0.9
        )
        
        insights = knowledge_graph.get_insights_for_task(
            \"Solve complex machine learning problem\"
        )
        
        assert isinstance(insights, dict)
        assert 'relevant_patterns' in insights
        assert 'success_patterns' in insights
        assert 'recommendations' in insights
    
    def test_statistics(self, knowledge_graph):
        \"\"\"Test knowledge graph statistics.\"\"\"
        # Add some test data
        knowledge_graph.add_knowledge_node(\"task\", {\"desc\": \"test\"})
        knowledge_graph.add_learning_pattern(\"test\", {\"data\": \"test\"}, 0.5)
        
        stats = knowledge_graph.get_statistics()
        
        assert isinstance(stats, dict)
        assert 'total_nodes' in stats
        assert 'total_patterns' in stats
        assert stats['total_nodes'] >= 1
        assert stats['total_patterns'] >= 1


class TestContinuousLearningSystem:
    \"\"\"Test suite for Continuous Learning System.\"\"\"
    
    @pytest.fixture
    def learning_system(self):
        \"\"\"Create a learning system for testing.\"\"\"
        knowledge_graph = UniversalKnowledgeGraph()
        return ContinuousLearningSystem(knowledge_graph)
    
    def test_initialization(self, learning_system):
        \"\"\"Test learning system initialization.\"\"\"
        assert learning_system is not None
        assert learning_system.knowledge_graph is not None
        assert len(learning_system.learning_sessions) == 0
    
    @pytest.mark.asyncio
    async def test_learn_from_task_execution(self, learning_system):
        \"\"\"Test learning from task execution.\"\"\"
        initial_sessions = len(learning_system.learning_sessions)
        
        await learning_system.learn_from_task_execution(
            task=\"Test machine learning task\",
            agents_used=[\"INTJ_Computer_Science_001\", \"ENTP_Physics_002\"],
            execution_results={'synthesis': 'Great collaboration'},
            success_metrics={'success_score': 0.85}
        )
        
        assert len(learning_system.learning_sessions) == initial_sessions + 1
        
        # Check that knowledge was added
        task_nodes = learning_system.knowledge_graph.find_nodes_by_type(\"task\")
        assert len(task_nodes) >= 1
    
    def test_get_learning_recommendations(self, learning_system):
        \"\"\"Test getting learning recommendations.\"\"\"
        recommendations = learning_system.get_learning_recommendations({
            'task': 'Analyze complex data patterns'
        })
        
        assert isinstance(recommendations, list)
        # Should have default recommendations even with empty knowledge base
        assert len(recommendations) > 0


# Integration test
class TestHiVASystemIntegration:
    \"\"\"Integration test for the complete HiVA system.\"\"\"
    
    @pytest.mark.asyncio
    async def test_system_components_integration(self):
        \"\"\"Test that all components can be created and work together.\"\"\"
        # Create all components
        template_registry = MBTIDomainTemplateRegistry()
        evolution_engine = HiVAEvolutionEngine(evolution_frequency=10.0)
        knowledge_graph = UniversalKnowledgeGraph()
        learning_system = ContinuousLearningSystem(knowledge_graph)
        
        # Verify components can interact
        templates = template_registry.get_optimal_templates(
            complexity_level=2,
            domain_requirements=['Physics', 'Computer_Science']
        )
        
        assert len(templates) > 0
        
        # Test evolution engine
        await evolution_engine.record_task_execution(
            task=\"Integration test\",
            agents_used=[\"test_agent_1\"],
            execution_result={'success_score': 0.7}
        )
        
        summary = evolution_engine.get_evolution_summary()
        assert summary['total_events'] >= 1
        
        # Test knowledge graph
        node_id = knowledge_graph.add_knowledge_node(
            \"integration_test\",
            {\"description\": \"Integration test node\"}
        )
        
        assert node_id in knowledge_graph.nodes


if __name__ == \"__main__\":
    # Run tests
    print(\"Running HiVA MBTI Dynamic Agent Generation System Test Suite\")
    print(\"=\" * 60)
    
    # Run async tests
    async def run_async_tests():
        # Test TaskComplexityAnalyzer fallback
        print(\"\n🔬 Testing Task Complexity Analyzer...\")
        try:
            analyzer = TaskComplexityAnalyzer()
            result = analyzer._fallback_analysis(\"What is quantum computing?\")
            print(f\"✅ Simple task complexity: {result.complexity_level.value}\")
            
            complex_result = analyzer._fallback_analysis(
                \"Design a comprehensive multi-disciplinary approach to sustainable urban development\"
            )
            print(f\"✅ Complex task complexity: {complex_result.complexity_level.value}\")
        except Exception as e:
            print(f\"❌ Task Complexity Analyzer test failed: {e}\")
        
        # Test MBTI Template Registry
        print(\"\n🎭 Testing MBTI Template Registry...\")
        try:
            registry = MBTIDomainTemplateRegistry()
            print(f\"✅ Template registry created with {registry.get_template_count()} templates\")
            
            template = registry.get_template('INTJ', 'Physics')
            if template:
                print(f\"✅ Retrieved INTJ Physics template: {template.mbti_type}\")
            else:
                print(\"❌ Failed to retrieve template\")
        except Exception as e:
            print(f\"❌ MBTI Template Registry test failed: {e}\")
        
        # Test HiVA Evolution Engine
        print(\"\n🧠 Testing HiVA Evolution Engine...\")
        try:
            engine = HiVAEvolutionEngine(evolution_frequency=1.0)
            await engine.start_evolution_engine()
            print(\"✅ Evolution engine started\")
            
            await engine.record_task_execution(
                \"Test task\",
                [\"INTJ_Physics_001\"],
                {'success_score': 0.8}
            )
            print(\"✅ Task execution recorded\")
            
            await engine.stop_evolution_engine()
            print(\"✅ Evolution engine stopped\")
        except Exception as e:
            print(f\"❌ HiVA Evolution Engine test failed: {e}\")
        
        # Test Knowledge Graph
        print(\"\n📚 Testing Knowledge Infrastructure...\")
        try:
            kg = UniversalKnowledgeGraph()
            node_id = kg.add_knowledge_node(
                \"test\",
                {\"description\": \"Test knowledge node\"}
            )
            print(f\"✅ Knowledge node created: {node_id}\")
            
            pattern_id = kg.add_learning_pattern(
                \"test_pattern\",
                {\"data\": \"test\"},
                0.8
            )
            print(f\"✅ Learning pattern created: {pattern_id}\")
            
            stats = kg.get_statistics()
            print(f\"✅ Knowledge graph stats: {stats['total_nodes']} nodes, {stats['total_patterns']} patterns\")
        except Exception as e:
            print(f\"❌ Knowledge Infrastructure test failed: {e}\")
    
    # Run the async tests
    asyncio.run(run_async_tests())
    
    print(\"\n🎯 Basic Integration Test...\")
    try:
        # Test basic integration
        registry = MBTIDomainTemplateRegistry()
        templates = registry.get_optimal_templates(
            complexity_level=3,
            domain_requirements=['Physics', 'Computer_Science']
        )
        print(f\"✅ Retrieved {len(templates)} optimal templates for complexity level 3\")
        
        for i, template in enumerate(templates[:3]):
            print(f\"  {i+1}. {template.mbti_type} {template.domain_expertise.domain_name} Expert\")
        
    except Exception as e:
        print(f\"❌ Integration test failed: {e}\")
    
    print(\"\n\" + \"=\" * 60)
    print(\"🚀 Test Suite Complete!\")
    print(\"\nTo run full pytest suite:\")
    print(\"pytest hiva_test_suite.py -v\")
    print(\"\nTo run specific test class:\")
    print(\"pytest hiva_test_suite.py::TestTaskComplexityAnalyzer -v\")