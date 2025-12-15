# -*- coding: utf-8 -*-
"""A simplified Deep Research Agent using the local WebSearchTool."""
import sys
from agentscope.agent import ReActAgent
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel
from agentscope.tool import web_search

# System prompt to encourage research behavior
RESEARCH_PROMPT = """You are a Deep Research Agent.
Your goal is to answer the user's questions by actively searching the web.

Capabilities:
- You have access to `web_search` to find information.
- You should use the tool multiple times if the first search is insufficient.
- Synthesize information from multiple results to provide a comprehensive answer.
- Always cite your sources (URL) in the final answer.

Process:
1. Analyze the user's request.
2. Formulate search queries.
3. Call `web_search`.
4. Read the results.
5. If unsure, search again with a refined query.
6. Provide a final detailed report.
"""

def main():
    # 1. Configuration
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    model = OpenAIChatModel(
        model_name="deepseek-chat",
        api_key=api_key,
        client_kwargs={"base_url": "https://api.deepseek.com"},
    )

    # 2. Agent Initialization
    researcher = ReActAgent(
        name="Researcher",
        sys_prompt=RESEARCH_PROMPT,
        model=model,
        tools=[web_search], # Inject our new tool
        verbose=True
    )

    # 3. Interactive Loop
    print("\n🕵️‍♂️ Deep Research Agent (Lite) Initialized.")
    print("Ask something dynamic! (e.g., 'What is the current stock price of NVIDIA vs AMD?')\n")
    
    while True:
        try:
            query = input("User> ")
            if query.lower() in ["exit", "quit"]: break
            
            msg = Msg(name="User", content=query, role="user")
            res = researcher(msg)
            
            print(f"\n🤖 Researcher:\n{res.content}\n")
            
        except KeyboardInterrupt:
            print("\nExiting...")
            break

if __name__ == "__main__":
    main()
