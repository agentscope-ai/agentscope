# -*- coding: utf-8 -*-
"""A2A agent implementation for AgentScope.

This module provides the A2A (Agent-to-Agent) protocol implementation,
enabling AgentScope agents to communicate with remote agents using the
A2A standard protocol.
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Any, Type

import httpx
from pydantic import BaseModel

from ._agent_base import AgentBase
from ..message import Msg
from ..formatter import A2AChatFormatter

if TYPE_CHECKING:
    from a2a.types import AgentCard
    from a2a.client import ClientConfig, Consumer
    from a2a.client.client_factory import TransportProducer
else:
    AgentCard = "a2a.types.AgentCard"
    ClientConfig = "a2a.client.ClientConfig"
    Consumer = "a2a.client.Consumer"
    TransportProducer = "a2a.client.client_factory.TransportProducer"


class A2AAgent(AgentBase):
    """An A2A agent implementation in AgentScope, which supports

    - Communication with remote agents using the A2A protocol
    - Bidirectional message conversion between AgentScope and A2A formats
    - Task lifecycle management with streaming and polling
    - Artifact handling and status tracking

    .. note:: Due to the limitation of A2A protocol. The A2AAgent class

    - Only support chatbot-scenario (a user and an assistant) interactions.
     To support multi-agent interactions requires the server side to handle
     the `name` field in the A2A messages properly.
    - Does not support structured output in `reply()` method due to the lack
     of structured output support in A2A protocol.
    - Stores observed messages locally and merges them with input messages
     when `reply()` is called. Observed messages are cleared after processing.
    """

    def __init__(
        self,
        agent_card: AgentCard,
        client_config: ClientConfig | None = None,
        consumers: list[Consumer] | None = None,
        additional_transport_producers: dict[str, TransportProducer]
        | None = None,
    ) -> None:
        """Initialize the A2A agent instance by the given agent card.

        Args:
            agent_card (`AgentCard`):
                The agent card that contains the information about the remote
                agent, such as its URL and capabilities.
            client_config (`ClientConfig | None`, optional):
                The configuration for the A2A client, including transport
                preferences and streaming options.
            consumers (`list[Consumer] | None`, optional):
                The list of consumers for handling A2A client events.
                These intercept request/response flows for logging,
                metrics, and security.
            additional_transport_producers (`dict[str, TransportProducer] | \
            None`, optional):
                Additional transport producers for creating A2A clients
                with specific transport protocols.
        """
        super().__init__()

        from a2a.types import AgentCard
        from a2a.client import ClientConfig, ClientFactory

        if not isinstance(agent_card, AgentCard):
            raise ValueError(
                f"agent_card must be an instance of AgentCard, "
                f"got {type(agent_card)}",
            )

        self.name: str = agent_card.name
        self.agent_card = agent_card

        # Create the client factory so that we can create clients later
        # in reply()
        self._a2a_client_factory = ClientFactory(
            config=client_config
            or ClientConfig(
                httpx_client=httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout=600),
                ),
            ),
            consumers=consumers,
        )

        # Register additional transport producers if provided
        if additional_transport_producers:
            for label, producer in additional_transport_producers.items():
                self._a2a_client_factory.register(
                    label,
                    producer,
                )

        # The variables to store observed messages
        self._observed_msgs: list[Msg] = []

        # The formatter used for message conversion
        self.formatter = A2AChatFormatter()

    def state_dict(self) -> dict:
        """Get the state dictionary of the A2A agent.

        Returns:
            `dict`:
                The state dictionary containing the observed messages.
        """
        return {
            "_observed_msgs": [msg.to_dict() for msg in self._observed_msgs],
        }

    def load_state_dict(self, state_dict: dict, strict: bool = True) -> None:
        """Load the state dictionary into the module.

        Args:
            state_dict (`dict`):
                The state dictionary to load.
            strict (`bool`, defaults to `True`):
                If `True`, raises an error if any key in the module is not
                found in the state_dict. If `False`, skips missing keys.

        Raises:
            `KeyError`:
                If a required key is missing in the state_dict when strict
                is `True`.
        """
        if "_observed_msgs" in state_dict:
            self._observed_msgs = [
                Msg.from_dict(d) for d in state_dict["_observed_msgs"]
            ]
        else:
            raise KeyError(
                "_observed_msgs key not found in state_dict",
            )

        if strict:
            for key in state_dict.keys():
                if key != "_observed_msgs":
                    raise KeyError(f"Unexpected key {key} in state_dict")

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """Receive the given message(s) without generating a reply.

        The observed messages are stored and will be merged with the
        input messages when `reply` is called. After `reply` completes,
        the stored messages will be cleared.

        Args:
            msg (`Msg | list[Msg] | None`):
                The message(s) to be observed. If None, no action is taken.
        """
        if msg is None:
            return

        if isinstance(msg, Msg):
            self._observed_msgs.append(msg)
        elif isinstance(msg, list) and all(isinstance(m, Msg) for m in msg):
            self._observed_msgs.extend(msg)
        else:
            raise TypeError(
                f"msg must be a Msg or a list of Msg, got {type(msg)}",
            )

    async def reply(
        self,
        msg: Msg | list[Msg] | None = None,
        **kwargs: Any,
    ) -> Msg:
        """Send message(s) to the remote A2A agent and receive a response.

        .. note:: This method merges any previously observed messages with the
        input messages, sends them to the remote agent, and clears the
        observed messages after processing.

        .. note:: The A2A protocol does not support structured output, so the
         `structured_model` parameter is not supported in this method.

        Args:
            msg (`Msg | list[Msg] | None`, optional):
                The message(s) to send to the remote agent. Can be a single
                Msg, a list of Msgs, or None. If None, only observed messages
                will be sent. Defaults to None.

        Returns:
            `Msg`:
                The response message from the remote agent. For tasks, this
                may be either a status update message or the final artifacts
                message, depending on the task state. If no messages are
                provided (both msg and observed messages are empty), returns
                a prompt message. If an error occurs during communication,
                returns an error message.
        """
        if "structured_model" in kwargs:
            raise ValueError(
                "structured_model is not supported in A2AAgent.reply() "
                "due to the lack of structured output support in A2A "
                "protocol.",
            )

        from a2a.types import Message as A2AMessage

        # Merge observed messages with input messages
        msgs_list = self._observed_msgs

        if msg is not None:
            if isinstance(msg, Msg):
                msgs_list.append(msg)
            else:
                msgs_list.extend(msg)

        # Create A2A client and send message
        client = self._a2a_client_factory.create(
            card=self.agent_card,
        )

        # Convert Msg objects into A2A Message object
        a2a_message = await self.formatter.format([_ for _ in msgs_list if _])

        response_msg = None
        async for item in client.send_message(a2a_message):
            if isinstance(item, A2AMessage):
                response_msg = await self.formatter.format_a2a_message(
                    self.name,
                    item,
                )
                await self.print(response_msg)

            elif isinstance(item, tuple):
                task, _ = item

                if task is not None:
                    for _ in await self.formatter.format_a2a_task(
                        self.name,
                        task,
                    ):
                        await self.print(_)
                        response_msg = _

        # Clear the observed messages after processing
        self._observed_msgs.clear()

        if response_msg:
            return response_msg

        raise ValueError(
            "No response received from remote agent",
        )

    # pylint: disable=unused-argument
    async def handle_interrupt(
        self,
        msg: Msg | list[Msg] | None = None,
        structured_model: Type[BaseModel] | None = None,
    ) -> Msg:
        """The post-processing logic when the reply is interrupted by the
        user or something else.
        """

        response_msg = Msg(
            self.name,
            "I noticed that you have interrupted me. What can I "
            "do for you?",
            "assistant",
            metadata={
                # Expose this field to indicate the interruption
                "_is_interrupted": True,
            },
        )

        await self.print(response_msg, True)

        # Add to observed messages for context in next reply
        self._observed_msgs.append(response_msg)

        return response_msg
