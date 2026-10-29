# -*- coding: utf-8 -*-
"""The aimlapi.com chat model implementation."""
from typing import Any, Literal
from urllib.parse import urlsplit

from .._base import ChatModelBase
from .._openai_chat import OpenAIChatModel
from ...credential import AIMLAPICredential
from ...formatter import FormatterBase, AIMLAPIChatFormatter

# Attribution headers identifying AgentScope as the calling application.
# ``HTTP-Referer`` / ``X-Title`` follow the OpenRouter convention and name the
# *host* project, not the provider. Never mutate this mapping — callers get a
# fresh copy per model instance.
_AIMLAPI_ATTRIBUTION_HEADERS = {
    "HTTP-Referer": "https://github.com/agentscope-ai/agentscope",
    "X-Title": "AgentScope",
    "X-AIMLAPI-Source": "agent/agentscope",
    "X-AIMLAPI-Partner-ID": "part_kY9LNtFpD5NNROdyhGmRvEqP",
}

_AIMLAPI_HOST_SUFFIX = "aimlapi.com"


def _is_aimlapi_origin(base_url: str | None) -> bool:
    """Whether ``base_url`` points at aimlapi.com itself.

    The credential's ``base_url`` is user-editable, so a self-hosted gateway
    or a third-party proxy can be pointed at this model class. Attribution
    headers must not ride along to such a host, hence the explicit origin
    check.

    Args:
        base_url (`str | None`):
            The configured base URL.

    Returns:
        `bool`:
            ``True`` when the host is ``aimlapi.com`` or a subdomain of it.
    """
    if not base_url:
        return False
    host = (urlsplit(str(base_url)).hostname or "").lower()
    return host == _AIMLAPI_HOST_SUFFIX or host.endswith(
        "." + _AIMLAPI_HOST_SUFFIX,
    )


def _build_client_kwargs(
    base_url: str | None,
    client_kwargs: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return client kwargs with the attribution headers merged in.

    Caller-supplied headers win on a key clash, and a new dict is built on
    every call so neither the module-level constant nor the caller's own
    mapping is mutated.

    Args:
        base_url (`str | None`):
            The configured base URL, used to scope the headers to our origin.
        client_kwargs (`dict[str, Any] | None`):
            Extra keyword arguments for ``openai.AsyncClient``.

    Returns:
        `dict[str, Any]`:
            A new client kwargs dict.
    """
    kwargs = dict(client_kwargs or {})
    if not _is_aimlapi_origin(base_url):
        return kwargs

    kwargs["default_headers"] = {
        **_AIMLAPI_ATTRIBUTION_HEADERS,
        **(kwargs.get("default_headers") or {}),
    }
    return kwargs


class AIMLAPIChatModel(OpenAIChatModel):
    """The aimlapi.com chat model.

    aimlapi.com aggregates several hundred models from different vendors
    behind one OpenAI-compatible ``/v1/chat/completions`` endpoint, so this
    class reuses :class:`OpenAIChatModel` for request building, streaming and
    response parsing rather than duplicating them. It differs in three ways:

    1. It takes an :class:`AIMLAPICredential` (no ``organization`` field).
    2. It attaches AgentScope attribution headers, scoped to the aimlapi.com
       origin so they cannot ride a request to another provider.
    3. It defaults to :class:`AIMLAPIChatFormatter`.

    .. note:: Request parameters that are unset are **omitted** rather than
        sent as ``null`` — inherited from :class:`OpenAIChatModel`. Several
        aimlapi.com models reject an explicit ``null`` for ``tools``,
        ``temperature``, ``seed`` and friends with a 400, which would
        otherwise break the second turn of every tool-calling loop.

    .. note:: ``/v1/completions`` does not exist on aimlapi.com, and
        ``/v1/responses`` only serves a small subset of the catalog, so only
        the chat-completions surface is used here.
    """

    type: Literal["aimlapi_chat"] = "aimlapi_chat"
    """The type of the chat model."""

    def __init__(
        self,
        credential: AIMLAPICredential,
        model: str,
        parameters: "AIMLAPIChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        context_size: int = 128000,
        formatter: FormatterBase | None = None,
        client_kwargs: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the aimlapi.com chat model.

        Args:
            credential (`AIMLAPICredential`):
                The aimlapi.com credential used to authenticate API calls.
            model (`str`):
                The model name, e.g. ``openai/gpt-5-5``. Any id from
                ``GET https://api.aimlapi.com/v1/models`` works; the bundled
                model cards only drive the picker.
            parameters (`AIMLAPIChatModel.Parameters | None`, defaults to \
            `None`):
                The API parameters. When ``None``, the default parameters
                will be used.
            stream (`bool`, defaults to `True`):
                Whether to enable streaming output.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries for the API.
            retry_delay (`float`, defaults to `1.0`):
                Seconds to sleep between retry attempts.
            context_size (`int`, defaults to `128000`):
                The model context size used for context compression.
            formatter (`FormatterBase | None`, defaults to `None`):
                The formatter that converts ``Msg`` objects to the format
                required by the API. When ``None``, an
                ``AIMLAPIChatFormatter`` instance will be used.
            client_kwargs (`dict[str, Any] | None`, defaults to `None`):
                Extra keyword arguments forwarded to ``openai.AsyncClient``
                (e.g. ``timeout``, ``default_headers``, ``http_client``).
                Headers given here take precedence over the attribution
                headers.
            extra_body (`dict[str, Any] | None`, defaults to `None`):
                Additional request body fields forwarded to the API.
        """
        # ``OpenAIChatModel.__init__`` reads ``credential.organization``,
        # which aimlapi.com has no equivalent of, so the base initializer is
        # invoked directly instead of through ``super()``. Adding a dead
        # ``organization`` field to the credential just to satisfy the parent
        # would put a meaningless input on the credential form.
        # pylint: disable=non-parent-init-called
        ChatModelBase.__init__(
            self,
            credential=credential,
            model=model,
            parameters=parameters or self.Parameters(),
            stream=stream,
            max_retries=max_retries,
            retry_delay=retry_delay,
            context_size=context_size,
        )
        self.formatter = formatter or AIMLAPIChatFormatter()
        self.client_kwargs = _build_client_kwargs(
            credential.base_url,
            client_kwargs,
        )
        self.extra_body = dict(extra_body) if extra_body is not None else None

        import openai

        self.client: openai.AsyncClient = openai.AsyncClient(
            api_key=self.credential.api_key.get_secret_value(),
            base_url=self.credential.base_url,
            **self.client_kwargs,
        )