# -*- coding: utf-8 -*-
"""``python -m acp_example`` — serve the AgentScope kernel over stdio."""
import asyncio

from .server import main

if __name__ == "__main__":
    asyncio.run(main())
