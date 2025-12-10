#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script to verify PlayerAgent meets all competition requirements.

Requirements tested:
1. Must implement observe function
2. Must support BaseModel structured output
3. Must support Session state management (state_dict/load_state_dict)
4. __call__ must return valid Msg objects
5. Constructor must only accept name parameter
"""

import asyncio
from agent import PlayerAgent, PlayerResponse
from agentscope.message import Msg


async def test_requirements():
    """Test all competition requirements."""
    print("=== Testing PlayerAgent Competition Requirements ===\n")
    
    # Test 1: Constructor only accepts name parameter
    print("1. Testing constructor requirements...")
    try:
        agent = PlayerAgent(name="TestPlayer")
        print("✓ Constructor accepts only name parameter")
    except Exception as e:
        print(f"✗ Constructor failed: {e}")
        return False
    
    # Test 2: observe function implementation
    print("\n2. Testing observe function...")
    try:
        test_msg = Msg("moderator", "Your role is werewolf", "assistant")
        await agent.observe(test_msg)
        print(f"✓ observe function implemented, role detected: {agent.role}")
    except Exception as e:
        print(f"✗ observe function failed: {e}")
        return False
    
    # Test 3: BaseModel structured output support
    print("\n3. Testing BaseModel structured output...")
    try:
        user_msg = Msg("user", "What should we do?", "user", metadata={"structured_model": PlayerResponse})
        response = await agent(user_msg)
        
        # Verify response is Msg object
        assert isinstance(response, Msg), f"Expected Msg, got {type(response)}"
        assert response.name == "TestPlayer", f"Expected name TestPlayer, got {response.name}"
        assert response.role == "assistant", f"Expected role assistant, got {response.role}"
        
        # Verify structured data in metadata
        assert hasattr(response, 'metadata'), "Response missing metadata attribute"
        assert isinstance(response.metadata, dict), "Metadata should be dict"
        assert 'response' in response.metadata, "Metadata missing 'response' field"
        assert 'confidence' in response.metadata, "Metadata missing 'confidence' field"
        assert 'strategy' in response.metadata, "Metadata missing 'strategy' field"
        
        print(f"✓ Structured output supported, metadata: {response.metadata}")
    except Exception as e:
        print(f"✗ Structured output failed: {e}")
        return False
    
    # Test 4: Regular __call__ returns valid Msg
    print("\n4. Testing regular __call__ function...")
    try:
        # Test with single message
        response = await agent(user_msg)
        
        # Verify response is Msg object
        assert isinstance(response, Msg), f"Expected Msg, got {type(response)}"
        assert response.name == "TestPlayer", f"Expected name TestPlayer, got {response.name}"
        assert response.role == "assistant", f"Expected role assistant, got {response.role}"
        assert isinstance(response.content, str), f"Expected string content, got {type(response.content)}"
        
        print(f"✓ Regular __call__ returns valid Msg: {response.content[:50]}...")
        
        # Test with None input
        response_none = await agent(None)
        assert isinstance(response_none, Msg), f"Expected Msg for None input, got {type(response_none)}"
        print("✓ __call__ with None input works correctly")
        
        # Test with list of messages
        msg_list = [
            Msg("user", "First message", "user"),
            Msg("user", "Second message", "user")
        ]
        response_list = await agent(msg_list)
        assert isinstance(response_list, Msg), f"Expected Msg for list input, got {type(response_list)}"
        print("✓ __call__ with list input works correctly")
        
    except Exception as e:
        print(f"✗ Regular __call__ failed: {e}")
        return False
    
    # Test 5: Session state management
    print("\n5. Testing session state management...")
    try:
        # Get state
        state = agent.state_dict()
        
        # Verify state contains required fields
        required_fields = ['name', 'role', 'is_alive', 'game_phase', 'known_players', 'suspicions']
        for field in required_fields:
            assert field in state, f"State missing required field: {field}"
        
        print(f"✓ state_dict() returns complete state")
        
        # Create new agent and load state
        new_agent = PlayerAgent(name="NewPlayer")
        new_agent.load_state_dict(state)
        
        # Verify state loaded correctly
        assert new_agent.name == agent.name, "Name not loaded correctly"
        assert new_agent.role == agent.role, "Role not loaded correctly"
        assert new_agent.is_alive == agent.is_alive, "Alive status not loaded correctly"
        assert new_agent.game_phase == agent.game_phase, "Game phase not loaded correctly"
        
        print(f"✓ load_state_dict() works correctly")
    except Exception as e:
        print(f"✗ State management failed: {e}")
        return False
    
    print("\n=== All Tests Passed! ===")
    print("\nPlayerAgent successfully meets all competition requirements:")
    print("✓ Constructor only accepts name parameter")
    print("✓ observe function implemented")
    print("✓ BaseModel structured output supported")
    print("✓ __call__ returns valid Msg objects")
    print("✓ Session state management supported")
    
    return True


async def test_integration():
    """Test integration with game scenarios."""
    print("\n=== Integration Test ===")
    
    # Create agent
    agent = PlayerAgent(name="Player1")
    
    # Simulate game scenario
    print("Simulating game scenario...")
    
    # Role assignment
    role_msg = Msg("moderator", "Your role is werewolf. You are a werewolf.", "assistant")
    await agent.observe(role_msg)
    print(f"✓ Role assigned: {agent.role}")
    
    # Day discussion
    day_msg = Msg("moderator", "Day 1 discussion begins. Players discuss who to eliminate.", "assistant")
    await agent.observe(day_msg)
    
    # Generate response
    response = await agent(day_msg)
    print(f"✓ Day response: {response.content[:60]}...")
    
    # Night action
    agent.game_phase = "night"
    night_msg = Msg("moderator", "Night phase. Werewolves choose who to eliminate.", "assistant", metadata={"structured_model": PlayerResponse})
    
    # Use structured output for strategic decision
    structured_response = await agent(night_msg)
    print(f"✓ Night strategic decision: {structured_response.metadata}")
    
    print("✓ Integration test completed successfully!")


if __name__ == "__main__":
    # Run tests
    success = asyncio.run(test_requirements())
    
    if success:
        asyncio.run(test_integration())
    else:
        print("\n❌ Tests failed!")