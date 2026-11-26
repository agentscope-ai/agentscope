# -*- coding: utf-8 -*-
"""Confirmation receipts across event replay, execution and checkpoints."""
# pylint: disable=protected-access
from unittest import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent
from agentscope.event import (
    ConfirmResult,
    RequireUserConfirmEvent,
    ToolResultEndEvent,
    UserConfirmResultEvent,
)
from agentscope.message import ToolCallBlock
from agentscope.permission import PermissionRule
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, Toolkit


class ReceiptTest(IsolatedAsyncioTestCase):
    """A receipt applies only to its own outstanding request."""

    def setUp(self) -> None:
        self.executed = []

        def preview(value: str) -> str:
            self.executed.append(value)
            return value

        self.agent = Agent(
            "agent",
            "Help",
            MockModel(),
            toolkit=Toolkit(tools=[FunctionTool(preview)]),
        )

    async def prompt(self, call_id: str) -> RequireUserConfirmEvent:
        """Park an actual tool call."""
        call = ToolCallBlock(
            id=call_id,
            name="preview",
            input='{"value":"draft"}',
        )
        self.agent.state.append_context(self.agent.name, [call])
        (request,) = [e async for e in self.agent._execute_tool_call(call)]
        self.assertIsInstance(request, RequireUserConfirmEvent)
        return request

    async def test_missing_request_id_keeps_current_card_actionable(
        self,
    ) -> None:
        """A legacy submission gets guidance and leaves the call parked."""
        request = await self.prompt("one")
        answer = ConfirmResult(
            confirmed=False,
            tool_call=request.tool_calls[0],
        )
        event = UserConfirmResultEvent(
            reply_id=request.reply_id,
            confirm_results=[answer],
        )
        with self.assertRaisesRegex(ValueError, "RequireUserConfirmEvent.id"):
            async for _ in self.agent.reply_stream(event):
                self.fail("Invalid receipt must not start a reply")
        self.assertEqual(
            self.agent.state.confirmation_request(self.agent.name, "one"),
            request.id,
        )
        answer.request_id = request.id
        self.assertEqual(
            self.agent.state.confirmation_status(
                self.agent.name,
                event,
            ),
            "ready",
        )

    async def test_batch_applies_first_matching_receipt(self) -> None:
        """Duplicates and obsolete grants cannot overwrite a rejection."""
        for index, order in enumerate(
            (("old", "current"), ("current", "old"), ("current", "current")),
        ):
            with self.subTest(order=order):
                request = await self.prompt(str(index))
                event = UserConfirmResultEvent(
                    reply_id=request.reply_id,
                    confirm_results=[
                        ConfirmResult(
                            request_id=request.id
                            if key == "current"
                            else "old",
                            confirmed=i != order.index("current"),
                            tool_call=request.tool_calls[0],
                        )
                        for i, key in enumerate(order)
                    ],
                )
                msg = self.agent.state.get_reply_message(self.agent.name)
                replay = msg.model_copy(deep=True)
                replay.append_event(event)
                ends = [
                    e.state
                    async for e in self.agent._handle_incoming_event(event)
                    if isinstance(e, ToolResultEndEvent)
                ]
                self.assertEqual(
                    (ends, msg.get_content_blocks("tool_call"), msg.metadata),
                    (["denied"], replay.get_content_blocks("tool_call"), {}),
                )
                self.assertEqual(replay.metadata, {})

    async def test_finished_duplicate_while_other_call_waits(self) -> None:
        """A late receipt leaves another open card usable."""
        first = await self.prompt("a")
        second = await self.prompt("b")

        def reject(prompt: RequireUserConfirmEvent) -> ConfirmResult:
            return ConfirmResult(
                request_id=prompt.id,
                confirmed=False,
                tool_call=prompt.tool_calls[0],
            )

        late = UserConfirmResultEvent(
            reply_id=first.reply_id,
            confirm_results=[reject(first)],
        )
        async for _ in self.agent._handle_incoming_event(late):
            pass
        actual = [e async for e in self.agent.reply_stream(late)]
        msg = self.agent.state.get_reply_message(self.agent.name)
        self.assertEqual(
            (actual, msg.metadata),
            (
                [],
                {"pending_confirmations": {"b": second.id}},
            ),
        )
        old = late.model_copy(deep=True)
        old.reply_id = "previous-reply"
        old.confirm_results[0].tool_call.id = "previous-call"
        self.assertEqual([e async for e in self.agent.reply_stream(old)], [])
        unknown = reject(second)
        unknown.tool_call.id = "unknown"
        with self.assertRaisesRegex(ValueError, "unknown"):
            async for _ in self.agent.reply_stream(
                late.model_copy(update={"confirm_results": [unknown]}),
            ):
                pass
        batch = late.model_copy(
            update={"confirm_results": [reject(first), reject(second)]},
        )
        self.assertTrue(await self.agent._check_incoming_event(batch))
        ends = [
            e.tool_call_id
            async for e in self.agent._handle_incoming_event(batch)
            if isinstance(e, ToolResultEndEvent)
        ]
        self.assertEqual((ends, msg.metadata, self.executed), (["b"], {}, []))

    async def test_invalid_edit_does_not_save_grant(self) -> None:
        """An edited call missing a required argument cannot save a rule."""
        request = await self.prompt("one")
        edited = request.tool_calls[0].model_copy(update={"input": "{}"})
        result = ConfirmResult(
            request_id=request.id,
            confirmed=True,
            tool_call=edited,
            rules=[
                PermissionRule(
                    tool_name="preview",
                    rule_content=None,
                    behavior="allow",
                    source="user",
                ),
            ],
        )
        async for _ in self.agent._handle_incoming_event(
            UserConfirmResultEvent(
                reply_id=request.reply_id,
                confirm_results=[result],
            ),
        ):
            pass
        msg = self.agent.state.get_reply_message(self.agent.name)
        (call,) = msg.get_content_blocks("tool_call")
        ends = [
            e.state
            async for e in self.agent._execute_tool_call(call)
            if isinstance(e, ToolResultEndEvent)
        ]
        self.assertEqual(
            (
                ends,
                self.agent.state.permission_context.allow_rules,
                self.executed,
            ),
            (["error"], {}, []),
        )
        await self.prompt("two")

    async def test_checkpoint_keeps_public_block_shape(self) -> None:
        """Re-save a persisted pending request with a public tool block."""
        request = await self.prompt("one")
        approval = self.agent.state.reply_context.tool_approvals["one"]
        checkpoint = AgentState.model_validate_json(
            self.agent.state.model_dump_json(),
        )
        for options in (
            {},
            {"exclude_unset": True},
            {"exclude_defaults": True},
        ):
            with self.subTest(options=options):
                state = AgentState.model_validate_json(
                    checkpoint.model_dump_json(**options),
                )
                message = state.get_reply_message("agent")
                (call,) = message.get_content_blocks("tool_call")
                self.assertEqual(
                    set(call.model_dump()),
                    {
                        "type",
                        "id",
                        "name",
                        "input",
                        "state",
                        "suggested_rules",
                        "created_at",
                        "finished_at",
                    },
                )
                self.assertEqual(
                    state.reply_context.tool_approvals["one"].model_dump(
                        mode="json",
                    ),
                    approval.model_dump(mode="json"),
                )
                self.assertEqual(
                    message.metadata,
                    {
                        "pending_confirmations": {"one": request.id},
                    },
                )
                answer = ConfirmResult(
                    request_id=request.id,
                    confirmed=True,
                    tool_call=call,
                )
                self.assertTrue(answer.answers(call, request.id))
