"""Public API exports for the Keenetic Router Pro client."""

from __future__ import annotations

from ._legacy import KeeneticClient
from .errors import KeeneticApiError, KeeneticAuthError
from .target import KeeneticConnectionTarget, normalize_connection_target

__all__ = [
    "KeeneticApiError",
    "KeeneticAuthError",
    "KeeneticClient",
    "KeeneticConnectionTarget",
    "normalize_connection_target",
]
