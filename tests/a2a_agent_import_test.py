# -*- coding: utf-8 -*-
"""Tests for importing the A2A adapter without its optional dependency."""
import subprocess
import sys
import unittest


class A2AAgentImportTest(unittest.TestCase):
    """Test the lazy public A2AAgent export."""

    def test_agent_module_import_does_not_require_a2a(self) -> None:
        """Importing agentscope.agent must not import the optional SDK."""
        script = """
import builtins
original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'a2a' or name.startswith('a2a.'):
        raise ImportError('blocked optional dependency')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from agentscope.agent import A2AAgent
assert A2AAgent.__name__ == 'A2AAgent'
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
