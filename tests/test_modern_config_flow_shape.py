"""Regression guards for the modernised config-flow patterns.

Validates that the deprecated ``async_update_entry + async_abort``
pair has been replaced with ``async_update_reload_and_abort``, which
HA introduced as the canonical reauth/reconfigure success path. The
older pattern leaks listener registrations and skips the integration
reload, so reverting it would silently leave users on stale clients.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "keenetic_router_pro"


def test_reauth_uses_modern_helper() -> None:
    src = (ROOT / "config_flow.py").read_text()
    # The reauth_confirm step must call async_update_reload_and_abort,
    # not the older self.async_abort(reason="reauth_successful").
    assert "async_update_reload_and_abort(" in src, (
        "config_flow should call async_update_reload_and_abort on success"
    )
    assert 'reason="reauth_successful"' in src, (
        "reauth success must surface reauth_successful reason"
    )
    assert 'reason="reconfigure_successful"' in src, (
        "reconfigure success must surface reconfigure_successful reason"
    )


def test_password_field_is_masked() -> None:
    """All credential schemas should use the password selector."""
    src = (ROOT / "config_flow.py").read_text()
    assert "_PASSWORD_SELECTOR" in src
    # Both reauth and reconfigure schemas must reuse the masked selector.
    assert src.count("_PASSWORD_SELECTOR") >= 2, (
        "password selector should be used in setup, reauth, and reconfigure schemas"
    )
