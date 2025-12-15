# -*- coding: utf-8 -*-
"""Gomoku Agent Wrapper (Hybrid: Minimax for moves, LLM for talk)."""
from typing import Tuple
from ai_engine import MinimaxAI
from agentscope.agent import ReActAgent
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel
from agentscope.formatter import DeepSeekChatFormatter

class GomokuAgentWrapper:
    def __init__(self, api_key: str, difficulty: str = "normal"):
        self.brain = MinimaxAI(difficulty)
        self.difficulty = difficulty
        
        # LLM for Commentary only (Optional)
        self.use_llm_talk = True
        if self.use_llm_talk:
            self.model = OpenAIChatModel(
                model_name="deepseek-chat",
                api_key=api_key,
                client_kwargs={"base_url": "https://api.deepseek.com"},
            )
            # Simple agent for chatting
            self.talker = ReActAgent(
                name="GomokuBot",
                sys_prompt="You are a competitive Gomoku player. Give a 1-sentence short, witty comment about the current game state.",
                model=self.model,
                formatter=DeepSeekChatFormatter(),
            )

    def get_move(self, grid) -> Tuple[int, int]:
        """Get move from Minimax Brain (Fast)."""
        return self.brain.get_move(grid)

    def get_comment(self, board_str):
        """Get comment from LLM (Slow, async, don't block game)."""
        # In a real game loop, we'd run this in a thread or background task.
        # For now, we skip implementation or just return a mock to ensure speed.
        return "Thinking..."
