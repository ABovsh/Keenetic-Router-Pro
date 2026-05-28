"""Fetch result handling for coordinator refreshes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from ..api import KeeneticAuthError

CRITICAL_FETCH_NAMES = {"system_info", "interfaces"}


@dataclass(frozen=True)
class FetchFailure:
    """Named fetch failure captured during one coordinator tick."""

    name: str
    error: BaseException


def ok_or_default(
    name: str,
    value: Any,
    default: Any,
    failed_fetches: list[FetchFailure],
    *,
    silent: bool = False,
) -> Any:
    """Return value or a safe default while preserving cancellation semantics."""
    if isinstance(value, asyncio.CancelledError):
        raise value
    if isinstance(value, BaseException):
        if not silent:
            failed_fetches.append(FetchFailure(name, value))
        return default
    return value


def critical_failures_to_exception(failed_fetches: list[FetchFailure]) -> None:
    """Raise the existing coordinator exception for critical fetch failures."""
    critical = [
        failure for failure in failed_fetches if failure.name in CRITICAL_FETCH_NAMES
    ]
    if not critical:
        return
    if any(isinstance(failure.error, KeeneticAuthError) for failure in critical):
        raise ConfigEntryAuthFailed("Keenetic credentials were rejected")
    details = ", ".join(f"{failure.name}: {failure.error!r}" for failure in critical)
    raise UpdateFailed(f"Critical router fetch failed ({details})")
