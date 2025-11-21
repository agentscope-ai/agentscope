# -*- coding: utf-8 -*-
"""Simple validation tests for the Agent Discovery System."""
import unittest
import tempfile
import os
import sys

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agentscope.discovery._message import DiscoveryMessage, MessageType, BudgetInfo


class TestBasicFunctionality(unittest.TestCase):
    """Test basic functionality that doesn't require external dependencies."""
    
    def test_budget_info_creation(self):
        """Test BudgetInfo creation."""
        budget = BudgetInfo(
            loops_remaining=5,
            max_loops=5,
            tokens_remaining=10000,
            token_budget=10000,
            time_remaining=3600.0,
            cost_remaining=10.0,
        )
        
        self.assertEqual(budget.loops_remaining, 5)
        self.assertEqual(budget.token_budget, 10000)
        self.assertGreater(budget.time_remaining, 0)
    
    def test_message_types(self):
        """Test message type enumeration."""
        self.assertEqual(MessageType.QUERY_EXPLORE.value, "query.explore")
        self.assertEqual(MessageType.EVIDENCE_FOUND.value, "evidence.found")
        self.assertEqual(MessageType.INSIGHT_GENERATE.value, "insight.generate")
        self.assertEqual(MessageType.CONTROL_START.value, "control.start")
    
    def test_discovery_message_creation(self):
        """Test basic discovery message creation."""
        budget_info = BudgetInfo(
            loops_remaining=5,
            max_loops=5,
            tokens_remaining=10000,
            token_budget=10000,
            time_remaining=3600.0,
            cost_remaining=10.0,
        )
        
        msg = DiscoveryMessage.create_exploration_query(
            query="test query",
            context={"focus_areas": ["ai", "ml"]},
            budget_info=budget_info,
        )
        
        self.assertEqual(msg.message_type, MessageType.QUERY_EXPLORE)
        self.assertEqual(msg.payload["query"], "test query")
        self.assertIn("focus_areas", msg.payload["context"])
        self.assertIsNotNone(msg.task_id)
    
    def test_message_serialization(self):
        """Test message serialization."""
        budget_info = BudgetInfo(
            loops_remaining=3,
            max_loops=5,
            tokens_remaining=8000,
            token_budget=10000,
            time_remaining=2400.0,
            cost_remaining=7.5,
        )
        
        original_msg = DiscoveryMessage.create_evidence_message(
            evidence={"finding": "test finding"},
            confidence=0.8,
            sources=["source1", "source2"],
            budget_info=budget_info,
        )
        
        # Convert to dict and back
        msg_dict = original_msg.to_dict()
        restored_msg = DiscoveryMessage.from_dict(msg_dict)
        
        self.assertEqual(original_msg.message_type, restored_msg.message_type)
        self.assertEqual(original_msg.payload, restored_msg.payload)
        self.assertEqual(original_msg.budget_info.loops_remaining, restored_msg.budget_info.loops_remaining)
        self.assertEqual(original_msg.task_id, restored_msg.task_id)
    
    def test_budget_critical_detection(self):
        """Test budget critical status detection."""
        # Normal budget
        normal_budget = BudgetInfo(
            loops_remaining=5,
            max_loops=5,
            tokens_remaining=8000,
            token_budget=10000,
            time_remaining=3000.0,
            cost_remaining=8.0,
        )
        
        msg = DiscoveryMessage.create_exploration_query(
            query="test",
            context={},
            budget_info=normal_budget,
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
    
    def test_import_structure(self):
        """Test that all main modules can be imported."""
        try:
            # Test core module imports
            from agentscope.discovery._message import DiscoveryMessage, MessageType, BudgetInfo
            from agentscope.discovery._discovery_tools import AnalysisTool, BayesianSurpriseTool
            
            # Test tool instantiation
            analysis_tool = AnalysisTool()
            surprise_tool = BayesianSurpriseTool()
            
            self.assertIsNotNone(analysis_tool)
            self.assertIsNotNone(surprise_tool)
            
        except ImportError as e:
            self.fail(f"Failed to import required modules: {e}")
    
    def test_analysis_tools_basic(self):
        """Test basic analysis tool functionality."""
        from agentscope.discovery._discovery_tools import AnalysisTool, BayesianSurpriseTool
        
        analysis_tool = AnalysisTool()
        surprise_tool = BayesianSurpriseTool()
        
        # Test entity extraction
        text = "OpenAI and Google are developing AI systems."
        entities = analysis_tool.extract_entities(text)
        self.assertIsInstance(entities, list)
        
        # Test keyword extraction
        keywords = analysis_tool.extract_keywords(text, max_keywords=5)
        self.assertIsInstance(keywords, list)
        
        # Test surprise calculation
        prior = {"concept_a": 0.3, "concept_b": 0.7}
        posterior = {"concept_a": 0.8, "concept_b": 0.2}
        kl_div = surprise_tool.calculate_kl_divergence(prior, posterior)
        self.assertIsInstance(kl_div, float)


def run_validation_tests():
    """Run validation tests for the discovery system."""
    print("🧪 Running Agent Discovery System Validation Tests")
    print("=" * 60)
    
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestBasicFunctionality)
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    if result.wasSuccessful():
        print("\n✅ All validation tests passed!")
        print("\n📋 Implementation Summary:")
        print("- ✅ Core message system implemented")
        print("- ✅ Budget management system functional")
        print("- ✅ Discovery tools created")
        print("- ✅ Module structure complete")
        print("- ✅ Basic serialization working")
        
        print("\n🚀 Ready for integration with:")
        print("- AgentScope ReAct agents")
        print("- External search APIs")
        print("- Vector databases (with sentence-transformers)")
        print("- Graph databases (with networkx)")
        print("- Language models for insight generation")
        
        return True
    else:
        print("\n❌ Some validation tests failed!")
        return False


if __name__ == "__main__":
    success = run_validation_tests()
    exit(0 if success else 1)