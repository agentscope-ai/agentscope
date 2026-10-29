# -*- coding: utf-8 -*-
"""The interactive console entry for trying an agent in the terminal."""
import asyncio
import signal

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
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from ..tool import (
    AskUser,
    AskUserAnswer,
    AskUserMetadata,
    AskUserParams,
)


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
        try:
            async for event in agent.reply_stream(inputs):
                renderer.render(event)
                if isinstance(
                    event,
                    (
                        RequireUserConfirmEvent,
                        RequireExternalExecutionEvent,
                    ),
                ):
                    pending = event
        except BaseException:
            # Failed streams may omit REPLY_END for several participants.
            pending = None
            renderer.reset()
            raise

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
        # Only a caller cancelling this task bumps its ``cancelling()``
        # counter, while a SIGINT cancels ``task`` alone and leaves it at 0.
        caller = asyncio.current_task()
        if caller is not None and caller.cancelling():
            raise
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


async def _read_other() -> str:
    """Read a non-empty custom answer for the Other option."""
    while True:
        answer = (
            await asyncio.to_thread(input, "Enter your answer: ")
        ).strip()
        if answer:
            return answer
        print("The answer cannot be empty.")


async def _ask_user(input_json: str) -> tuple[str, dict]:
    """Collect native question answers and their structured metadata."""
    params = AskUserParams.model_validate_json(input_json)
    answers = []
    for question in params.questions:
        print(f"\n[{question.header}] {question.question}")
        if question.context:
            print(f"{question.context}")
        for index, option in enumerate(question.options, start=1):
            print(f"  {index}. {option.label}: {option.description}")
            if option.preview and not question.multi_select:
                print(f"     {option.preview}")
        other_index = len(question.options) + 1
        print(f"  {other_index}. Other")
        prompt = (
            f"Select numbers [1-{other_index}] separated by commas, "
            f"or type your answer: "
            if question.multi_select
            else f"Select a number [1-{other_index}], or type your answer: "
        )
        while True:
            raw = (await asyncio.to_thread(input, prompt)).strip()
            if not raw:
                continue
            parts = [part.strip() for part in raw.split(",")]
            if not all(part.isdigit() for part in parts):
                selected, other = [], raw
                break
            try:
                indexes = {int(part) for part in parts}
            except ValueError:
                continue
            if not indexes <= set(range(1, other_index + 1)) or (
                not question.multi_select and len(indexes) > 1
            ):
                print(f"Choose option numbers from 1 to {other_index}.")
                continue
            selected = [
                option.label
                for i, option in enumerate(question.options, start=1)
                if i in indexes
            ]
            other = await _read_other() if other_index in indexes else None
            break
        answers.append(
            AskUserAnswer(
                question=question.question,
                selected=selected,
                other=other,
            ),
        )
    lines = []
    for answer in answers:
        values = answer.selected + ([answer.other] if answer.other else [])
        lines.append(f"{answer.question}\n{', '.join(values)}")
    output = "\n\n".join(lines)
    return output, AskUserMetadata(answers=answers).model_dump(mode="json")


async def _request_user_input(
    pending: RequireExternalExecutionEvent,
) -> ExternalExecutionResultEvent:
    """Collect answers for pending native AskUser calls."""
    results: list[ToolResultBlock] = []
    for tool_call in pending.tool_calls:
        if tool_call.name != AskUser.name:
            raise ValueError(
                f"The console cannot execute external tool "
                f"'{tool_call.name}'.",
            )
        output, metadata = await _ask_user(tool_call.input)
        results.append(
            ToolResultBlock(
                id=tool_call.id,
                name=tool_call.name,
                output=output,
                metadata=metadata,
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
    ``AskUser`` choices, and turns Ctrl+C into an interruption
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
