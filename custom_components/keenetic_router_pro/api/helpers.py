"""Pure helper functions for the Keenetic API client."""

from __future__ import annotations

from typing import Any, Dict, List

import aiohttp

from ..utils import coerce_bool, coerce_int
from .constants import (
    _CLI_TOKEN_RE,
    _SENSITIVE_NAMES,
    _SENSITIVE_RESPONSE_RE,
)
from .errors import KeeneticApiError


def _validate_cli_arg(value: str, label: str) -> str:
    """Return a safe Keenetic CLI token or raise for command injection input."""
    if value is None:
        raise KeeneticApiError(f"Empty {label}")
    raw_value = str(value)
    candidate = raw_value.strip()
    if not candidate:
        raise KeeneticApiError(f"Empty {label}")
    if candidate != raw_value:
        raise KeeneticApiError(f"Unsafe {label}")
    if not _CLI_TOKEN_RE.fullmatch(candidate):
        raise KeeneticApiError(f"Unsafe {label}")
    return candidate


def _response_summary(text: str, limit: int = 240) -> str:
    """Return a short, single-line response excerpt with obvious secrets redacted."""
    summary = " ".join(str(text).split())
    if not summary:
        return "<empty>"
    summary = _SENSITIVE_RESPONSE_RE.sub(r"\1<redacted>", summary)
    if len(summary) > limit:
        return f"{summary[:limit]}..."
    return summary


def _payload_summary(payload: Any) -> Any:
    """Return a compact, non-secret representation of an outgoing JSON payload."""
    if payload is None:
        return None
    if isinstance(payload, dict):
        return {
            str(key): "<redacted>"
            if str(key).lower() in _SENSITIVE_NAMES
            else type(value).__name__
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return f"list[{len(payload)}]"
    return type(payload).__name__


def _to_int(value: Any, default: int = 0) -> int:
    """Return an int from loosely typed Keenetic RCI values."""
    return coerce_int(value, default)


def _truthy(value: Any) -> bool:
    """Return True only for actual truthy Keenetic-style values."""
    return coerce_bool(value)


def _cookie_header_from_response(resp: aiohttp.ClientResponse) -> str | None:
    """Extract a Cookie header value from a Set-Cookie response header."""
    raw_cookie = resp.headers.get("Set-Cookie", "")
    if not raw_cookie:
        return None
    cookie_kv = raw_cookie.split(";", 1)[0].strip()
    if "=" not in cookie_kv:
        return None
    return cookie_kv


def _is_endpoint_missing(err: BaseException) -> bool:
    """Return True if ``err`` indicates the router did not recognise the RCI endpoint."""
    msg = str(err).lower()
    return ("not found" in msg) or ("404" in msg)


def _dict_items(value: Any) -> List[Dict[str, Any]]:
    """Return dict entries from a Keenetic list/dict payload."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        children = [item for item in value.values() if isinstance(item, dict)]
        if children:
            return children
        if value:
            return [value]
    return []


def _nested_dict_items(data: Any, *keys: str) -> List[Dict[str, Any]]:
    """Return dict entries from a list payload or first matching nested key."""
    if isinstance(data, list):
        return _dict_items(data)
    if not isinstance(data, dict):
        return []

    for key in keys:
        value = data.get(key)
        if isinstance(value, dict) and any(
            marker in value for marker in ("cid", "mac", "id", "ip", "address")
        ):
            return [value]
        items = _dict_items(value)
        if items:
            return items
    return []


def _normalize_interfaces(raw: Any) -> List[Dict[str, Any]]:
    """Convert raw /rci/show/interface output to a universal list."""
    if isinstance(raw, dict):
        result: List[Dict[str, Any]] = []
        for key, val in raw.items():
            if not isinstance(val, dict):
                continue
            if "id" not in val:
                val = {**val, "id": key}
            result.append(val)
        return result
    if isinstance(raw, list):
        return _dict_items(raw)
    return []


def _extract_parse_messages(data: Any) -> List[str]:
    """Return textual log/message lines from a Keenetic response."""
    lines: List[str] = []

    def _walk(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            lines.extend(line for line in value.splitlines() if line)
            return
        if isinstance(value, list):
            for item in value:
                _walk(item)
            return
        if isinstance(value, dict):
            for key in ("message", "text", "line", "event"):
                if key in value:
                    _walk(value.get(key))
                    return
            parts = [
                str(value[key])
                for key in ("level", "time", "module", "ident", "service")
                if value.get(key) not in (None, "")
            ]
            msg = value.get("msg") or value.get("description")
            if msg:
                parts.append(str(msg))
            if parts:
                lines.append(" ".join(parts))
                return
            for nested in value.values():
                _walk(nested)

    _walk(data)
    return lines
