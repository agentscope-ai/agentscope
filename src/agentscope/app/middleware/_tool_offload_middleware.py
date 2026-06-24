# -*- coding: utf-8 -*-
"""Middleware that offloads long-running tool calls to background tasks.

When a tool times out, this middleware:

- Lets the underlying asyncio task keep running via
  :class:`BackgroundTaskManager` (the task is **never cancelled**).
- Yields a synthetic placeholder :class:`ToolResponse` so the agent
  loop unblocks immediately.
- On task completion, pushes the real result as a
  :class:`HintBlock` to the session's inbox via the message bus and
  enqueues a wakeup. From there the result follows the same path as a
  team message: :class:`InboxMiddleware` drains it into context on
  the next reasoning step (same run or a fresh wakeup-driven run),
  and :class:`WakeupDispatcher` ensures an idle session is started by
  some process.

The middleware no longer keeps its own pending-result store or
retrigger callback — both responsibilities now live in the
bus/wakeup infrastructure, which works correctly across processes.
"""
import asyncio
import json
from copy import deepcopy
from typing import AsyncGenerator, Callable

from .._manager import BackgroundTaskManager
from ...middleware import MiddlewareBase
from ...tool import ToolChunk, ToolResponse
from ...message import (
    DataBlock,
    HintBlock,
    TextBlock,
    ToolResultState,
)
from ...agent import Agent
from ..message_bus import MessageBus, MessageBusKeys
from .._bus_ops import enqueue_run_trigger
from ..._logging import logger


# Sentinel object used to signal end-of-stream in the drain queue.
_QUEUE_SENTINEL = object()


class ToolOffloadMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Middleware that offloads timed-out tool calls to background tasks.

    .. note::
        Tools with ``is_state_injected=True`` receive the live
        ``agent.state`` object. Offloading them could race on shared
        state, so they are always executed synchronously instead.

    Args:
        bg_manager (`BackgroundTaskManager`):
            Application-level background task manager. Used to register
            the running asyncio task so :class:`ToolStop` can target it.
        message_bus (`MessageBus`):
            Application message bus. The completion callback uses it to
            push the result HintBlock to the session's inbox and to
            enqueue a wakeup so an idle session is woken on any process.
        user_id (`str`):
            User id of the current request — included in the wakeup
            payload so the dispatcher can re-invoke ``ChatService.run``.
        agent_id (`str`):
            Agent record id (not the display name) — same purpose as
            ``user_id``.
        timeout_secs (`float`, defaults to ``10.0``):
            Maximum seconds to wait for a tool execution before
            offloading it to the background.
    """

    def __init__(
        self,
        bg_manager: BackgroundTaskManager,
        message_bus: MessageBus,
        user_id: str,
        agent_id: str,
        timeout_secs: float = 10.0,
    ) -> None:
        """Bind dependencies.

        Args:
            bg_manager (`BackgroundTaskManager`):
                Application background task manager.
            message_bus (`MessageBus`):
                Application message bus.
            user_id (`str`):
                User id of the current request.
            agent_id (`str`):
                Agent record id (not the display name).
            timeout_secs (`float`, defaults to ``10.0``):
                Tool execution timeout before offloading to background.
        """
        self._bg_manager = bg_manager
        self._message_bus = message_bus
        self._user_id = user_id
        self._agent_id = agent_id
        self._timeout_secs = timeout_secs

    # ------------------------------------------------------------------
    # Middleware hooks
    # ------------------------------------------------------------------

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
        - It is registered with :attr:`_bg_manager` so that
          :class:`ToolStop` can target it and shutdown can cancel it.
        - A separate watcher coroutine is spawned to await the task's
          completion and then push the result as a
          :class:`HintBlock` to the session inbox + enqueue a wakeup.
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
        tool = await agent.toolkit.get_tool(tool_call.name)
        if tool is not None and (
            tool.is_state_injected or tool.is_external_tool
        ):
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
        # Timeout path: spawn deliverer, register drain_task, yield synthetic
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

        async def _deliver_when_done() -> None:
            """Wait for the offloaded tool to finish, then push its
            result to the session inbox + enqueue a wakeup.

            On cancellation (e.g. ``ToolStop``) or unhandled exception
            no result is delivered — the agent is left without a
            completion notification in those edge cases.
            """
            try:
                await drain_task
            except asyncio.CancelledError:
                logger.info(
                    "Background tool '%s' cancelled, skipping delivery: "
                    "session_id=%s",
                    tool_name,
                    session_id,
                )
                return
            except Exception:  # pylint: disable=broad-except
                logger.warning(
                    "Background tool '%s' failed, skipping delivery: "
                    "session_id=%s",
                    tool_name,
                    session_id,
                    exc_info=True,
                )
                return

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

            tool_call_id = tool_call.id

            hint_source = json.dumps(
                {
                    "label": "tool_output",
                    "sublabel": f"{tool_name} · {tool_call_id}",
                },
                ensure_ascii=False,
            )

            if response is None or len(response.content) == 0:
                hint = HintBlock(
                    hint=(
                        f"<system-notification>"
                        f"Tool '{tool_name}' running in background "
                        f"(id={tool_call_id}) has completed with no output."
                        f"</system-notification>"
                    ),
                    source=hint_source,
                )

            else:
                # Preserve all content blocks (text + multimodal) from
                # the tool response so nothing is lost.
                content_blocks: list[TextBlock | DataBlock] = deepcopy(
                    response.content,
                )

                prefix = (
                    f"<system-notification>"
                    f"Tool '{tool_name}' running in background "
                    f"(id={tool_call_id}) has completed.\n\n"
                    f"Result:\n\n"
                )

                if isinstance(content_blocks[0], TextBlock):
                    content_blocks[0].text = prefix + content_blocks[0].text

                else:
                    content_blocks.insert(0, TextBlock(text=prefix))

                suffix = "</system-notification>"

                if isinstance(content_blocks[-1], TextBlock):
                    content_blocks[-1].text += suffix
                else:
                    content_blocks.append(
                        TextBlock(text=suffix),
                    )

                hint = HintBlock(
                    hint=content_blocks,
                    source=hint_source,
                )

            # Deliver via inbox + wakeup — same path as a team message.
            # InboxMiddleware drains the inbox into context on the next
            # reasoning step, and WakeupDispatcher kicks an idle session
            # on whichever process picks up the wakeup.
            logger.info(
                "Background tool '%s' completed, pushing result to inbox "
                "and enqueueing wakeup: session_id=%s",
                tool_name,
                session_id,
            )
            await self._message_bus.queue_push(
                MessageBusKeys.inbox(session_id),
                hint.model_dump(mode="json"),
            )
            await enqueue_run_trigger(
                self._message_bus,
                user_id=self._user_id,
                session_id=session_id,
                agent_id=self._agent_id,
            )

        asyncio.create_task(_deliver_when_done())
        task_id = await self._bg_manager.register_task(
            asyncio_task=drain_task,
            session_id=session_id,
            agent_id=self._agent_id,
            user_id=self._user_id,
            tool_name=tool_name,
        )

        logger.info(
            "Synthetic ToolResponse yielded for offloaded tool '%s': "
            "task_id=%s, session_id=%s, agent_id=%s",
            tool_name,
            task_id,
            session_id,
            agent.name,
        )

        placeholder_text = f"""<system-reminder>Tool '{tool_name}' is \
running in background (id={task_id}) for over {self._timeout_secs}s. \
You will be notified automatically when it finishes, so **DO NOT** poll, \
query, or wait for the result yourself. **DO NOT** call any waiting tool \
such as `bash sleep`. You have exactly two valid options:
1. Continue with other independent tasks and ignore this tool for now; or
2. If there is nothing else to do, simply give a text reply without calling \
any tool, which ends the current reasoning loop — just do nothing and end \
this run.
</system-reminder>"""

        yield ToolChunk(
            content=[TextBlock(text=placeholder_text)],
            state=ToolResultState.SUCCESS,
        )

        yield ToolResponse(
            content=[TextBlock(text=placeholder_text)],
            state=ToolResultState.SUCCESS,
            id=tool_call.id,
        )
