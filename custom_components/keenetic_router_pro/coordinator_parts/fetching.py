"""Fetch result handling for coordinator refreshes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from ..api import KeeneticAuthError

CRITICAL_FETCH_NAMES = {"system_info", "interfaces"}

# How many consecutive transient critical-fetch failures to tolerate by
# keeping the last-known-good snapshot before failing the coordinator (which
# flips every entity to ``unavailable``). The Keenetic RCI surface is served
# by a modest router CPU and occasionally drops a single ``/rci/show/system``
# request under load — a one-off timeout should not nuke the whole
# integration for a tick. Auth failures are never tolerated (see
# ``evaluate_critical_failures``).
CRITICAL_FETCH_GRACE_TICKS = 3


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


@dataclass(frozen=True)
class CriticalFetchDecision:
    """Outcome of evaluating critical fetch failures for one tick.

    ``action`` is one of:
      * ``"ok"``       — no critical failure this tick.
      * ``"auth"``     — credentials rejected; raise ``ConfigEntryAuthFailed``.
      * ``"tolerate"`` — transient failure within the grace window; keep the
        last-known-good snapshot and do not fail the coordinator.
      * ``"fail"``     — raise ``UpdateFailed`` (no snapshot to keep, or the
        grace window is exhausted).
    """

    action: str
    streak: int
    message: str = ""


def evaluate_critical_failures(
    failed_fetches: list[FetchFailure],
    *,
    have_previous_data: bool,
    streak: int,
    grace_ticks: int = CRITICAL_FETCH_GRACE_TICKS,
) -> CriticalFetchDecision:
    """Decide how to handle critical fetch failures, tolerating transient ones.

    A single dropped ``system_info`` / ``interfaces`` fetch (e.g. a router-side
    timeout) should not flip every entity to ``unavailable`` when we still hold
    a valid previous snapshot. Tolerate up to ``grace_ticks`` consecutive
    transient failures, then fail for real so a genuine outage still surfaces.
    Authentication failures bypass the grace window entirely — retrying a
    rejected credential just delays the reauth flow.
    """
    critical = [
        failure for failure in failed_fetches if failure.name in CRITICAL_FETCH_NAMES
    ]
    if not critical:
        return CriticalFetchDecision("ok", 0)
    if any(isinstance(failure.error, KeeneticAuthError) for failure in critical):
        return CriticalFetchDecision(
            "auth", streak, "Keenetic credentials were rejected"
        )
    details = ", ".join(f"{failure.name}: {failure.error!r}" for failure in critical)
    new_streak = streak + 1
    if have_previous_data and new_streak <= grace_ticks:
        return CriticalFetchDecision(
            "tolerate",
            new_streak,
            (
                f"Transient critical router fetch failure {new_streak}/{grace_ticks}; "
                f"keeping last-known data ({details})"
            ),
        )
    return CriticalFetchDecision(
        "fail", new_streak, f"Critical router fetch failed ({details})"
    )
