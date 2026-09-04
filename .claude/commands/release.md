---
description: Promote rc to main (set version, merge, Sonar), then — only if asked — tag and publish the GitHub release with the keenetic_router_pro.zip asset.
argument-hint: "<new-version>  (e.g. 1.17.0)"
disable-model-invocation: true
---

# Release (rc → main → tag, two gates)

Since 2026-09-02 this fork ships like eveus: work stacks on the ephemeral `rc`
branch, and **"merge to main" and "release" are two separate asks**. This command
runs gate 1 in full and then **stops** — it does not tag unless you say so.

Full contract: the `keenetic-improve` skill (`SKILL.md` Steps 1–2 and
`references/release-mechanics.md`).

## Inputs

`$1` — the new version (`MAJOR.MINOR.PATCH`, no `v` prefix).

## Steps (stop on first failure)

### 1. Validate input and branch state

```bash
NEW_VER="$1"
[[ "$NEW_VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Bad version: $NEW_VER"; exit 1; }
MANIFEST=custom_components/keenetic_router_pro/manifest.json
CUR=$(python3 -c "import json;print(json.load(open('$MANIFEST'))['version'])")
[[ "$CUR" != "$NEW_VER" ]] || { echo "nothing to bump"; exit 1; }
git fetch origin
git log --oneline origin/main..rc || { echo "no rc branch — nothing to promote"; exit 1; }
echo "Promoting rc: $CUR -> $NEW_VER"
```

Grade `$NEW_VER` from the **last published release to the round's end state**,
not commit by commit: patch for fixes and perf, **minor** for new
entities/behaviour, for a changed user-perceivable cadence, or for removing or
renaming a published entity attribute, major for breaking config entries or
deleted entities. A `⚠️ Breaking` entry under a patch bump is always wrong.

### 2. Set the version on `rc` (this is the only place it is allowed)

`manifest.json` `version`, the README version badge, and the CHANGELOG heading
all move to `$NEW_VER` together — `tests/test_release_contracts.py` checks it.
Rename the numberless `## Unreleased` section to `## $NEW_VER`.

### 3. CHANGELOG content

Group under `### Security` / `### Bug fixes` / `### Improvements`. **There is no
`Internal` section** — this is public, HACS-user-facing copy. No tool or agent
names, no coverage numbers, test counts, phases or audit process. Never write a
`🐛 Fixed` bullet for a defect that only ever existed in unreleased code.

### 4. Update `README.md` — only if user-facing

New entity, behaviour change, install step. Pure internal fixes do **not** touch
README. Re-read "Why this fork" when scope shifted.

### 5. Verify (the gate)

```bash
PYTHONPYCACHEPREFIX=/tmp/keenetic-pycache .venv/bin/python -m compileall -q custom_components tests
.venv/bin/python -m ruff check custom_components/keenetic_router_pro tests
.venv/bin/python -m coverage run --source=custom_components/keenetic_router_pro -m pytest -q tests
.venv/bin/python -m coverage report --show-missing --fail-under=90
```

Check each exit code directly. **Never pipe pytest into another command** — the
pipe's exit code hides a red suite.

### 6. Gate 1 — merge to main

```bash
git add custom_components/keenetic_router_pro/manifest.json CHANGELOG.md README.md
git commit -m "release: $NEW_VER — <one-line summary>"
git push origin rc
git checkout main && git merge --no-ff rc && git push origin main
git push origin --delete rc && git branch -d rc
```

Stage explicit paths — never `git add -A`. No `Co-Authored-By` trailer.

### 7. Sonar (gate 1 is not done until this is clean)

The `sonar` job runs **only on pushes to `main`** (`ci.yml`), so this is the
first scan of the round. Wait for it, read the gate, and fix **every** finding —
not just the ones in files you touched. A Sonar fix on already-merged code goes
straight to `main`, no `rc` detour.

### 8. STOP

Gate 1 is complete. **Do not tag and do not publish a release unless Anton
separately asked for one** ("release" / "зарелізь").

### 9. Gate 2 — only on that second ask

Tag `v$NEW_VER`, publish the GitHub release via the REST API (no `gh` on this
host; token from `~/.git-credentials`), then **verify the asset landed**:
`.github/workflows/release-asset.yaml` attaches `keenetic_router_pro.zip` on
`release: published`, and since 1.16.0 `hacs.json` sets `zip_release: true` — a
release without that asset is **not installable at all**. Commands:
`keenetic-improve/references/release-mechanics.md`.

## What this command does NOT do

- Does not tag or publish a release on its own (step 8 stops).
- Does not bump anything while work is still in flight on `rc`.
