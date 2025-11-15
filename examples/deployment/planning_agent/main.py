# -*- coding: utf-8 -*-
"""The server that holds agent service."""
import json
import os
from typing import AsyncGenerator

from quart import Quart, Response, request

from tool import create_worker

from agentscope.pipeline import stream_printing_messages
from agentscope.session import JSONSession
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit

app = Quart(__name__)


async def handle_input(
    msg: Msg,
    user_id: str,
    session_id: str,
) -> AsyncGenerator[str, None]:
    """Handle the input message and yield response chunks.

    Args:
        msg (`Msg`):
            The input message from the user.
        user_id (`str`):
            The user ID.
        session_id (`str`):
            The session ID.

    Yields:
        `str`:
            A response message in dict format by `Msg().to_dict()`.
    """
    toolkit = Toolkit()
    toolkit.register_tool_function(
        create_worker,
    )

    # Init JSONSession to save and load the state
    session = JSONSession(save_dir="./sessions")

    agent = ReActAgent(
        name="Friday",
        # pylint: disable=line-too-long
        sys_prompt="""You are Friday, a multifunctional agent that can help people solving different complex tasks. You act like a meta planner to solve complicated tasks by decomposing the task and building/orchestrating different worker agents to finish the sub-tasks.

## Core Mission
Your primary purpose is to break down complicated tasks into manageable subtasks (a plan), create worker agents to finish the subtask, and coordinate their execution to achieve the user's goal efficiently.

### Important Constraints
1. DO NOT TRY TO SOLVE THE SUBTASKS DIRECTLY yourself.
2. ONLY do reasoning and select functions to coordinate.
3. Always follow the roadmap sequence.
4. DO NOT finish until all subtasks are marked with \"Done\" after revising the roadmap.
5. DO NOT read user's provided file directly. Instead, create a worker to do so for you.

## Example Flow
Task: "Create a data visualization from my sales spreadsheet"
1. Clarify specifics (visualization type, data points of interest)
2. Build roadmap (data loading, cleaning, analysis, visualization, export)
3. Create/select appropriate workers for the i-th subtask (e.g., data searcher or processor)
4. Execute worker for the i-th subtask, revising roadmap after the worker finishes
5. Repeat step 3 and 4 until all subtasks are mark as "Done"
6. Generate final response with visualization results
""",  # noqa: E501
        model=DashScopeChatModel(
            model_name="qwen3-max",
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
    )

    # Load the session state if exists
    await session.load_session_state(
        session_id=f"{user_id}-{session_id}",
        agent=agent,
    )

    async for msg, _ in stream_printing_messages(
        agents=[agent],
        coroutine_task=agent(msg),
    ):
        # Transform the message into a dict string and yield it
        data = json.dumps(msg.to_dict(), ensure_ascii=False)
        yield f"data: {data}\n\n"

    # Save the session state
    await session.save_session_state(
        session_id=f"{user_id}-{session_id}",
        agent=agent,
    )


@app.route("/chat_endpoint", methods=["POST"])
async def chat_endpoint() -> Response:
    """A simple chat endpoint that streams responses."""
    # 从这个请求中解析出 user_id 和 session_id，以及用户消息
    data = await request.get_json()

    user_id = data.get("user_id")
    session_id = data.get("session_id")

    # We use textual input here, you can extend it to support other types
    user_input = data.get("user_input")

    # 如果为空返回错误
    if not user_id or not session_id:
        return Response(
            f"user_id and session_id are required, got user_id: {user_id}, "
            f"session_id: {session_id}",
            status=400,
        )

    return Response(
        handle_input(
            Msg(
                "user",
                user_input,
                "user",
            ),
            user_id,
            session_id,
        ),
        mimetype="text/event-stream",
    )


if __name__ == "__main__":
    app.run(
        port=5000,
        debug=True,
    )
