# Keenetic Router Pro — Codex Instructions

## Repository Workflow

- After making, verifying, and committing changes in this repository, always push the current branch to the configured GitHub remote.
- If the user explicitly asks to work on `main`, commit and push directly to `main`.
- Do not push when verification fails, merge conflicts remain unresolved, or unrelated user changes would be included accidentally.
- If a push is blocked by permissions or authentication, report the blocker clearly and say what access is missing.

## Local-Only Analysis Artifacts

- Keep Graphify output local-only. Do not commit `graphify-out/` files to this public repository.
- Regenerate Graphify locally when architecture analysis is needed, then leave the generated HTML, JSON, reports, locks, and cache files untracked.
- Do not commit `.DS_Store` or other machine-local metadata.
