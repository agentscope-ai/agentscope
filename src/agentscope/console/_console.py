# -*- coding: utf-8 -*-
"""The interactive console entry for trying an agent in the terminal."""
import asyncio
import json
import signal

import jsonschema

from ._renderer import ConsoleRenderer, Verbosity
from ..agent import Agent
from ..pipeline import PipelineProtocol
from ..event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ..message import (
    Msg,
    TextBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from ..tool import RequestUserInput


async def _run_reply(
    agent: Agent | PipelineProtocol,
    renderer: ConsoleRenderer,
    inputs: (
        Msg
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
    ),
) -> RequireUserConfirmEvent | RequireExternalExecutionEvent | None:
    """Consume one ``reply_stream`` call, rendering every event.

    Ctrl+C during streaming cancels the reply task; the agent handles the
    cancellation itself (closing tool calls, emitting interrupted events)
    as long as ``react_config.interruption_raise_cancelled_error`` keeps
    its default ``False``.

    Returns:
        `RequireUserConfirmEvent | RequireExternalExecutionEvent | None`:
            The pending outside-interaction request if the reply parked,
            otherwise `None`.
    """
    pending: RequireUserConfirmEvent | RequireExternalExecutionEvent | None = (
        None
    )

    async def consume() -> None:
        nonlocal pending
        async for event in agent.reply_stream(inputs):
            renderer.render(event)
            if isinstance(
                event,
                (RequireUserConfirmEvent, RequireExternalExecutionEvent),
            ):
                pending = event

    task = asyncio.ensure_future(consume())
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, task.cancel)
        sigint_hooked = True
    except (NotImplementedError, RuntimeError):
        # e.g. Windows event loop — fall back to KeyboardInterrupt
        sigint_hooked = False

    try:
        await task
    except asyncio.CancelledError:
        # Raised when the agent re-raises after the interruption
        pass
    finally:
        if sigint_hooked:
            loop.remove_signal_handler(signal.SIGINT)

    return pending


async def _confirm(
    pending: RequireUserConfirmEvent,
) -> UserConfirmResultEvent:
    """Ask the user to confirm each pending tool call via stdin.

    Answering ``a`` (always) also accepts the suggested permission
    rules, so matching calls won't ask again within this process.
    """
    results = []
    for tool_call in pending.tool_calls:
        prompt = f"Allow '{tool_call.name}'? [y]es / [N]o"
        if tool_call.suggested_rules:
            prompt += " / [a]lways"
        answer = (await asyncio.to_thread(input, f"{prompt} ")).strip().lower()
        always = bool(tool_call.suggested_rules) and answer in (
            "a",
            "always",
        )
        results.append(
            ConfirmResult(
                confirmed=always or answer in ("y", "yes"),
                tool_call=tool_call,
                rules=tool_call.suggested_rules if always else None,
            ),
        )
    return UserConfirmResultEvent(
        reply_id=pending.reply_id,
        confirm_results=results,
    )


async def _read_choice(maximum: int) -> int:
    """Read a one-based option number and return its zero-based index."""
    while True:
        answer = (
            await asyncio.to_thread(
                input,
                f"Select an option [1-{maximum}]: ",
            )
        ).strip()
        if answer.isdigit() and 1 <= int(answer) <= maximum:
            return int(answer) - 1
        print(f"Enter a number from 1 to {maximum}.")


async def _read_other() -> str:
    """Read a non-empty custom answer for the Other option."""
    while True:
        answer = (
            await asyncio.to_thread(input, "Enter your answer: ")
        ).strip()
        if answer:
            return answer
        print("The answer cannot be empty.")


async def _request_user_input(
    pending: RequireExternalExecutionEvent,
) -> ExternalExecutionResultEvent:
    """Collect results for pending ``RequestUserInput`` tool calls."""
    results: list[ToolResultBlock] = []
    for tool_call in pending.tool_calls:
        if tool_call.name != RequestUserInput.name:
            raise ValueError(
                f"The console cannot execute external tool "
                f"'{tool_call.name}'.",
            )

        tool_input = json.loads(tool_call.input)
        try:
            jsonschema.validate(
                tool_input,
                RequestUserInput.input_schema,
            )
        except jsonschema.ValidationError as error:
            raise ValueError(
                f"Invalid RequestUserInput payload: {error.message}",
            ) from error
        options = tool_input["options"]
        question = tool_input["question"]
        print(f"\n{question}")
        for index, option in enumerate(options, start=1):
            recommended = " (Recommended)" if option.get("recommended") else ""
            label = option["label"]
            print(f"  {index}. {label}{recommended}")
            description = option.get("description")
            if description:
                print(f"     {description}")

        other_index = len(options)
        print(f"  {other_index + 1}. Other")
        selected = await _read_choice(other_index + 1)
        payload: dict[str, str | int]
        if selected == other_index:
            payload = {
                "type": "other",
                "text": await _read_other(),
            }
        else:
            payload = {
                "type": "option",
                "option_index": selected,
                "label": options[selected]["label"],
            }

        results.append(
            ToolResultBlock(
                id=tool_call.id,
                name=tool_call.name,
                output=[
                    TextBlock(
                        text=json.dumps(payload, ensure_ascii=False),
                    ),
                ],
                state=ToolResultState.SUCCESS,
            ),
        )

    return ExternalExecutionResultEvent(
        reply_id=pending.reply_id,
        execution_results=results,
    )


async def launch_console(
    agent: Agent | PipelineProtocol,
    user_name: str = "user",
    verbosity: Verbosity = "default",
    max_tool_result_lines: int | None = 20,
) -> None:
    """Chat with the given agent interactively in the terminal.

    A lightweight try-out/debugging entry — no session management, no
    persistence: the conversation lives in ``agent.state`` and ends with
    the process. Reads user messages from stdin, renders every streamed
    :class:`~agentscope.event.AgentEvent`, asks for tool-call
    confirmation (y/n) when the agent requires it, handles structured
    ``RequestUserInput`` choices, and turns Ctrl+C into an interruption
    of the current reply. Type ``exit``/``quit`` or press Ctrl+D to leave.

    .. code-block:: python

        agent = Agent(name=..., model=..., toolkit=...)
        await launch_console(agent)

    Args:
        agent (`Agent | PipelineProtocol`):
            The agent or pipeline to interact with.
        user_name (`str`, defaults to `"user"`):
            The name attached to the user's input messages, also used
            as the input prompt.
        verbosity (`Verbosity`, defaults to `"default"`):
            - `"quiet"`: only the streamed reply text and errors.
            - `"default"`: plus thinking, tool calls/results, hint
              blocks, token usage and human-in-the-loop notices.
            - `"debug"`: plus lifecycle events and other events that
              are invisible by default.
        max_tool_result_lines (`int | None`, defaults to `20`):
            Truncate the printed tool results to this number of lines.
            `None` means no truncation.
    """
    renderer = ConsoleRenderer(
        verbosity=verbosity,
        max_tool_result_lines=max_tool_result_lines,
    )
    renderer.console.print(
        "Chat with the agent. Type 'exit' (or Ctrl+D) to quit.",
        style="dim",
    )

    while True:
        try:
            query = (
                await asyncio.to_thread(input, f"\n{user_name}> ")
            ).strip()
        except (EOFError, KeyboardInterrupt):
            break
        if query in ("exit", "quit"):
            break
        if not query:
            continue

        inputs: (
            Msg
            | UserConfirmResultEvent
            | UserInterruptEvent
            | ExternalExecutionResultEvent
        ) = UserMsg(name=user_name, content=query)
        while True:
            pending = await _run_reply(agent, renderer, inputs)
            if pending is None:
                break
            try:
                if isinstance(pending, RequireUserConfirmEvent):
                    inputs = await _confirm(pending)
                else:
                    inputs = await _request_user_input(pending)
            except (EOFError, KeyboardInterrupt):
                # Abort the parked reply so the next input starts clean
                inputs = UserInterruptEvent(reply_id=pending.reply_id)
            except ValueError as error:
                renderer.console.print(str(error), style="red")
                inputs = UserInterruptEvent(reply_id=pending.reply_id)
