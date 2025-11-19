# -*- coding: utf-8 -*-
"""The studio related hook functions in agentscope."""
from typing import Any

import requests
import shortuuid

from ..agent import AgentBase, UserAgent


def as_studio_forward_message_pre_print_hook(
    self: AgentBase,
    kwargs: dict[str, Any],
    studio_url: str,
    run_id: str,
) -> None:
    """The pre-speak hook to forward messages to the studio."""
    msg = kwargs["msg"]

    message_data = msg.to_dict()

    if hasattr(self, "_reply_id"):
        reply_id = getattr(self, "_reply_id")
    else:
        reply_id = shortuuid.uuid()

    n_retry = 0
    while True:
        try:
            res = requests.post(
                f"{studio_url}/trpc/pushMessage",
                json={
                    "runId": run_id,
                    "replyId": reply_id,
                    "name": getattr(self, "name", msg.name),
                    "role": "user"
                    if isinstance(self, UserAgent)
                    else "assistant",
                    "msg": message_data,
                },
            )
            res.raise_for_status()
            break
        except Exception as e:
            if n_retry < 3:
                n_retry += 1
                continue

            raise e from None
