"""Translation drift guards."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "keenetic_router_pro"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _shape(value: object) -> object:
    """Return a nested key-only shape for translation comparison."""
    if isinstance(value, dict):
        return {key: _shape(child) for key, child in sorted(value.items())}
    return "<leaf>"


def test_english_translations_match_strings_config_options_and_issues() -> None:
    """English translations must stay in sync with Home Assistant strings."""
    strings = _load_json(INTEGRATION / "strings.json")
    english = _load_json(INTEGRATION / "translations" / "en.json")

    for key in ("config", "options", "issues"):
        assert _shape(english.get(key, {})) == _shape(strings.get(key, {}))
