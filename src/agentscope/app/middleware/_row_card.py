# -*- coding: utf-8 -*-
"""Open a row quoted in a chat turn through the ordinary tool pipeline."""
import json
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from typing import Any

from ...message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
from ...middleware import MiddlewareBase
from ...model import ChatResponse
from ...agent import Agent


def _row_number(text: str) -> int | None:
    digits = "一二三四五六七八九"
    match = re.fullmatch(
        r"(?:请)?(?:打开|查看)(?:刚才的?)?\s*第\s*"
        r"([0-9]{1,9}|[一二三四五六七八九]?十[一二三四五六七八九]?|[一二三四五六七八九])"
        r"\s*(?:张|条|行|个)(?:单据|工单|记录)?[。！!]?",
        text.strip(),
    ) or re.fullmatch(r"open row ([0-9]{1,9})", text.strip(), re.I)
    if not match:
        return None
    ordinal = match[1]
    if ordinal.isdigit():
        return int(ordinal)
    if "十" in ordinal:
        tens, units = ordinal.split("十")
        return (digits.index(tens) + 1 if tens else 1) * 10 + (
            digits.index(units) + 1 if units else 0
        )
    return digits.index(ordinal) + 1


def _output(result: ToolResultBlock) -> str:
    if isinstance(result.output, str):
        return result.output
    return "\n".join(
        block.text for block in result.output if isinstance(block, TextBlock)
    )


class RowCardMiddleware(MiddlewareBase):
    """Resolve only the quoted message and call its registered row reader."""

    def __init__(
        self,
        get_message: Callable[[str], Awaitable[Msg | None]],
    ) -> None:
        super().__init__()
        self.get_message = get_message
        self.request: Msg | None = None

    async def on_reply(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable,
    ) -> AsyncGenerator:
        """Bind a quote to this invocation, never to the next user turn."""
        inputs = input_kwargs.get("inputs")
        messages = inputs if isinstance(inputs, list) else [inputs]
        self.request = next(
            (
                msg
                for msg in reversed(messages)
                if isinstance(msg, Msg)
                and msg.role == "user"
                and "row_card_reply" in msg.metadata
            ),
            None,
        )
        async with aclosing(next_handler(**input_kwargs)) as stream:
            async for event in stream:
                yield event

    async def _open(self, agent: Agent, call_id: str) -> ToolCallBlock:
        from ...business.row_references import OpenRowTool

        quote = self.request.metadata["row_card_reply"]
        if not isinstance(quote, dict) or not all(
            isinstance(quote.get(key), str) and 0 < len(quote[key]) <= 256
            for key in ("message_id", "tool_call_id")
        ):
            raise ValueError("缺少原列表消息，请重新选择卡片。")
        row = _row_number(self.request.get_text_content())
        if not row:
            raise ValueError("请指定要打开的行，例如“打开第二张”。")
        msg = await self.get_message(quote["message_id"])
        results = (
            msg.get_content_blocks("tool_result")
            if msg and msg.role == "assistant"
            else []
        )
        for result in results:
            if result.id != quote["tool_call_id"] or result.state != "success":
                continue
            name = re.fullmatch(r"query_recent_([a-zA-Z0-9_-]+)", result.name)
            if not name:
                break
            try:
                page = json.loads(_output(result))
                if page["row_card"]["version"] != 1 or not isinstance(
                    page["list_ref"],
                    str,
                ):
                    break
            except (ValueError, KeyError, TypeError):
                break
            tool = await agent.toolkit.get_tool(f"open_{name[1]}_row")
            if not isinstance(tool, OpenRowTool):
                raise ValueError("当前会话没有这张卡片的只读详情工具。")
            return ToolCallBlock(
                id=call_id,
                name=tool.name,
                input=json.dumps(
                    {"list_ref": page["list_ref"], "row_number": row},
                ),
            )
        raise ValueError("在当前会话中找不到原列表，请重新查询。")

    async def on_model_call(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable,
    ) -> Any:
        """Generate one checked tool call, including across a parked reply."""
        call_id = f"row_{agent.state.reply_id}"
        reply = agent.state.get_reply_message(agent.name)
        result = (
            next(
                (
                    block
                    for block in reply.get_content_blocks("tool_result")
                    if block.id == call_id
                ),
                None,
            )
            if reply
            else None
        )
        if result is not None:
            if result.state == "success":
                # Indent as literal text so upstream values cannot add links
                # or other markup to the detail message.
                text = _output(result)
                try:
                    text = json.dumps(
                        json.loads(text),
                        ensure_ascii=False,
                        indent=2,
                    )
                except ValueError:
                    pass
                text = "详情\n\n" + "\n".join(
                    "    " + line for line in text.splitlines()
                )
            else:
                text = "无法打开原单，请检查当前权限及账套，或重新查询已过期的列表。"
            block = TextBlock(text=text)
        elif self.request is None:
            return await next_handler(**input_kwargs)
        elif input_kwargs.get("tool_choice") == "none":
            block = TextBlock(text="本轮无法执行详情查询，请稍后重试。")
        else:
            try:
                block = await self._open(agent, call_id)
            except ValueError as exc:
                block = TextBlock(text=str(exc))
        return ChatResponse(content=[block], is_last=True)
