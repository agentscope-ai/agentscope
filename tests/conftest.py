# -*- coding: utf-8 -*-
"""Test configuration for AgentScope filesystem module."""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# Register a lightweight agentscope package stub that exposes the src tree
# without executing the heavy top-level ``agentscope.__init__``.
if "agentscope" not in sys.modules:
    pkg = types.ModuleType("agentscope")
    pkg.__path__ = [str(SRC / "agentscope")]
    sys.modules["agentscope"] = pkg

# Ensure parent src directory is available for module discovery.
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Explicitly import prerequisites used by filesystem code.
importlib.import_module("agentscope.module")
importlib.import_module("agentscope.filesystem")
