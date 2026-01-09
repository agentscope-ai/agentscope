# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches line-too-long unused-argument too-many-statements
# flake8: noqa: E501
"""Browser search mixin for DFS-based search strategy."""
from typing import Union, Optional, Any, Dict
import json
import copy
from collections import defaultdict
import uuid
import re
from agentscope.message import Msg, ToolUseBlock

with open(
    "examples/agent_browser/build_in_prompt/browser_agent_dfs_prompt.md",
    "r",
    encoding="utf-8",
) as f:
    _BROWSER_AGENT_DFS_PROMPT = f.read()


class BrowserSearchMixin:
    """Mixin class providing DFS-based search capabilities for browser agents."""

    async def _extract_current_url(self) -> str:
        """Navigate to the specified URL using the browser_navigate tool."""
        tool_call = ToolUseBlock(
            id=str(uuid.uuid4()),  # Get the unique ID
            name="browser_tabs",
            input={"action": "list"},
            type="tool_use",
        )

        response = await self.toolkit.call_tool_function(tool_call)

        response_str = ""
        async for chunk in response:
            response_str = chunk.content[0]["text"]

        match = re.search(
            r"-\s*\d+:\s*\(current\).*?\((https?://[^\)]+)\)",
            response_str,
        )
        if match:
            return match.group(1)

        return ""

    async def _deduplicate_actions_and_memories(
        self,
        actions: list,
        memories: Optional[list] = None,
    ) -> tuple[list, list]:
        seen = set()
        unique_actions = []
        unique_memories = []
        for idx, action in enumerate(actions):
            # Copy input and remove 'element' for uniqueness check
            input_copy = dict(action["input"])
            input_copy.pop("element", None)
            key = (
                action["name"],
                json.dumps(input_copy, sort_keys=True),
            )
            if key not in seen:
                seen.add(key)
                unique_actions.append(
                    [action],
                )  # keep the same structure as input
                if memories is not None:
                    unique_memories.append(memories[idx])
        return unique_actions, unique_memories

    async def _replay(
        self,
        page_history: list,
        memory_history: dict,
    ) -> Msg:
        """Replay memory history to the same status.
        Replay to the last page in the page history.
        """

        msg_response = None

        if page_history[-1]["url"] != await self._extract_current_url():
            await self._navigate_to_start_url(page_history[-1]["url"])

        self.memory.load_state_dict(memory_history)

        return msg_response

    async def _reply_with_dfs(
        self,
        msg: Optional[Union[Msg, list[Msg]]] = None,
    ) -> Msg:
        """The dfs search reply method of the agent."""

        branch_factor = 5
        await self.print(Msg("system", "DFS Search Enabled", "system"), True)
        await self._navigate_to_start_url()
        await self.memory.add(msg)

        page_history = [
            {
                "tool_calls": None,
                "info": None,
                "url": await self._extract_current_url(),
            },
        ]

        step_idx = 0
        self.search_history = defaultdict(list)

        tmp_page_history = copy.deepcopy(page_history)
        tmp_memory_history = copy.deepcopy(self.memory.state_dict())

        observe_msg = await self._build_observation()
        msg_reasoning = await self._reasoning(observe_msg)

        action_queue = []
        action_queue.append(
            (
                step_idx,
                list(msg_reasoning.get_content_blocks("tool_use")),
                tmp_page_history,
                tmp_memory_history,  # memory history
                0,
            ),
        )

        # Load the DFS-specific prompt template
        self.reasoning_prompt: str = (
            self.reasoning_prompt
            + _BROWSER_AGENT_DFS_PROMPT.format(branch_factor=branch_factor)
        )

        reply_msg = None

        while action_queue and step_idx < self.max_iters:
            # Default dfs strategy
            item = action_queue.pop(-1)

            (
                _,
                curr_actions,
                curr_page_history,
                curr_memory_history,
                curr_depth,
            ) = item

            step_idx += 1

            # Reset environment to prepare for next action
            _ = await self._replay(
                curr_page_history,
                curr_memory_history,
            )

            await self.print(
                Msg(
                    "system",
                    f"Explore next action: {step_idx}/{self.max_iters}",
                    "system",
                ),
                True,
            )

            tmp_page_history = copy.deepcopy(curr_page_history)

            if (
                curr_actions is None
                or curr_actions[0]["name"] == "browser_wait_for"
                or curr_actions[0]["name"] == "browser_navigate_back"
                or curr_actions[0]["name"] == "browser_handle_dialog"
            ):
                continue

            msg_reasoning = Msg(
                self.name,
                curr_actions,
                role="assistant",
            )

            await self.memory.add(msg_reasoning)

            futures = [self._acting(tool_call) for tool_call in curr_actions]

            # Sequential tool calls
            acting_responses = [await _ for _ in futures]

            # Find the first non-None replying message from the acting
            for acting_msg in acting_responses:
                reply_msg = reply_msg or acting_msg

            if reply_msg:
                break

            # record the action
            current_url = await self._extract_current_url()
            snapshot_text = await self._get_snapshot_in_text()

            page_entry: Dict[str, Any] = {
                "tool_calls": curr_actions,
                "info": str(snapshot_text),
                "url": current_url,
            }
            tmp_page_history.append(page_entry)

            tmp_memory_history = copy.deepcopy(self.memory.state_dict())

            try:
                next_actions = []
                while len(next_actions) == 0:
                    observe_msg = await self._build_observation()
                    msg_reasoning = await self._reasoning(observe_msg)

                    next_actions = list(
                        msg_reasoning.get_content_blocks("tool_use"),
                    )

                    if next_actions:
                        next_actions.reverse()

            except Exception as e:
                await self.print(
                    Msg(
                        "system",
                        f"Error finding next tool calls: {e}",
                        "system",
                    ),
                    True,
                )
                self.memory.load_state_dict(tmp_memory_history)

            # need to determine when to generate responses when there are multiple actions
            next_actions, _ = await self._deduplicate_actions_and_memories(
                next_actions,
            )

            # Add next actions to the queue
            for _, na in enumerate(next_actions):
                item = (
                    step_idx,
                    na,
                    copy.deepcopy(tmp_page_history),
                    copy.deepcopy(tmp_memory_history),
                    curr_depth + 1,
                )
                action_queue.append(item)

        if not reply_msg:
            await self._summarizing()

        await self.memory.add(reply_msg)
        return reply_msg
