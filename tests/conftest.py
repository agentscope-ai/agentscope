# -*- coding: utf-8 -*-
"""Test configuration for AgentScope.

Keep tests runnable from a source checkout without requiring editable installs.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# Ensure parent src directory is available for module discovery.
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Import package entrypoint early to surface import-time regressions.
importlib.import_module("agentscope")

# Explicitly import prerequisites used by filesystem code.
importlib.import_module("agentscope.module")
importlib.import_module("agentscope.filesystem")
