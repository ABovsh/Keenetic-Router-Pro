"""Low-level Keenetic API constants."""

from __future__ import annotations

import re

RCI_ROOT = "/rci"
_SENSITIVE_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "key",
        "login",
        "pass",
        "password",
        "psk",
        "secret",
        "username",
    }
)
_SENSITIVE_RESPONSE_RE = re.compile(
    r'(?i)("?(?:authorization|cookie|key|login|pass|password|psk|secret|username)"?\s*[:=]\s*)'
    r'("[^"]*"|\'[^\']*\'|[^,\s;}\]]+)'
)
_CLI_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:/@+-]+$")
_DNS_PROXY_STAT_RE = re.compile(
    r"^\s*(?P<ip>\S+)\s+"
    r"(?P<port>\d+)\s+"
    r"(?P<sent>\d+)\s+"
    r"(?P<answered>\d+)\s+"
    r"(?P<nxdomain>\d+)\s+"
    r"(?P<median>\d+)ms\s+"
    r"(?P<average>\d+)ms\s+"
    r"(?P<rank>\d+)\s*$"
)
_IPSEC_VICI_OOM_RE = re.compile(
    r"IpSec::Vici::Stats:\s+out of memory(?:\s+\[(?P<code>[^\]]+)\])?",
    re.IGNORECASE,
)
