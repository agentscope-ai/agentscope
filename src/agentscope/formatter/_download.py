# -*- coding: utf-8 -*-
"""Download untrusted media without accessing private network services."""
import ipaddress
import socket

import httpx


def _download_media(url: str) -> bytes:
    """Fetch media from public HTTP(S) addresses, following up to 5 redirects.

    Every destination is resolved and checked before connecting to a numeric
    IP. The original hostname is retained for routing and TLS verification.
    Environment proxies are not used, since they can resolve destinations
    independently of these checks. Redirects do not forward credentials.

    Args:
        url (`str`):
            The remote media URL.

    Returns:
        `bytes`:
            The downloaded media.

    Raises:
        ValueError: If a destination is not public HTTP(S), or there are too
            many redirects.
        httpx.HTTPError: If downloading the media fails.
        socket.gaierror: If a hostname cannot be resolved.
    """
    for _ in range(6):
        target = httpx.URL(url)
        if (
            target.scheme not in ("http", "https")
            or not target.host
            or target.userinfo
            or "%" in target.host
        ):
            raise ValueError(
                "Media URLs must use HTTP(S) without credentials "
                "or scope IDs.",
            )

        hostname = target.raw_host.decode("ascii")
        port = target.port or (443 if target.scheme == "https" else 80)
        addresses = list(
            dict.fromkeys(
                ipaddress.ip_address(info[4][0])
                for info in socket.getaddrinfo(
                    hostname,
                    port,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP,
                )
            ),
        )
        if not addresses or any(
            not address.is_global
            or address.is_multicast
            or address.is_reserved
            or (
                isinstance(address, ipaddress.IPv6Address)
                and (
                    address.sixtofour is not None or address.teredo is not None
                )
            )
            for address in addresses
        ):
            raise ValueError(
                "Media URLs must resolve only to public IP addresses.",
            )

        for index, address in enumerate(addresses):
            try:
                # A fresh client also prevents connection/cookie reuse when
                # different redirect hosts share the same numeric IP.
                with httpx.Client(timeout=30, trust_env=False) as client:
                    with client.stream(
                        "GET",
                        target.copy_with(host=str(address)),
                        headers={"Host": target.netloc.decode("ascii")},
                        extensions={"sni_hostname": hostname},
                        follow_redirects=False,
                    ) as response:
                        if response.has_redirect_location:
                            url = str(
                                target.join(response.headers["location"]),
                            )
                            break
                        response.raise_for_status()
                        return response.read()
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if index == len(addresses) - 1:
                    raise

    raise ValueError("Media URL exceeded the limit of 5 redirects.")
