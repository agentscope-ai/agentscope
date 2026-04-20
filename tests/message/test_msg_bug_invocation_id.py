# -*- coding: utf-8 -*-
"""Test for Msg invocation_id serialization bug.

Bug Description:
    Msg.to_dict() 方法没有序列化 invocation_id 字段，导致序列化后
    反序列化时该字段丢失。这是一个典型的序列化/反序列化不对称 Bug。

复现步骤:
    1. 创建一个带有 invocation_id 的 Msg 对象
    2. 调用 to_dict() 序列化
    3. 调用 from_dict() 反序列化
    4. 比较原始对象和反序列化对象的 invocation_id 字段

代码位置:
    - src/agentscope/message/_message_base.py:75-85 (to_dict() 方法)
    - src/agentscope/message/_message_base.py:87-100 (from_dict() 方法)
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from agentscope.message import Msg


def test_msg_invocation_id_serialization():
    """Test that invocation_id is properly serialized and deserialized."""
    original_msg = Msg(
        name="test_agent",
        content="Hello, world!",
        role="assistant",
        invocation_id="test_invocation_123",
    )
    
    assert original_msg.invocation_id == "test_invocation_123", \
        f"Expected invocation_id to be 'test_invocation_123', got {original_msg.invocation_id}"
    
    msg_dict = original_msg.to_dict()
    
    assert "invocation_id" in msg_dict, \
        "Serialized dict should contain 'invocation_id' key. " \
        "This is the BUG: to_dict() is missing invocation_id field."
    
    assert msg_dict["invocation_id"] == "test_invocation_123", \
        f"Expected invocation_id in dict to be 'test_invocation_123', got {msg_dict.get('invocation_id')}"
    
    deserialized_msg = Msg.from_dict(msg_dict)
    
    assert deserialized_msg.invocation_id == "test_invocation_123", \
        f"Expected deserialized invocation_id to be 'test_invocation_123', got {deserialized_msg.invocation_id}"
    
    assert deserialized_msg.name == original_msg.name
    assert deserialized_msg.content == original_msg.content
    assert deserialized_msg.role == original_msg.role
    assert deserialized_msg.metadata == original_msg.metadata
    assert deserialized_msg.timestamp == original_msg.timestamp
    assert deserialized_msg.id == original_msg.id


def test_msg_invocation_id_none():
    """Test that invocation_id=None is handled correctly."""
    msg_without_invocation_id = Msg(
        name="test_agent",
        content="Hello!",
        role="user",
    )
    
    assert msg_without_invocation_id.invocation_id is None, \
        "Expected invocation_id to be None when not provided"
    
    msg_dict_no_id = msg_without_invocation_id.to_dict()
    
    deserialized_no_id = Msg.from_dict(msg_dict_no_id)
    
    assert deserialized_no_id.invocation_id is None, \
        "Expected deserialized invocation_id to be None when original was None"


def test_msg_to_dict_from_dict_roundtrip():
    """Test complete round-trip serialization/deserialization."""
    original = Msg(
        name="assistant_agent",
        content="This is a test message",
        role="assistant",
        metadata={"key": "value", "number": 42},
        timestamp="2026-04-19 10:30:00.000",
        invocation_id="api_call_001",
    )
    
    serialized = original.to_dict()
    deserialized = Msg.from_dict(serialized)
    
    assert deserialized.id == original.id, "id should be preserved"
    assert deserialized.name == original.name, "name should be preserved"
    assert deserialized.content == original.content, "content should be preserved"
    assert deserialized.role == original.role, "role should be preserved"
    assert deserialized.metadata == original.metadata, "metadata should be preserved"
    assert deserialized.timestamp == original.timestamp, "timestamp should be preserved"
    assert deserialized.invocation_id == original.invocation_id, \
        "invocation_id should be preserved - THIS IS THE BUG FIX"


if __name__ == "__main__":
    test_msg_invocation_id_serialization()
    test_msg_invocation_id_none()
    test_msg_to_dict_from_dict_roundtrip()
    print("All tests passed!")
