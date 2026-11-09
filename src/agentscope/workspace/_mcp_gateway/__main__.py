# -*- coding: utf-8 -*-
"""``python -m agentscope.workspace._mcp_gateway`` entry point.

Inside the workspace container the gateway is launched as::

    python -m agentscope.workspace._mcp_gateway --config <path> --port <port>

so this module simply forwards to :func:`_mcp_gateway_app.main`.
"""

from ._mcp_gateway_app import main

if __name__ == "__main__":
    main()
