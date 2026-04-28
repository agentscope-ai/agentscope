# -*- coding: utf-8 -*-
"""The unified agent class in AgentScope library."""
import uuid
from typing import AsyncGenerator

from ..tool import ToolChoice

from ..event import (
    AgentEvent,
    EventType,
    ModelCallEndedEvent,
    ModelCallStartedEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
    ThinkingBlockDeltaEvent,
    ThinkingBlockEndEvent,
    ThinkingBlockStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultBinaryDeltaEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    RequireUserConfirmEvent,
    RequireExternalExecutionEvent,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
)
from ..model import ChatResponse, ChatUsage, ChatModelBase
from ..message import (
    Msg,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
    DataBlock,
    Base64Source,
    URLSource,
)


DEFAULT_COMPRESSION_PROMPT = (
    "<system-hint>You have been working on the task described above but have "
    "not yet completed it. "
    "Now write a continuation summary that will allow you to resume work "
    "efficiently in a future context window "
    "where the conversation history will be replaced with this summary. "
    "Your summary should be structured, concise, and actionable.</system-hint>"
)


class Agent:
    """The unified agent class in AgentScope library."""

    def __init__(
        self,
        name: str,
        sys_prompt: str,
        model: "ChatModelBase",
        max_iters: int = 20,
    ) -> None:
        """Initialize an agent instance."""
        if max_iters <= 0:
            raise ValueError("max_iters must be greater than 0")

        self.name = name
        self._sys_prompt = sys_prompt
        self.model = model
        self.max_iters = max_iters

        # TODO: storage - these should be loaded from storage
        self._loaded = False
        self.context: list[Msg] = []
        self.reply_id: str = ""
        self.cur_iter: int = 0
        self.confirmed_tool_call_ids: list[str] = []
        self.cur_summary: str = ""

    @property
    def sys_prompt(self) -> str:
        """Get the system prompt of the agent."""
        # TODO: Add toolkit skills prompt when toolkit is implemented
        return self._sys_prompt

    async def load_state(self) -> None:
        """Load the state from storage if available."""
        # TODO: Implement storage loading

    async def save_state(self) -> None:
        """Save the state to storage if available."""
        # TODO: Implement storage saving

    async def reply_stream(
        self,
        msgs: Msg | list[Msg] | None = None,
        event: AgentEvent | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Reply to the given message and stream agent events."""
        await self.load_state()
        try:
            async for chunk in self._reply(msgs=msgs, event=event):
                if not isinstance(chunk, Msg):
                    yield chunk
        finally:
            await self.save_state()

    async def reply(
        self,
        msgs: Msg | list[Msg] | None = None,
        event: AgentEvent | None = None,
    ) -> Msg:
        """Reply to the given message, consuming all streamed events."""
        await self.load_state()
        try:
            final_msg: Msg | None = None
            async for chunk in self._reply(msgs=msgs, event=event):
                if chunk.is_last:
                    final_msg = chunk.msg
            if final_msg is None:
                raise RuntimeError("Agent did not produce a final message.")
            return final_msg
        finally:
            await self.save_state()

    def _save_to_context(
        self,
        blocks: list,
        _usage: ChatUsage | None = None,
    ) -> None:
        """Save content blocks into the context."""
        if len(self.context) == 0:
            self.context.append(
                Msg(name=self.name, content=list(blocks), role="assistant"),
            )
        else:
            last_msg = self.context[-1]
            if last_msg.role == "assistant" and last_msg.name == self.name:
                if isinstance(last_msg.content, str):
                    last_msg.content = [TextBlock(text=last_msg.content)]
                last_msg.content.extend(blocks)
                # TODO: Merge usage if needed
            else:
                self.context.append(
                    Msg(
                        name=self.name,
                        content=list(blocks),
                        role="assistant",
                    ),
                )

    def _get_pending_tool_calls(self) -> list[ToolCallBlock]:
        """Get tool calls from the last assistant message that have no result
        yet."""
        if len(self.context) == 0:
            return []
        last_msg = self.context[-1]
        if last_msg.role != "assistant":
            return []
        content = (
            last_msg.content if isinstance(last_msg.content, list) else []
        )
        tool_calls = [b for b in content if isinstance(b, ToolCallBlock)]
        result_ids = {b.id for b in content if isinstance(b, ToolResultBlock)}
        return [tc for tc in tool_calls if tc.id not in result_ids]

    def _get_awaiting_tool_calls(self) -> dict:
        """Get the awaiting tool calls that need user confirmation or external
        execution."""
        pending_tool_calls = self._get_pending_tool_calls()
        pre_tool_calls: list = []

        for tool_call in enumerate(pending_tool_calls):
            # TODO: toolkit checks for user confirmation and external execution
            pre_tool_calls.append(tool_call)

        return {
            "awaiting_type": None,
            "expected_event_type": None,
            "awaiting_tool_calls": [],
            "pre_tool_calls": pre_tool_calls,
        }

    async def _reply(
        self,
        msgs: Msg | list[Msg] | None = None,
        event: AgentEvent | None = None,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Core reply logic. Yields chunks with is_last flag."""
        awaiting_info = self._get_awaiting_tool_calls()
        expected_event_type = awaiting_info["expected_event_type"]

        if expected_event_type is not None:
            if event is None or event.type != expected_event_type:
                raise ValueError(
                    f"Agent is awaiting '{expected_event_type}' but received "
                    f"event of type '{event.type if event else 'none'}'.",
                )

            if isinstance(event, ExternalExecutionResultEvent):
                self._save_to_context(list(event.execution_results))

            elif isinstance(event, UserConfirmResultEvent):
                for result in event.confirm_results:
                    confirmed = result.confirmed
                    tool_call = result.tool_call
                    if confirmed:
                        self.confirmed_tool_call_ids.append(tool_call["id"])
                    else:
                        rejection_text = (
                            f"<system-info>**Note** the user rejected the "
                            f"execution of tool "
                            f'"{tool_call["name"]}"!</system-info>'
                        )
                        yield ToolResultStartEvent(
                            reply_id=self.reply_id,
                            tool_call_id=tool_call["id"],
                            tool_call_name=tool_call["name"],
                        )
                        yield ToolResultTextDeltaEvent(
                            reply_id=self.reply_id,
                            tool_call_id=tool_call["id"],
                            delta=rejection_text,
                        )
                        yield ToolResultEndEvent(
                            reply_id=self.reply_id,
                            tool_call_id=tool_call["id"],
                            state="interrupted",
                        )
                        self._save_to_context(
                            [
                                ToolResultBlock(
                                    id=tool_call["id"],
                                    name=tool_call["name"],
                                    output=[TextBlock(text=rejection_text)],
                                    state="interrupted",
                                ),
                            ],
                        )

                processed_ids = {
                    r.get("tool_call", {}).get("id")
                    for r in event.confirm_results
                }
                if self.context:
                    last_content = self.context[-1].content
                    if isinstance(last_content, list):
                        for block in last_content:
                            if (
                                isinstance(block, ToolCallBlock)
                                and block.id in processed_ids
                            ):
                                block.await_user_confirmation = False

        else:
            self.cur_iter = 0
            self.reply_id = str(uuid.uuid4())
            self.confirmed_tool_call_ids = []

            yield RunStartedEvent(
                session_id="",
                reply_id=self.reply_id,
                name=self.name,
                role="assistant",
            )

        if isinstance(msgs, list):
            self.context.extend(msgs)
        elif msgs is not None:
            self.context.append(msgs)

        while self.cur_iter < self.max_iters:
            pending_tool_calls = self._get_pending_tool_calls()
            if len(pending_tool_calls) == 0:
                await self._compress_memory_if_needed()
                async for evt in self._reasoning(
                    tool_choice=ToolChoice(mode="auto"),
                ):
                    yield evt

            awaiting_info = self._get_awaiting_tool_calls()
            awaiting_type = awaiting_info["awaiting_type"]
            awaiting_tool_calls = awaiting_info["awaiting_tool_calls"]
            pre_tool_calls = awaiting_info["pre_tool_calls"]

            for tool_call in pre_tool_calls:
                async for evt in self._acting(tool_call=tool_call):
                    yield evt
                self.confirmed_tool_call_ids = [
                    cid
                    for cid in self.confirmed_tool_call_ids
                    if cid != tool_call.id
                ]

            if awaiting_type is not None:
                if awaiting_type == EventType.REQUIRE_USER_CONFIRM:
                    yield RequireUserConfirmEvent(
                        reply_id=self.reply_id,
                        tool_calls=awaiting_tool_calls,
                    )
                else:
                    yield RequireExternalExecutionEvent(
                        reply_id=self.reply_id,
                        tool_calls=awaiting_tool_calls,
                    )
                waiting_text = (
                    "Waiting for user confirmation ..."
                    if awaiting_type == EventType.REQUIRE_USER_CONFIRM
                    else "Waiting for external execution ..."
                )
                yield Msg(
                    name=self.name,
                    content=[TextBlock(text=waiting_text)],
                    role="assistant",
                )
                return

            if len(pre_tool_calls) == 0:
                break

            self.cur_iter += 1

        last_block = None
        if self.context:
            last_content = self.context[-1].content
            if isinstance(last_content, list) and last_content:
                last_block = last_content[-1]
        if last_block is None or not isinstance(last_block, TextBlock):
            async for evt in self._reasoning(
                tool_choice=ToolChoice(mode="none"),
            ):
                yield evt

        yield RunFinishedEvent(
            session_id="",
            reply_id=self.reply_id,
        )

        final_block = None
        if self.context:
            last_content = self.context[-1].content
            if isinstance(last_content, list) and last_content:
                final_block = last_content[-1]
        yield Msg(
            id=self.reply_id,
            name=self.name,
            content=[final_block] if final_block else [],
            role="assistant",
        )

    async def _reasoning(
        self,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncGenerator:
        """Core reasoning logic. Yields chunks with is_last flag."""
        # TODO: Pass tool schemas from toolkit when toolkit is implemented

        yield ModelCallStartedEvent(
            reply_id=self.reply_id,
            model_name=self.model.model_name,
        )

        system_msg = Msg(
            name="system",
            content=[TextBlock(text=self.sys_prompt)],
            role="system",
        )
        messages = [system_msg]
        if self.cur_summary:
            messages.append(
                Msg(
                    name="user",
                    content=[TextBlock(text=self.cur_summary)],
                    role="user",
                ),
            )
        messages.extend(self.context)

        # TODO: Pass tools and tool_choice when toolkit is implemented
        res = await self.model(
            messages=messages,
            tool_choice=tool_choice,
        )

        block_ids: dict = {
            "text_block_id": None,
            "thinking_block_id": None,
            "tool_call_ids": [],
        }
        completed_response: ChatResponse | None = None

        if self.model.stream:
            async for chunk in res:
                if chunk.is_last:
                    completed_response = chunk
                    break
                async for evt in self._convert_chat_response_to_event(
                    block_ids,
                    chunk,
                ):
                    yield evt
        elif isinstance(res, ChatResponse):
            completed_response = res
            async for evt in self._convert_chat_response_to_event(
                block_ids,
                res,
            ):
                yield evt

        if block_ids["text_block_id"] is not None:
            yield TextBlockEndEvent(
                reply_id=self.reply_id,
                block_id=block_ids["text_block_id"],
            )
        if block_ids["thinking_block_id"] is not None:
            yield ThinkingBlockEndEvent(
                reply_id=self.reply_id,
                block_id=block_ids["thinking_block_id"],
            )
        for tool_call_id in block_ids["tool_call_ids"]:
            yield ToolCallEndEvent(
                reply_id=self.reply_id,
                tool_call_id=tool_call_id,
            )

        yield ModelCallEndedEvent(
            reply_id=self.reply_id,
            input_tokens=completed_response.usage.input_tokens
            if completed_response.usage
            else 0,
            output_tokens=completed_response.usage.output_tokens
            if completed_response.usage
            else 0,
        )

        self._save_to_context(
            list(completed_response.content),
            completed_response.usage,
        )

    async def _acting(
        self,
        tool_call: ToolCallBlock,
    ) -> AsyncGenerator:
        """Core acting logic. Yields chunks with is_last flag."""
        yield ToolResultStartEvent(
            reply_id=self.reply_id,
            tool_call_id=tool_call.id,
            tool_call_name=tool_call.name,
        )

        # TODO: Implement toolkit execution when toolkit is available
        # res = self.toolkit.call_tool_function(tool_call)
        # async for tool_res in res:
        #     if tool_res.is_last:
        #         result_block = {
        #             "type": "tool_result",
        #             "id": tool_call["id"],
        #             "name": tool_call["name"],
        #             "output": tool_res.content,
        #             "state": tool_res.state,
        #         }
        #         self._save_to_context([result_block])
        #         return
        #     async for evt in self._convert_tool_response_to_event(
        #     tool_call, tool_res
        #     ):
        #         yield evt
        raise NotImplementedError("Toolkit not yet implemented.")

    async def _observe(self, msgs: Msg | list[Msg] | None = None) -> None:
        """Receive external observation message(s) and save them into
        context."""
        await self.load_state()
        if isinstance(msgs, list):
            self.context.extend(msgs)
        elif msgs is not None:
            self.context.append(msgs)

    async def _convert_chat_response_to_event(
        self,
        block_ids: dict,
        chunk: ChatResponse,
    ) -> AsyncGenerator:
        """Convert a ChatResponse chunk into a sequence of agent events."""
        for block in chunk.content:
            if isinstance(block, TextBlock):
                if block_ids["text_block_id"] is None:
                    block_ids["text_block_id"] = str(uuid.uuid4())
                    yield TextBlockStartEvent(
                        reply_id=self.reply_id,
                        block_id=block_ids["text_block_id"],
                    )
                yield TextBlockDeltaEvent(
                    reply_id=self.reply_id,
                    block_id=block_ids["text_block_id"],
                    delta=block.text,
                )

            elif isinstance(block, ThinkingBlock):
                if block_ids["thinking_block_id"] is None:
                    block_ids["thinking_block_id"] = str(uuid.uuid4())
                    yield ThinkingBlockStartEvent(
                        reply_id=self.reply_id,
                        block_id=block_ids["thinking_block_id"],
                    )
                yield ThinkingBlockDeltaEvent(
                    reply_id=self.reply_id,
                    block_id=block_ids["thinking_block_id"],
                    delta=block.thinking,
                )

            elif isinstance(block, ToolCallBlock):
                if block.id not in block_ids["tool_call_ids"]:
                    block_ids["tool_call_ids"].append(block.id)
                    yield ToolCallStartEvent(
                        reply_id=self.reply_id,
                        tool_call_id=block.id,
                        tool_call_name=block.name,
                    )
                yield ToolCallDeltaEvent(
                    reply_id=self.reply_id,
                    tool_call_id=block.id,
                    delta=block.input,
                )

    async def _convert_tool_response_to_event(
        self,
        tool_call: ToolCallBlock,
        tool_res: object,
    ) -> AsyncGenerator:
        """Convert a ToolResponse into a sequence of agent events."""
        # TODO: Properly typed once toolkit is implemented
        for block in tool_res.content:  # type: ignore[attr-defined]
            if isinstance(block, TextBlock):
                yield ToolResultTextDeltaEvent(
                    reply_id=self.reply_id,
                    tool_call_id=tool_call.id,
                    delta=block.text,
                )

            elif isinstance(block, DataBlock):
                if isinstance(block.source, Base64Source):
                    yield ToolResultBinaryDeltaEvent(
                        reply_id=self.reply_id,
                        tool_call_id=tool_call.id,
                        media_type=block.source.media_type,
                        data=block.source.data,
                    )
                elif isinstance(block.source, URLSource):
                    yield ToolResultBinaryDeltaEvent(
                        reply_id=self.reply_id,
                        tool_call_id=tool_call.id,
                        media_type=block.source.media_type,
                        url=block.source.url,
                    )

        yield ToolResultEndEvent(
            reply_id=self.reply_id,
            tool_call_id=tool_call.id,
            state=tool_res.state,  # type: ignore[attr-defined]
        )

    def _split_context_for_compression(self) -> tuple[list[Msg], list[Msg]]:
        """Split context into parts to compress and parts to keep recent."""
        # TODO: Implement full splitting logic when compression_config is
        #  available
        return [], list(self.context)

    async def _compress_memory_if_needed(self) -> None:
        """Compress the agent's memory if the token count exceeds the
        threshold."""
        # TODO: Implement when compression_config is available
