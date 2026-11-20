"""Keenetic API exceptions."""

from __future__ import annotations


class KeeneticApiError(Exception):
    """Base API error.

    ``status`` carries the real HTTP status code when the error originated
    from an HTTP response; it stays ``None`` for transport/parse errors so
    endpoint-missing detection can fall back to message heuristics.
    """

    def __init__(self, message: object = "", *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class KeeneticAuthError(KeeneticApiError):
    """Authentication failed."""


class KeeneticRciBlockedError(KeeneticApiError):
    """The router refuses this RCI path outright, whatever the credentials.

    KeeneticOS 5.1.9 (NDM-4882) closed the unauthenticated RCI on
    ``127.0.0.1:79``: a port forward or a KeenDNS proxy that ends there now
    answers 403 with an ``X-Detail`` lockdown code. Re-entering the password
    cannot help, so this is deliberately not a ``KeeneticAuthError``.
    """
