# -*- coding: utf-8 -*-
"""Headless Test for Game Creator AI."""
import os
import sys

# Add path
sys.path.append(os.path.join(os.getcwd(), "examples", "agent", "game_creator"))

from creator import GameCreatorAI

def test_generation():
    api_key = "sk-33241ff45e3a454986732123b5e7214c"
    creator = GameCreatorAI(api_key)
    
    prompt = "Create a simple Pong game. The enemy paddle should follow the ball."
    print(f"Testing generation for: {prompt}")
    
    result = creator.create_game(prompt)
    
    if result.get("error"):
        print(f"FAILED: {result['error']}")
    else:
        print(f"SUCCESS: Generated {result['name']}")
        print(f"Code Length: {len(result['code'])} chars")
        
        # Save to verify existence
        with open(f"examples/agent/game_creator/games/test_{result['name']}.py", "w", encoding='utf-8') as f:
            f.write(result['code'])
            
if __name__ == "__main__":
    test_generation()
