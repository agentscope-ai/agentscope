# -*- coding: utf-8 -*-
"""
通用 roundtrip 测试：验证 Msg 类的序列化/反序列化对称性。

测试目标：
    验证 Msg.to_dict() 和 Msg.from_dict() 是完全对称的，
    即 Msg.from_dict(msg.to_dict()) 返回的对象与原始对象完全一致。

测试覆盖的字段：
    - id: 消息唯一标识符
    - name: 消息发送者名称
    - role: 消息角色 (user/assistant/system)
    - content: 消息内容 (字符串或 ContentBlock 列表)
    - metadata: 元数据字典
    - timestamp: 时间戳字符串
    - invocation_id: API 调用标识符
"""
import sys
import os
from copy import deepcopy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from agentscope.message import (
    Msg,
    TextBlock,
    ImageBlock,
    ToolUseBlock,
    ToolResultBlock,
    Base64Source,
    URLSource,
)


def test_msg_roundtrip_all_fields():
    """
    测试完整填充所有字段的 Msg 对象的 roundtrip 序列化/反序列化。
    
    覆盖场景：
    1. 所有字段都有值的情况
    2. content 是字符串的情况
    3. content 是 ContentBlock 列表的情况
    4. invocation_id 为 None 的情况
    """
    
    original = Msg(
        name="test_agent_name",
        content="This is a test message content",
        role="assistant",
        metadata={"key1": "value1", "key2": 123, "key3": {"nested": "dict"}},
        timestamp="2026-04-19 12:34:56.789",
        invocation_id="api_invocation_001",
    )
    
    original_id = original.id
    
    serialized = original.to_dict()
    
    assert "id" in serialized, "Serialized dict should contain 'id'"
    assert "name" in serialized, "Serialized dict should contain 'name'"
    assert "role" in serialized, "Serialized dict should contain 'role'"
    assert "content" in serialized, "Serialized dict should contain 'content'"
    assert "metadata" in serialized, "Serialized dict should contain 'metadata'"
    assert "timestamp" in serialized, "Serialized dict should contain 'timestamp'"
    assert "invocation_id" in serialized, "Serialized dict should contain 'invocation_id'"
    
    assert serialized["id"] == original_id
    assert serialized["name"] == "test_agent_name"
    assert serialized["role"] == "assistant"
    assert serialized["content"] == "This is a test message content"
    assert serialized["metadata"] == {"key1": "value1", "key2": 123, "key3": {"nested": "dict"}}
    assert serialized["timestamp"] == "2026-04-19 12:34:56.789"
    assert serialized["invocation_id"] == "api_invocation_001"
    
    deserialized = Msg.from_dict(serialized)
    
    assert deserialized.id == original_id, "id should be preserved"
    assert deserialized.name == original.name, "name should be preserved"
    assert deserialized.role == original.role, "role should be preserved"
    assert deserialized.content == original.content, "content should be preserved"
    assert deserialized.metadata == original.metadata, "metadata should be preserved"
    assert deserialized.timestamp == original.timestamp, "timestamp should be preserved"
    assert deserialized.invocation_id == original.invocation_id, "invocation_id should be preserved"


def test_msg_roundtrip_with_content_blocks():
    """
    测试 content 为 ContentBlock 列表的情况。
    """
    content_blocks = [
        TextBlock(type="text", text="Hello, world!"),
        ImageBlock(
            type="image",
            source=URLSource(type="url", url="https://example.com/image.jpg")
        ),
        ToolUseBlock(
            type="tool_use",
            id="tool_call_001",
            name="get_weather",
            input={"city": "Beijing"},
        ),
    ]
    
    original = Msg(
        name="assistant",
        content=content_blocks,
        role="assistant",
        metadata={"blocks": True},
        timestamp="2026-04-19 14:00:00.000",
        invocation_id="block_test_001",
    )
    
    original_id = original.id
    original_content = deepcopy(original.content)
    
    serialized = original.to_dict()
    
    assert serialized["content"] == original_content
    
    deserialized = Msg.from_dict(serialized)
    
    assert deserialized.id == original_id
    assert deserialized.content == original_content
    assert deserialized.invocation_id == "block_test_001"


def test_msg_roundtrip_invocation_id_none():
    """
    测试 invocation_id 为 None 的情况。
    """
    original = Msg(
        name="user",
        content="Hello!",
        role="user",
        invocation_id=None,
    )
    
    original_id = original.id
    
    serialized = original.to_dict()
    
    assert "invocation_id" in serialized
    assert serialized["invocation_id"] is None
    
    deserialized = Msg.from_dict(serialized)
    
    assert deserialized.id == original_id
    assert deserialized.invocation_id is None


def test_msg_roundtrip_empty_metadata():
    """
    测试 metadata 为空字典的情况。
    """
    original = Msg(
        name="system",
        content="System prompt",
        role="system",
        metadata={},
        invocation_id="system_msg_001",
    )
    
    serialized = original.to_dict()
    deserialized = Msg.from_dict(serialized)
    
    assert deserialized.metadata == {}
    assert deserialized.invocation_id == "system_msg_001"


def test_msg_roundtrip_metadata_none():
    """
    测试 metadata 为 None 的情况（会被转换为空字典）。
    """
    original = Msg(
        name="assistant",
        content="Test",
        role="assistant",
        metadata=None,
        invocation_id="meta_none_001",
    )
    
    assert original.metadata == {}, "metadata=None should be converted to empty dict"
    
    serialized = original.to_dict()
    deserialized = Msg.from_dict(serialized)
    
    assert deserialized.metadata == {}
    assert deserialized.invocation_id == "meta_none_001"


def test_msg_full_roundtrip_comparison():
    """
    完整的 roundtrip 测试：比较原始对象和反序列化对象的所有字段。
    """
    original = Msg(
        name="full_test_agent",
        content="Full roundtrip test message",
        role="assistant",
        metadata={
            "string": "value",
            "integer": 42,
            "float": 3.14,
            "list": [1, 2, 3],
            "dict": {"nested": "value"},
            "none": None,
            "bool": True,
        },
        timestamp="2026-04-19 18:30:00.123",
        invocation_id="full_roundtrip_001",
    )
    
    original_id = original.id
    
    deserialized = Msg.from_dict(original.to_dict())
    
    assert deserialized.id == original_id
    assert deserialized.name == original.name
    assert deserialized.content == original.content
    assert deserialized.role == original.role
    assert deserialized.metadata == original.metadata
    assert deserialized.timestamp == original.timestamp
    assert deserialized.invocation_id == original.invocation_id
    
    assert deserialized.__repr__() == original.__repr__()


if __name__ == "__main__":
    test_msg_roundtrip_all_fields()
    test_msg_roundtrip_with_content_blocks()
    test_msg_roundtrip_invocation_id_none()
    test_msg_roundtrip_empty_metadata()
    test_msg_roundtrip_metadata_none()
    test_msg_full_roundtrip_comparison()
    print("All roundtrip tests passed!")
