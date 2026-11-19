# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Independent voice policy survives queuing and parked reply resumes."""
import asyncio
from typing import Any, AsyncGenerator
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, call, patch

from fastapi import HTTPException
from chat_submission_cancellation_test import _reserve
from pydantic import ValidationError
from service_chat_locking_test import (
    _Access,
    _Storage,
    _WorkspaceManager,
    _get_toolkit,
)

from agentscope.app._bus_ops import (
    cancel_chat_submission,
    enqueue_chat_input,
    list_chat_inputs,
)
from agentscope.app._manager import CancelDispatcher, ChatRunRegistry
from agentscope.app._manager._chat_input_queue import ChatInputQueue
from agentscope.app._router._schema import ChatRequest, UpdateSessionRequest
from agentscope.app._router._session import update_session
from agentscope.app._router._chat import chat
from agentscope.app._service import ChatService
from agentscope.app._service._model import REPLY_CHAT_CONFIG
from agentscope.app._service._tts_model import validate_tts_model_config
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.middleware._chat_tts_middleware import (
    ChatTTSMiddleware,
    REPLY_TTS_CONFIG,
)
from agentscope.app.storage import (
    AgentData,
    AgentRecord,
    ASRModelConfig,
    ChatModelConfig,
    ChatModelSnapshot,
    CredentialRecord,
    PipelineVoiceConfig,
    SessionConfig,
    SessionRecord,
    TTSModelConfig,
    VoiceProfileData,
    VoiceProfileRecord,
)
from agentscope.event import (
    ReplyEndEvent,
    ReplyStartEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from agentscope.message import AssistantMsg, Msg, ToolCallBlock, UserMsg
from agentscope.state import AgentState
from agentscope.tts import TTSModelBase
from agentscope.model import ChatModelBase
from agentscope.credential import OpenAICredential


def _tts(model: str) -> TTSModelConfig:
    """Build a distinguishable speech configuration."""
    return TTSModelConfig(
        type="openai_credential",
        credential_id="c",
        model=model,
        parameters={"voice": "alloy"},
    )


class _ReplyAgent:
    """Execute the production speech middleware without external models."""

    def __init__(
        self,
        name: str,
        state: AgentState,
        middlewares: list,
        model: object,
        model_config: object,
        **_: object,
    ) -> None:
        self.name = name
        self.state = state
        self.model = model
        self.model_config = model_config
        self.models_used = []
        self.speech = next(
            mw for mw in middlewares if isinstance(mw, ChatTTSMiddleware)
        )

    async def reply_stream(self, inputs: object) -> AsyncGenerator:
        """Emit reply boundaries through the actual speech policy."""
        fallback = self.model_config.fallback_model
        self.models_used.append(
            (
                self.model.config,
                fallback.config if fallback is not None else None,
            ),
        )

        async def reply(**_: object) -> AsyncGenerator:
            messages = inputs if isinstance(inputs, list) else [inputs]
            if inputs is None or isinstance(messages[0], Msg):
                self.state.reply_id = (
                    messages[0].id + "-reply"
                    if inputs is not None
                    else "inbox-reply"
                )
                yield ReplyStartEvent(
                    session_id="session-1",
                    reply_id=self.state.reply_id,
                    name=self.name,
                )
            yield ReplyEndEvent(
                session_id="session-1",
                reply_id=self.state.reply_id,
                finished_reason="completed",
            )

        async for event in self.speech.on_reply(
            self,
            {"inputs": inputs},
            reply,
        ):
            yield event


class PipelineVoiceConfigTest(IsolatedAsyncioTestCase):
    """Cover ordinary chat isolation and persisted per-reply policy."""

    async def asyncSetUp(self) -> None:
        """Assemble the real chat service with in-memory dependencies."""
        self.agent = AgentRecord(
            id="agent-1",
            user_id="user-1",
            data=AgentData(name="agent"),
        )
        self.session = SessionRecord(
            id="session-1",
            user_id="user-1",
            agent_id=self.agent.id,
            config=SessionConfig(
                workspace_id="workspace-1",
                chat_model_config=ChatModelConfig(
                    type="test",
                    credential_id="c",
                    model="chat",
                    parameters={},
                ),
                pipeline_voice_config=PipelineVoiceConfig(
                    asr_model_config=ASRModelConfig(
                        type="openai_credential",
                        credential_id="c",
                        model="asr",
                        parameters={},
                    ),
                    tts_model_config=_tts("voice"),
                ),
            ),
        )
        self.storage = _Storage(self.session)
        self.bus = await self.enterAsyncContext(InMemoryMessageBus())
        self.service = ChatService(
            self.storage,
            _WorkspaceManager(),
            object(),
            object(),
            self.bus,
            _Access(self.agent),
            custom_agent_cls=_ReplyAgent,
        )
        self.service._access.resolve_credential = AsyncMock()
        self.agents = []

        def create_agent(**kwargs: Any) -> _ReplyAgent:
            agent = _ReplyAgent(**kwargs)
            self.agents.append(agent)
            return agent

        self.service._agent_cls = create_agent

        async def create_model(
            _user: str,
            config: ChatModelConfig,
            _access: object,
        ) -> ChatModelBase:
            model = MagicMock(spec=ChatModelBase)
            model.config = config.model_copy(deep=True)
            return model

        self.create_model = self.enterContext(
            patch(
                "agentscope.app._service._chat.get_model",
                new=AsyncMock(side_effect=create_model),
            ),
        )
        self.enterContext(
            patch("agentscope.app._service._chat.get_toolkit", _get_toolkit),
        )
        self.validate = self.enterContext(
            patch(
                "agentscope.app._service._chat.validate_tts_model_config",
                new_callable=AsyncMock,
            ),
        )
        model = MagicMock(spec=TTSModelBase)
        model.realtime = False
        model.__aenter__ = AsyncMock(return_value=model)
        model.__aexit__ = AsyncMock(return_value=None)
        self.create_tts = self.enterContext(
            patch(
                "agentscope.app._service._chat.get_tts_model",
                new=AsyncMock(return_value=model),
            ),
        )

    async def _run(self, inputs: object, **kwargs: object) -> None:
        """Run one input through normal persistence and middleware."""
        await self.service._run_impl(
            "user-1",
            "session-1",
            "agent-1",
            inputs,
            **kwargs,
        )

    async def test_submission_binding_survives_message_reuse(self) -> None:
        """The real queue/service binds each submission before reply events."""
        queue = ChatInputQueue(self.bus, self.storage, self.service)
        self.addAsyncCleanup(queue.close)
        replies = ["A", "B"]
        queue_ids = {}

        async def reply_stream(
            agent: _ReplyAgent,
            inputs: object,
        ) -> AsyncGenerator:
            self.assertIsInstance(inputs, Msg)
            agent.state.reply_id = replies.pop(0)
            yield ReplyStartEvent(
                session_id="session-1",
                reply_id=agent.state.reply_id,
                name=agent.name,
            )
            # The service has persisted the binding before advancing here.
            self.assertEqual(
                await self.service.input_result(
                    "user-1",
                    "session-1",
                    "agent-1",
                    "shared",
                    submission_id=agent.state.reply_id,
                ),
                {
                    "state": "reply",
                    "reply_id": agent.state.reply_id,
                    "queue_item_id": queue_ids[agent.state.reply_id],
                },
            )
            yield ReplyEndEvent(
                session_id="session-1",
                reply_id=agent.state.reply_id,
                finished_reason="completed",
            )

        with patch.object(_ReplyAgent, "reply_stream", reply_stream):
            for submission in ("A", "B"):
                await _reserve(
                    self.bus,
                    submission,
                    "user-1",
                    "session-1",
                    "agent-1",
                )
                item = await enqueue_chat_input(
                    self.bus,
                    "user-1",
                    "session-1",
                    "agent-1",
                    UserMsg("user", "voice", id="shared"),
                    submission_id=submission,
                )
                queue_ids[submission] = item["id"]
                self.assertTrue(
                    await queue.drain_chat_inputs(
                        "user-1",
                        "session-1",
                        "agent-1",
                    ),
                )
        for submission in ("A", "B"):
            self.assertEqual(
                await self.service.input_result(
                    "user-1",
                    "session-1",
                    "agent-1",
                    "shared",
                    submission_id=submission,
                ),
                {
                    "state": "finished",
                    "reply_id": submission,
                    "queue_item_id": queue_ids[submission],
                },
            )

    async def test_cancel_during_service_initialization(self) -> None:
        """Real service assembly propagates cancellation before reply start."""
        # pylint: disable=cell-var-from-loop
        registry = ChatRunRegistry()
        self.service._run_registry = registry
        queue = ChatInputQueue(
            self.bus,
            self.storage,
            self.service,
            registry,
        )
        self.addAsyncCleanup(queue.close)
        dispatcher = CancelDispatcher(self.bus, registry, MagicMock())
        async with registry, dispatcher:
            for factory in ("get_model", "get_toolkit", "get_tts_model"):
                with self.subTest(factory=factory):
                    await _reserve(
                        self.bus,
                        factory,
                        "user-1",
                        "session-1",
                        "agent-1",
                    )
                    entered = asyncio.Event()

                    async def block(*_args: object, **_kw: object) -> None:
                        entered.set()
                        await asyncio.Event().wait()

                    item = await enqueue_chat_input(
                        self.bus,
                        "user-1",
                        "session-1",
                        "agent-1",
                        UserMsg("user", "voice", id=factory),
                        submission_id=factory,
                        pipeline_tts_config=_tts("voice"),
                    )
                    with patch(
                        f"agentscope.app._service._chat.{factory}",
                        new=AsyncMock(side_effect=block),
                    ):
                        task = registry.spawn(
                            queue.drain_chat_inputs(
                                "user-1",
                                "session-1",
                                "agent-1",
                            ),
                            session_id="session-1",
                        )
                        await asyncio.wait_for(entered.wait(), 2)
                        self.assertEqual(
                            await cancel_chat_submission(
                                self.bus,
                                "user-1",
                                "session-1",
                                "agent-1",
                                factory,
                            ),
                            {
                                "state": "started",
                                "queue_item_id": item["id"],
                            },
                        )
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(task, 2)
                    self.assertEqual(
                        await cancel_chat_submission(
                            self.bus,
                            "user-1",
                            "session-1",
                            "agent-1",
                            factory,
                        ),
                        {
                            "state": "cancelled",
                            "queue_item_id": item["id"],
                        },
                    )

    async def _park_voice_reply(self) -> Msg:
        """Persist a reply awaiting confirmation with a stale credential."""
        current = self.session.config.chat_model_config
        old = current.model_copy(update={"credential_id": "deleted"})
        reply = AssistantMsg(
            id="parked",
            name=self.agent.data.name,
            content=[
                ToolCallBlock(
                    id="tool",
                    name="ask",
                    input="{}",
                    state="asking",
                ),
            ],
            metadata={
                REPLY_CHAT_CONFIG: ChatModelSnapshot(
                    primary=old,
                    fallback=old,
                ).model_dump(mode="json"),
            },
        )
        self.session.state.reply_id = reply.id
        self.session.state.context.append(reply.model_copy(deep=True))
        await self.storage.upsert_message("user-1", "session-1", reply)
        return reply

    async def test_stop_parked_reply_after_credential_removed(self) -> None:
        """Stopping closes the original tools and unblocks queued chat."""
        current = self.session.config.chat_model_config
        reply = await self._park_voice_reply()
        queue = ChatInputQueue(self.bus, self.storage, self.service)
        self.addAsyncCleanup(queue.close)
        await enqueue_chat_input(
            self.bus,
            "user-1",
            "session-1",
            "agent-1",
            UserMsg("user", "next", id="next"),
        )
        published = self.enterContext(
            patch.object(self.bus, "log_append", wraps=self.bus.log_append),
        )
        with patch(
            "agentscope.app._service._chat.get_model",
            side_effect=LookupError("Credential deleted"),
        ) as load:
            await self._run(UserInterruptEvent(reply_id=reply.id))
            await self._run(UserInterruptEvent(reply_id=reply.id))
            load.assert_not_awaited()
        events = [entry.args[1] for entry in published.await_args_list]
        self.assertEqual(
            [(event["type"], event["reply_id"]) for event in events],
            [
                ("REPLY_START", "parked"),
                ("TOOL_RESULT_START", "parked"),
                ("TOOL_RESULT_TEXT_DELTA", "parked"),
                ("TOOL_RESULT_END", "parked"),
                ("REPLY_END", "parked"),
            ],
        )
        self.assertEqual(events[-1]["finished_reason"], "interrupted")
        closed = await self.storage.get_message(
            "user-1",
            "session-1",
            reply.id,
        )
        self.assertEqual(closed.finished_reason, "interrupted")
        self.assertEqual(closed.metadata, reply.metadata)
        for message in (closed, self.session.state.context[-1]):
            self.assertEqual(
                [(b.type, b.id, b.state) for b in message.content],
                [
                    ("tool_call", "tool", "finished"),
                    ("tool_result", "tool", "interrupted"),
                ],
            )
        self.assertTrue(
            await queue.drain_chat_inputs("user-1", "session-1", "agent-1"),
        )
        self.assertEqual(
            [agent.models_used for agent in self.agents],
            [[(current, None)]],
        )

    async def test_parked_stop_survives_failed_live_delivery(self) -> None:
        """Missing live events remain replayable after state is committed."""
        reply = await self._park_voice_reply()
        key = MessageBusKeys.session_events(self.session.id)
        publish = self.bus.publish

        async def fail_live(channel: str, payload: dict) -> None:
            if channel == key:
                raise ConnectionError("Live subscriber disconnected")
            await publish(channel, payload)

        with patch.object(self.bus, "publish", side_effect=fail_live):
            await self._run(UserInterruptEvent(reply_id=reply.id))

        replay = await self.bus.log_read(key)
        cursor = await self.bus.registry_get(
            MessageBusKeys.session_event_checkpoint(self.session.id),
            MessageBusKeys.SESSION_EVENT_CURSOR_FIELD,
        )
        self.assertEqual(cursor, replay[-1][0])
        self.assertEqual(
            [(e["reply_id"], e["type"]) for _, e in replay],
            [
                (reply.id, "REPLY_START"),
                (reply.id, "TOOL_RESULT_START"),
                (reply.id, "TOOL_RESULT_TEXT_DELTA"),
                (reply.id, "TOOL_RESULT_END"),
                (reply.id, "REPLY_END"),
            ],
        )
        persisted = await self.storage.get_message(
            "user-1",
            self.session.id,
            reply.id,
        )
        self.assertEqual(persisted.finished_reason, "interrupted")
        self.assertEqual(
            self.session.state.get_awaiting_tool_calls(self.agent.data.name),
            [],
        )
        self.assertEqual({m.id for m in self.storage.messages}, {reply.id})

    async def test_snapshot_queue_resume_and_next_ordinary_reply(self) -> None:
        """Queued voice keeps its policy; subsequent chat stays silent."""
        voice_model = self.session.config.chat_model_config.model_copy(
            update={"model": "voice-llm", "parameters": {"temperature": 0.2}},
        )
        self.session.config.pipeline_voice_config.chat_model_config = (
            voice_model
        )
        models, snapshot = await self.service.pipeline_snapshot(
            "user-1",
            "session-1",
            "agent-1",
        )
        original = snapshot.model_dump(mode="json")
        response = await chat(
            ChatRequest(
                agent_id="agent-1",
                session_id="session-1",
                input=UserMsg("user", "voice input", id="voice"),
                mode="pipeline_voice",
            ),
            user_id="user-1",
            storage=self.storage,
            chat_service=self.service,
            chat_run_registry=None,
            message_bus=self.bus,
        )
        self.assertEqual(response.status, "queued")
        snapshot.parameters["voice"] = "changed locally"
        self.session.config.pipeline_voice_config.tts_model_config = _tts(
            "changed session",
        )
        self.session.config.pipeline_voice_config.chat_model_config = (
            voice_model.model_copy(update={"model": "new-voice"})
        )
        queue = ChatInputQueue(self.bus, self.storage, self.service)
        self.addAsyncCleanup(queue.close)
        self.assertTrue(
            await queue.drain_chat_inputs("user-1", "session-1", "agent-1"),
        )
        reply = await self.storage.get_message(
            "user-1",
            "session-1",
            "voice-reply",
        )
        self.assertEqual(
            reply.metadata,
            {
                REPLY_TTS_CONFIG: original,
                REPLY_CHAT_CONFIG: models.model_dump(mode="json"),
            },
        )
        await self._run(
            UserConfirmResultEvent(reply_id="voice-reply", confirm_results=[]),
        )
        await self._run(UserMsg("user", "ordinary", id="ordinary"))
        self.assertEqual(
            [
                c.args[1].model_dump(mode="json")
                for c in self.create_tts.await_args_list
            ],
            [original, original],
        )
        ordinary = await self.storage.get_message(
            "user-1",
            "session-1",
            "ordinary-reply",
        )
        self.assertEqual(
            ordinary.metadata,
            {
                REPLY_TTS_CONFIG: None,
                REPLY_CHAT_CONFIG: ChatModelSnapshot(
                    primary=self.session.config.chat_model_config,
                ).model_dump(mode="json"),
            },
        )
        self.assertEqual(
            [agent.models_used for agent in self.agents],
            [
                [(voice_model, None)],
                [(voice_model, None)],
                [(self.session.config.chat_model_config, None)],
            ],
        )
        self.assertIsNone(self.session.config.tts_model_config)

    async def test_silent_resume_and_enabled_ordinary_chat(self) -> None:
        """A silent reply stays silent when ordinary TTS is enabled later."""
        await self._run(UserMsg("user", "silent", id="silent"))
        self.session.config.tts_model_config = _tts("ordinary")
        await self._run(
            UserConfirmResultEvent(
                reply_id="silent-reply",
                confirm_results=[],
            ),
        )
        self.create_tts.assert_not_awaited()
        await self._run(UserMsg("user", "speaking", id="speaking"))
        self.assertEqual(
            [c.args[1] for c in self.create_tts.await_args_list],
            [_tts("ordinary")],
        )

    async def test_follow_chat_freezes_primary_and_fallback(self) -> None:
        """Following captures the effective pair, not a mutable reference."""
        primary = self.session.config.chat_model_config.model_copy(deep=True)
        fallback = primary.model_copy(update={"model": "backup"})
        self.session.config.fallback_chat_model_config = fallback
        models, speech = await self.service.pipeline_snapshot(
            "user-1",
            "session-1",
            "agent-1",
        )
        self.session.config.chat_model_config = primary.model_copy(
            update={"model": "changed"},
        )
        self.session.config.fallback_chat_model_config = None
        await self._run(
            UserMsg("user", "follow", id="follow"),
            pipeline_chat_config=models,
            pipeline_tts_config=speech,
        )
        await self._run(
            UserConfirmResultEvent(
                reply_id="follow-reply",
                confirm_results=[],
            ),
        )
        self.assertEqual(
            [agent.models_used for agent in self.agents],
            [[(primary, fallback)], [(primary, fallback)]],
        )

    async def test_same_run_restores_ordinary_models(self) -> None:
        """An inbox followup uses ordinary LLM/fallback after a voice reply."""
        primary = self.session.config.chat_model_config
        fallback = primary.model_copy(update={"model": "backup"})
        self.session.config.fallback_chat_model_config = fallback
        voice = primary.model_copy(update={"model": "voice"})
        self.session.config.pipeline_voice_config.chat_model_config = voice
        models, speech = await self.service.pipeline_snapshot(
            "user-1",
            "session-1",
            "agent-1",
        )
        self.assertEqual(models, ChatModelSnapshot(primary=voice))
        with patch(
            "agentscope.app._service._chat.has_pending_inbox_or_release",
            side_effect=[True, False],
        ):
            await self._run(
                UserMsg("user", "voice", id="voice"),
                pipeline_chat_config=models,
                pipeline_tts_config=speech,
            )
        self.assertEqual(
            self.agents[0].models_used,
            [(voice, None), (primary, fallback)],
        )
        reply = await self.storage.get_message(
            "user-1",
            "session-1",
            "inbox-reply",
        )
        self.assertEqual(
            reply.metadata,
            {
                REPLY_TTS_CONFIG: None,
                REPLY_CHAT_CONFIG: ChatModelSnapshot(
                    primary=primary,
                    fallback=fallback,
                ).model_dump(mode="json"),
            },
        )

    async def test_independent_llm_without_normal_model(self) -> None:
        """A voice model is sufficient even when ordinary chat is unset."""
        voice = self.session.config.chat_model_config
        self.session.config.chat_model_config = None
        self.session.config.pipeline_voice_config.chat_model_config = voice
        models, speech = await self.service.pipeline_snapshot(
            "user-1",
            "session-1",
            "agent-1",
        )
        await self._run(
            UserMsg("user", "voice"),
            pipeline_chat_config=models,
            pipeline_tts_config=speech,
        )
        self.assertEqual(self.agents[0].models_used, [(voice, None)])
        self.session.config.pipeline_voice_config.chat_model_config = None
        with self.assertRaises(HTTPException) as error:
            await self.service.pipeline_snapshot(
                "user-1",
                "session-1",
                "agent-1",
            )
        self.assertEqual(error.exception.status_code, 422)

    async def test_inaccessible_voice_model_is_rejected(self) -> None:
        """Snapshotting does not bypass credential access checks."""
        self.service._access.resolve_credential.side_effect = HTTPException(
            status_code=404,
        )
        with self.assertRaises(HTTPException) as error:
            await self.service.pipeline_snapshot(
                "user-1",
                "session-1",
                "agent-1",
            )
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(await list_chat_inputs(self.bus, "session-1"), [])

    async def test_ordinary_request_cannot_override_speech_in_metadata(
        self,
    ) -> None:
        """Only the explicit request mode selects server voice settings."""
        message = UserMsg(
            "user",
            "ordinary",
            metadata={
                "pipeline_chat_config": ChatModelSnapshot(
                    primary=self.session.config.chat_model_config.model_copy(
                        update={"model": "untrusted"},
                    ),
                ).model_dump(mode="json"),
                "pipeline_tts_config": _tts("untrusted").model_dump(
                    mode="json",
                ),
                REPLY_TTS_CONFIG: _tts("untrusted").model_dump(mode="json"),
            },
        )
        await chat(
            ChatRequest(
                agent_id="agent-1",
                session_id="session-1",
                input=message,
            ),
            user_id="user-1",
            storage=self.storage,
            chat_service=self.service,
            chat_run_registry=None,
            message_bus=self.bus,
        )
        items = await list_chat_inputs(self.bus, "session-1")
        self.assertEqual(len(items), 1)
        self.assertNotIn("pipeline_tts_config", items[0])
        queue = ChatInputQueue(self.bus, self.storage, self.service)
        self.addAsyncCleanup(queue.close)
        self.assertTrue(
            await queue.drain_chat_inputs(
                "user-1",
                "session-1",
                "agent-1",
            ),
        )
        self.create_tts.assert_not_awaited()
        self.validate.assert_not_awaited()

    async def test_followup_in_same_run_uses_default(self) -> None:
        """An inbox followup cannot inherit the voice turn's settings."""
        middleware = ChatTTSMiddleware(
            _tts("voice"),
            _tts("ordinary"),
            self.create_tts,
        )

        async def reply(**_: object) -> AsyncGenerator:
            yield ReplyStartEvent(
                session_id="s",
                reply_id="r",
                name="agent",
            )

        observed = []
        for _ in range(2):
            events = [
                event
                async for event in middleware.on_reply(
                    MagicMock(),
                    {},
                    reply,
                )
            ]
            observed.append(events[0].metadata)
        self.assertEqual(
            observed,
            [
                {REPLY_TTS_CONFIG: _tts("voice").model_dump(mode="json")},
                {REPLY_TTS_CONFIG: _tts("ordinary").model_dump(mode="json")},
            ],
        )

    def _configure_voice_profile(self) -> VoiceProfileRecord:
        """Exercise real profile validation with owner-scoped test storage."""
        config = TTSModelConfig(
            type="openai_credential",
            credential_id="c",
            model="gpt-4o-mini-tts",
            voice_profile_id="profile",
            parameters={"voice": "voice-old", "response_format": "wav"},
        )
        self.session.config.pipeline_voice_config.tts_model_config = config
        profile = VoiceProfileRecord(
            id="profile",
            user_id="user-1",
            data=VoiceProfileData(
                name="Voice",
                engine="openai_tts",
                credential_id="c",
                model=config.model,
                voice="voice-old",
            ),
        )
        self.storage.get_voice_profile = AsyncMock(return_value=profile)
        self.service._access.resolve_credential = AsyncMock(
            return_value=CredentialRecord(
                id="c",
                user_id="user-1",
                data={"type": "openai_credential", "api_key": "test"},
            ),
        )
        self.validate.side_effect = validate_tts_model_config
        return profile

    async def test_pipeline_follows_updated_voice_profile(self) -> None:
        """Submission refreshes bindings without weakening session writes."""
        for changes, parameters in (
            (
                {"voice": "voice-new"},
                {"voice": "voice-new", "response_format": "wav"},
            ),
            (
                {"credential_id": "new-c"},
                {"voice": "voice-old", "response_format": "wav"},
            ),
            ({"model": "tts-1", "voice": "alloy"}, {"voice": "alloy"}),
        ):
            with self.subTest(changes=changes):
                profile = self._configure_voice_profile()
                config = self.session.config.pipeline_voice_config
                before = config.tts_model_config.model_dump()
                profile.data = profile.data.model_copy(update=changes)
                access = self.service._access
                access.resolve_credential.return_value.id = (
                    profile.data.credential_id
                )
                models, speech = await self.service.pipeline_snapshot(
                    "user-1",
                    "session-1",
                    "agent-1",
                )
                self.assertEqual(
                    (models, speech.model_dump()),
                    (
                        ChatModelSnapshot(
                            primary=self.session.config.chat_model_config,
                        ),
                        {
                            **before,
                            "credential_id": profile.data.credential_id,
                            "model": profile.data.model,
                            "parameters": parameters,
                        },
                    ),
                )
                self.assertEqual(config.tts_model_config.model_dump(), before)
                self.storage.get_voice_profile.assert_awaited_once_with(
                    "user-1",
                    "profile",
                )
                self.assertEqual(
                    access.resolve_credential.await_args_list,
                    [
                        call("user-1", profile.data.credential_id),
                        call("user-1", "c"),
                    ],
                )
                with self.assertRaises(HTTPException) as error:
                    await validate_tts_model_config(
                        "user-1",
                        config.tts_model_config,
                        access,
                        self.storage,
                    )
                self.assertEqual(error.exception.status_code, 400)

    async def test_pipeline_rejects_invalid_current_profile(self) -> None:
        """Missing profiles and revoked credentials cannot use stale data."""
        for failure, status in (
            ("missing", 404),
            ("incomplete", 400),
            ("revoked", 404),
            ("shared", 403),
            ("parameters", 400),
        ):
            with self.subTest(failure=failure):
                profile = self._configure_voice_profile()
                access = self.service._access
                if failure == "missing":
                    self.storage.get_voice_profile.return_value = None
                elif failure == "incomplete":
                    profile.data.voice = None
                elif failure == "revoked":
                    access.resolve_credential.side_effect = HTTPException(404)
                elif failure == "shared":
                    access.resolve_credential.return_value.user_id = "other"
                else:
                    config = self.session.config.pipeline_voice_config
                    config.tts_model_config.parameters[
                        "response_format"
                    ] = "invalid"
                with self.assertRaises(HTTPException) as error:
                    await self.service.pipeline_snapshot(
                        "user-1",
                        "session-1",
                        "agent-1",
                    )
                self.assertEqual(error.exception.status_code, status)

    async def test_pipeline_checks_resolved_audio_format(self) -> None:
        """Changing models drops obsolete parameters before the PCM guard."""
        profile = self._configure_voice_profile()
        config = self.session.config.pipeline_voice_config
        config.tts_model_config.parameters["response_format"] = "pcm"
        with self.assertRaises(HTTPException) as error:
            await self.service.pipeline_snapshot(
                "user-1",
                "session-1",
                "agent-1",
            )
        self.assertEqual(error.exception.status_code, 422)
        profile.data.model = "tts-1"
        profile.data.voice = "alloy"
        _, speech = await self.service.pipeline_snapshot(
            "user-1",
            "session-1",
            "agent-1",
        )
        self.assertEqual(speech.parameters, {"voice": "alloy"})
        self.assertEqual(
            config.tts_model_config.parameters,
            {
                "voice": "voice-old",
                "response_format": "pcm",
            },
        )

    async def test_missing_voice_and_raw_pcm_are_rejected(self) -> None:
        """Ordinary speech configuration never fills missing voice settings."""
        voice = self.session.config.pipeline_voice_config
        voice.tts_model_config.parameters["response_format"] = "pcm"
        for config in (voice, None, PipelineVoiceConfig()):
            with self.subTest(config=config):
                self.session.config.pipeline_voice_config = config
                self.session.config.tts_model_config = _tts("ordinary")
                with self.assertRaises(HTTPException) as error:
                    await self.service.pipeline_snapshot(
                        "user-1",
                        "session-1",
                        "agent-1",
                    )
                self.assertEqual(error.exception.status_code, 422)
        self.validate.assert_not_awaited()

    def test_mode_defaults_and_continuation_validation(self) -> None:
        """Only fresh messages opt into pipeline speech."""
        request = {"agent_id": "a", "session_id": "s", "input": None}
        self.assertEqual(ChatRequest(**request).mode, "chat")
        with self.assertRaises(ValidationError):
            ChatRequest(**request, mode="pipeline_voice")

    async def test_patch_isolates_voice_and_validates_credentials(
        self,
    ) -> None:
        """The PATCH handler preserves omitted fields and honors null."""
        access = MagicMock()
        access.get_resource = AsyncMock()
        access.resolve_credential = AsyncMock(
            return_value=SimpleNamespace(
                data=OpenAICredential(api_key="test").model_dump(mode="json"),
                user_id="user-1",
            ),
        )

        async def save(**kwargs: object) -> SessionRecord:
            self.session.config = kwargs["config"]
            return self.session

        self.storage.upsert_session = AsyncMock(side_effect=save)

        async def update(body: dict) -> SessionRecord:
            return await update_session(
                "session-1",
                UpdateSessionRequest(**body),
                "agent-1",
                "user-1",
                self.storage,
                access,
                self.bus,
            )

        normal = _tts("tts-1").model_dump(mode="json")
        normal_chat = self.session.config.chat_model_config.model_copy(
            deep=True,
        )
        voice = self.session.config.pipeline_voice_config.model_dump(
            mode="json",
        )
        voice["tts_model_config"] = normal
        voice["chat_model_config"] = normal_chat.model_copy(
            update={"model": "voice", "parameters": {"temperature": 0.2}},
        ).model_dump(mode="json")
        await update({"tts_model_config": normal})
        saved = await update({"pipeline_voice_config": voice})
        self.assertEqual(saved.config.chat_model_config, normal_chat)
        self.assertEqual(
            {
                key: saved.config.model_dump(mode="json")[key]
                for key in (
                    "asr_model_config",
                    "tts_model_config",
                    "pipeline_voice_config",
                )
            },
            {
                "asr_model_config": None,
                "tts_model_config": normal,
                "pipeline_voice_config": voice,
            },
        )
        omitted = await update({"name": "renamed"})
        self.assertEqual(
            omitted.config.pipeline_voice_config.model_dump(mode="json"),
            voice,
        )
        profile = self._configure_voice_profile()
        profile.data.voice = "recloned"
        bound = self.session.config.pipeline_voice_config.model_dump()
        asr = {**bound["asr_model_config"], "model": "new-asr"}
        changes = {"asr_model_config": asr, "chat_model_config": None}
        saved = await update({"pipeline_voice_config": changes})
        self.assertEqual(
            saved.config.pipeline_voice_config.model_dump(),
            {**bound, **changes},
        )
        with self.assertRaises(HTTPException) as stale:
            edit = {"tts_model_config": bound["tts_model_config"]}
            await update({"pipeline_voice_config": edit})
        self.assertEqual(stale.exception.status_code, 400)
        cleared = await update({"pipeline_voice_config": None})
        self.assertEqual(
            {
                key: cleared.config.model_dump(mode="json")[key]
                for key in (
                    "asr_model_config",
                    "tts_model_config",
                    "pipeline_voice_config",
                )
            },
            {
                "asr_model_config": None,
                "tts_model_config": normal,
                "pipeline_voice_config": None,
            },
        )
        access.get_resource.side_effect = HTTPException(status_code=404)
        with self.assertRaises(HTTPException) as error:
            await update({"pipeline_voice_config": voice})
        self.assertEqual(error.exception.status_code, 404)
        self.assertIsNone(self.session.config.pipeline_voice_config)
