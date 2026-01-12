# -*- coding: utf-8 -*-
"""Set up an A2A server with a ReAct agent to handle the input query"""
import os
import uuid
from typing import AsyncGenerator, Any

from agent_card import agent_card

from a2a.server.events import Event
from a2a.types import (
    TaskStatus,
    TaskState,
    MessageSendParams,
    TaskStatusUpdateEvent,
)
from a2a.server.apps import A2AStarletteApplication

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter, A2AChatFormatter
from agentscope.model import DashScopeChatModel
from agentscope.pipeline import stream_printing_messages
from agentscope.session import JSONSession
from agentscope.tool import (
    Toolkit,
    execute_python_code,
    execute_shell_command,
    view_text_file,
)


class SimpleStreamHandler:
    """A simple request handler that handles the input query by an
    ReAct agent."""

    async def on_message_send_stream(
        self,  # pylint: disable=unused-argument
        params: MessageSendParams,
        *args: Any,
        **kwargs: Any,
    ) -> AsyncGenerator[Event, None]:
        """Handles the message_send method by the agent

        Args:
            params (`MessageSendParams`):
                The parameters for sending the message.

        Returns:
            `AsyncGenerator[Event, None]`:
                An asynchronous generator that yields task status update
                events.
        """
        task_id = params.message.task_id or uuid.uuid4().hex
        context_id = params.message.context_id or "default-context"
        # ============ Agent Logic ============

        # Register the tool functions
        toolkit = Toolkit()
        toolkit.register_tool_function(execute_python_code)
        toolkit.register_tool_function(execute_shell_command)
        toolkit.register_tool_function(view_text_file)

        # Create the agent instance
        agent = ReActAgent(
            name="Friday",
            sys_prompt="You're a helpful assistant named Friday.",
            model=DashScopeChatModel(
                model_name="qwen-max",
                api_key=os.getenv("DASHSCOPE_API_KEY"),
            ),
            formatter=DashScopeChatFormatter(),
            toolkit=toolkit,
        )

        session = JSONSession(save_dir="./sessions")
        await session.load_session_state(
            session_id="test-a2a-agent",
            agent=agent,
        )

        # Convert the A2A message to AgentScope Msg objects
        formatter = A2AChatFormatter()
        as_msg = await formatter.format_a2a_message(
            name="Friday",
            message=params.message,
        )

        yield TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.working),
            final=False,
        )

        async for msg, last in stream_printing_messages(
            agents=[agent],
            coroutine_task=agent(as_msg),
        ):
            # The A2A streaming response is one complete Message object rather
            # than accumulated or incremental text
            if last:
                a2a_message = await formatter.format([msg])

                yield TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(
                        state=TaskState.working,
                        message=a2a_message,
                    ),
                    final=False,
                )

        # Finish the task
        yield TaskStatusUpdateEvent(
            task_id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.completed),
            final=True,
        )

        await session.save_session_state(
            session_id="test-a2a-agent",
            agent=agent,
        )


handler = SimpleStreamHandler()
app_instance = A2AStarletteApplication(
    agent_card,
    handler,
)
app = app_instance.build()
