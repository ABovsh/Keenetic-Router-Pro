---
description: Cut a Keenetic-Router-Pro release — bump manifest, update CHANGELOG (and README if user-facing), verify, push to main. No tags, no GitHub releases.
argument-hint: "<new-version>  (e.g. 1.7.62)"
disable-model-invocation: true
---

# Release (direct-to-main contract)

This fork ships by pushing **directly to `main`**. There are **no git tags and no
GitHub releases** — the manifest version + CHANGELOG entry are the release record.

## Inputs

`$1` — the new version (`MAJOR.MINOR.PATCH`, no `v` prefix).

## Steps (stop on first failure)

### 1. Validate input

```bash
NEW_VER="$1"
[[ "$NEW_VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Bad version: $NEW_VER"; exit 1; }
MANIFEST=custom_components/keenetic_router_pro/manifest.json
CUR=$(python3 -c "import json;print(json.load(open('$MANIFEST'))['version'])")
echo "Bumping: $CUR -> $NEW_VER"
[[ "$CUR" != "$NEW_VER" ]] || { echo "nothing to bump"; exit 1; }
```

### 2. Bump `manifest.json`

Set `version` to `$NEW_VER` (preserve the file's existing formatting/indentation).

### 3. Update `CHANGELOG.md`

Add a new `## $NEW_VER` section at the top (under the header preamble), grouped
by what the user notices (`### 🔒 Privacy`, `### 🐛 Fixed`, `### 🔧 Changed`,
`### ✨ Added`). Keep it **user-facing** — no tool/agent names, no test/coverage
mentions. Follow the Keep a Changelog style already used in the file.

### 4. Update `README.md` — only if user-facing

If this release changes anything a user sees (a new entity, a behaviour change),
refresh the relevant README section. Pure internal fixes do **not** touch README.

### 5. Verify (the gate)

```bash
python3 -m compileall -q custom_components tests
python3 -m ruff check custom_components/keenetic_router_pro tests
python3 -m pytest -q tests
```

All three must be clean. Fix anything red before continuing.

### 6. Commit and push to main

```bash
git add -A
git commit -m "release: v$NEW_VER — <one-line summary>"
git push origin main
```

No `Co-Authored-By` trailer. No `git tag`. No `gh release`.

### 7. Post-push: SonarCloud

After the push, check the SonarCloud gate on `main` and **fix every new finding**
before considering the release done.

## What this command does NOT do

- Does not create tags or GitHub releases (this fork never does).
- Does not open a PR (there are no feature branches here).
