# -*- coding: utf-8 -*-
"""AI Game Creator Logic (DeepSeek Wrapper)."""
import os
import re
from typing import Optional
from agentscope.agent import ReActAgent
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel
from agentscope.formatter import DeepSeekChatFormatter
from prompts import ARCHITECT_PROMPT, CODER_PROMPT, ERROR_FIX_PROMPT, REVIEWER_PROMPT

class GameCreatorAI:
    def __init__(self, api_key: str):
        self.api_key = api_key
        
        # Configure model
        self.model = OpenAIChatModel(
            model_name="deepseek-chat",
            api_key=self.api_key,
            client_kwargs={"base_url": "https://api.deepseek.com"},
        )
        
        # Agents
        self.architect = ReActAgent(
            name="Architect",
            sys_prompt=ARCHITECT_PROMPT,
            model=self.model,
            formatter=DeepSeekChatFormatter(),
        )
        
        self.coder = ReActAgent(
            name="Coder",
            sys_prompt=CODER_PROMPT,
            model=self.model,
            formatter=DeepSeekChatFormatter(),
        )
        
        self.reviewer = ReActAgent(
            name="Reviewer",
            sys_prompt=REVIEWER_PROMPT,
            model=self.model,
            formatter=DeepSeekChatFormatter(),
        )

    def create_game(self, user_request: str) -> dict:
        """
        Generates a game from a user request with Multi-Agent Review.
        """
        print(f"Creator: Designing game for '{user_request}'...")
        
        # 1. Design Phase
        design_msg = Msg(name="User", content=user_request, role="user")
        design_res = self._run_agent(self.architect, design_msg)
        design_content = design_res.content
        if isinstance(design_content, list):
             design_content = " ".join([str(c) for c in design_content])
        
        print(f"Architect Design:\n{design_content[:200]}...") 
        
        # Extract Name
        name_match = re.search(r"\[Game Name\]: (.*)", design_content, re.IGNORECASE)
        game_name = name_match.group(1).strip() if name_match else "generated_game"
        safe_name = "".join([c if c.isalnum() else "_" for c in game_name]).lower()
        if not safe_name: safe_name = "game"

        # 2. Coding Phase (Initial)
        coding_request = f"Here is the design:\n{design_content}\nPlease write the Pygame code for this."
        code_msg = Msg(name="Architect", content=coding_request, role="user")
        
        print(f"Creator: Coding '{safe_name}'...")
        code_res = self._run_agent(self.coder, code_msg)
        code_content = code_res.content
        # Ensure string
        if isinstance(code_content, list):
            # Sometimes agentscope Msg content can be list of dicts (multimodal)
            code_content = " ".join([str(c) for c in code_content])
        
        # Extract Code Block
        code = self._extract_code(str(code_content))
        
        # 3. Review Loop (Max 2 retries to save time)
        MAX_RETRIES = 2
        for i in range(MAX_RETRIES):
            if not code: break
            
            print(f"Creator: Reviewing Round {i+1}...")
            review_msg = Msg(name="System", content=f"Please review this code:\n```python\n{code}\n```", role="user")
            review_res = self._run_agent(self.reviewer, review_msg)
            critique = review_res.content
            
            # Normalize content (AgentScope sometimes returns list/dict)
            if isinstance(critique, list):
                critique = " ".join([str(c) for c in critique])
            elif isinstance(critique, dict):
                critique = str(critique)
            else:
                critique = str(critique)
            
            if "PASS" in critique:
                print("Creator: Review PASS.")
                break
            else:
                print(f"Creator: Review Failed. Issues: {critique[:100]}...")
                # Fix
                fix_request = f"The previous code had issues:\n{critique}\nPlease fix the code and return the full corrected script."
                fix_msg = Msg(name="Reviewer", content=fix_request, role="user")
                fix_res = self._run_agent(self.coder, fix_msg)
                code_content = fix_res.content
                code = self._extract_code(code_content)

        if not code:
            return {"error": "Failed to generate valid Python code."}

        return {
            "name": safe_name,
            "display_name": game_name,
            "code": code,
            "error": None
        }

    def _run_agent(self, agent, msg):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(agent(msg))
            return res
        finally:
            loop.close()

    def _extract_code(self, content):
        """Extracts content inside ```python ... ``` blocks."""
        if not content: return None
        content = str(content)
        
        # 1. Standard Markdown
        pattern = r"```python\s+(.*?)\s+```"
        match = re.search(pattern, content, re.DOTALL)
        if match: return match.group(1)
            
        # 2. Generic Markdown
        pattern_generic = r"```\s+(.*?)\s+```"
        match_g = re.search(pattern_generic, content, re.DOTALL)
        if match_g: return match_g.group(1)
            
        # 3. Fallback: If "import pygame" exists, assume raw code (or stripped)
        if "import pygame" in content:
            print("Creator: Warning - converting raw text to code (no markdown found).")
            return content
            
        print(f"Creator: Failed to extract code. Raw content start: {content[:100]}...")
        return None
