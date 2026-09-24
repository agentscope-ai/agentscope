# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Back-compat probe for the ``extra_agent_middlewares`` factory.

The factory gained a fourth ``workspace`` argument. ``ChatService`` probes
each factory's signature once at construction so factories written against
the original three-argument shape keep being called with three arguments.
"""
import functools
from unittest import TestCase

from agentscope.app._service._chat import ChatService


def _probe(factory: object) -> bool:
    """Return whether ``ChatService`` would pass ``workspace`` to
    ``factory``.

    Args:
        factory (`object`):
            The candidate ``extra_agent_middlewares`` factory.

    Returns:
        `bool`:
            ``True`` when the service resolved the four-argument shape.
    """
    service = ChatService(
        storage=None,
        workspace_manager=None,
        scheduler_manager=None,
        background_task_manager=None,
        message_bus=None,
        resource_access_service=None,
        extra_agent_middlewares=factory,
    )
    return service._middlewares_take_workspace


async def _legacy(user_id: str, agent_id: str, session_id: str) -> list:
    """A factory written before ``workspace`` existed."""
    del user_id, agent_id, session_id
    return []


async def _with_workspace(
    user_id: str,
    agent_id: str,
    session_id: str,
    workspace: object,
) -> list:
    """A factory that opted into the fourth argument."""
    del user_id, agent_id, session_id, workspace
    return []


class _CallableLegacy:
    """A callable object using the three-argument shape."""

    async def __call__(self, user_id: str, agent_id: str, sid: str) -> list:
        """Return no middlewares."""
        del user_id, agent_id, sid
        return []


class ExtraMiddlewareFactoryProbeTest(TestCase):
    """The probe must classify every callable shape correctly."""

    def test_legacy_factory_is_called_without_workspace(self) -> None:
        """A three-argument factory must not receive ``workspace``."""
        self.assertFalse(_probe(_legacy))

    def test_new_factory_receives_workspace(self) -> None:
        """A four-argument factory must receive ``workspace``."""
        self.assertTrue(_probe(_with_workspace))

    def test_no_factory_is_inert(self) -> None:
        """``None`` must not be probed as if it were callable."""
        self.assertFalse(_probe(None))

    def test_var_positional_factory_receives_workspace(self) -> None:
        """``*args`` absorbs the fourth argument, so pass it."""

        async def factory(*args: object) -> list:
            del args
            return []

        self.assertTrue(_probe(factory))

    def test_callable_object_is_probed_on_its_call(self) -> None:
        """``__call__`` is what gets invoked, so it is what is measured."""
        self.assertFalse(_probe(_CallableLegacy()))

    def test_partial_keeps_the_underlying_shape(self) -> None:
        """``functools.partial`` must not be mistaken for a legacy shape."""
        self.assertTrue(_probe(functools.partial(_with_workspace)))
        self.assertFalse(_probe(functools.partial(_legacy)))
