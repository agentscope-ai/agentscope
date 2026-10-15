# -*- coding: utf-8 -*-
"""Content-block fallback extension points."""
import inspect
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass, field
from typing import AsyncGenerator, Awaitable, Callable, TYPE_CHECKING

from ._base import MiddlewareBase
from .._logging import logger
from ..exception import ContentBlockedError
from ..message import Msg, TextBlock, ToolResultBlock
from ..model import ChatModelBase, ChatResponse, FinishedReason
from ..tool import ToolChoice

if TYPE_CHECKING:
    from ..agent import Agent

_MASKED_CONTENT = "[Content omitted after a provider content block.]"


def _merge_quarantine(
    target: dict[str, list[str]],
    additions: dict[str, list[str]],
) -> None:
    """Merge message-scoped block IDs without retaining duplicate IDs."""
    for message_id, block_ids in additions.items():
        target[message_id] = list(
            dict.fromkeys([*target.get(message_id, []), *block_ids]),
        )


@dataclass(frozen=True)
class ContentBlockFallbackContext:
    """The context passed to a content-block fallback strategy."""

    agent: "Agent"
    """The agent whose model call was blocked."""

    current_model: ChatModelBase
    """The model used for the blocked call."""

    messages: list[Msg]
    """A defensive copy of the messages used for the blocked call.

    A strategy can mutate these messages in place and return them without
    changing the agent's stored conversation context.
    """

    tools: list[dict]
    """The tool schemas used for the blocked call."""

    tool_choice: ToolChoice | None
    """The tool choice used for the blocked call."""

    error: ContentBlockedError
    """The normalized provider error."""

    attempt: int
    """The one-based fallback attempt number."""


@dataclass(frozen=True)
class ContentBlockFallbackDecision:
    """A strategy decision to retry with replacement messages."""

    messages: list[Msg]
    """The protocol-valid replacement messages for the next model call."""

    quarantined_blocks: dict[str, list[str]] = field(default_factory=dict)
    """Proposed assistant text/tool-result block IDs, keyed by message ID.

    After successful recovery, the middleware records these IDs in the
    agent state and replaces their content with the built-in placeholder on
    future calls. Custom strategies leave this empty for transient edits.
    These IDs describe effective isolation, not confirmed policy violations.
    """


class ContentBlockFallbackStrategy(ABC):
    """Create a replacement request after a content block."""

    @abstractmethod
    async def recover(
        self,
        context: ContentBlockFallbackContext,
    ) -> ContentBlockFallbackDecision | None:
        """Return replacement messages, or ``None`` to stop recovery."""


class ReverseMaskStrategy(ContentBlockFallbackStrategy):
    """Mask one assistant message per attempt, from newest to oldest.

    Replace non-empty text blocks and tool-result outputs with a placeholder.
    Tool outputs are replaced in full, including any nested media. Preserve
    tool calls, IDs, metadata, thinking blocks, and other content blocks.
    User and system messages are never modified.

    Mutate only the defensive message copy supplied by the middleware. Skip
    already masked content so successive attempts retain earlier masks and
    move backwards. No per-call state is stored on the strategy instance.
    Return ``None`` when no candidate remains. Propose quarantine IDs for
    successful recovery to persist without modifying conversation history.
    """

    async def recover(
        self,
        context: ContentBlockFallbackContext,
    ) -> ContentBlockFallbackDecision | None:
        """Mask the newest assistant message with eligible content."""
        for message in reversed(context.messages):
            if message.role != "assistant":
                continue
            block_ids = []
            for block in message.content:
                if isinstance(block, TextBlock):
                    if block.text and block.text != _MASKED_CONTENT:
                        block.text = _MASKED_CONTENT
                        block_ids.append(block.id)
                elif isinstance(block, ToolResultBlock):
                    if block.output and block.output != _MASKED_CONTENT:
                        block.output = _MASKED_CONTENT
                        block_ids.append(block.id)
            if block_ids:
                return ContentBlockFallbackDecision(
                    messages=context.messages,
                    quarantined_blocks={message.id: block_ids},
                )
        return None


class ContentBlockFallbackMiddleware(MiddlewareBase):
    """Retry blocked model calls with messages supplied by a strategy.

    The middleware retries only the intercepted model call. It never re-runs
    the surrounding reasoning or acting loop, so already completed tools are
    not executed again.
    """

    def __init__(
        self,
        strategy: ContentBlockFallbackStrategy,
        max_fallback_attempts: int = 3,
    ) -> None:
        """Initialize the content-block fallback middleware.

        Args:
            strategy (`ContentBlockFallbackStrategy`):
                The downstream strategy that supplies replacement messages.
            max_fallback_attempts (`int`, defaults to `3`):
                The maximum number of replacement calls per middleware chain.
                A separately configured fallback model starts a new chain.
        """
        if max_fallback_attempts < 0:
            raise ValueError("max_fallback_attempts must be non-negative.")
        self.strategy = strategy
        self.max_fallback_attempts = max_fallback_attempts

    async def clear_quarantine(self, agent: "Agent") -> None:
        """Clear this middleware's isolation records for an idle agent.

        Args:
            agent (`Agent`):
                The agent whose subsequent calls should use original content.
                Save its state through the normal session persistence path
                to retain this reset across reloads.
        """
        agent.state.middle_context.pop(await self.get_middleware_key(), None)

    async def _apply_quarantine(
        self,
        agent: "Agent",
        messages: list[Msg],
    ) -> list[Msg]:
        """Apply committed masks to a copy of the current model input."""
        state = agent.state.middle_context.get(
            await self.get_middleware_key(),
            {},
        )
        quarantined = state.get("quarantined_blocks", {})
        if not quarantined:
            return messages
        messages = deepcopy(messages)
        for message in messages:
            if message.role != "assistant":
                continue
            block_ids = quarantined.get(message.id, [])
            for block in message.content:
                if block.id not in block_ids:
                    continue
                if isinstance(block, TextBlock):
                    block.text = _MASKED_CONTENT
                elif isinstance(block, ToolResultBlock):
                    block.output = _MASKED_CONTENT
        return messages

    async def _recover_messages(
        self,
        *,
        agent: "Agent",
        current_model: ChatModelBase,
        messages: list[Msg],
        tools: list[dict],
        tool_choice: ToolChoice | None,
        error: ContentBlockedError,
        completed_attempts: int,
        pending_quarantine: dict[str, list[str]],
    ) -> tuple[list[Msg], int]:
        """Ask the strategy for the next replacement message list."""
        attempt = completed_attempts + 1
        details = (
            f"agent={agent.name}, model={current_model.model}, "
            f"provider={error.provider}, code={error.original_code}, "
            f"request_id={error.request_id}, phase={error.phase}"
        )
        message = f"Content-block fallback: blocked; {details}."
        logger.warning(message)
        if attempt > self.max_fallback_attempts:
            message = (
                f"Content-block fallback: budget exhausted "
                f"({completed_attempts}/{self.max_fallback_attempts}); "
                f"{details}."
            )
            logger.warning(message)
            raise error

        decision = await self.strategy.recover(
            ContentBlockFallbackContext(
                agent=agent,
                current_model=current_model,
                messages=deepcopy(messages),
                tools=tools,
                tool_choice=tool_choice,
                error=error,
                attempt=attempt,
            ),
        )
        if decision is None:
            message = (
                f"Content-block fallback: strategy stopped at attempt "
                f"{attempt}/{self.max_fallback_attempts}; {details}."
            )
            logger.warning(message)
            raise error
        message = (
            f"Content-block fallback: retrying with replacement messages "
            f"({attempt}/{self.max_fallback_attempts}); {details}."
        )
        logger.info(message)
        _merge_quarantine(pending_quarantine, decision.quarantined_blocks)
        return decision.messages, attempt

    async def _complete_recovery(
        self,
        agent: "Agent",
        current_model: ChatModelBase,
        completed_attempts: int,
        pending_quarantine: dict[str, list[str]],
    ) -> None:
        """Commit successful isolation and report recovery without payloads."""
        if pending_quarantine:
            state = agent.state.middle_context.setdefault(
                await self.get_middleware_key(),
                {},
            )
            _merge_quarantine(
                state.setdefault("quarantined_blocks", {}),
                pending_quarantine,
            )
        if completed_attempts:
            message = (
                f"Content-block fallback: recovered after "
                f"{completed_attempts} replacement call(s); "
                f"agent={agent.name}, model={current_model.model}."
            )
            logger.info(message)

    async def _wrap_stream(
        self,
        *,
        stream: AsyncGenerator[ChatResponse, None],
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[
            ...,
            Awaitable[ChatResponse | AsyncGenerator[ChatResponse, None]],
        ],
        messages: list[Msg],
        completed_attempts: int,
        pending_quarantine: dict[str, list[str]],
    ) -> AsyncGenerator[ChatResponse, None]:
        """Recover a stream only when no chunk has been emitted."""
        current_stream = stream
        current_messages = messages
        attempts = completed_attempts

        while True:
            emitted = False
            completed = False
            interrupted = False
            try:
                async for chunk in current_stream:
                    emitted = True
                    interrupted = interrupted or (
                        chunk.finished_reason == FinishedReason.INTERRUPTED
                    )
                    yield chunk
                completed = True
            except ContentBlockedError as error:
                if emitted or error.partial_output:
                    error.partial_output = True
                    message = (
                        f"Content-block fallback: stopped after partial "
                        f"output; agent={agent.name}, "
                        f"model={input_kwargs['current_model'].model}, "
                        f"provider={error.provider}, "
                        f"code={error.original_code}, "
                        f"request_id={error.request_id}."
                    )
                    logger.warning(message)
                    raise
                current_messages, attempts = await self._recover_messages(
                    agent=agent,
                    current_model=input_kwargs["current_model"],
                    messages=current_messages,
                    tools=input_kwargs["tools"],
                    tool_choice=input_kwargs["tool_choice"],
                    error=error,
                    completed_attempts=attempts,
                    pending_quarantine=pending_quarantine,
                )
            finally:
                await current_stream.aclose()

            if completed:
                if not interrupted:
                    await self._complete_recovery(
                        agent,
                        input_kwargs["current_model"],
                        attempts,
                        pending_quarantine,
                    )
                return

            while True:
                try:
                    result = await next_handler(messages=current_messages)
                    break
                except ContentBlockedError as error:
                    current_messages, attempts = await self._recover_messages(
                        agent=agent,
                        current_model=input_kwargs["current_model"],
                        messages=current_messages,
                        tools=input_kwargs["tools"],
                        tool_choice=input_kwargs["tool_choice"],
                        error=error,
                        completed_attempts=attempts,
                        pending_quarantine=pending_quarantine,
                    )

            if isinstance(result, ChatResponse):
                if result.finished_reason == FinishedReason.COMPLETED:
                    await self._complete_recovery(
                        agent,
                        input_kwargs["current_model"],
                        attempts,
                        pending_quarantine,
                    )
                yield result
                return
            current_stream = result

    async def on_model_call(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[
            ...,
            Awaitable[ChatResponse | AsyncGenerator[ChatResponse, None]],
        ],
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Recover blocked calls using protocol-valid replacement messages."""
        messages = await self._apply_quarantine(
            agent,
            input_kwargs["messages"],
        )
        completed_attempts = 0
        pending_quarantine: dict[str, list[str]] = {}

        while True:
            try:
                result = await next_handler(messages=messages)
                break
            except ContentBlockedError as error:
                messages, completed_attempts = await self._recover_messages(
                    agent=agent,
                    current_model=input_kwargs["current_model"],
                    messages=messages,
                    tools=input_kwargs["tools"],
                    tool_choice=input_kwargs["tool_choice"],
                    error=error,
                    completed_attempts=completed_attempts,
                    pending_quarantine=pending_quarantine,
                )

        if not inspect.isasyncgen(result):
            if result.finished_reason == FinishedReason.COMPLETED:
                await self._complete_recovery(
                    agent,
                    input_kwargs["current_model"],
                    completed_attempts,
                    pending_quarantine,
                )
            return result

        return self._wrap_stream(
            stream=result,
            agent=agent,
            input_kwargs=input_kwargs,
            next_handler=next_handler,
            messages=messages,
            completed_attempts=completed_attempts,
            pending_quarantine=pending_quarantine,
        )
