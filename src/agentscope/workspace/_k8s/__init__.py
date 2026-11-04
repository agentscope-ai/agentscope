# -*- coding: utf-8 -*-
"""Kubernetes-backed workspace package.

Re-exports :class:`K8sWorkspace` and :class:`K8sBackend` so callers
can write ``from agentscope.workspace._k8s import K8sWorkspace``
without having to poke at the underlying module layout.
"""

from ._k8s_workspace import K8sWorkspace
from ._k8s_backend import K8sBackend

__all__ = ["K8sWorkspace", "K8sBackend"]
