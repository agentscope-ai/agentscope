# -*- coding: utf-8 -*-
"""Unit tests for ChatService._build_agent extensibility hook."""
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.app._service._chat import ChatService


class _CustomChatService(ChatService):
    """Subclass that overrides _build_agent to record the call."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.build_agent_called_with = None

    async def _build_agent(self, user_id, agent_id, session_id, middlewares):
        agent = await super()._build_agent(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            middlewares=middlewares,
        )
        self.build_agent_called_with = (user_id, agent_id, session_id)
        return agent


class TestChatServiceBuildAgentHook(IsolatedAsyncioTestCase):
    """Test that _build_agent is the single assembly point and is overridable."""

    def _make_service(self, cls=ChatService):
        return cls(
            storage=MagicMock(),
            session_manager=MagicMock(),
            background_task_manager=MagicMock(),
            workspace_manager=MagicMock(),
        )

    async def test_default_build_agent_calls_get_agent(self):
        """_build_agent delegates to get_agent with the correct arguments."""
        service = self._make_service()
        mock_agent = MagicMock()

        with patch(
            "agentscope.app._service._chat.get_agent",
            new_callable=AsyncMock,
            return_value=mock_agent,
        ) as mock_get_agent:
            result = await service._build_agent(
                user_id="u1",
                agent_id="a1",
                session_id="s1",
                middlewares=[],
            )

        mock_get_agent.assert_awaited_once_with(
            storage=service._storage,
            workspace_manager=service._workspace_manager,
            user_id="u1",
            agent_id="a1",
            session_id="s1",
            middlewares=[],
            extra_agent_middlewares=None,
            extra_agent_tools=None,
        )
        self.assertIs(result, mock_agent)

    async def test_subclass_override_is_called_by_stream_chat(self):
        """stream_chat calls self._build_agent, so subclass overrides take effect."""
        service = self._make_service(cls=_CustomChatService)
        mock_agent = MagicMock()

        # Patch get_agent so assembly succeeds without real infrastructure.
        with patch(
            "agentscope.app._service._chat.get_agent",
            new_callable=AsyncMock,
            return_value=mock_agent,
        ):
            # Patch the session manager so stream_chat can enter its context.
            run_ctx = MagicMock()
            run_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            run_ctx.__aexit__ = AsyncMock(return_value=False)
            service._session_manager.run.return_value = run_ctx

            # Agent reply_stream yields nothing — we only care that
            # _build_agent was invoked on the subclass.
            async def _empty_stream(inputs):
                return
                yield  # noqa: unreachable — makes this an async generator

            mock_agent.reply_stream = _empty_stream
            service._storage.upsert_message = AsyncMock()
            service._storage.update_session_state = AsyncMock()

            from agentscope.message import UserMsg

            async for _ in service.stream_chat(
                user_id="u1",
                session_id="s1",
                agent_id="a1",
                input_msg=None,
            ):
                pass

        self.assertEqual(
            service.build_agent_called_with,
            ("u1", "a1", "s1"),
        )
