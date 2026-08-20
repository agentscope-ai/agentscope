# -*- coding: utf-8 -*-
"""Standalone helper scripts shipped as package resources.

Scripts in this package are deployed into remote workspaces (Docker /
E2B) at initialization time and invoked via ``exec_shell``. They must
remain importable *without* ``agentscope`` installed — the host reads
them as raw bytes via :mod:`importlib.resources` and ships them into
the workspace environment.
"""
