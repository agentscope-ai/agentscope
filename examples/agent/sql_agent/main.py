# -*- coding: utf-8 -*-
"""Data Analysis with Enterprise SQL Agent"""
import os
import asyncio
import pandas as pd
import sqlite3
import agentscope
from agentscope.agent import ReActAgent
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit
from agentscope.tool._database.sqlite import execute_sql, get_schema
# Import Formatter
from agentscope.formatter import DeepSeekChatFormatter

# Helper to setup DB
def setup_database(db_path, csv_path):
    if os.path.exists(db_path):
        os.remove(db_path)
    
    conn = sqlite3.connect(db_path)
    if os.path.exists(csv_path):
        df = pd.read_csv(csv_path)
        # Clean column names
        df.columns = [c.replace(" ", "_").lower() for c in df.columns]
        df.to_sql("titanic", conn, index=False)
        print(f"Database setup at {db_path} with {len(df)} rows.")
    else:
        print("Warning: CSV not found, creating empty DB.")
        
    conn.close()

async def run_conversation(agent):
    print("\nDescribe your data question (e.g. 'How many survivors were there?'). Type 'exit' to quit.")
    while True:
        try:
            # Note: input() is blocking, but for this simple example it's fine.
            # Ideally we'd use an async input library or run in executor,
            # but standard input() works in basic asyncio loop if we don't mind blocking other tasks.
            user_input = input("\nUser: ")
            if user_input.lower() in ["exit", "quit"]:
                break
                
            msg = Msg(name="User", content=user_input, role="user")
            res = await agent(msg) # Await the agent
            print(f"\nAnalyst: {res.content}")
        except KeyboardInterrupt:
            break

def main():
    # 1. Config
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("⚠️  DEEPSEEK_API_KEY not found.")
        return
    db_path = "titanic.db"
    csv_path = "titanic.csv"
    
    # 2. Setup Data
    setup_database(db_path, csv_path)
    
    # 3. Init AgentScope
    agentscope.init(project="SQL_Agent_Demo", name="Run_1")
    
    abs_db_path = os.path.abspath(db_path)
    
    # 4. Setup Toolkit
    toolkit = Toolkit()
    toolkit.register_tool_function(
        execute_sql, 
        preset_kwargs={"database_path": abs_db_path}
    )
    toolkit.register_tool_function(
        get_schema, 
        preset_kwargs={"database_path": abs_db_path}
    )
    
    # 5. Model
    model = OpenAIChatModel(
        config_name="deepseek-chat",
        model_name="deepseek-chat",
        api_key=api_key,
        client_kwargs={"base_url": "https://api.deepseek.com"}
    )
    
    sys_prompt = f"""You are an Enterprise Data Analyst.
You have access to a SQLite database at: {abs_db_path}

Your Goal: Answer user questions by querying the database.

Rules:
1. First, use `get_schema` to understand the table structure.
2. Then, write and execute SQL queries using `execute_sql`.
3. If the query fails, fix the SQL and try again.
4. Finally, answer the user's question based on the data.
"""

    agent = ReActAgent(
        name="SQL_Analyst",
        sys_prompt=sys_prompt,
        model=model,
        formatter=DeepSeekChatFormatter(), 
        toolkit=toolkit, 
        print_hint_msg=True
    )
    
    # 6. Run Async Loop
    asyncio.run(run_conversation(agent))

if __name__ == "__main__":
    main()
