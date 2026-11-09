# -*- coding: utf-8 -*-
"""In-workspace MCP gateway package.

The gateway is a single self-contained script that runs *inside* the
workspace environment (Docker / E2B). It is copied into the container
at image build time and executed by the workspace at startup.

The script must remain importable without ``agentscope`` installed —
the host reads it as raw text via :mod:`importlib.resources` and ships
it into the container.
"""
