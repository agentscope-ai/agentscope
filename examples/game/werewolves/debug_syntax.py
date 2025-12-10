import ast
import traceback

try:
    with open('agent.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    print("File read successfully, size:", len(content))
    
    # Try to parse the file
    tree = ast.parse(content)
    print("Syntax OK!")
    
except Exception as e:
    print(f"Error type: {type(e).__name__}")
    print(f"Error message: {e}")
    traceback.print_exc()
