---
description: Set up oc-connect - check OpenCode, prove the guard, add job rows to the status line
allowed-tools: Bash(*/bin/oc *)
---
Set up oc-connect for this machine, step by step, and report each result in one line:

1. `"${CLAUDE_PLUGIN_ROOT}/bin/oc" setup` - OpenCode found? Which agents and models? oh-my-opencode is optional.
2. `"${CLAUDE_PLUGIN_ROOT}/bin/oc" selftest` - must end with "guard works". If it says NOT PROVEN, show the calls and stop.
3. Status line: `"${CLAUDE_PLUGIN_ROOT}/bin/oc" install-statusline --dry-run` shows what would change in `~/.claude/settings.json`. Show it to the user and ask before running it without `--dry-run`. The existing status line stays and runs above the job rows.
4. On macOS with Terminal.app: tabs need System Settings > Privacy & Security > Accessibility > Terminal. Without it, workers open in windows instead of tabs. Mention it only if the user uses Terminal.app.
