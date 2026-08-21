# -*- coding: utf-8 -*-
"""Render an :class:`MCPCard` template into a connectable client."""
from string import Template
from typing import Any

import jsonschema

from ..hub import MCPCard
from ...mcp import MCPClient


class MCPRenderError(ValueError):
    """Raised when user-supplied values do not fit an :class:`MCPCard`.

    The single error type the install endpoint catches to answer 400 —
    schema violations, unfilled placeholders and rejected client configs
    all surface through it.
    """


def _effective_values(schema: dict, values: dict) -> dict:
    """Overlay submitted values on defaults declared by the input schema."""
    effective = {
        name: prop["default"]
        for name, prop in schema.get("properties", {}).items()
        if isinstance(prop, dict) and "default" in prop
    }
    effective.update(values)
    return effective


def _omit_unfilled_optional_env(
    config: dict,
    values: dict,
    declared: set[str],
    required: set[str],
) -> None:
    """Drop env entries whose optional inputs were not supplied.

    Optional placeholders elsewhere remain errors: omitting a URL or an
    argument can change the meaning of a config. Environment entries are
    independent, so leaving one unset means not exporting that variable.
    """
    env = config.get("env")
    if not isinstance(env, dict):
        return

    optional = declared - required
    for key, value in list(env.items()):
        if not isinstance(value, str):
            continue
        identifiers = set(Template(value).get_identifiers())
        if (identifiers & optional) - values.keys():
            del env[key]


def _substitute(
    node: Any,
    values: dict,
    declared: set[str],
    missing: set[str],
) -> Any:
    """Recursively substitute ``${...}`` in every string leaf.

    Placeholders naming a declared input but absent from ``values`` are
    collected into ``missing`` rather than raising, so the caller can
    report them all at once.

    Args:
        node (`Any`):
            The current node of the dumped config tree.
        values (`dict`):
            The user-supplied values keyed by input name.
        declared (`set[str]`):
            The input names declared by the card's ``inputs_schema``.
        missing (`set[str]`):
            The accumulator for declared-but-unsupplied placeholders.

    Returns:
        `Any`:
            The node with its string leaves substituted.
    """
    if isinstance(node, str):
        template = Template(node)
        # Undeclared placeholders (e.g. a literal ``$HOME`` in stdio
        # args) are deliberately left untouched by ``safe_substitute``.
        identifiers = set(template.get_identifiers())
        missing.update((identifiers & declared) - values.keys())
        return template.safe_substitute(values)

    if isinstance(node, dict):
        return {
            key: _substitute(value, values, declared, missing)
            for key, value in node.items()
        }

    if isinstance(node, list):
        return [_substitute(item, values, declared, missing) for item in node]

    return node


def render_mcp(
    card: MCPCard,
    values: dict,
    name: str | None = None,
) -> MCPClient:
    """Turn a card plus the user's answers into a connectable client.

    Args:
        card (`MCPCard`):
            The hub card carrying the config template and input schema.
        values (`dict`):
            The user-supplied values keyed by input name.
        name (`str | None`, optional):
            The name for the installed client. Defaults to the card name.

    Returns:
        `MCPClient`:
            The client ready to hand to ``workspace.add_mcp``.

    Raises:
        `MCPRenderError`:
            When ``values`` violate the card's ``inputs_schema``, leave a
            declared placeholder unfilled, or produce a config the
            client rejects.
    """
    values = _effective_values(card.inputs_schema, values)
    try:
        jsonschema.validate(values, card.inputs_schema)
    except jsonschema.ValidationError as e:
        raise MCPRenderError(
            f"Invalid values for MCP {card.name!r}: {e.message}",
        ) from e

    declared = set(card.inputs_schema.get("properties", {}))
    required = set(card.inputs_schema.get("required", []))
    missing: set[str] = set()
    config_template = card.config_template.model_dump(mode="json")
    _omit_unfilled_optional_env(
        config_template,
        values,
        declared,
        required,
    )
    config = _substitute(
        config_template,
        values,
        declared,
        missing,
    )

    if missing:
        raise MCPRenderError(
            f"MCP {card.name!r} needs a value for: "
            f"{', '.join(sorted(missing))}",
        )

    try:
        return MCPClient(
            name=name or card.name,
            is_stateful=card.is_stateful,
            mcp_config=config,
        )
    except ValueError as e:
        raise MCPRenderError(
            f"MCP {card.name!r} produced an invalid client: {e}",
        ) from e
