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
