"""Connection target normalization for the Keenetic API client."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .errors import KeeneticApiError

_PORT_RANGE_ERR = "Port must be between 1 and 65535"


@dataclass(frozen=True)
class KeeneticConnectionTarget:
    """Normalized Keenetic HTTP target."""

    host: str
    port: int
    ssl: bool

    @property
    def base_url(self) -> str:
        """Return the normalized base URL for API requests."""
        scheme = "https" if self.ssl else "http"
        return f"{scheme}://{self.host}:{self.port}"


def normalize_connection_target(host: str, port: int, ssl: bool) -> KeeneticConnectionTarget:
    """Normalize host/port/SSL input from config flows and existing entries.

    ``host`` may be a bare host name/IP or a full URL with an optional port.
    Paths, query strings and fragments are rejected because the integration
    appends its own ``/rci/...`` paths.
    """
    raw_host = str(host or "").strip()
    if not raw_host:
        raise KeeneticApiError("Host is required")

    parsed = urlparse(raw_host if "://" in raw_host else f"//{raw_host}")
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        raise KeeneticApiError(f"Unsupported URL scheme: {parsed.scheme}")
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise KeeneticApiError("Host must not include a path, query string or fragment")

    normalized_host = parsed.hostname or raw_host
    normalized_host = normalized_host.strip()
    if not normalized_host:
        raise KeeneticApiError("Host is required")
    if any(ch.isspace() for ch in normalized_host):
        raise KeeneticApiError("Host must not contain whitespace")

    normalized_ssl = parsed.scheme == "https" if parsed.scheme else bool(ssl)
    try:
        url_port = parsed.port
    except ValueError as err:
        raise KeeneticApiError(_PORT_RANGE_ERR) from err
    try:
        normalized_port = url_port if url_port is not None else int(port)
    except (TypeError, ValueError) as err:
        raise KeeneticApiError(_PORT_RANGE_ERR) from err
    if not 1 <= normalized_port <= 65535:
        raise KeeneticApiError(_PORT_RANGE_ERR)

    return KeeneticConnectionTarget(
        host=normalized_host,
        port=normalized_port,
        ssl=normalized_ssl,
    )
