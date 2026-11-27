# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Clean up a realtime TTS client if session setup fails."""
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

from agentscope.credential import DashScopeCredential
from agentscope.tts import DashScopeRealtimeTTSModel


class DashScopeConnectCleanupTest(IsolatedAsyncioTestCase):
    """A failed connection must not leak or prevent a retry."""

    async def test_setup_failure_closes_client_and_allows_retry(self) -> None:
        """Preserve the original error even when cleanup also fails."""
        model = DashScopeRealtimeTTSModel(
            credential=DashScopeCredential(api_key="test"),
        )
        failed = Mock()
        failed.update_session.side_effect = RuntimeError("setup failed")
        failed.close.side_effect = RuntimeError("cleanup failed")
        retry = Mock()
        clients = iter([failed, retry])

        def create() -> None:
            model._tts_client = next(clients)

        with patch.object(model, "_create_client", side_effect=create):
            with self.assertRaisesRegex(RuntimeError, "setup failed"):
                await model.connect()
            failed.close.assert_called_once_with()
            await model.close()
            failed.close.assert_called_once_with()
            await model.connect()
            retry.update_session.assert_called_once()
            await model.close()
            retry.close.assert_called_once_with()
