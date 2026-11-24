# -*- coding: utf-8 -*-
"""Bounded provider payloads preserve original approval evidence."""
# pylint: disable=protected-access
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from permission_review_test import _review, _tool
from utils import MockModel

from agentscope.classifier import ChoiceAnswer, ClassifierResponse
from agentscope.message import (
    AssistantMsg,
    HintBlock,
    ThinkingBlock,
    ToolCallBlock,
    UserMsg,
)
from agentscope.model import ChatModelBase, ChatResponse, StructuredResponse
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionEngine,
    PermissionMode,
    PermissionReview,
)
from agentscope.permission._evidence import (
    AuthorizationEvidence,
    retain_authorization_evidence,
)
from agentscope.permission.reviewer import (
    ChatPermissionReviewer,
    ClassifierPermissionReviewer,
)


def _record(index: int, text: str) -> dict:
    """Build an original source-tagged instruction."""
    return AuthorizationEvidence(
        source="user",
        message_id=f"m{index}",
        block_id=f"b{index}",
        author="user",
        created_at="2026-11-18T00:00:00Z",
        text=text,
    ).model_dump()


def _request(evidence: list[dict]) -> dict:
    """Include every fixed part of a real approval request."""
    return {
        "action": {
            "tool_name": "Deploy",
            "description": "Publish a release",
            "input_schema": {"type": "object"},
            "arguments": {"release": 42, "target": "staging"},
        },
        "application_instructions": "Honor all user restrictions.",
        "approval_message": "Deployment requires approval",
        "approval_reason": "Safety check",
        "bypass_immune": True,
        "tool_decision": {"behavior": "ask", "message": "Check destination"},
        "working_directories": [],
        "transcript": [],
        "authorization_evidence": evidence,
    }


def _api_size(messages: list, tools: list) -> int:
    """Measure message text and the full tool payload at the API boundary."""
    return sum(len(msg.get_text_content().encode()) for msg in messages) + len(
        json.dumps(tools, ensure_ascii=False).encode(),
    )


class _BoundaryModel(MockModel):
    """Use the built-in structured-output path up to the API."""

    _call_api_with_structured_output = (
        ChatModelBase._call_api_with_structured_output
    )


class _PreprocessingModel(_BoundaryModel):
    """Apply a model-specific policy before invoking the base strategy."""

    edit = "append"
    policy = "Require release-owner approval."

    async def generate_structured_output(
        self,
        messages: list,
        structured_model: type,
        **kwargs: object,
    ) -> StructuredResponse:
        policy = UserMsg(name="review", content=self.policy)
        if self.edit == "append":
            messages.append(policy)
        elif self.edit == "nested":
            messages[0].content[0].text += self.policy
        elif self.edit == "thinking":
            messages.append(
                AssistantMsg(
                    name="review",
                    content=[ThinkingBlock(thinking=self.policy)],
                ),
            )
        elif self.edit == "replace":
            messages = [*messages, policy]
        return await super().generate_structured_output(
            messages,
            structured_model,
            **kwargs,
        )


class PermissionBudgetTest(IsolatedAsyncioTestCase):
    """Test transport and fail-closed behavior with deterministic models."""

    def _chat(self, model: MockModel | None = None) -> tuple:
        """Capture the complete review request at the model API boundary."""
        model = model or _BoundaryModel(context_size=32768, stream=False)
        calls = []

        async def generate(messages: list, tools: list, **_: object) -> object:
            request = json.loads(messages[1].content[0].text)
            function = tools[0]["function"]
            self.assertEqual(
                function["parameters"],
                PermissionReview.model_json_schema(),
            )
            calls.append(request)
            self.assertLessEqual(
                _api_size(messages, tools),
                reviewer.max_input_bytes,
            )
            content = _review(
                policy_violation="REVOKE" in json.dumps(request),
            ).model_dump()
            return ChatResponse(
                content=[
                    ToolCallBlock(
                        id="result",
                        name=function["name"],
                        input=json.dumps(content),
                    ),
                ],
                is_last=True,
            )

        model._call_api = AsyncMock(side_effect=generate)
        reviewer = ChatPermissionReviewer(model)
        return reviewer, calls

    async def test_small_context_uses_token_units(self) -> None:
        """A short approval request fits a supported 8K model."""
        reviewer, calls = self._chat()
        reviewer.model.context_size = 8192
        count = AsyncMock(wraps=reviewer.model.count_tokens)
        reviewer.model.count_tokens = count
        await reviewer(_request([]))
        self.assertEqual(calls, [_request([])])
        count.assert_awaited_once()
        sent = reviewer.model._call_api.await_args
        self.assertIs(count.await_args.args[0], sent.kwargs["messages"])
        self.assertIs(count.await_args.kwargs["tools"], sent.kwargs["tools"])

    async def test_exact_byte_limit_at_api(self) -> None:
        """A fixed request can be sent exactly at its measured API size."""
        reviewer, calls = self._chat()
        request = {"action": {"description": "更新发布草稿"}}
        await reviewer(request)
        sent = reviewer.model._call_api.await_args.kwargs
        limit = _api_size(sent["messages"], sent["tools"])
        reviewer.max_input_bytes = limit
        await reviewer(request)
        reviewer.max_input_bytes = limit - 1
        with self.assertRaisesRegex(ValueError, "review budget"):
            await reviewer(request)
        self.assertEqual(calls, [request] * 2)

    async def test_fallback_reuses_counted_input(self) -> None:
        """A second strategy sends the same budgeted messages and tools."""
        for edit in ("none", "append", "nested", "replace"):
            with self.subTest(edit=edit):
                model = _PreprocessingModel(context_size=32768, stream=False)
                model.edit = edit
                reviewer, _ = self._chat(model)
                count = AsyncMock(wraps=model.count_tokens)
                model.count_tokens = count
                model._call_api.side_effect = [
                    ChatResponse(content=[], is_last=True),
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id="result",
                                name="generate_structured_output",
                                input=_review().model_dump_json(),
                            ),
                        ],
                        is_last=True,
                    ),
                ]
                self.assertEqual(await reviewer(_request([])), _review())
                self.assertEqual(count.await_count, 1 if edit == "none" else 2)
                sent = count.await_args.args[0]
                self.assertEqual(
                    any(
                        model.policy in msg.get_text_content() for msg in sent
                    ),
                    edit != "none",
                )
                first, second = model._call_api.await_args_list
                for attempt in (first, second):
                    self.assertIs(
                        attempt.kwargs["messages"],
                        count.await_args.args[0],
                    )
                    self.assertIs(
                        attempt.kwargs["tools"],
                        count.await_args.kwargs["tools"],
                    )

    async def test_over_budget_preprocessing_uses_engine_fallback(
        self,
    ) -> None:
        """Neither byte nor token overflow reaches the transport."""
        for limit in ("bytes", "block_bytes", "tokens"):
            for interactive in (True, False):
                with self.subTest(limit=limit, interactive=interactive):
                    model = _PreprocessingModel(
                        context_size=8192,
                        stream=False,
                    )
                    model.edit = (
                        "thinking" if limit == "block_bytes" else "replace"
                    )
                    model.policy = "x" * 16000
                    reviewer, _ = self._chat(model)
                    if limit != "tokens":
                        reviewer.max_input_bytes = 8192
                        model.context_size = 32768
                    decision = await PermissionEngine(
                        PermissionContext(
                            mode="auto",
                            interactive=interactive,
                        ),
                        reviewer=reviewer,
                    ).check_permission(_tool(), {})
                    self.assertEqual(
                        (decision.behavior, decision.review),
                        (
                            PermissionBehavior.ASK
                            if interactive
                            else PermissionBehavior.DENY,
                            None,
                        ),
                    )
                    model._call_api.assert_not_awaited()

    async def test_concurrent_reviews_keep_separate_budgets(self) -> None:
        """Overlapping calls on one model keep independent final checks."""
        barrier = asyncio.Barrier(2)

        class SharedModel(_PreprocessingModel):
            """Synchronize both overrides before entering the base model."""

            async def generate_structured_output(
                self,
                messages: list,
                structured_model: type,
                **kwargs: object,
            ) -> StructuredResponse:
                await barrier.wait()
                return await super().generate_structured_output(
                    messages,
                    structured_model,
                    **kwargs,
                )

        model = SharedModel(context_size=32768, stream=False)
        model.policy = "x" * 10000
        large, calls = self._chat(model)
        small = ChatPermissionReviewer(model, max_input_bytes=8192)

        async def small_request() -> None:
            with self.assertRaisesRegex(ValueError, "review budget"):
                await small({"action": "small"})

        await asyncio.gather(small_request(), large({"action": "large"}))
        self.assertEqual(calls, [{"action": "large"}])

    async def test_full_request_preserves_all_original_evidence(self) -> None:
        """The single assessment receives complete, ordered evidence."""
        evidence = [
            _record(0, "GRANT staging deployment"),
            _record(1, "Unrelated note"),
            _record(2, "REVOKE staging deployment"),
        ]
        request = _request(evidence)
        request["transcript"] = [{"role": "user", "content": "Check only."}]
        request["schedule_authorization"] = {"evidence": [evidence[0]]}
        request["untrusted_context"] = ["Prior staging deployment"]
        original = deepcopy(request)
        reviewer, calls = self._chat()
        self.assertFalse((await reviewer(request)).allowed)
        self.assertEqual(calls, [original])
        self.assertEqual(request, original)

    async def test_archive_overlap_keeps_one_body_and_turn_order(self) -> None:
        """A retained instruction and its transcript position share a body."""
        first = UserMsg("user", "GRANT " + "x" * 15000)
        revoke = UserMsg("user", "REVOKE")
        later = UserMsg("user", "REVOKE")
        archive = retain_authorization_evidence([], [first, revoke])
        reviewer, calls = self._chat()
        decision = await PermissionEngine(
            PermissionContext(mode="auto"),
            reviewer=reviewer,
        ).check_permission(
            _tool(),
            {},
            messages=[first, revoke, later],
            authorization_evidence=archive,
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)
        (request,) = calls
        self.assertEqual(
            request["authorization_evidence"],
            [item.model_dump() for item in archive],
        )
        self.assertEqual(
            [msg["content"] for msg in request["transcript"]],
            [
                [{"type": "authorization_evidence_ref", "index": 0}],
                [{"type": "text", "text": "REVOKE"}],
                [{"type": "text", "text": "REVOKE"}],
            ],
        )

    async def test_short_archived_turns_fit_default_review_budget(
        self,
    ) -> None:
        """Inline short instructions so an unattended review still fits."""
        messages = [UserMsg("user", "OK") for _ in range(94)]
        archive = retain_authorization_evidence([], messages)
        reviewer, calls = self._chat()
        decision = await PermissionEngine(
            PermissionContext(mode="auto", interactive=False),
            reviewer=reviewer,
        ).check_permission(
            _tool(),
            {},
            messages=messages,
            authorization_evidence=archive,
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)
        (request,) = calls
        self.assertEqual(
            request["transcript"],
            PermissionEngine._review_transcript(messages),
        )
        self.assertEqual(
            request["authorization_evidence"],
            [item.model_dump() for item in archive],
        )

    def test_only_matching_trusted_blocks_reference_the_archive(self) -> None:
        """Changed content and untrusted hints retain their live provenance."""
        stop = "Stop before publishing the release."
        original = AssistantMsg(
            "agent",
            [HintBlock(source="steering", source_role="user", hint=stop)],
        )
        archive = retain_authorization_evidence([], [original])
        changed, untrusted, generated = [
            original.model_copy(deep=True) for _ in range(3)
        ]
        changed.content[0].hint = "Continue"
        untrusted.content[0].source_role = None
        generated.metadata["authorization_source"] = "generated"
        transcript = PermissionEngine._review_transcript(
            [original, changed, untrusted, generated],
            archive,
        )
        hint = {"type": "hint", "source": "steering", "source_role": "user"}
        self.assertEqual(
            [msg["content"][0] for msg in transcript],
            [
                {
                    "type": "authorization_evidence_ref",
                    "index": 0,
                    "source": "steering",
                    "source_role": "user",
                },
                {**hint, "hint": "Continue"},
                {**hint, "source_role": None, "hint": stop},
                {**hint, "source_role": None, "hint": stop},
            ],
        )

    async def test_oversized_history_falls_back_without_provider_calls(
        self,
    ) -> None:
        """No partial evidence is sent to chat or classifier models."""
        evidence = [
            AuthorizationEvidence(**_record(0, "GRANT staging deployment")),
            AuthorizationEvidence(**_record(1, "Unrelated note " * 3000)),
            AuthorizationEvidence(**_record(2, "REVOKE staging deployment")),
        ]
        chat, _ = self._chat()
        classify = AsyncMock()
        for reviewer, provider in (
            (chat, chat.model._call_api),
            (ClassifierPermissionReviewer(classify), classify),
        ):
            for interactive in (True, False):
                with self.subTest(
                    reviewer=type(reviewer).__name__,
                    interactive=interactive,
                ):
                    engine = PermissionEngine(
                        PermissionContext(
                            mode="auto",
                            interactive=interactive,
                        ),
                        reviewer=reviewer,
                    )
                    with self.assertLogs("as", level="INFO") as logged:
                        decision = await engine.check_permission(
                            _tool(),
                            {},
                            authorization_evidence=evidence,
                        )
                    self.assertEqual(
                        [record.getMessage() for record in logged.records],
                        [
                            "Automatic review skipped: Complete approval "
                            "input exceeds the review budget",
                        ],
                    )
                    self.assertEqual(
                        (decision.behavior, decision.review),
                        (
                            PermissionBehavior.ASK
                            if interactive
                            else PermissionBehavior.DENY,
                            None,
                        ),
                    )
                    provider.assert_not_awaited()

    async def test_fixed_fields_and_token_budget_are_not_truncated(
        self,
    ) -> None:
        """Huge actions, instructions, or token counts prevent dispatch."""
        for field in (
            "action",
            "application_instructions",
            "tokens",
            "output",
        ):
            with self.subTest(field=field):
                request = _request([])
                reviewer, calls = self._chat()
                if field == "tokens":
                    reviewer.model.count_tokens = AsyncMock(return_value=32768)
                elif field == "output":
                    reviewer.model.parameters = SimpleNamespace(
                        max_tokens=32768,
                    )
                else:
                    request[field] = "exact operation " * 10000
                with self.assertRaises(ValueError):
                    await reviewer(request)
                self.assertEqual(calls, [])

    async def test_provider_deadline_and_cancellation(self) -> None:
        """Provider deadlines fall back and cancellation propagates."""
        reviewer, _ = self._chat()
        reviewer.call_timeout = 0.01

        async def blocked(**_kwargs: object) -> object:
            await asyncio.Event().wait()
            return None

        reviewer.model.generate_structured_output = AsyncMock(
            side_effect=blocked,
        )
        with self.assertRaises(TimeoutError):
            await reviewer(_request([]))
        reviewer.model.generate_structured_output.side_effect = (
            asyncio.CancelledError
        )
        with self.assertRaises(asyncio.CancelledError):
            await reviewer(_request([]))
        reviewer.call_timeout = 1.0
        reviewer.model.generate_structured_output.side_effect = blocked
        decision = await PermissionEngine(
            PermissionContext(mode=PermissionMode.AUTO),
            reviewer=reviewer,
            review_timeout=0.01,
        ).check_permission(
            _tool(),
            {},
        )
        self.assertEqual(
            (decision.behavior, decision.decision_reason),
            (
                PermissionBehavior.ASK,
                "Automatic review failed: TimeoutError",
            ),
        )

    async def test_classifier_sends_one_complete_budgeted_request(
        self,
    ) -> None:
        """Questions and the full evidence count toward the byte limit."""
        answers = {
            "risk_level": "high",
            "user_authorization": "high",
            "policy_violation": "yes",
        }
        model = AsyncMock(
            return_value=ClassifierResponse(
                model="jev",
                content={
                    name: ChoiceAnswer(
                        choice=value,
                        confidence=0.95,
                        probabilities={value: 0.95},
                    )
                    for name, value in answers.items()
                },
            ),
        )
        reviewer = ClassifierPermissionReviewer(model)
        request = _request([_record(0, "GRANT 部署"), _record(1, "REVOKE")])
        self.assertFalse((await reviewer(request)).allowed)
        sent = model.await_args.kwargs
        self.assertEqual(sent["state"], request)
        size = len(
            json.dumps(
                {
                    "state": request,
                    "questions": {
                        key: question.model_dump()
                        for key, question in sent["questions"].items()
                    },
                },
                ensure_ascii=False,
            ).encode(),
        )
        reviewer.max_input_bytes = size
        await reviewer(request)
        reviewer.max_input_bytes = size - 1
        with self.assertRaisesRegex(ValueError, "review budget"):
            await reviewer(request)
        self.assertEqual(model.await_count, 2)
