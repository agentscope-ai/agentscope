# -*- coding: utf-8 -*-
"""Configs for Distributed mode."""

from loguru import logger


class DistConf(dict):
    """Distribution configuration for agent."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = None,
        max_pool_size: int = 8192,
        max_expire_time: int = 7200,
        max_timeout_seconds: int = 5,
        local_mode: bool = True,
        lazy_launch: bool = False,
    ):
        """Init the distributed configuration.

        Args:
            host (`str`, defaults to `"localhost"`):
                Hostname of the rpc agent server.
            port (`int`, defaults to `None`):
                Port of the rpc agent server.
            max_pool_size (`int`, defaults to `8192`):
                Max number of task results that the server can accommodate.
            max_expire_time (`int`, defaults to `7200`):
                Max expire time of task results in seconds.
            max_timeout_seconds (`int`, defaults to `5`):
                Max timeout seconds for rpc calls.
            local_mode (`bool`, defaults to `True`):
                Whether the started rpc server only listens to local
                requests.
            lazy_launch (`bool`, defaults to `False`):
                Deprecated.
        """
        self["host"] = host
        self["port"] = port
        self["max_pool_size"] = max_pool_size
        self["max_expire_time"] = max_expire_time
        self["max_timeout_seconds"] = max_timeout_seconds
        self["local_mode"] = local_mode
        if lazy_launch:
            logger.warning("lazy_launch is deprecated.")
