"""Public API exports for the Keenetic Router Pro client."""

from __future__ import annotations

from .client import KeeneticClient
from .errors import KeeneticApiError, KeeneticAuthError
from .helpers import (
    _dict_items,
    _is_endpoint_missing,
    _nested_dict_items,
    _payload_summary,
    _response_summary,
    _validate_cli_arg,
)
from .target import KeeneticConnectionTarget, normalize_connection_target

__all__ = [
    "_dict_items",
    "_is_endpoint_missing",
    "_nested_dict_items",
    "_payload_summary",
    "_response_summary",
    "_validate_cli_arg",
    "KeeneticApiError",
    "KeeneticAuthError",
    "KeeneticClient",
    "KeeneticConnectionTarget",
    "normalize_connection_target",
]
