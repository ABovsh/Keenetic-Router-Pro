"""Public release metadata and documentation contract tests."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "keenetic_router_pro"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_public_metadata_is_internally_consistent() -> None:
    """HACS-facing metadata must describe the same integration."""
    manifest = _load_json(INTEGRATION / "manifest.json")
    hacs = _load_json(ROOT / "hacs.json")
    strings = _load_json(INTEGRATION / "strings.json")

    assert manifest["domain"] == "keenetic_router_pro"
    assert manifest["name"] == "Keenetic Router Pro"
    assert hacs["name"] == manifest["name"]
    assert strings["config"]["step"]["user"]["title"] == manifest["name"]
    assert manifest["iot_class"] == "local_polling"
    assert manifest["config_flow"] is True
    assert hacs["render_readme"] is True
    assert hacs["homeassistant"] == "2024.5.0"
    assert manifest["documentation"].endswith("Keenetic-Router-Pro")
    assert manifest["issue_tracker"].endswith("Keenetic-Router-Pro/issues")


def test_public_version_surfaces_match() -> None:
    """Manifest version, README badge, and latest changelog section must match."""
    manifest = _load_json(INTEGRATION / "manifest.json")
    version = manifest["version"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert f"badge/version-{version}-blue.svg" in readme
    assert re.search(rf"^## {re.escape(version)}(?:\\s|$)", changelog, re.MULTILINE)


def test_required_public_docs_exist_and_describe_release_mode() -> None:
    """Public docs should explain source installs, security, and release process."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "release-checklist.md").read_text(encoding="utf-8")

    assert "standard HACS source downloads" in readme
    assert "does not require release assets" in readme
    assert "no cloud dependency" in readme.lower()
    assert "KeenDNS protected" in readme
    assert "Repair" in readme
    assert "diagnostics" in security.lower()
    assert "pytest" in checklist
    assert "coverage" in checklist
    assert "manifest.json" in checklist
    assert "CHANGELOG.md" in checklist


def test_challenge_authentication_copy_is_consistent() -> None:
    """Home Assistant form labels should use polished challenge-auth wording."""
    strings = _load_json(INTEGRATION / "strings.json")
    english = _load_json(INTEGRATION / "translations" / "en.json")

    for payload in (strings, english):
        text = json.dumps(payload, ensure_ascii=False)
        assert "Challenge Auth" not in text
        assert "Use challenge authentication" in text
        assert "NDW2" in text
