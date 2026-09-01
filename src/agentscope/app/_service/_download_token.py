# -*- coding: utf-8 -*-
"""Short-lived signed download tokens.

A browser-native fetch (an ``<iframe>`` PDF preview, an ``<img>`` tag,
or a click-to-download navigation) carries no custom headers, so it
cannot present ``X-User-ID``.  These helpers mint and verify a
capability that rides in the URL instead: an HMAC over
``(expires_at, user_id, path)`` keyed by the app-wide download secret.

The scheme is identical to the one
:class:`~agentscope.app._service._workspace.WorkspaceService` uses for
workspace files; it lives here as free functions so other routers
(knowledge base document preview in v1) can sign against the same
``app.state.download_secret`` without depending on the workspace
service.
"""
import base64
import hashlib
import hmac
import time

# Long enough to survive a slow round trip and the user's click, short
# enough that a token leaked through a log or history is already dead.
DEFAULT_DOWNLOAD_TOKEN_TTL = 60


def sign_download_token(
    secret: str,
    user_id: str,
    path: str,
    ttl: int = DEFAULT_DOWNLOAD_TOKEN_TTL,
) -> tuple[str, int]:
    """Mint a token authorizing one download.

    Args:
        secret (`str`):
            The app-wide signing secret
            (``app.state.download_secret``).
        user_id (`str`):
            The already-authenticated caller.
        path (`str`):
            The resource the token authorizes, verbatim as the
            download request will re-derive it.
        ttl (`int`, defaults to ``60``):
            Seconds the token stays valid.

    Returns:
        `tuple[str, int]`:
            The token and its expiry as a Unix timestamp.
    """
    expires_at = int(time.time()) + ttl
    signature = _signature(secret, expires_at, user_id, path)
    token = (
        f"{expires_at}"
        f".{_b64(user_id.encode('utf-8'))}"
        f".{_b64(signature)}"
    )
    return token, expires_at


def verify_download_token(secret: str, token: str, path: str) -> str:
    """Return the user a token was granted to, for this path.

    The path is not read out of the token but re-derived from the
    request, so a token for one resource cannot be replayed against
    another.

    Args:
        secret (`str`):
            The app-wide signing secret.
        token (`str`):
            The token from the request.
        path (`str`):
            The resource the request is asking for.

    Returns:
        `str`:
            The user ID the token was minted for.

    Raises:
        `ValueError`:
            The token is malformed, expired, or does not match.
    """
    try:
        raw_expiry, raw_user, raw_signature = token.split(".")
        expires_at = int(raw_expiry)
        user_id = _unb64(raw_user).decode("utf-8")
        signature = _unb64(raw_signature)
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError("Malformed download token.") from e

    expected = _signature(secret, expires_at, user_id, path)
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid download token.")
    if expires_at < time.time():
        raise ValueError("Expired download token.")
    return user_id


def _signature(
    secret: str,
    expires_at: int,
    user_id: str,
    path: str,
) -> bytes:
    """Compute the MAC binding an expiry, a user and a path.

    ``\\0`` separates the fields because it cannot occur in any of
    them, so no combination can be re-cut into a different triple.

    Args:
        secret (`str`): The signing secret.
        expires_at (`int`): Unix timestamp after which to refuse.
        user_id (`str`): The user the capability was granted to.
        path (`str`): The one resource the capability covers.

    Returns:
        `bytes`: The raw HMAC-SHA256 digest.
    """
    message = f"{expires_at}\0{user_id}\0{path}".encode("utf-8")
    return hmac.new(
        secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).digest()


def _b64(raw: bytes) -> str:
    """Encode without padding, which is not URL-safe to round-trip."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    """Reverse :func:`_b64`, restoring the stripped padding."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
