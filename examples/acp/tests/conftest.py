# -*- coding: utf-8 -*-
"""Make the example package importable when running pytest from
``examples/acp/``."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
