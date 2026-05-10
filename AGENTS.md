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

## Keenetic CLI / RCI Reference

- For any work involving Keenetic CLI commands, `/rci/parse`, `/rci/show`, router behavior, mesh/MWS commands, firmware operations, WAN/interface statistics, WireGuard/IPsec, HTTP proxy, or static NAT, use the local Keenetic command references before changing code or recommending commands.
- Primary curated reference: `/Users/abovsh/Vault/05-resources/plugins/keenetic-admin/skills/keenetic-admin/references/kn-1811-cli-ha-router-monitoring.md`.
- Authoritative PDF source: `/Users/abovsh/Vault/04-areas/home-network/cli_manual_kn-1811.pdf`.
- Related operational notes live under `/Users/abovsh/Vault/04-areas/home-network/`, especially `ha-integration.md`, `2026-05-03-keenetic-config-assessment.md`, and `routers/`.
- Prefer the curated reference for fast decisions, but consult the PDF when validating exact command syntax or when the integration touches an unverified CLI/RCI surface.
