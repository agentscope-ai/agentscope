import asyncio

import pytest

from agentscope.message import UserMsg
from agentscope.model._dashscope._model import DashScopeChatModel, DashScopeCredential


def test_dashscope_thinking_enable_not_sent_when_unset():
    """``DashScopeChatModel`` must not send ``enable_thinking`` when the
    caller never configured it.

    ``Parameters.thinking_enable`` used to be a non-optional ``bool``
    defaulting to ``False``, so the ``is not None`` guard in ``_call_api``
    was always true and ``extra_body["enable_thinking"] = False`` was
    silently injected into every request — disabling thinking mode for
    Qwen3+ hybrid-reasoning models whose server-side default is thinking
    enabled. The field is now tri-state (``bool | None = None``), matching
    the volcengine model, so an unset parameter leaves the request payload
    untouched while explicit ``True``/``False`` still take effect.
    """
    model = DashScopeChatModel(
        credential=DashScopeCredential(api_key="sk-test"),
        model="qwen-plus",
        stream=False,
    )
    assert model.parameters.thinking_enable is None

    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        # Abort before any network access; the payload is already captured.
        raise RuntimeError("captured")

    model.client.chat.completions.create = fake_create

    with pytest.raises(RuntimeError, match="captured"):
        asyncio.run(
            model._call_api(
                "qwen-plus",
                [UserMsg(name="user", content="hi")],
            )
        )

    extra_body = captured.get("extra_body") or {}
    assert "enable_thinking" not in extra_body, (
        "enable_thinking was force-injected even though the caller never set "
        f"thinking_enable: {extra_body}"
    )


def test_dashscope_thinking_enable_explicit_false_is_sent():
    """An explicit ``thinking_enable=False`` must still disable thinking.

    Some Qwen models default to thinking enabled on the server side, so an
    explicit ``False`` must keep flowing into ``extra_body`` — only the
    *unset* case stops sending the flag.
    """
    model = DashScopeChatModel(
        credential=DashScopeCredential(api_key="sk-test"),
        model="qwen-plus",
        stream=False,
        parameters=DashScopeChatModel.Parameters(thinking_enable=False),
    )
    assert model.parameters.thinking_enable is False

    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        # Abort before any network access; the payload is already captured.
        raise RuntimeError("captured")

    model.client.chat.completions.create = fake_create

    with pytest.raises(RuntimeError, match="captured"):
        asyncio.run(
            model._call_api(
                "qwen-plus",
                [UserMsg(name="user", content="hi")],
            )
        )

    extra_body = captured.get("extra_body") or {}
    assert extra_body.get("enable_thinking") is False, (
        "explicit thinking_enable=False should disable thinking: "
        f"{extra_body}"
    )
