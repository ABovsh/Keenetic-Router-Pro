---
description: Run the pre-push gate — compile, ruff, pytest at the CI coverage floor (90), plus the rc/main bookkeeping check CI cannot see.
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

### 2. Ruff (minimal lint set from pyproject.toml)

```bash
.venv/bin/python -m ruff check custom_components/keenetic_router_pro tests
```

Expected: `All checks passed!`.

### 3. Tests at the CI coverage floor

```bash
.venv/bin/python -m coverage run --source=custom_components/keenetic_router_pro -m pytest -q tests
.venv/bin/python -m coverage report --show-missing --fail-under=90
```

`.github/workflows/ci.yml` runs `--fail-under=90`. Match it; never lower it to
make a run pass. Run the two commands separately and check each exit code —
**never pipe pytest into another command**, the pipe's exit code hides a red
suite.

### 4. Bookkeeping — which branch are you on?

```bash
BR=$(git rev-parse --abbrev-ref HEAD)
MANIFEST=custom_components/keenetic_router_pro/manifest.json
VER=$(python3 -c "import json;print(json.load(open('$MANIFEST'))['version'])")
echo "branch=$BR manifest=$VER"
```

- **On `rc`** (the normal case): `manifest.json`, the README badge and the
  CHANGELOG heading must all still sit at the **last published stable** —
  running notes belong under a numberless `## Unreleased`. A bump here fails
  `tests/test_release_contracts.py`. Verify nothing moved:
  `git diff --quiet origin/main -- "$MANIFEST" || echo "STOP: manifest changed on rc"`
- **On `main`** (promotion, or a docs/CI/Sonar fix): if the version moved,
  `grep -q "^## $VER" CHANGELOG.md` must hold and the README badge must match.
  Promote through `/release <version>`, not by hand.

### 5. Leak guard — the two filters are not the same set

The local `pre-push` hook and `.github/workflows/leak-guard.yml` block
**different** files. The hook blocks `docs/(superpowers|internal|plans)/`,
`graphify-out/`, `.env`, `secrets.yaml`, `*.pem`, `*_findings.md`; the workflow
blocks any `superpowers/` or `internal/` directory, `*_PLAN.md`, `*_SPEC.md`,
`*.plan.md`, `AUDIT_FINDINGS*.md`, `*-assessment-*.md`. Passing the hook does
**not** mean CI will pass.

```bash
git ls-files | grep -nE '(^|/)(superpowers|internal)/|_PLAN\.md$|_SPEC\.md$|\.plan\.md$|AUDIT_FINDINGS.*\.md$|-assessment-.*\.md$|(^|/)graphify-out/|(^|/)\.env$|(^|/)secrets\.ya?ml$|\.pem$|_findings\.md$' \
  && echo "STOP: internal artifact is tracked" || echo "leak guard: clean"
```

## Exit criteria

One-line PASS/FAIL per step. All green → clear to push.
