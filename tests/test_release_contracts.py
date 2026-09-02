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
    assert hacs["zip_release"] is True
    assert hacs["filename"] == f"{manifest['domain']}.zip"
    assert hacs["content_in_root"] is False
    assert manifest["documentation"].endswith("Keenetic-Router-Pro")
    assert manifest["issue_tracker"].endswith("Keenetic-Router-Pro/issues")


def test_public_version_surfaces_match() -> None:
    """Manifest version, README badge, and latest changelog section must match."""
    manifest = _load_json(INTEGRATION / "manifest.json")
    version = manifest["version"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert f"badge/version-{version}-blue" in readme
    assert re.search(rf"^## {re.escape(version)}(?:\\s|$)", changelog, re.MULTILINE)


def test_readme_uses_real_sonarcloud_badges() -> None:
    """README badges should point at the configured SonarCloud project."""
    sonar = (ROOT / "sonar-project.properties").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    project_key = re.search(r"^sonar\.projectKey=(.+)$", sonar, re.MULTILINE)

    assert project_key is not None
    project = project_key.group(1)
    assert f"project={project}&metric=alert_status" in readme
    assert f"project={project}&metric=reliability_rating" in readme
    assert f"project={project}&metric=security_rating" in readme
    assert f"project={project}&metric=sqale_rating" in readme
    assert f"shields.io/sonar/coverage/{project}" in readme
    assert f"component_measures?id={project}&metric=coverage" in readme


def test_release_asset_workflow_matches_hacs_filename() -> None:
    """HACS installs from the release asset, so the workflow must build that name."""
    hacs = _load_json(ROOT / "hacs.json")
    workflow = (ROOT / ".github" / "workflows" / "release-asset.yaml").read_text(
        encoding="utf-8"
    )
    domain = _load_json(INTEGRATION / "manifest.json")["domain"]

    assert hacs["filename"] in workflow
    # The zip must hold the CONTENTS of the integration dir, not a nested folder.
    assert f"HEAD:custom_components/{domain}" in workflow


def test_required_public_docs_exist_and_describe_release_mode() -> None:
    """Public docs should explain release installs, security, and release process."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "release-checklist.md").read_text(encoding="utf-8")

    assert "release asset" in readme
    assert "no cloud dependency" in readme.lower()
    assert "KeenDNS protected" in readme
    assert "Repair" in readme
    assert "diagnostics" in security.lower()
    assert "pytest" in checklist
    assert "coverage" in checklist
    assert "manifest.json" in checklist
    assert "CHANGELOG.md" in checklist
    assert "release asset" in checklist


def test_challenge_authentication_copy_is_consistent() -> None:
    """Home Assistant form labels should use polished challenge-auth wording."""
    strings = _load_json(INTEGRATION / "strings.json")
    english = _load_json(INTEGRATION / "translations" / "en.json")

    for payload in (strings, english):
        text = json.dumps(payload, ensure_ascii=False)
        assert "Challenge Auth" not in text
        assert "Use challenge authentication" in text
        assert "NDW2" in text
