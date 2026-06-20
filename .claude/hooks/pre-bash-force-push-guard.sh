#!/usr/bin/env bash
# PreToolUse hook — block accidental FORCE-pushes to main/master.
#
# This fork's normal workflow is `git push origin main` (no branches, no tags) —
# that is allowed. What is NOT allowed from an agent session is a force-push that
# rewrites public history. Exits 2 to block; exit 0 lets the command through.
#
# Block condition: a `git push` combining --force/-f/--force-with-lease with the
# main or master ref. A plain (fast-forward) push to main passes through.

set -euo pipefail

command -v jq >/dev/null 2>&1 || exit 0

INPUT="$(cat)"
COMMAND="$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')"

[[ -z "$COMMAND" ]] && exit 0

if printf '%s' "$COMMAND" | grep -Eq 'git[[:space:]]+push' \
   && printf '%s' "$COMMAND" | grep -Eq -- '--force([[:space:]]|=|$)|--force-with-lease|[[:space:]]-f([[:space:]]|$)' \
   && printf '%s' "$COMMAND" | grep -Eq '(master|main)([[:space:]]|$|:)'; then
    cat >&2 <<'EOF'
BLOCKED: force-push to main/master detected.

This fork publishes directly to main; a force-push would rewrite history every
HACS user pulls from. Plain `git push origin main` is fine. If you truly need to
force-push, run it yourself from a terminal (this guard only stops agent pushes).
EOF
    exit 2
fi

exit 0
