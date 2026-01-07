# -*- coding: utf-8 -*-
"""A2A client example demonstrating agent card fetching and message sending."""
import logging

from typing import Any
from uuid import uuid4

import httpx

from a2a.client import A2ACardResolver, A2AClient
from a2a.types import (
    AgentCard,
    MessageSendParams,
    SendMessageRequest,
)
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    EXTENDED_AGENT_CARD_PATH,
)


async def main() -> None:
    """Main function demonstrating A2A client usage."""
    # Configure logging to show INFO level messages
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)  # Get a logger instance

    # --8<-- [start:A2ACardResolver]

    base_url = "http://localhost:10002"

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout=300.0),
    ) as httpx_client:
        # Initialize A2ACardResolver
        resolver = A2ACardResolver(
            httpx_client=httpx_client,
            base_url=base_url,
            # agent_card_path uses default,
            # extended_agent_card_path also uses default
        )
        # --8<-- [end:A2ACardResolver]

        # Fetch Public Agent Card and Initialize Client
        final_agent_card_to_use: AgentCard | None = None

        try:
            logger.info(
                "Attempting to fetch public agent card from: %s%s",
                base_url,
                AGENT_CARD_WELL_KNOWN_PATH,
            )
            _public_card = (
                await resolver.get_agent_card()
            )  # Fetches from default public path
            logger.info("Successfully fetched public agent card:")
            logger.info(
                _public_card.model_dump_json(indent=2, exclude_none=True),
            )
            final_agent_card_to_use = _public_card
            logger.info(
                "\nUsing PUBLIC agent card for client initialization "
                "(default).",
            )

            if _public_card.supports_authenticated_extended_card:
                try:
                    logger.info(
                        "\nPublic card supports authenticated extended card. "
                        "Attempting to fetch from: %s%s",
                        base_url,
                        EXTENDED_AGENT_CARD_PATH,
                    )
                    auth_headers_dict = {
                        "Authorization": (
                            "Bearer dummy-token-for-extended-card"
                        ),
                    }
                    _extended_card = await resolver.get_agent_card(
                        relative_card_path=EXTENDED_AGENT_CARD_PATH,
                        http_kwargs={"headers": auth_headers_dict},
                    )
                    logger.info(
                        "Successfully fetched authenticated extended "
                        "agent card:",
                    )
                    logger.info(
                        _extended_card.model_dump_json(
                            indent=2,
                            exclude_none=True,
                        ),
                    )
                    final_agent_card_to_use = (
                        _extended_card  # Update to use the extended card
                    )
                    logger.info(
                        "\nUsing AUTHENTICATED EXTENDED agent card "
                        "for client initialization.",
                    )
                except Exception as e_extended:
                    logger.warning(
                        "Failed to fetch extended agent card: %s. "
                        "Will proceed with public card.",
                        e_extended,
                        exc_info=True,
                    )
            elif (
                _public_card
            ):  # supports_authenticated_extended_card is False or None
                logger.info(
                    "\nPublic card does not indicate support for an "
                    "extended card. Using public card.",
                )

        except Exception as e:
            logger.error(
                "Critical error fetching public agent card: %s",
                e,
                exc_info=True,
            )
            raise RuntimeError(
                "Failed to fetch the public agent card. Cannot continue.",
            ) from e

        # --8<-- [start:send_message]
        client = A2AClient(
            httpx_client=httpx_client,
            agent_card=final_agent_card_to_use,
        )
        logger.info("A2AClient initialized.")

        send_message_payload: dict[str, Any] = {
            "message": {
                "role": "user",
                "parts": [
                    {
                        "kind": "text",
                        "text": "find top 5 chinese restaurants in new york",
                    },
                ],
                "messageId": uuid4().hex,
            },
        }
        request = SendMessageRequest(
            id=str(uuid4()),
            params=MessageSendParams(**send_message_payload),
        )

        response = await client.send_message(request)
        print(response.model_dump(mode="json", exclude_none=True))
        # --8<-- [end:send_message]

        # # --8<-- [start:send_message_streaming]

        # streaming_request = SendStreamingMessageRequest(
        #     id=str(uuid4()), params=MessageSendParams(**send_message_payload)
        # )

        # stream_response = client.send_message_streaming(streaming_request)

        # async for chunk in stream_response:
        #     print(chunk.model_dump(mode='json', exclude_none=True))
        # --8<-- [end:send_message_streaming]


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
