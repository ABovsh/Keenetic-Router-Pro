# Release Checklist

Use this checklist before tagging or announcing a Keenetic Router Pro release.

## Metadata

- `custom_components/keenetic_router_pro/manifest.json` has the new patch version.
- `README.md` version badge matches the manifest version.
- `CHANGELOG.md` latest section matches the manifest version.
- `hacs.json` still declares the supported Home Assistant version.
- Public docs still describe standard HACS source downloads and no release asset requirement.

## Verification

Run these commands from the repository root:

```bash
PYTHONPYCACHEPREFIX=/tmp/keenetic-pycache python3 -m compileall -q custom_components tests
python3 -m coverage run --source=custom_components/keenetic_router_pro -m pytest -q tests
python3 -m coverage report --show-missing --fail-under=40
```

## Security And Privacy

- Password fields remain masked in setup, reauth, and reconfigure flows.
- Diagnostics and logs do not expose passwords, session cookies, full MAC inventories, or DNS-over-HTTPS tokens.
- Plaintext HTTP still raises a Home Assistant Repair issue for non-loopback hosts.
- `SECURITY.md` still explains what redaction can and cannot protect.

## Home Assistant Deployment

- Deploy only after tests pass and the repository is clean.
- Copy `custom_components/keenetic_router_pro/` to the Home Assistant custom component directory.
- Confirm the deployed `manifest.json` version matches the release.
- Restart Home Assistant, or reload the integration when Home Assistant supports reloading this custom integration cleanly in the test environment.
