"""OOM log counter helpers for the Keenetic coordinator."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from ..const import DOMAIN
from ..utils import coerce_int


_LOGGER = logging.getLogger(f"custom_components.{DOMAIN}.coordinator")
_KEENETIC_LOG_TS_FORMATS = ("%b %d %H:%M:%S", "%b  %d %H:%M:%S")


def parse_keenetic_log_ts(
    value: str | None,
    now: datetime | None = None,
) -> datetime | None:
    """Parse a Keenetic syslog timestamp (``May 27 17:33:48``) into datetime.

    The router omits the year. We assume the current year, with a one-month
    look-ahead window: if the parsed month is December and we're currently
    in January, roll back to the previous year so events that crossed the
    new-year boundary stay in chronological order.
    """
    if not isinstance(value, str) or not value:
        return None
    now = now or datetime.now()
    for fmt in _KEENETIC_LOG_TS_FORMATS:
        try:
            parsed = datetime.strptime(f"2000 {value.strip()}", f"%Y {fmt}")
        except ValueError:
            continue
        year = now.year
        if parsed.month == 12 and now.month == 1:
            year = now.year - 1
        try:
            return parsed.replace(year=year)
        except ValueError:
            # Feb 29 parsed against the leap base year cannot be re-stamped
            # onto a non-leap year; clamp to Feb 28 instead of dropping the
            # event from the OOM counter.
            if parsed.month == 2 and parsed.day == 29:
                return parsed.replace(year=year, day=28)
            return None
    return None


def advance_oom_state(
    state: dict[str, Any],
    events: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return updated monotonic OOM state from timestamped log events."""
    last_seen_iso = state.get("last_seen_iso")
    last_seen_dt = None
    if isinstance(last_seen_iso, str) and last_seen_iso:
        try:
            last_seen_dt = datetime.fromisoformat(last_seen_iso)
        except ValueError:
            _LOGGER.debug("Ignoring invalid stored OOM timestamp: %r", last_seen_iso)
        else:
            if last_seen_dt.tzinfo is not None:
                last_seen_dt = last_seen_dt.replace(tzinfo=None)

    # Existing stores from 1.7.46 only have ``last_seen_iso``. Treat that as
    # one event at the stored timestamp so an overlapping window does not
    # recount it after upgrade.
    last_seen_count = coerce_int(state.get("last_seen_count"), 0)
    now_dt = now or datetime.now()
    if last_seen_dt is not None and last_seen_dt > now_dt:
        _LOGGER.debug(
            "Ignoring future stored OOM timestamp after clock rollback: %r",
            last_seen_iso,
        )
        last_seen_dt = None
        last_seen_count = 0
    if last_seen_dt is not None and last_seen_count <= 0:
        last_seen_count = 1

    counts_by_ts: dict[datetime, int] = {}
    for event in events or []:
        if not isinstance(event, (list, tuple)) or not event:
            continue
        event_dt = parse_keenetic_log_ts(event[0], now=now_dt)
        if event_dt is None:
            continue
        if event_dt > now_dt:
            # Clock skew / year-rollover artefact: a future-dated event would
            # be counted now AND persisted, permanently over-counting.
            continue
        counts_by_ts[event_dt] = counts_by_ts.get(event_dt, 0) + 1

    if not counts_by_ts:
        return {
            "last_seen_iso": last_seen_dt.isoformat() if last_seen_dt else None,
            "last_seen_count": last_seen_count,
            "total": coerce_int(state.get("total"), 0),
        }

    new_total = coerce_int(state.get("total"), 0)
    new_last_seen = last_seen_dt
    for event_dt, count in counts_by_ts.items():
        if last_seen_dt is not None and event_dt < last_seen_dt:
            continue
        if last_seen_dt is not None and event_dt == last_seen_dt:
            new_total += max(0, count - last_seen_count)
        else:
            new_total += count
        if new_last_seen is None or event_dt > new_last_seen:
            new_last_seen = event_dt

    if new_last_seen is None:
        new_last_seen_count = 0
    elif new_last_seen == last_seen_dt:
        # The log window can slide so fewer events at the last-seen second
        # remain visible; keep the high-water mark or a later re-grown
        # window would double-count the same-second events.
        new_last_seen_count = max(
            last_seen_count, counts_by_ts.get(new_last_seen, 0)
        )
    else:
        new_last_seen_count = counts_by_ts.get(new_last_seen, 0)
    return {
        "last_seen_iso": new_last_seen.isoformat() if new_last_seen else None,
        "last_seen_count": new_last_seen_count,
        "total": new_total,
    }
