# -*- coding: utf-8 -*-
"""The runtime configuration in agentscope.

.. note:: You should import this module as ``import ._config``, then use the
 variables defined in this module, instead of ``from ._config import xxx``.
 Because when the variables are changed, the changes will not be reflected in
 the imported module.
"""
from datetime import datetime

import shortuuid


def _generate_random_suffix(length: int) -> str:
    """Generate a random suffix."""
    return shortuuid.uuid()[:length]


project = "UnnamedProject_At" + datetime.now().strftime("%Y%m%d")
name = datetime.now().strftime("%H%M%S_") + _generate_random_suffix(4)
run_id: str = shortuuid.uuid()
created_at: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
trace_enabled: bool = False
