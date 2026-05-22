"""Keenetic API exceptions."""

from __future__ import annotations


class KeeneticApiError(Exception):
    """Base API error."""


class KeeneticAuthError(KeeneticApiError):
    """Authentication failed."""
