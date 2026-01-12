# -*- coding: utf-8 -*-
"""Extension mapping for agentscope."""
from typing import Dict


_EXTENSION_MAP = {
    "runtime": {
        "import_name": "agentscope_runtime",
        "pip_name": "agentscope-runtime",
    },
}


def _get_extension_map() -> Dict:
    """Return the extension mapping dictionary (private)."""
    return _EXTENSION_MAP
