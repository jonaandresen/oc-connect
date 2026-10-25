# Development

Everything you need to change, test and ship oc-connect. What was measured about OpenCode and Claude Code, and what is still open, is in [measured.md](measured.md).

## Prerequisites

- `python3` 3.9 or newer - standard library only, nothing to install
- [Claude Code](https://claude.com/claude-code) for `claude plugin validate` and for trying the working copy
- [OpenCode 1](https://opencode.ai) (1.18 or newer) only for the live selftest

## Project layout

```
.claude-plugin/       plugin.json (version, MCP server, options), marketplace.json
bin/oc                entry point for the CLI, the hooks, the monitor and the MCP server
lib/occ/
  core.py             job state, repository helpers, OpenCode HTTP client
  ops.py              job operations (start, send, report, review, done, selftest, ...)
  cli.py              `oc <command>`
  mcp.py              MCP server: the tools and rules Claude sees
  hooks.py            Claude Code hooks (delegation gate, commit gate) and status line installer
  monitor.py          one line per job event, delivered to Claude as a notification
  statusline.py       job rows under Claude's status line
  board.py            live board for a terminal
  terminal.py         open and close worker tabs (tmux, Terminal.app, iTerm2, WezTerm, kitty)
  reaper.py           closes a worker's tab once Claude no longer uses it
  profile.py          the workers' own OpenCode configuration
commands/  skills/    slash commands and the delegate skill
hooks/  monitors/     hook and monitor registration
templates/            guard.json (worker permissions), worker-prompt.md
tests/test_occ.py     unit tests against a fake OpenCode server
scripts/check.sh      the gate
docs/                 user docs, this file, measured.md, demo.gif
```

## Check

```bash
scripts/check.sh
```

The gate. Runs the unit tests on `python3` and on the system Python 3.9 (`/usr/bin/python3` on macOS), then `claude plugin validate --strict .`. Ends with `RESULT: GREEN` or `RED`. Needs no model and changes nothing.

The live check against a real OpenCode 1 - both guard layers, in a throw-away repo:

```bash
bin/oc selftest --model opencode-go/deepseek-v4.1-flash
```

Try the working copy in Claude Code: `claude --plugin-dir .`

## Rules for changes

- Standard library only, Python 3.9 compatible (`from __future__ import annotations`, no `match`, no `X | Y` at runtime).
- Every fix gets a test, and every test gets a defeat test: break the fix, see the test turn red.
- A claim about OpenCode or Claude Code behaviour goes into code or docs only after it was measured, and the measurement goes into [measured.md](measured.md).
- End-to-end probes run in a scratch repo with a local bare `origin`, never in a real project. Clean up with `oc done <slug> --force`, and close only the terminal tabs you opened yourself.

## Branches

- `v1` (this branch): oc-connect 1.x for OpenCode 1, fixes only.
- `main` and `v2`: oc-connect 2.x for OpenCode 2.

A fix that concerns both goes into both branches. The major version follows OpenCode's.

## Ship

1. Version in `.claude-plugin/plugin.json`, entry in `CHANGELOG.md`.
2. `scripts/check.sh` green, commit, `git push`.
3. `claude plugin marketplace update oc-connect && claude plugin update oc@oc-connect`, restart Claude Code.

The installed copy lives in `~/.claude/plugins/cache/oc-connect/oc/<version>/`. Never edit it except for a throw-away experiment, and restore it afterwards.
