# -*- coding: utf-8 -*-
"""Direct test of message functionality only."""
import unittest
import os
import sys
import json
import tempfile

# Direct import without going through __init__.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src', 'agentscope', 'discovery'))

from _message import DiscoveryMessage, MessageType, BudgetInfo


class TestMessageDirect(unittest.TestCase):
    """Direct test of message functionality."""
    
    def test_budget_info(self):
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
    
    def test_message_creation(self):
        """Test message creation."""
        budget = BudgetInfo(5, 5, 10000, 10000, 3600.0, 10.0)
        
        msg = DiscoveryMessage.create_exploration_query(
            query="test query",
            context={"focus_areas": ["ai"]},
            budget_info=budget,
        )
        
        self.assertEqual(msg.message_type, MessageType.QUERY_EXPLORE)
        self.assertEqual(msg.payload["query"], "test query")
    
    def test_serialization(self):
        """Test message serialization."""
        budget = BudgetInfo(3, 5, 8000, 10000, 2400.0, 7.5)
        
        msg = DiscoveryMessage.create_evidence_message(
            evidence={"finding": "test"},
            confidence=0.8,
            sources=["source1"],
            budget_info=budget,
        )
        
        # Test dict conversion
        msg_dict = msg.to_dict()
        restored = DiscoveryMessage.from_dict(msg_dict)
        
        self.assertEqual(msg.message_type, restored.message_type)
        self.assertEqual(msg.payload, restored.payload)
        self.assertEqual(msg.task_id, restored.task_id)


if __name__ == "__main__":
    print("🧪 Testing Core Message Functionality")
    print("=" * 40)
    
    unittest.main(verbosity=2)