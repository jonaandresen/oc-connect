# oc-connect - working on the plugin

Claude Code plugin: Claude is the lead, OpenCode workers implement. What it does for users is in `README.md`, the version history in `CHANGELOG.md`. This file lives in `.claude/` because `claude plugin validate --strict` rejects a `CLAUDE.md` at the plugin root.

How to change, check and ship the plugin:

@../docs/development.md

What was measured, what is verified and what is still open - keep it current, every new measurement goes there with version and date:

@../docs/measured.md

Commits carry no Claude/AI signature lines.

## Agent skills

### Issue tracker

GitHub Issues in jonaandresen/oc-connect (public), via `gh`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root (created lazily). See `docs/agents/domain.md`.
