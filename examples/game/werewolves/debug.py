#!/usr/bin/env python3
import sys
import traceback

print("Python version:", sys.version)
print()

try:
    print("Trying to import agent module...")
    import agent
    print("Import successful!")
except Exception as e:
    print(f"Import failed with error: {type(e).__name__}: {e}")
    print("\nFull traceback:")
    traceback.print_exc()