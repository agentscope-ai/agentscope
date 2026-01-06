# -*- coding: utf-8 -*-
"""Unittests for the config module."""
import asyncio
import threading
from unittest.async_case import IsolatedAsyncioTestCase

import agentscope
from agentscope import _config


result = {}


async def async_task(field: str) -> str:
    """A sample async task to demonstrate context variable usage."""
    prefix = "async_task"
    agentscope.init(
        run_id=f"{prefix}_run_id",
        project=f"{prefix}_project",
        name=f"{prefix}_name",
    )
    if field == "run_id":
        return _config.run_id
    elif field == "project":
        return _config.project
    elif field == "name":
        return _config.name
    else:
        return ""


def sync_task(field: str) -> None:
    """A sample sync task to demonstrate context variable usage."""
    prefix = "sync_task"
    agentscope.init(
        run_id=f"{prefix}_run_id",
        project=f"{prefix}_project",
        name=f"{prefix}_name",
    )
    if field == "run_id":
        result["value"] = _config.run_id
    elif field == "project":
        result["value"] = _config.project
    elif field == "name":
        result["value"] = _config.name
    else:
        result["value"] = None


class ConfigTest(IsolatedAsyncioTestCase):
    """Unittests for the config module."""

    async def test_config_attributes(self) -> None:
        """Test the config attributes."""
        agentscope.init(
            project="root_project",
            name="root_name",
            run_id="root_run_id",
        )

        for field in ["project", "name", "run_id"]:
            # Test root context
            self.assertEqual(getattr(_config, field), f"root_{field}")

            # Test asynchronous task
            res = await asyncio.create_task(async_task(field))
            self.assertEqual(res, f"async_task_{field}")

            # Test synchronous task in a separate thread
            thread = threading.Thread(target=sync_task, args=(field,))
            thread.start()
            thread.join()

            self.assertEqual(result["value"], f"sync_task_{field}")
