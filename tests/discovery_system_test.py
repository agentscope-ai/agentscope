# -*- coding: utf-8 -*-
"""Unit tests for the Agent Discovery System."""
import unittest
import tempfile
import os
import json
import asyncio
from unittest.mock import Mock, AsyncMock, patch

# Import discovery system components
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agentscope.discovery._message import DiscoveryMessage, MessageType, BudgetInfo
from agentscope.discovery._state import ExplorationState, SurpriseEvent, TemporaryInsight
from agentscope.discovery._knowledge_infrastructure import VectorDatabase, GraphDatabase, DocumentChunk, ConceptNode
from agentscope.discovery._discovery_tools import SearchTool, AnalysisTool, BayesianSurpriseTool
from agentscope.discovery._user_proxy_agent import UserProxyAgent


class TestDiscoveryMessage(unittest.TestCase):
    """Test DiscoveryMessage functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.budget_info = BudgetInfo(
            loops_remaining=5,
            max_loops=5,
            tokens_remaining=10000,
            token_budget=10000,
            time_remaining=3600.0,
            cost_remaining=10.0,
        )
    
    def test_message_creation(self):
        """Test creating discovery messages."""
        msg = DiscoveryMessage.create_exploration_query(
            query="test query",
            context={"focus_areas": ["ai", "ml"]},
            budget_info=self.budget_info,
        )
        
        self.assertEqual(msg.message_type, MessageType.QUERY_EXPLORE)
        self.assertEqual(msg.payload["query"], "test query")
        self.assertIn("focus_areas", msg.payload["context"])
        
    def test_message_serialization(self):
        """Test message serialization and deserialization."""
        original_msg = DiscoveryMessage.create_evidence_message(
            evidence={"finding": "test finding"},
            confidence=0.8,
            sources=["source1", "source2"],
            budget_info=self.budget_info,
        )
        
        # Convert to dict and back
        msg_dict = original_msg.to_dict()
        restored_msg = DiscoveryMessage.from_dict(msg_dict)
        
        self.assertEqual(original_msg.message_type, restored_msg.message_type)
        self.assertEqual(original_msg.payload, restored_msg.payload)
        self.assertEqual(original_msg.budget_info.loops_remaining, restored_msg.budget_info.loops_remaining)
    
    def test_budget_checks(self):
        """Test budget constraint checking."""
        # Normal budget
        msg = DiscoveryMessage.create_exploration_query(
            query="test",
            context={},
            budget_info=self.budget_info,
        )
        self.assertFalse(msg.is_budget_critical())
        
        # Critical budget
        critical_budget = BudgetInfo(
            loops_remaining=1,
            max_loops=5,
            tokens_remaining=500,
            token_budget=10000,
            time_remaining=30.0,
            cost_remaining=0.5,
        )
        
        msg.update_budget(critical_budget)
        self.assertTrue(msg.is_budget_critical())


class TestExplorationState(unittest.TestCase):
    """Test ExplorationState functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.state = ExplorationState()
    
    def test_state_initialization(self):
        """Test state initialization."""
        self.assertIsNotNone(self.state.session_id)
        self.assertEqual(self.state.current_loop, 0)
        self.assertFalse(self.state.is_budget_exhausted())
    
    def test_loop_management(self):
        """Test exploration loop management."""
        # Start new loop
        success = self.state.start_new_loop()
        self.assertTrue(success)
        self.assertEqual(self.state.current_loop, 1)
        self.assertEqual(self.state.budget_info.loops_remaining, 4)
        
        # Exhaust budget
        self.state.budget_info.loops_remaining = 0
        success = self.state.start_new_loop()
        self.assertFalse(success)
    
    def test_budget_tracking(self):
        """Test budget consumption tracking."""
        initial_tokens = self.state.budget_info.tokens_remaining
        
        self.state.update_budget(tokens_used=1000, time_used=100.0, cost_used=1.0)
        
        self.assertEqual(self.state.budget_info.tokens_remaining, initial_tokens - 1000)
        self.assertLessEqual(self.state.budget_info.time_remaining, 3500.0)
        self.assertLessEqual(self.state.budget_info.cost_remaining, 9.0)
    
    def test_insight_management(self):
        """Test insight and surprise event management."""
        # Add surprise event
        event = SurpriseEvent(
            content="surprising discovery",
            surprise_score=0.9,
            paradigm_shift=True,
        )
        self.state.add_surprise_event(event)
        
        self.assertEqual(len(self.state.surprise_buffer), 1)
        high_surprise = self.state.get_high_surprise_events(threshold=0.8)
        self.assertEqual(len(high_surprise), 1)
        
        # Add temporary insight
        insight = TemporaryInsight(
            insight="novel connection",
            confidence=0.8,
            novelty_score=0.9,
        )
        self.state.add_temporary_insight(insight)
        
        self.assertEqual(len(self.state.working_memory), 1)
        high_conf = self.state.get_high_confidence_insights(threshold=0.7)
        self.assertEqual(len(high_conf), 1)
    
    def test_state_serialization(self):
        """Test state serialization and deserialization."""
        # Add some data to state
        self.state.visited_concepts.add("concept1")
        self.state.exploration_frontier.append("frontier_concept")
        
        # Serialize to dict
        state_dict = self.state.to_dict()
        
        # Deserialize
        restored_state = ExplorationState.from_dict(state_dict)
        
        self.assertEqual(self.state.session_id, restored_state.session_id)
        self.assertEqual(self.state.visited_concepts, restored_state.visited_concepts)
        self.assertEqual(self.state.exploration_frontier, restored_state.exploration_frontier)


class TestVectorDatabase(unittest.TestCase):
    """Test VectorDatabase functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.db = VectorDatabase(
            storage_path=os.path.join(self.temp_dir, "test_vector_db.json"),
            use_faiss=False,  # Disable FAISS for testing
        )
    
    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_chunk_addition(self):
        """Test adding document chunks."""
        chunks = [
            DocumentChunk(
                id="chunk1",
                content="This is test content about artificial intelligence.",
                source_file="test1.txt",
                chunk_index=0,
                metadata={"type": "test"},
            ),
            DocumentChunk(
                id="chunk2", 
                content="Machine learning algorithms are powerful tools.",
                source_file="test2.txt",
                chunk_index=0,
                metadata={"type": "test"},
            ),
        ]
        
        self.db.add_document_chunks(chunks)
        
        self.assertEqual(len(self.db.chunks), 2)
        self.assertIn("chunk1", self.db.chunk_index)
        self.assertIn("chunk2", self.db.chunk_index)
    
    def test_similarity_search(self):
        """Test similarity search functionality."""
        # Add test chunks
        chunks = [
            DocumentChunk(
                id="ai_chunk",
                content="Artificial intelligence and machine learning",
                source_file="ai.txt",
                chunk_index=0,
                metadata={},
            ),
            DocumentChunk(
                id="cooking_chunk",
                content="Cooking recipes and kitchen techniques",
                source_file="cooking.txt", 
                chunk_index=0,
                metadata={},
            ),
        ]
        
        self.db.add_document_chunks(chunks)
        
        # Search for AI-related content
        results = self.db.search_similar("artificial intelligence", top_k=5)
        
        self.assertGreater(len(results), 0)
        # First result should be more relevant
        best_chunk, best_score = results[0]
        self.assertEqual(best_chunk.id, "ai_chunk")
    
    def test_persistence(self):
        """Test database persistence."""
        # Add chunks and save
        chunks = [
            DocumentChunk(
                id="persist_test",
                content="Test persistence functionality",
                source_file="persist.txt",
                chunk_index=0,
                metadata={"test": True},
            ),
        ]
        
        self.db.add_document_chunks(chunks)
        self.db.save_to_storage()
        
        # Create new database and load
        new_db = VectorDatabase(
            storage_path=os.path.join(self.temp_dir, "test_vector_db.json"),
            use_faiss=False,
        )
        
        self.assertEqual(len(new_db.chunks), 1)
        self.assertEqual(new_db.chunks[0].id, "persist_test")


class TestGraphDatabase(unittest.TestCase):
    """Test GraphDatabase functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.db = GraphDatabase(
            storage_path=os.path.join(self.temp_dir, "test_graph_db.json")
        )
    
    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    def test_concept_management(self):
        """Test adding and finding concepts."""
        concept = ConceptNode(
            id="ai_concept",
            name="Artificial Intelligence",
            concept_type="field",
            confidence=0.9,
            frequency=5,
            sources=["source1"],
            properties={"domain": "computer_science"},
        )
        
        self.db.add_concept(concept)
        
        self.assertIn("ai_concept", self.db.concepts)
        self.assertTrue(self.db.graph.has_node("ai_concept"))
        
        # Test finding concepts
        found = self.db.find_concepts(name_pattern="Artificial")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].name, "Artificial Intelligence")
    
    def test_relationship_management(self):
        """Test adding relationships between concepts."""
        # Add two concepts
        concept1 = ConceptNode(
            id="ai", name="AI", concept_type="field",
            confidence=0.9, frequency=1, sources=[], properties={}
        )
        concept2 = ConceptNode(
            id="ml", name="Machine Learning", concept_type="subfield", 
            confidence=0.8, frequency=1, sources=[], properties={}
        )
        
        self.db.add_concept(concept1)
        self.db.add_concept(concept2)
        
        # Add relationship
        self.db.add_relationship("ai", "ml", "contains", weight=0.9)
        
        self.assertTrue(self.db.graph.has_edge("ai", "ml"))
        edge_data = self.db.graph["ai"]["ml"]
        self.assertEqual(edge_data["relationship_type"], "contains")
        self.assertEqual(edge_data["weight"], 0.9)
    
    def test_graph_analysis(self):
        """Test graph analysis functions."""
        # Create a small test graph
        concepts = [
            ConceptNode(id="a", name="A", concept_type="test", confidence=1.0, frequency=1, sources=[], properties={}),
            ConceptNode(id="b", name="B", concept_type="test", confidence=1.0, frequency=1, sources=[], properties={}),
            ConceptNode(id="c", name="C", concept_type="test", confidence=1.0, frequency=1, sources=[], properties={}),
        ]
        
        for concept in concepts:
            self.db.add_concept(concept)
        
        # Add relationships
        self.db.add_relationship("a", "b", "related", weight=0.8)
        self.db.add_relationship("b", "c", "related", weight=0.7)
        
        # Test centrality measures
        centrality = self.db.calculate_centrality_measures()
        self.assertIn("a", centrality)
        self.assertIn("degree", centrality["a"])
        
        # Test finding related concepts
        related = self.db.find_related_concepts("a", max_distance=2)
        self.assertGreater(len(related), 0)


class TestAnalysisTools(unittest.TestCase):
    """Test analysis tools functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.analysis_tool = AnalysisTool()
        self.surprise_tool = BayesianSurpriseTool()
    
    def test_entity_extraction(self):
        """Test entity extraction."""
        text = "OpenAI and Google are developing advanced AI systems."
        entities = self.analysis_tool.extract_entities(text)
        
        # Should find capitalized entities
        entity_texts = [e["text"] for e in entities]
        self.assertIn("OpenAI", entity_texts)
        self.assertIn("Google", entity_texts)
    
    def test_keyword_extraction(self):
        """Test keyword extraction."""
        text = "Machine learning algorithms learn patterns from data to make predictions."
        keywords = self.analysis_tool.extract_keywords(text, max_keywords=5)
        
        self.assertIsInstance(keywords, list)
        self.assertGreater(len(keywords), 0)
        
        # Each keyword should be a (word, frequency) tuple
        for keyword, freq in keywords:
            self.assertIsInstance(keyword, str)
            self.assertIsInstance(freq, int)
    
    def test_surprise_calculation(self):
        """Test Bayesian surprise calculation."""
        prior = {"concept_a": 0.3, "concept_b": 0.7}
        posterior = {"concept_a": 0.8, "concept_b": 0.2}
        
        kl_div = self.surprise_tool.calculate_kl_divergence(prior, posterior)
        self.assertIsInstance(kl_div, float)
        self.assertGreater(kl_div, 0)  # Should be positive for different distributions
    
    def test_surprise_assessment(self):
        """Test surprise level assessment."""
        evidence = "This unexpected finding contradicts previous theories."
        existing_knowledge = {"established_fact": "previous theory"}
        
        assessment = self.surprise_tool.assess_surprise_level(evidence, existing_knowledge)
        
        self.assertIn("overall_surprise", assessment)
        self.assertIn("kl_divergence", assessment)
        self.assertIn("indicators", assessment)
        
        # Should detect contradiction indicator
        indicators = assessment["indicators"]
        self.assertGreater(indicators["contradicts_existing"], 0)


class TestUserProxyAgent(unittest.IsolatedAsyncioTestCase):
    """Test UserProxyAgent functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.agent = UserProxyAgent(
            name="TestUserProxy",
            max_loops=3,
            token_budget=5000,
            time_budget=1800.0,
            cost_budget=5.0,
            session_save_path=self.temp_dir,
        )
    
    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    async def test_session_initialization(self):
        """Test discovery session initialization."""
        # Create temporary knowledge base
        kb_path = os.path.join(self.temp_dir, "kb")
        os.makedirs(kb_path)
        
        with open(os.path.join(kb_path, "test.txt"), 'w') as f:
            f.write("Test knowledge base content about AI and machine learning.")
        
        session_info = await self.agent.start_discovery_session(
            knowledge_base_path=kb_path,
            initial_idea="How does AI work?",
            focus_areas=["artificial intelligence", "machine learning"],
        )
        
        self.assertEqual(session_info["status"], "initialized")
        self.assertIn("session_id", session_info)
        self.assertIsNotNone(self.agent.current_state)
    
    async def test_session_status(self):
        """Test getting session status."""
        # No active session
        status = await self.agent.get_session_status()
        self.assertEqual(status["status"], "no_active_session")
        
        # Create session and check status
        kb_path = os.path.join(self.temp_dir, "kb")
        os.makedirs(kb_path)
        with open(os.path.join(kb_path, "test.txt"), 'w') as f:
            f.write("Test content")
        
        await self.agent.start_discovery_session(kb_path, "test idea")
        
        status = await self.agent.get_session_status()
        self.assertIn("session_id", status)
        self.assertIn("budget_status", status)
        self.assertIn("progress", status)
    
    async def test_termination(self):
        """Test session termination."""
        # Create session first
        kb_path = os.path.join(self.temp_dir, "kb")
        os.makedirs(kb_path)
        with open(os.path.join(kb_path, "test.txt"), 'w') as f:
            f.write("Test content")
        
        await self.agent.start_discovery_session(kb_path, "test idea")
        
        # Request termination
        result = await self.agent.request_termination("testing")
        
        self.assertEqual(result["status"], "termination_requested")
        self.assertEqual(result["reason"], "testing")
        self.assertIn("preliminary_results", result)


def run_discovery_tests():
    """Run all discovery system tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add test cases
    test_classes = [
        TestDiscoveryMessage,
        TestExplorationState,
        TestVectorDatabase,
        TestGraphDatabase,
        TestAnalysisTools,
        TestUserProxyAgent,
    ]
    
    for test_class in test_classes:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTests(tests)
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_discovery_tests()
    exit(0 if success else 1)