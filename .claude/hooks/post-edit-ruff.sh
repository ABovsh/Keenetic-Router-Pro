#!/usr/bin/env bash
# PostToolUse hook — tight feedback loop on Python edits.
#
# Runs after any Edit/Write/MultiEdit. If the touched file is Python, run
# `ruff check --fix` on just that file and surface anything that isn't clean.
# Non-blocking (always exits 0): this is a feedback loop, not a gate — the
# pre-push check and CI are the gates.
#
# Note: only `ruff check --fix` (the minimal lint set from pyproject.toml), NOT
# `ruff format` — this fork does not enforce a format standard, so we don't want
# local edits silently reformatted in ways CI never checks.

set -uo pipefail

command -v jq >/dev/null 2>&1 || exit 0

INPUT="$(cat)"
FILE_PATH="$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty')"

[[ -z "$FILE_PATH" ]] && exit 0
[[ "$FILE_PATH" == *.py ]] || exit 0

if command -v ruff >/dev/null 2>&1; then
    RUFF=(ruff)
elif command -v python3 >/dev/null 2>&1; then
    RUFF=(python3 -m ruff)
else
    exit 0
fi

CHECK_OUTPUT="$("${RUFF[@]}" check --fix "$FILE_PATH" 2>&1 || true)"

if [[ -n "$CHECK_OUTPUT" && "$CHECK_OUTPUT" != *"All checks passed!"* ]]; then
    printf 'ruff check (post-edit): %s\n' "$FILE_PATH" >&2
    printf '%s\n' "$CHECK_OUTPUT" >&2
fi

exit 0
