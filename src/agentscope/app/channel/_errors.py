# -*- coding: utf-8 -*-
"""Channel module exception."""


class ChannelError(Exception):
    """A channel operation failed.

    Carries the HTTP status the router should surface, so one exception
    covers every case (not-found → 404, duplicate bot → 409, bad request
    → 400) without a subclass per case — the router reads
    :attr:`status_code` and the message.
    """

    def __init__(self, message: str, status_code: int = 400) -> None:
        """Bind the error message and the HTTP status to surface.

        Args:
            message (`str`): The human-readable error.
            status_code (`int`, defaults to 400): The HTTP status the
                router should return.
        """
        super().__init__(message)
        self.status_code = status_code
