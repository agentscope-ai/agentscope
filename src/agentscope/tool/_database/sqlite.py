# -*- coding: utf-8 -*-
"""A tool for interacting with SQLite databases."""
import sqlite3
import json
from typing import Union, List, Dict, Any

from agentscope.tool import ToolResponse
from agentscope.message import TextBlock

def execute_sql(
    database_path: str,
    query: str,
    max_rows: int = 50
) -> ToolResponse:
    """
    Execute a SQL query against a SQLite database.
    
    Args:
        database_path (str): The path to the SQLite database file.
        query (str): The SQL query to execute.
        max_rows (int): Maximum number of rows to return (default 50) to prevent context overflow.
        
    Returns:
        ToolResponse: The execution results or error message.
    """
    try:
        # Connect to the database
        conn = sqlite3.connect(database_path)
        cursor = conn.cursor()
        
        # Execute the query
        cursor.execute(query)
        
        # Fetch results if it's a SELECT query
        if query.strip().upper().startswith("SELECT"):
            columns = [description[0] for description in cursor.description]
            rows = cursor.fetchmany(max_rows)
            result = [dict(zip(columns, row)) for row in rows]
            
            # Check if there were more rows
            remaining = cursor.fetchall()
            if remaining:
                result.append(f"... (and {len(remaining)} more rows)")
            
            content_str = json.dumps(result, indent=2, default=str)
        else:
            # Commit changes for INSERT/UPDATE/DELETE
            conn.commit()
            content_str = "Query executed successfully."
            
        conn.close()
        return ToolResponse(content=[TextBlock(type="text", text=content_str)])
        
    except sqlite3.Error as e:
        return ToolResponse(content=[TextBlock(type="text", text=f"SQLite Error: {str(e)}")])
    except Exception as e:
        return ToolResponse(content=[TextBlock(type="text", text=f"Error: {str(e)}")])

def get_schema(database_path: str) -> ToolResponse:
    """
    Get the schema (CREATE TABLE statements) of the database.
    
    Args:
        database_path (str): The path to the SQLite database file.
        
    Returns:
        ToolResponse: The schema description.
    """
    try:
        conn = sqlite3.connect(database_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        schema_str = ""
        for table in tables:
            if table[0]: # Ensure not None
                schema_str += table[0] + ";\n\n"
        
        conn.close()
        
        if not schema_str:
            return ToolResponse(content=[TextBlock(type="text", text="Database is empty/no tables found.")])
            
        return ToolResponse(content=[TextBlock(type="text", text=schema_str)])
        
    except Exception as e:
        return ToolResponse(content=[TextBlock(type="text", text=f"Error getting schema: {str(e)}")])
