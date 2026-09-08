---
description: Run the pre-push gate — compile and pytest at the CI coverage floor (90), plus the rc/main bookkeeping check CI cannot see.
disable-model-invocation: true
---

# Pre-push Check

Run this **before** any push. It reproduces what CI gates on, plus the
release-bookkeeping checks CI cannot see.

## Steps (run in order, stop on first failure)

### 1. Compile

```bash
PYTHONPYCACHEPREFIX=/tmp/keenetic-pycache .venv/bin/python -m compileall -q custom_components tests
```

### 2. Tests at the CI coverage floor

```bash
.venv/bin/python -m coverage run --source=custom_components/keenetic_router_pro -m pytest -q tests
.venv/bin/python -m coverage report --show-missing --fail-under=90
```

`.github/workflows/ci.yml` runs `--fail-under=90`. Match it; never lower it to
make a run pass. Run the two commands separately and check each exit code —
**never pipe pytest into another command**, the pipe's exit code hides a red
suite. Test collection also imports `KeeneticClient` through the HA stubs, so it
is the MRO smoke check; a bare Python import fails because HA is not installed.

### 3. Bookkeeping — which branch are you on?

```bash
BR=$(git rev-parse --abbrev-ref HEAD)
MANIFEST=custom_components/keenetic_router_pro/manifest.json
VER=$(python3 -c "import json;print(json.load(open('$MANIFEST'))['version'])")
echo "branch=$BR manifest=$VER"
```

- **On `rc`** (the normal case): `manifest.json`, the README badge and the
  numbered CHANGELOG heading must all still sit at the **last published
  stable**; running notes belong under a numberless `## Unreleased`. The release
  tests check surface equality, while this ancestry check enforces no bump on
  `rc`:
  `git diff --quiet origin/main -- "$MANIFEST" || echo "STOP: manifest changed on rc"`
- **On `main`** (promotion, or a docs/CI/Sonar fix): if the version moved,
  `grep -q "^## $VER" CHANGELOG.md` must hold and the README badge must match.
  Promote through `/release <version>`, not by hand.

### 4. Leak guard

The local pre-push hook and CI use byte-identical copies of
`.github/leak-guard.sh`. Run that source of truth against the commits about to
be published; do not duplicate its regexes. CI also runs Gitleaks against file
content, so this path check is necessary but not a complete credential scan.

```bash
UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)
if [ -n "$UPSTREAM" ]; then
  .github/leak-guard.sh range "$(git merge-base HEAD "$UPSTREAM")" HEAD
else
  .github/leak-guard.sh tree HEAD
fi
```

## Exit criteria

One-line PASS/FAIL per step. All green → clear to push.
