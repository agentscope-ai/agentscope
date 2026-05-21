# -*- coding: utf-8 -*-
"""Middleware that offloads long-running tool calls to background tasks and
injects their results back into the agent context before the next reasoning
step.
"""
import asyncio
from typing import Any, AsyncGenerator, Callable

from ...middleware import MiddlewareBase
from ...tool import ToolChunk, ToolResponse
from ...message import AssistantMsg, HintBlock, TextBlock, ToolResultState
from ...agent import Agent
from .._manager import BackgroundTaskManager
from ..._logging import logger


# Sentinel object used to signal end-of-stream in the drain queue.
_QUEUE_SENTINEL = object()


class ToolOffloadMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Middleware that offloads timed-out tool calls to background tasks.

    When a tool execution exceeds ``timeout_secs`` this middleware:

    1. Lets the underlying :mod:`asyncio` task keep running in the
       background via :class:`~agentscope.app.BackgroundTaskManager`.
       The task is **never cancelled**.
    2. Returns a synthetic :class:`~agentscope.tool.ToolResponse` to the
       agent immediately so the ReAct loop can continue without blocking.
    3. When the background task finishes the real tool result is pushed
       into :class:`~agentscope.app.BackgroundTaskManager` result storage.
    4. Before the next :meth:`on_reasoning` call the pending results are
       pulled from the manager and injected into ``agent.state.context``
       so the model is informed of the completed background result.

    .. note::
        Tools with ``is_state_injected=True`` receive the live
        ``agent.state`` object.  Offloading such tools to a background
        task may cause concurrent state mutations and is therefore
        **blocked** — they are always executed synchronously.

    .. code-block:: python

        from agentscope.app import BackgroundTaskManager, ToolOffloadMiddleware

        bg_manager = BackgroundTaskManager()
        middleware = ToolOffloadMiddleware(
            bg_manager=bg_manager,
            timeout_secs=15.0
        )

        agent = Agent(
            ...,
            middlewares=[middleware],
        )

    Args:
        bg_manager (`BackgroundTaskManager`):
            The application-level background task manager shared across
            all agents and services.  Used only for task registration and
            cancellation via :class:`~agentscope.app.TaskStop`.
        timeout_secs (`float`, optional):
            Maximum seconds to wait for a tool execution before offloading
            it to the background.  Defaults to ``15.0``.
    """

    def __init__(
        self,
        bg_manager: "BackgroundTaskManager",
        timeout_secs: float = 10.0,
        user_id: str = "",
        agent_id: str = "",
        session_manager: Any = None,
        retrigger_fn: Callable[..., Any] | None = None,
    ) -> None:
        """Initialise the middleware.

        Args:
            bg_manager (`BackgroundTaskManager`):
                The application-level background task manager.
            timeout_secs (`float`, optional):
                Timeout in seconds before a tool call is offloaded.
                Defaults to ``10.0``.
            user_id (`str`, optional):
                The user id for the current request.
            agent_id (`str`, optional):
                The agent record ID (not the display name).
            session_manager:
                The session manager, used to check if a session is active.
            retrigger_fn:
                Async callable ``(session_id, agent_id, user_id) -> None``
                that triggers a new agent reply when the session is idle.
        """
        self._bg_manager = bg_manager
        self._timeout_secs = timeout_secs
        self._user_id = user_id
        self._agent_id = agent_id
        self._session_manager = session_manager
        self._retrigger_fn = retrigger_fn

    # ------------------------------------------------------------------
    # Middleware hooks
    # ------------------------------------------------------------------

    async def on_reasoning(  # type:ignore[override]
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Inject pending background results before reasoning.

        Pops :class:`~agentscope.message.HintBlock` objects pushed by
        completed background tasks and appends them to the last assistant
        message in ``agent.state.context`` (or creates a new one with
        ``state.reply_id`` if the last message is not from the agent).

        Args:
            agent (`Agent`):
                The executing agent.
            input_kwargs (`dict`):
                Reasoning input kwargs (contains ``tool_choice``).
            next_handler (`Callable[..., AsyncGenerator]`):
                The downstream middleware or core reasoning logic.

        Yields:
            Events produced by the reasoning process.
        """
        hint_blocks = self._bg_manager.pop_results(agent.state.session_id)
        if hint_blocks:
            logger.info(
                "Injecting %d pending background result(s) into context: "
                "session_id=%s, agent_id=%s",
                len(hint_blocks),
                agent.state.session_id,
                agent.name,
            )
            if len(agent.state.context) > 0:
                last_msg = agent.state.context[-1]
                if (
                    last_msg.role == "assistant"
                    and last_msg.name == agent.name
                ):
                    last_msg.content.extend(hint_blocks)
                else:
                    agent.state.context.append(
                        AssistantMsg(
                            id=agent.state.reply_id,
                            name=agent.name,
                            content=list(hint_blocks),
                        ),
                    )
            else:
                agent.state.context.append(
                    AssistantMsg(
                        id=agent.state.reply_id,
                        name=agent.name,
                        content=list(hint_blocks),
                    ),
                )

        async for evt in next_handler(**input_kwargs):
            yield evt

    async def on_acting(  # type:ignore[override]
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Execute a tool with timeout; offload to background on expiry.

        The inner ``next_handler`` generator is wrapped in an
        :mod:`asyncio` task whose output is fed through a
        :class:`asyncio.Queue`.  Items are consumed with a rolling
        deadline.  If the deadline fires before the tool finishes:

        - The running task is **not** cancelled.
        - It is registered with :attr:`_bg_manager` via
          :meth:`~BackgroundTaskManager.register_task`.
        - An ``on_complete`` callback (owned by this middleware) drains the
          queue, builds a ``<system-notification>`` message, and stores it
          in :attr:`_pending_messages`.
        - A synthetic :class:`~agentscope.tool.ToolResponse` notifying
          the agent of the background task id is yielded instead.

        .. note::
            Tools with ``is_state_injected=True`` or ``is_external_tool=True``
            bypass this logic and are always executed synchronously.

        Args:
            agent (`Agent`):
                The executing agent.
            input_kwargs (`dict`):
                Acting input kwargs (contains ``tool_call``).
            next_handler (`Callable[..., AsyncGenerator]`):
                The downstream middleware or ``_acting_impl``.

        Yields:
            `ToolChunk | ToolResponse`:
                Normal results when the tool finishes in time, or a
                synthetic ``ToolResponse`` when offloaded.
        """
        tool_call = input_kwargs["tool_call"]

        # ----------------------------------------------------------------
        # Guard: state-injected tools and external tools are never offloaded.
        # - is_state_injected: the tool receives the live agent.state object;
        #   running it concurrently in a background task could cause race
        #   conditions on agent.state. For now, we fall back to synchronous
        #   execution. Once background tasks can be given an isolated state
        #   snapshot this guard should become a hard RuntimeError instead.
        # - is_external_tool: external tools wait for a human/external system
        #   to push a result back; offloading them makes no sense because the
        #   agent would lose track of the pending confirmation.
        registered = agent.toolkit.tools.get(tool_call.name)
        if registered is not None and (
            registered.tool.is_state_injected
            or registered.tool.is_external_tool
        ):
            logger.info(
                "Tool '%s' is state-injected or external, executing "
                "synchronously: session_id=%s, agent_id=%s",
                tool_call.name,
                agent.state.session_id,
                agent.name,
            )
            async for item in next_handler(**input_kwargs):
                yield item
            return

        # ----------------------------------------------------------------
        # Wrap next_handler in a Task, draining output into a Queue
        # ----------------------------------------------------------------
        queue: asyncio.Queue = asyncio.Queue()

        async def _drain_to_queue() -> None:
            """Drain next_handler output into *queue*."""
            try:
                async for item in next_handler(**input_kwargs):
                    await queue.put(item)
            except Exception as exc:  # pylint: disable=broad-except
                await queue.put(exc)
            finally:
                await queue.put(_QUEUE_SENTINEL)

        drain_task: asyncio.Task = asyncio.create_task(_drain_to_queue())

        # ----------------------------------------------------------------
        # Consume items until the deadline or normal completion
        # ----------------------------------------------------------------
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._timeout_secs
        pre_collected: list = []
        completed = False

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break

            try:
                item = await asyncio.wait_for(
                    queue.get(),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                break

            if item is _QUEUE_SENTINEL:
                completed = True
                break

            if isinstance(item, BaseException):
                drain_task.cancel()
                raise item

            pre_collected.append(item)

            # ToolResponse is always the terminal item from _acting_impl
            if isinstance(item, ToolResponse):
                completed = True
                break

        if completed:
            for item in pre_collected:
                yield item
            drain_task.cancel()
            return

        # ----------------------------------------------------------------
        # Timeout path: build on_complete callback, register, yield synthetic
        # ----------------------------------------------------------------
        session_id = agent.state.session_id
        tool_name = tool_call.name
        snapshot = list(pre_collected)

        logger.info(
            "Tool '%s' timed out after %.1fs, offloading to background: "
            "session_id=%s, agent_id=%s",
            tool_name,
            self._timeout_secs,
            session_id,
            agent.name,
        )

        async def _on_complete() -> None:
            """Drain remaining queue items and push notification message."""
            remaining_items: list = []
            while not queue.empty():
                try:
                    item = queue.get_nowait()
                    if isinstance(item, ToolResponse):
                        remaining_items.append(item)
                        break
                except asyncio.QueueEmpty:
                    break

            all_items = snapshot + remaining_items
            response: ToolResponse | None = next(
                (i for i in all_items if isinstance(i, ToolResponse)),
                None,
            )

            if response is not None:
                texts = [
                    b.text
                    for b in response.content
                    if isinstance(b, TextBlock)
                ]
                result_text = "\n".join(texts)
                hint_text = (
                    f"<system-notification>\n"
                    f"Background task for tool '{tool_name}' has completed.\n"
                    f"Result:\n{result_text}\n"
                    f"</system-notification>"
                )
            else:
                hint_text = (
                    f"<system-notification>\n"
                    f"Background task for tool '{tool_name}' has completed "
                    f"with no result.\n"
                    f"</system-notification>"
                )

            logger.info(
                "Background tool '%s' completed, pushing result to pending "
                "hints: session_id=%s",
                tool_name,
                session_id,
            )
            self._bg_manager.push_result(
                session_id,
                HintBlock(hint=hint_text),
            )

            # Re-trigger the agent if the session is idle
            if (
                self._session_manager is not None
                and self._retrigger_fn is not None
                and not self._session_manager.is_running(session_id)
            ):
                logger.info(
                    "Session idle after background tool '%s' completed, "
                    "triggering re-invocation: session_id=%s, agent_id=%s",
                    tool_name,
                    session_id,
                    self._agent_id,
                )
                asyncio.create_task(
                    self._retrigger_fn(
                        session_id,
                        self._agent_id,
                        self._user_id,
                    ),
                )

        task_id = await self._bg_manager.register_task(
            asyncio_task=drain_task,
            session_id=session_id,
            agent_id=self._agent_id,
            user_id=self._user_id,
            on_complete=_on_complete,
        )

        logger.info(
            "Synthetic ToolResponse yielded for offloaded tool '%s': "
            "task_id=%s, session_id=%s, agent_id=%s",
            tool_name,
            task_id,
            session_id,
            agent.name,
        )

        placeholder_text = (
            f"<system-reminder>Tool '{tool_name}' is taking "
            f"longer than expected and has been moved to the "
            f"background (task_id={task_id}). "
            f"You may continue with other tasks. "
            f"The result will be injected into the context "
            f"automatically when finished.</system-reminder>"
        )

        yield ToolChunk(
            content=[TextBlock(text=placeholder_text)],
            state=ToolResultState.SUCCESS,
        )

        yield ToolResponse(
            content=[TextBlock(text=placeholder_text)],
            state=ToolResultState.SUCCESS,
            id=tool_call.id,
        )
