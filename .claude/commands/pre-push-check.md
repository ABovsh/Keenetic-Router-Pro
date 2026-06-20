---
description: Run the pre-push gate — compile, ruff, pytest with the coverage baseline, and a manifest/CHANGELOG sync check — before pushing to main.
disable-model-invocation: true
---

# Pre-push Check

Run this **before** `git push origin main`. It reproduces what CI gates on, plus
the release-bookkeeping checks CI cannot see.

## Steps (run in order, stop on first failure)

### 1. Compile

```bash
python3 -m compileall -q custom_components tests
```

### 2. Ruff (minimal lint set from pyproject.toml)

```bash
python3 -m ruff check custom_components/keenetic_router_pro tests
```

Expected: `All checks passed!`.

### 3. Tests with the coverage baseline

```bash
python3 -m coverage run -m pytest -q tests && python3 -m coverage report --fail-under=40
```

Mirrors CI (`--fail-under=40`). All tests pass and coverage holds.

### 4. Release-bookkeeping (this fork's rule: every change bumps the manifest)

```bash
MANIFEST=custom_components/keenetic_router_pro/manifest.json
VER=$(python3 -c "import json;print(json.load(open('$MANIFEST'))['version'])")
git diff --quiet origin/main -- "$MANIFEST" || echo "manifest version is $VER (changed) — confirm CHANGELOG has a matching ## $VER entry"
grep -q "^## $VER" CHANGELOG.md && echo "CHANGELOG has $VER" || echo "WARNING: no '## $VER' section in CHANGELOG.md"
```

If the code changed but the manifest version did not, or the CHANGELOG has no
matching entry, stop and run `/release <version>`.

## Exit criteria

One-line PASS/FAIL per step. All green → clear to `git push origin main`.
