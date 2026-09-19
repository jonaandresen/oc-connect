# Changelog

## 0.4.0

- Guard, second layer: the worker environment rewrites every git push URL (explicit ones included) and disables docker and git over ssh. The OpenCode guard alone let `bash -c "git push"`, `command git push`, `/usr/bin/git push` and `env git push` through (measured). `oc selftest` now runs in a throw-away repo, tries indirect forms too, and judges by what reached the remote.
- Jobs belong to the Claude session that started them; only its monitor reports them, orphans are adopted. `answer` takes the question id and refuses when that question is gone.
- Monitor reports failed turns (API errors, rate limits), stalls (`OC_STALL_MIN`), cost steps (`OC_BUDGET`), questions answered directly in OpenCode, and the start of a job.
- Review gate: `review` records checks, defeat test and gaps, tied to the worktree's exact content; a PreToolUse hook blocks `git commit` in a job worktree without it.
- `done` refuses while uncommitted or unpushed work exists (`force` to discard).
- `delegate` returns right away; the tab opens directly in the job's session (pre-created) and installs dependencies itself.
- Terminal.app: new tabs are detected by window id (every tab is its own AppleScript window). Fixes a bug where the worker command could land in the tab running Claude Code.
- Status lines read the session's message list only when it changed.
- Worker agent keeps waiting through questions; the monitor does not repeat its finish.

## 0.3.0

Rewrite as a real Claude Code plugin.

- Core in Python (standard library, 3.9+) instead of bash; one library for CLI, MCP server, monitor and status lines.
- MCP server `opencode` with tools and instructions, so every session knows when delegating is worth it (`autonomy` option).
- Monitor: Claude is notified when a job asks, finishes or dies.
- Agent `opencode-worker` plus `subagentStatusLine`: jobs show live in the Claude Code agent panel.
- Prompt hook: "do this with OpenCode" is recognised in German and English.
- Subagents such as oh-my-opencode's `fixer` work as job agents (sent per prompt via the API; `--agent` on the TUI rejects them).
- Guard config lives in the state dir instead of the repository; untracked repo `opencode.json` is still applied.
- `.env` files found via git instead of a fixed list; pnpm, yarn and bun lockfiles supported.
- `oc selftest` reads all messages of the test session (the reply alone holds only the last step).
- English UI and docs. Jobs of the old bash version are imported automatically.

## 0.2.0

German bash version: one tab and one OpenCode process per job, status line, board.
