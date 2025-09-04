# -*- coding: utf-8 -*-
"""The built-in hook functions in agentscope."""
from functools import partial

from ._studio_hooks import (
    as_studio_forward_message_pre_print_hook,
)
from .. import _config
from ..agent import AgentBase


__all__ = [
    "as_studio_forward_message_pre_print_hook",
]


def _equip_as_studio_hooks(
    studio_url: str,
) -> None:
    """Connect to the agentscope studio."""
    AgentBase.register_class_hook(
        "pre_print",
        "as_studio_forward_message_pre_print_hook",
        partial(
            as_studio_forward_message_pre_print_hook,
            studio_url=studio_url,
            run_id=_config.run_id,
        ),
    )
