#!/usr/bin/env bash
# PreToolUse hook — block accidental FORCE-pushes to main/master.
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

A force-push would rewrite public branch history. Check the branch and retry
with a fast-forward push.
EOF
    exit 2
fi

exit 0
