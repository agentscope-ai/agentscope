"""Model cards advertised by loopback OpenAI-compatible servers."""

from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from ...credential import OpenAICredential
from ...model import ModelCard, OpenAIChatModel


async def list_local_models(
    credential: OpenAICredential,
) -> list[ModelCard] | None:
    """Use the server's own chat catalog for loopback OpenAI-compatible URLs."""
    if not credential.base_url:
        return None
    address = urlsplit(credential.base_url)
    if address.hostname not in {"localhost", "127.0.0.1", "::1"}:
        return None
    if (
        address.scheme not in {"http", "https"}
        or address.username is not None
        or address.password is not None
        or address.query
        or address.fragment
    ):
        raise HTTPException(
            status_code=400, detail="Invalid local model server URL"
        )

    try:
        async with httpx.AsyncClient(
            timeout=5,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.get(
                credential.base_url.rstrip("/") + "/models",
                headers={
                    "Authorization": f"Bearer {credential.api_key.get_secret_value()}",
                },
            )
            if response.status_code in (404, 405):
                # Some compatible servers implement only chat completions.
                return None
            response.raise_for_status()
            entries = response.json()["data"]
        if not isinstance(entries, list):
            raise TypeError("Expected a model list")
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not read models from the local model server.",
        ) from exc

    schema = OpenAIChatModel.Parameters.model_json_schema()
    schema["properties"].pop("voice", None)
    chat_entries = {
        entry["id"]: entry
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("id"), str)
        and entry["id"]
        and entry.get("model_type") in (None, "llm", "vlm")
    }
    cards = []
    for name, entry in chat_entries.items():
        display_name = entry.get("display_name")
        target = (
            chat_entries.get(display_name)
            if isinstance(display_name, str)
            else None
        )
        # oMLX exposes Anthropic compatibility names for the same local weights.
        # Keep the alias selectable if its underlying model has no catalog entry.
        if (
            entry.get("owned_by") == "omlx"
            and entry.get("anthropic_family_tier")
            and target is not None
            and target.get("owned_by") == "omlx"
            and not target.get("anthropic_family_tier")
            and display_name != name
        ):
            continue
        size = entry.get("max_model_len")
        context = size if type(size) is int and size > 0 else 8192
        limit = entry.get("max_tokens")
        output = min(
            context, limit if type(limit) is int and limit > 0 else 4096
        )
        label = (
            f"{display_name} ({name})"
            if isinstance(display_name, str)
            and display_name
            and display_name != name
            else name
        )
        cards.append(
            ModelCard(
                name=name,
                label=label,
                model_class=OpenAIChatModel.type,
                status="active",
                input_types=["text/plain", "image/*"]
                if entry.get("model_type") == "vlm"
                else ["text/plain"],
                context_size=context,
                output_size=output,
                parameter_schema=schema,
                parameters_overrides={},
            )
        )
    return cards
