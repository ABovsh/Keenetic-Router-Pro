"""Hardening tests for 1.7.73.

Security-audit follow-up: the plaintext-HTTP default is mitigated by a
repair issue after setup, but the config-flow SSL toggle itself must warn
the user at the moment they make the choice.
"""
from __future__ import annotations

import json
from pathlib import Path

TRANSLATIONS = (
    Path(__file__).parents[1]
    / "custom_components"
    / "keenetic_router_pro"
    / "translations"
    / "en.json"
)


def _steps() -> dict:
    return json.loads(TRANSLATIONS.read_text())["config"]["step"]


def test_connection_step_warns_about_plaintext_http():
    step = _steps()["connection"]
    desc = step.get("data_description", {}).get("ssl", "")
    assert "plaintext" in desc.lower() or "unencrypted" in desc.lower()


def test_reconfigure_connection_step_warns_about_plaintext_http():
    step = _steps()["reconfigure_connection"]
    desc = step.get("data_description", {}).get("ssl", "")
    assert "plaintext" in desc.lower() or "unencrypted" in desc.lower()
