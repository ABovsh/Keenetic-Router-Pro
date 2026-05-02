"""Test bootstrap for lightweight unit tests.

The parser/helper tests do not need a full Home Assistant installation.
Provide the tiny exception surface imported by the integration API module.
"""

from __future__ import annotations

import sys
import types


class HomeAssistantError(Exception):
    """Minimal stand-in for homeassistant.exceptions.HomeAssistantError."""


homeassistant = types.ModuleType("homeassistant")
exceptions = types.ModuleType("homeassistant.exceptions")
exceptions.HomeAssistantError = HomeAssistantError
homeassistant.exceptions = exceptions

sys.modules.setdefault("homeassistant", homeassistant)
sys.modules.setdefault("homeassistant.exceptions", exceptions)
